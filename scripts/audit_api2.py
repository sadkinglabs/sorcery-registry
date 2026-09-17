"""Second pass: headers, compression, error shapes, endpoint templates, and the
data claims the site's own documentation makes. Read-only."""
import gzip
import io
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.audit_http import opened  # noqa: E402

BASE = "https://api.kairosarchive.net"
UA = "sorcery-registry-audit/1.0 (+https://kairosarchive.net)"
OUT = []


def raw(url, headers=None, method="GET", follow=True):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, method=method)
    opener = urllib.request.build_opener()
    if not follow:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
    try:
        with opened(opener, req, identified="User-Agent" in h) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def check(cid, claim, ok, evidence):
    OUT.append((cid, claim, ok, evidence))
    print(f"{'PASS' if ok else 'FAIL'}  {cid:7} {claim}\n        {str(evidence)[:300]}")


def note(cid, what, evidence):
    OUT.append((cid, what, None, evidence))
    print(f"NOTE  {cid:7} {what}\n        {str(evidence)[:300]}")


status, _, body = raw(f"{BASE}/versions.json")
if status != 200:
    print(f"FAIL  K0     versions.json could not be read: HTTP {status}\n        {body[:200]!r}")
    sys.exit(1)
versions = json.loads(body)
tag = versions["latest"]["v3"]
root = f"{BASE}/{tag}"

# ---------- K. what the edge actually sends ----------
for cid, url in [("K1", f"{BASE}/versions.json"), ("K2", f"{root}/index.json")]:
    status, headers, body = raw(url)
    interesting = {k.lower(): v for k, v in headers.items()
                   if k.lower() in ("cache-control", "content-type", "etag", "age", "cf-cache-status",
                                    "content-encoding", "vary", "access-control-allow-origin", "last-modified")}
    note(cid, f"headers on {url.rsplit('/', 1)[1]}", json.dumps(interesting))

status, headers, _ = raw(f"{BASE}/versions.json")
cc = headers.get("cache-control") or headers.get("Cache-Control") or ""
check("K3", "docs: versions.json is cached 60 s", "max-age=60" in cc, f"Cache-Control: {cc!r} (empty means the object carries none)")

# ---------- L. compression on the wire ----------
for cid, path in [("L1", "registry.json"), ("L2", "index/cards.json"), ("L3", "history/cards.json")]:
    status, headers, body = raw(f"{root}/{path}", headers={"Accept-Encoding": "gzip, br"})
    enc = headers.get("content-encoding") or headers.get("Content-Encoding") or "none"
    # urllib does not decompress; body is what crossed the wire.
    decoded = len(gzip.decompress(body)) if enc == "gzip" else len(body)
    check(cid, f"{path} is compressed for clients that ask",
          enc != "none", f"Content-Encoding: {enc}; {len(body)/1024:.0f} KB on the wire, {decoded/1024:.0f} KB decoded")

# ---------- M. error shapes ----------
for cid, path, what in [("M1", "cards/C999999.json", "unknown card id"),
                        ("M2", "printings/P999999.json", "unknown printing id"),
                        ("M3", "sets/099.json", "unknown set code"),
                        ("M4", "cards/not-an-id.json", "malformed id")]:
    status, headers, body = raw(f"{root}/{path}")
    ct = headers.get("content-type") or headers.get("Content-Type") or ""
    check(cid, f"{what} is a clean 404", status == 404, f"HTTP {status}, Content-Type: {ct}, {len(body)} bytes")
status, headers, body = raw(f"{BASE}/images/P000001.nosuchkey.normal.webp")
check("M5", "an unknown image address is a 404", status == 404, f"HTTP {status}")

# ---------- N. conditional and HEAD ----------
status, headers, body = raw(f"{root}/index.json")
etag = headers.get("etag") or headers.get("ETag")
if etag:
    s2, h2, b2 = raw(f"{root}/index.json", headers={"If-None-Match": etag})
    check("N1", "a conditional request saves the body", s2 == 304, f"ETag {etag} -> HTTP {s2}, {len(b2)} bytes")
else:
    check("N1", "a conditional request saves the body", False, "no ETag on the response")
status, headers, body = raw(f"{root}/registry.json", method="HEAD")
cl = headers.get("content-length") or headers.get("Content-Length")
check("N2", "HEAD works, so a client can check size before downloading 6 MB",
      status == 200 and len(body) == 0 and cl, f"HTTP {status}, Content-Length: {cl}, body {len(body)} bytes")
s1, h1, _ = raw(f"{root}/index.json")
s2, h2, _ = raw(f"{root}/index.json")
note("N3", "edge cache status on two consecutive fetches",
     f"{h1.get('cf-cache-status') or h1.get('Cf-Cache-Status')} then {h2.get('cf-cache-status') or h2.get('Cf-Cache-Status')}")

