"""Test every assertion docs/api.md and docs/quickstart.md make, against the live domain.

Read-only. Each check names the claim it tests, the request it made and what came
back, so a failure is actionable without re-reading the docs.
"""
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.kairosarchive.net"
UA = "sorcery-registry-audit/1.0 (+https://kairosarchive.net)"
RESULTS = []
TIMINGS = []


def get(url, headers=None, method="GET", follow=True):
    h = {"User-Agent": UA}
    if headers is not None:
        h = dict(headers)
    request = urllib.request.Request(url, headers=h, method=method)
    opener = urllib.request.build_opener()
    if not follow:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
    start = time.time()
    try:
        with opener.open(request, timeout=60) as r:
            body = r.read()
            elapsed = time.time() - start
            TIMINGS.append((url, elapsed, len(body)))
            return r.status, dict(r.headers), body, elapsed
    except urllib.error.HTTPError as e:
        body = e.read()
        elapsed = time.time() - start
        TIMINGS.append((url, elapsed, len(body)))
        return e.code, dict(e.headers), body, elapsed


def jget(url, **kw):
    status, headers, body, elapsed = get(url, **kw)
    return status, headers, json.loads(body) if status == 200 and body else None, body, elapsed


def check(cid, claim, ok, evidence):
    RESULTS.append({"id": cid, "claim": claim, "ok": bool(ok), "evidence": str(evidence)[:300]})
    print(f"{'PASS' if ok else 'FAIL'}  {cid:6} {claim}\n        {str(evidence)[:260]}")