# ---------- O. content types ----------
for cid, path, want in [("O1", "index.json", "application/json"), ("O2", "registry.json.sha256", "text"),
                        ("O3", "RELEASED", "text")]:
    status, headers, _ = raw(f"{root}/{path}", method="HEAD")
    ct = (headers.get("content-type") or headers.get("Content-Type") or "")
    check(cid, f"{path} is served as {want}", want in ct, f"Content-Type: {ct!r}")

# ---------- P. every endpoint template in index.json resolves ----------
index = json.loads(raw(f"{root}/index.json")[2])
registry = json.loads(raw(f"{root}/registry.json")[2])
sample = {"codex_id": registry["cards"][0]["codex_id"], "printing_id": registry["printings"][0]["printing_id"],
          "set_code": registry["sets"][0]["set_code"],
          "slug": next(r["slug"] for r in registry["slug_history"] if r["valid_to"] is None)}
bad = []
for name, template in index["endpoints"].items():
    path = template
    for key, value in sample.items():
        path = path.replace("{" + key + "}", value)
    if "{" in path:
        bad.append((name, template, "unresolved placeholder"))
        continue
    status, _, _ = raw(f"{root}/{path}", method="HEAD")
    if status != 200:
        bad.append((name, path, f"HTTP {status}"))
check("P1", "every endpoint template in index.json resolves to a real object",
      not bad, f"{len(index['endpoints'])} templates, failures: {bad}")

# ---------- Q. data claims the docs make ----------
import math
bad_power = [c["codex_id"] for c in registry["cards"]
             if c["power"] != (None if c["attack"] is None or c["defense"] is None
                               else (c["attack"] if c["attack"] == c["defense"]
                                     else math.floor((c["attack"] + c["defense"]) / 2)))]
check("Q1", "power equals attack when attack equals defense, else floor of the average",
      not bad_power, f"{len(registry['cards'])} cards checked, mismatches: {bad_power[:5]}")
bk = next((c for c in registry["cards"] if c["name"] == "Black Knight"), None)
check("Q2", "the documented example: Black Knight 5/3 carries power 4",
      bk and bk["attack"] == 5 and bk["defense"] == 3 and bk["power"] == 4,
      f"{bk['codex_id'] if bk else '?'}: attack={bk['attack'] if bk else '?'} defense={bk['defense'] if bk else '?'} power={bk['power'] if bk else '?'}")
life_non_avatar = [c["codex_id"] for c in registry["cards"] if c["life"] is not None and c["category"] != "Avatar"]
check("Q3", "life belongs to Avatars", not life_non_avatar, f"non-Avatars carrying life: {life_non_avatar}")
by_pid = {p["printing_id"]: p for p in registry["printings"]}
mismatch = [c["codex_id"] for c in registry["cards"]
            if c["default_printing_id"] and c["image_urls"] != by_pid[c["default_printing_id"]]["image_urls"]]
check("Q4", "a card carries its default printing's image_urls", not mismatch, f"mismatches: {mismatch[:5]}")
status_mismatch = [c["codex_id"] for c in registry["cards"]
                   if c["default_printing_id"] and c["image_status"] != by_pid[c["default_printing_id"]]["image_status"]]
check("Q5", "a card carries its default printing's image_status", not status_mismatch, f"mismatches: {status_mismatch[:5]}")
lowres = [p for p in registry["printings"] if p["image_status"] == "lowres"]
ok_ = [p for p in registry["printings"] if p["image_status"] == "ok"]
check("Q6", "lowres and ok describe real source sizes", bool(lowres) and bool(ok_),
      f"{len(ok_)} ok, {len(lowres)} lowres, {len(registry['printings']) - len(ok_) - len(lowres)} missing")
try:
    from PIL import Image
    for cid, group, claim in [("Q7", lowres, (380, 531)), ("Q8", ok_, (744, 1039))]:
        p = group[0]
        body = raw(p["image_urls"]["original"])[2]
        with Image.open(io.BytesIO(body)) as im:
            size = im.size
        check(cid, f"{p['image_status']} originals are about {claim[0]}x{claim[1]}",
              size == claim, f"{p['printing_id']}: {size[0]}x{size[1]}")
except ImportError:
    pass
schema = json.loads(raw(f"{root}/schema.json")[2])
try:
    import jsonschema
    jsonschema.validate(registry, schema)
    check("Q9", "the served export validates against the served schema", True, "jsonschema: valid")
except ImportError:
    check("Q9", "the served export validates against the served schema", False, "jsonschema not installed")
except Exception as e:
    check("Q9", "the served export validates against the served schema", False, str(e)[:200])