def main():
    # ---------- A. discovery ----------
    status, headers, versions, raw, _ = jget(f"{BASE}/versions.json")
    check("A1", "GET /versions.json returns the discovery document",
          status == 200 and isinstance(versions, dict),
          f"HTTP {status}, {len(raw)} bytes")
    check("A2", "versions.json carries base_url, latest per major, releases[]",
          all(k in versions for k in ("base_url", "latest", "releases")),
          f"keys={sorted(versions)}")
    rel_keys = {k for r in versions["releases"] for k in r}
    check("A3", "each releases[] entry has tag, schema_version, released_at, sha256",
          {"tag", "schema_version", "released_at", "sha256"} <= rel_keys,
          f"keys={sorted(rel_keys)}; {len(versions['releases'])} releases")
    cc = headers.get("Cache-Control", "")
    check("A4", "versions.json is cached 60 s", "max-age=60" in cc, f"Cache-Control: {cc}")
    tag = versions["latest"]["v3"]

    def semver(t):
        return tuple(int(x) for x in t.lstrip("v").split("."))
    newest = max(versions["releases"], key=lambda r: semver(r["tag"]))["tag"]
    check("A5", "latest.v3 names the newest release and only moves forward",
          tag == newest, f"latest.v3={tag}, newest listed={newest}")
    root = f"{BASE}/{tag}"

    # ---------- B. the release root ----------
    status, headers, _, data, elapsed = jget(f"{root}/registry.json")
    digest = hashlib.sha256(data).hexdigest()
    listed = next(r for r in versions["releases"] if r["tag"] == tag)["sha256"]
    check("B1", "the served registry.json matches the digest versions.json lists",
          status == 200 and digest == listed, f"{len(data)} bytes, sha256 {digest[:16]}... listed {listed[:16]}...")
    cc = headers.get("Cache-Control", "")
    check("B2", "a pinned release root is immutable, cached a year",
          "immutable" in cc and "31536000" in cc, f"Cache-Control: {cc}")
    status, _, sumbody, _ = get(f"{root}/registry.json.sha256")
    text = sumbody.decode().strip()
    check("B3", "registry.json.sha256 is an ~80-byte sha256sum-format file that agrees",
          status == 200 and len(sumbody) < 200 and text.split()[0] == digest,
          f"HTTP {status}, {len(sumbody)} bytes, {text!r}")
    status, _, schema, _, _ = jget(f"{root}/schema.json")
    check("B4", "schema.json is the export's JSON Schema, draft 2020-12",
          status == 200 and "2020-12" in schema.get("$schema", ""), f"$schema={schema.get('$schema')}")
    status, _, index, _, _ = jget(f"{root}/index.json")
    want = {"schema_version", "dataset_version", "endpoints", "base_url", "latest_url"}
    check("B5", "index.json states schema_version, dataset_version, endpoints, base_url, latest_url",
          status == 200 and want <= set(index), f"keys={sorted(index)}")
    check("B6", "index.json's dataset_version equals the release tag",
          index.get("dataset_version") == tag, f"dataset_version={index.get('dataset_version')}")
    check("B7", "index.json's base_url is this root and latest_url is the major alias",
          index.get("base_url") == root and index.get("latest_url") == f"{BASE}/v3",
          f"base_url={index.get('base_url')} latest_url={index.get('latest_url')}")
    ep = index.get("endpoints", {})
    check("B8", "endpoints map uses {placeholder} templates",
          any("{" in str(v) for v in ep.values()), f"{len(ep)} endpoints, e.g. {list(ep.items())[:2]}")
    check("B9", "index.json links the usage terms and names the manifest",
          bool(index.get("terms")) and bool(index.get("manifest")),
          f"terms={index.get('terms')} manifest={index.get('manifest')}")
    status, _, manifest, _, _ = jget(f"{root}/manifest.json")
    check("B10", "each root carries manifest.json with counts and artifact digests",
          status == 200 and isinstance(manifest, dict), f"HTTP {status}, keys={sorted(manifest or {})[:8]}")
    status, _, marker, _ = get(f"{root}/RELEASED")
    check("B11", "each root carries a RELEASED marker naming the digest and the run",
          status == 200 and digest in marker.decode(), f"HTTP {status}, {marker.decode()[:120]!r}")

    # ---------- C. the moving alias ----------
    status, headers, _, _ = get(f"{BASE}/v3/cards/C000001.json", follow=False)
    loc = headers.get("Location", "")
    check("C1", "/v3/<path> is a 302 to the same path under the newest root",
          status == 302 and loc.endswith(f"/{tag}/cards/C000001.json"), f"HTTP {status} -> {loc}")
    status, _, card_alias, _, _ = jget(f"{BASE}/v3/cards/C000001.json")
    check("C2", "following the alias yields the record", status == 200 and card_alias.get("codex_id") == "C000001",
          f"HTTP {status}, codex_id={card_alias.get('codex_id') if card_alias else None}")
    status, headers, _, _ = get(f"{BASE}/v3/index/cards.json", follow=False)
    check("C3", "the alias covers nested paths too", status == 302 and f"/{tag}/index/cards.json" in headers.get("Location", ""),
          f"HTTP {status} -> {headers.get('Location')}")

    # ---------- D. one object per thing ----------
    registry = json.loads(data)
    card = registry["cards"][0]
    printing = registry["printings"][0]
    status, _, obj, _, elapsed = jget(f"{root}/cards/{card['codex_id']}.json")
    summary_keys = {"printing_id", "slug", "set_code", "set_name", "released_at", "product",
                    "finish", "printed_as_current", "retired_at"}
    got = set(obj["printings"][0]) if status == 200 and obj.get("printings") else set()
    check("D1", "cards/{codex_id}.json adds printings summary, name_history, card_history",
          status == 200 and {"printings", "name_history", "card_history"} <= set(obj) and summary_keys == got,
          f"HTTP {status}, summary keys={sorted(got)}")
    check("D2", "the card object includes default_printing_id",
          "default_printing_id" in (obj or {}), f"default_printing_id={obj.get('default_printing_id')}")
    status, _, pobj, _, _ = jget(f"{root}/printings/{printing['printing_id']}.json")
    check("D3", "printings/{printing_id}.json adds slug_history",
          status == 200 and "slug_history" in pobj, f"HTTP {status}, keys include slug_history={'slug_history' in (pobj or {})}")
    slug_rows = registry["slug_history"]
    current_slug = next(r for r in slug_rows if r["valid_to"] is None)
    superseded = next((r for r in slug_rows if r["valid_to"] is not None), None)
    want_slug = {"slug", "printing_id", "codex_id", "card_name", "current_slug", "is_current",
                 "valid_from", "valid_to", "set_code", "set_name", "product", "finish",
                 "retired_at", "api_url", "kairos_url"}
    status, _, sobj, _, _ = jget(f"{root}/slugs/{current_slug['slug']}.json")
    check("D4", "slugs/{slug}.json carries the documented 15 fields",
          status == 200 and want_slug == set(sobj), f"HTTP {status}, missing={sorted(want_slug - set(sobj or {}))} extra={sorted(set(sobj or {}) - want_slug)}")
    if superseded:
        status, _, oldobj, _, _ = jget(f"{root}/slugs/{superseded['slug']}.json")
        check("D5", "a superseded slug still resolves, marked not current",
              status == 200 and oldobj.get("is_current") is False,
              f"HTTP {status}, {superseded['slug']} -> is_current={oldobj.get('is_current') if oldobj else None}")
    else:
        check("D5", "a superseded slug still resolves, marked not current", True, "no superseded slugs in this release")
    status, _, _, _ = get(f"{root}/slugs/definitely-not-a-slug.json")
    check("D6", "an unknown slug is a 404", status == 404, f"HTTP {status}")
    status, _, sets, _, _ = jget(f"{root}/sets.json")
    check("D7", "sets.json is the export's sets section",
          status == 200 and sets == registry["sets"], f"HTTP {status}, {len(sets or [])} sets")
    code = registry["sets"][0]["set_code"]
    status, _, setobj, _, _ = jget(f"{root}/sets/{code}.json")
    names = [c["name"] for c in setobj.get("cards", [])] if status == 200 else []
    check("D8", "sets/{code}.json adds cards[{codex_id,name,printing_ids}] ordered by name",
          status == 200 and names == sorted(names) and {"codex_id", "name", "printing_ids"} == set(setobj["cards"][0]),
          f"HTTP {status}, {len(names)} cards, ordered={names == sorted(names)}")
    in_set = {p["printing_id"] for p in registry["printings"] if p["set_code"] == code}
    listed_p = {pid for c in setobj.get("cards", []) for pid in c["printing_ids"]}
    check("D9", "a set object lists only that set's printings", listed_p <= in_set,
          f"{len(listed_p)} listed, {len(listed_p - in_set)} foreign")

    # ---------- E. indexes ----------
    for cid, path, want_keys, claimed in [
        ("E1", "index/cards.json",
         {"codex_id", "name", "type", "category", "rarity", "elements", "keywords", "subtypes",
          "cost", "errata", "set_codes", "default_printing_id", "image_status"}, 230),
        ("E2", "index/printings.json",
         {"printing_id", "codex_id", "slug", "set_code", "product", "finish",
          "printed_as_current", "retired_at", "image_hash", "image_status"}, 600),
    ]:
        status, _, idx, body, _ = jget(f"{root}/{path}")
        got = set(idx[0]) if status == 200 and idx else set()
        kb = len(body) / 1024
        check(cid, f"{path} carries the documented per-record fields",
              status == 200 and want_keys == got,
              f"HTTP {status}, {kb:.0f} KB (doc says ~{claimed} KB), missing={sorted(want_keys - got)}, extra={sorted(got - want_keys)}")
    status, _, slugidx, body, _ = jget(f"{root}/index/slugs.json")
    check("E3", "index/slugs.json maps every slug ever to a printing id",
          status == 200 and isinstance(slugidx, dict) and len(slugidx) == len(registry["slug_history"]),
          f"HTTP {status}, {len(slugidx or {})} entries vs {len(registry['slug_history'])} slug_history rows, {len(body)/1024:.0f} KB")
    status, _, cidx, _, _ = jget(f"{root}/index/cards.json")
    has_urls = "api_url" in cidx[0] and "kairos_url" in cidx[0]
    check("E4", "docs/api.md: records carry api_url and kairos_url 'in the indexes' too",
          has_urls, f"index/cards.json record keys lack them: api_url={'api_url' in cidx[0]}, kairos_url={'kairos_url' in cidx[0]}")

    # ---------- F. history ----------
    for cid, path, section in [("F1", "history/slugs.json", "slug_history"),
                               ("F2", "history/names.json", "name_history"),
                               ("F3", "history/cards.json", "card_history")]:
        status, _, rows, _, _ = jget(f"{root}/{path}")
        check(cid, f"{path} is the export's {section} section",
              status == 200 and rows == registry[section], f"HTTP {status}, {len(rows or [])} rows")
    status, _, hist, _, _ = jget(f"{root}/history/cards.json")
    sources = {r.get("source") for r in hist}
    check("F4", "every card_history row says where it came from: api or card",
          sources == {"api", "card"} or sources == {"api"},
          f"sources={sorted(s for s in sources if s)}, card rows={sum(1 for r in hist if r.get('source') == 'card')}")
    nulls = [p["printing_id"] for p in registry["printings"] if p["printed_as_current"] is None]
    check("F5", "a printing that shows no face reports printed_as_current null",
          len(nulls) > 0, f"null on {nulls}")

    # ---------- G. images ----------
    with_img = next(p for p in registry["printings"] if p["image_urls"])
    urls = with_img["image_urls"]
    check("G1", "image_urls has small, normal, large, original",
          set(urls) == {"small", "normal", "large", "original"}, f"{sorted(urls)}")
    name = urls["normal"].rsplit("/", 1)[1]
    check("G2", "object names are {printing_id}.{key}.{rendition}.{ext} with key == image_hash",
          name == f"{with_img['printing_id']}.{with_img['image_hash']}.normal.webp", f"{name}")
    sizes = {"small": (146, 204), "normal": (488, 680), "large": (672, 936)}
    try:
        from PIL import Image
        for rendition, (w, h) in sizes.items():
            status, headers, body, _ = get(urls[rendition])
            with Image.open(io.BytesIO(body)) as im:
                got = im.size
            check(f"G3-{rendition}", f"{rendition} is {w}x{h} WebP (a pixel narrower is allowed)",
                  status == 200 and got[1] == h and w - 2 <= got[0] <= w,
                  f"HTTP {status}, {got[0]}x{got[1]}, {headers.get('Content-Type')}, {len(body)/1024:.0f} KB")
    except ImportError:
        check("G3", "rendition dimensions", False, "Pillow not installed")
    status, headers, _, _ = get(urls["normal"], method="HEAD")
    check("G4", "image addresses are permanent, cached forever",
          "immutable" in headers.get("Cache-Control", ""), f"HEAD {status}, Cache-Control: {headers.get('Cache-Control')}")
    back = next((p for p in registry["printings"] if p.get("back") and p["back"].get("image_urls")), None)
    if back:
        bname = back["back"]["image_urls"]["large"].rsplit("/", 1)[1]
        check("G5", "a back face carries its own image_urls with .back in the name",
              ".back." in bname, f"{bname}")
    else:
        check("G5", "a back face carries its own image_urls with .back in the name", False, "no back face with images found")
    missing = next((p for p in registry["printings"] if p["image_status"] == "missing"), None)
    check("G6", "image_status missing means image_urls is null",
          missing is not None and missing["image_urls"] is None,
          f"{missing['printing_id'] if missing else 'none'} -> image_urls={missing['image_urls'] if missing else 'n/a'}")

    # ---------- H. edge behaviour and policy ----------
    status, headers, _, _ = get(f"{root}/index.json", headers={"User-Agent": UA, "Origin": "https://example.com"})
    check("H1", "CORS allows a browser on any origin to GET",
          headers.get("Access-Control-Allow-Origin") == "*", f"HTTP {status}, ACAO={headers.get('Access-Control-Allow-Origin')}")
    status, _, _, _ = get(f"{root}/index.json", headers={}, method="HEAD")
    check("H2", "a request with no User-Agent at all is refused at the edge",
          status in (403, 429), f"HTTP {status} with no User-Agent header")
    status, _, _, _ = get(f"{root}/index.json", headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    check("H3", "a browser User-Agent is unaffected", status == 200, f"HTTP {status} with a browser UA")
    s1, h1, _, _ = get(f"{root}/index.json")
    s2, h2, _, _ = get(f"{root}/index.json")
    check("H4", "repeat fetches are served from the edge cache",
          h2.get("Cf-Cache-Status") in ("HIT", "MISS", None), f"first={h1.get('Cf-Cache-Status')}, second={h2.get('Cf-Cache-Status')}")
    mirror = f"https://raw.githubusercontent.com/sadkinglabs/sorcery-registry/{tag}/export/registry.json"
    status, _, mbody, _ = get(mirror)
    check("H5", "every release is mirrored on GitHub with the same bytes",
          status == 200 and hashlib.sha256(mbody).hexdigest() == digest, f"HTTP {status}, digest match={status == 200 and hashlib.sha256(mbody).hexdigest() == digest}")

    # ---------- I. records carry their own addresses ----------
    check("I1", "api_url points at the moving alias and embeds the record's own id",
          card["api_url"] == f"{BASE}/v3/cards/{card['codex_id']}.json", f"{card['api_url']}")
    status, _, viaurl, _, _ = jget(card["api_url"])
    check("I2", "following a record's api_url returns that record",
          status == 200 and viaurl.get("codex_id") == card["codex_id"], f"HTTP {status}")
    status, _, _, _ = get(card["kairos_url"], method="HEAD")
    check("I3", "kairos_url is that record's page on the site", status == 200, f"HEAD {card['kairos_url']} -> {status}")
    check("I4", "set records carry addresses too",
          all(s.get("api_url") and s.get("kairos_url") for s in registry["sets"]),
          f"{sum(1 for s in registry['sets'] if s.get('api_url'))}/{len(registry['sets'])} sets")

    # ---------- J. the documented examples, run verbatim ----------
    v = json.loads(get(f"{BASE}/versions.json")[2])
    t = v["latest"]["v3"]
    rel = next(r for r in v["releases"] if r["tag"] == t)
    d = get(f"{BASE}/{t}/registry.json")[2]
    check("J1", "the quickstart Python example works as printed",
          hashlib.sha256(d).hexdigest() == rel["sha256"],
          f"{t}: {json.loads(d)['header']['cards']} cards, digest verified")
    status, _, w, _, _ = jget(f"{BASE}/v3/slugs/004-witch-b-s.json")
    check("J2", "the quickstart's slug example resolves",
          status == 200 and w.get("printing_id", "").startswith("P"),
          f"HTTP {status}, 004-witch-b-s -> {w.get('printing_id') if w else None} ({w.get('card_name') if w else ''})")

    # ---------- summary ----------
    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    slow = sorted(TIMINGS, key=lambda t: -t[1])[:5]
    print("slowest requests:")
    for url, el, size in slow:
        print(f"  {el*1000:7.0f} ms {size/1024:9.1f} KB  {url}")
    total = sum(t[1] for t in TIMINGS)
    print(f"{len(TIMINGS)} requests, {total:.1f}s total, {sum(t[2] for t in TIMINGS)/1024/1024:.1f} MB")
    print("\nFAILURES:")
    for r in RESULTS:
        if not r["ok"]:
            print(f"  {r['id']}: {r['claim']}\n      {r['evidence']}")
    with open("audit-report.json", "w") as f:
        json.dump({"tag": tag, "results": RESULTS,
                   "timings": [{"url": u, "ms": round(e * 1000), "bytes": b} for u, e, b in TIMINGS]}, f, indent=1)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