# ---------- S. the error contract ----------
# What a client meets when something goes wrong. Until custom errors are
# configured these report as notes; once a status answers with JSON the
# full contract is asserted, so configuring it turns these into guards.
ERROR_CASES = [
    ("S1", "a missing object", f"{root}/cards/C999999.json", {}, "GET"),
    ("S2", "a request with no User-Agent", f"{root}/index.json", None, "GET"),
    ("S3", "a method the bucket does not serve", f"{root}/index.json", {}, "DELETE"),
    ("S4", "a path outside any release root", f"{BASE}/not-a-release/index.json", {}, "GET"),
]
for cid, what, url, extra, method in ERROR_CASES:
    headers = None if extra is None else dict(extra)
    if headers is None:
        req = urllib.request.Request(url, method=method)  # no User-Agent at all
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status, hdrs, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, hdrs, body = e.code, e.headers, e.read()
    else:
        headers["Origin"] = "https://example.com"
        status, hdrs, body = raw(url, headers=headers, method=method)
    ct = (hdrs.get("content-type") or "").split(";")[0]
    acao = hdrs.get("access-control-allow-origin")
    retry = hdrs.get("retry-after")
    facts = (f"HTTP {status}, {ct or 'no content-type'}, {len(body)} bytes, "
             f"CORS header: {acao or 'absent'}" + (f", Retry-After: {retry}" if retry else ""))
    if ct == "application/json":
        try:
            payload = json.loads(body)
        except Exception:
            payload = None
        check(cid, f"{what} answers with the JSON error contract",
              payload is not None and "error" in payload and acao == "*" and len(body) < 1024,
              f"{facts}; body={str(payload)[:120]}")
    else:
        note(cid, f"{what} (custom error not configured)", facts)

# The front door is a redirect, not an error: a person pasting the bare domain
# should land on discovery. Do not follow it, or a 302 reads as a 200 and the
# error-envelope assertions above fire on a perfectly good response.
status, hdrs, _ = raw(f"{BASE}/", headers={"Origin": "https://example.com"}, method="GET", follow=False)
target = hdrs.get("location") or ""
if status in (301, 302, 307, 308):
    check("S5", "the bare domain sends a client to the discovery document",
          target.rstrip("/").endswith("/versions.json"), f"HTTP {status} -> {target}")
else:
    note("S5", "the bare domain (no redirect configured)", f"HTTP {status}, {len(_)} bytes")

# A browser doing a conditional cross-origin GET sends If-None-Match, which is
# not a CORS-safelisted header, so it preflights first. If OPTIONS is refused,
# every cross-origin client is stuck re-downloading bodies it already holds.
status, hdrs, body = raw(f"{root}/index.json", method="OPTIONS",
                         headers={"Origin": "https://example.com",
                                  "Access-Control-Request-Method": "GET",
                                  "Access-Control-Request-Headers": "if-none-match"})
facts = (f"HTTP {status}, allow-origin: {hdrs.get('access-control-allow-origin') or 'absent'}, "
         f"allow-methods: {hdrs.get('access-control-allow-methods') or 'absent'}, "
         f"allow-headers: {hdrs.get('access-control-allow-headers') or 'absent'}")
if status in (200, 204) and hdrs.get("access-control-allow-origin"):
    check("S6", "a CORS preflight for a conditional GET is answered", True, facts)
else:
    note("S6", "a CORS preflight for a conditional GET is refused", facts)

# ---------- R. the documented curl commands, verbatim ----------
cmds = [
    ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-H", "User-Agent: my-deck-tool/1.0 (me@example.com)",
     f"{BASE}/versions.json"],
    ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-H", "User-Agent: my-deck-tool/1.0 (me@example.com)",
     f"{BASE}/v3/cards/C000001.json"],
    ["curl", "--fail", "--location", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
     "--user-agent", "my-card-tool/1.0 (contact@example.com)", f"{BASE}/v3/cards/C000139.json"],
]
for i, cmd in enumerate(cmds, 1):
    r = subprocess.run(cmd, capture_output=True, text=True)
    check(f"R{i}", "a documented curl command works as printed",
          r.returncode == 0 and r.stdout.strip() in ("200", "302"), f"exit {r.returncode}, HTTP {r.stdout.strip()}")

passed = sum(1 for _, _, ok, _ in OUT if ok is True)
failed = sum(1 for _, _, ok, _ in OUT if ok is False)
print(f"\n{passed} passed, {failed} failed, {sum(1 for _, _, ok, _ in OUT if ok is None)} notes")
print("FAILURES:")
for cid, claim, ok, ev in OUT:
    if ok is False:
        print(f"  {cid}: {claim}\n      {ev}")
sys.exit(0)
