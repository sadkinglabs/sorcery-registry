"""The release workflow's hosted-side checks and pointer flip.

    python -m registry.hosted check-images --export dist/registry.json
    python -m registry.hosted verify-root --base-url URL --tag vX.Y.Z --dist dist
    python -m registry.hosted flip-alias --base-url URL --major v3 --tag vX.Y.Z \
        --versions versions.json --zone ZONE_ID --rule-id RULE_ID

Everything here runs after `dist/` has been uploaded to the release root
and before the GitHub release is created, in this order:

  check-images   every image_urls value in the export answers HEAD 200
                 through the CDN - no published record may reference an
                 object that is not served (a no-op while all are null).
  verify-root    what the CDN serves at the release root is, byte for byte,
                 what was built: registry.json hashes to the committed
                 digest, index.json names the tag, a card object and a slug
                 object answer. Only after this does the workflow write the
                 RELEASED marker and list the release in versions.json.
  flip-alias     point the moving major alias (/v3/) at the release, via
                 the zone's Single Redirect rule, but only when
                 versions.json says the release is the newest of its major
                 - never backwards - and then confirm the alias redirects
                 there. The Cloudflare API token is read from CF_API_TOKEN.

The pure parts (which URLs to check, the rule body, whether to flip) are
functions with tests; the network parts use only the standard library.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import SCHEMA_VERSION, SITE_BASE

USER_AGENT = f"sorcery-registry-release/schema{SCHEMA_VERSION} (+{SITE_BASE})"
CF_API = "https://api.cloudflare.com/client/v4"


# --------------------------------------------------------------------------
# Pure parts
# --------------------------------------------------------------------------

def image_urls_in(export):
    """Every image address a published record references, sorted."""
    urls = set()
    for record in export["cards"] + export["printings"]:
        for face in (record, record.get("back") or {}):
            for url in (face.get("image_urls") or {}).values():
                urls.add(url)
    return sorted(urls)


def should_flip(versions_doc, major, tag):
    """The alias moves only to the newest listed release of its major."""
    return versions_doc.get("latest", {}).get(major) == tag


def redirect_rule(base_url, major, tag, description=None):
    """The Single Redirect rule that sends /{major}/* to /{tag}/*.

    The target keeps the rest of the path: substring() drops the leading
    "/v3" (its length is the major's plus the slash) and the release root
    is prepended. 302, not 301: a permanent redirect would be cached by
    browsers past the next release."""
    base_url = base_url.rstrip("/")
    host = base_url.split("://", 1)[1].split("/", 1)[0]
    prefix_len = len(major) + 1
    return {
        "description": description or f"{major} alias -> {tag} (set by the release workflow)",
        "expression": f'(http.host eq "{host}" and starts_with(http.request.uri.path, "/{major}/"))',
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "status_code": 302,
                "target_url": {
                    "expression": f'concat("{base_url}/{tag}", '
                                  f'substring(http.request.uri.path, {prefix_len}))'},
                "preserve_query_string": True,
            }
        },
        "enabled": True,
    }


# --------------------------------------------------------------------------
# Network parts (standard library only)
# --------------------------------------------------------------------------

def _request(url, method="GET", data=None, headers=None, timeout=30):
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(request, timeout=timeout)


def _status(url, method="HEAD"):
    """(status, headers) with header names lower-cased: servers differ in
    case (Content-Type, content-type) and the checks must not."""
    try:
        with _request(url, method) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}


def _read(url):
    with _request(url) as response:
        return response.read()


def check_images(export_path):
    export = json.loads(Path(export_path).read_text(encoding="utf-8"))
    urls = image_urls_in(export)
    missing = []
    for url in urls:
        status, _ = _status(url)
        if status != 200:
            missing.append(f"{status} {url}")
    if missing:
        print("::error::published records reference images the CDN does not serve:")
        print("\n".join(missing))
        return 1
    print(f"images: {len(urls)} referenced, all served")
    return 0


def verify_root(base_url, tag, dist):
    root = f"{base_url.rstrip('/')}/{tag}"
    dist = Path(dist)
    expected = (dist / "registry.json.sha256").read_text(encoding="utf-8").split()[0]
    problems = []

    served = _read(f"{root}/registry.json")
    actual = hashlib.sha256(served).hexdigest()
    if actual != expected:
        problems.append(f"registry.json served with digest {actual}, built {expected}")

    index = json.loads(_read(f"{root}/index.json"))
    if index.get("dataset_version") != tag:
        problems.append(f"index.json names dataset_version {index.get('dataset_version')!r}, "
                        f"expected {tag!r}")
    if index.get("base_url") != root:
        problems.append(f"index.json names base_url {index.get('base_url')!r}, expected {root!r}")

    card = json.loads((dist / "index" / "cards.json").read_text(encoding="utf-8"))[0]["codex_id"]
    slug = next(iter(json.loads((dist / "index" / "slugs.json").read_text(encoding="utf-8"))))
    for path in (f"cards/{card}.json", f"slugs/{slug}.json", "registry.json.sha256",
                 "schema.json", "manifest.json"):
        status, headers = _status(f"{root}/{path}", method="GET")
        if status != 200:
            problems.append(f"{status} {root}/{path}")
        elif path.endswith(".json") and "application/json" not in headers.get("content-type", ""):
            problems.append(f"{root}/{path} served as {headers.get('content-type')!r}")

    if problems:
        print("::error::the release root does not serve what was built:")
        print("\n".join(problems))
        return 1
    print(f"verified {root}: registry.json {actual}, index.json {tag}, "
          f"cards/{card}.json, slugs/{slug}.json")
    return 0


def _cf(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        with _request(f"{CF_API}{path}", method, data, headers) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read() or b"{}")
    if not payload.get("success"):
        raise RuntimeError(f"Cloudflare API {method} {path}: {payload.get('errors')}")
    return payload["result"]


def flip_alias(base_url, major, tag, versions_path, zone, rule_id, attempts=20):
    doc = json.loads(Path(versions_path).read_text(encoding="utf-8"))
    if not should_flip(doc, major, tag):
        print(f"{major} stays at {doc.get('latest', {}).get(major)}: {tag} is not the newest")
        return 0
    token = os.environ.get("CF_API_TOKEN")
    if not token:
        print("::error::CF_API_TOKEN is not set")
        return 1
    entrypoint = _cf("GET", f"/zones/{zone}/rulesets/phases/http_request_dynamic_redirect/entrypoint",
                     token)
    current = next((r for r in entrypoint.get("rules", []) if r["id"] == rule_id), None)
    if current is None:
        print(f"::error::rule {rule_id} is not in the zone's dynamic redirect ruleset "
              f"{entrypoint['id']}")
        return 1
    rule = redirect_rule(base_url, major, tag, current.get("description"))
    _cf("PATCH", f"/zones/{zone}/rulesets/{entrypoint['id']}/rules/{rule_id}", token, rule)

    probe = f"{base_url.rstrip('/')}/{major}/index.json"
    want = f"{base_url.rstrip('/')}/{tag}/index.json"
    for _ in range(attempts):
        status, headers = _status(probe, method="GET")
        if status == 302 and headers.get("location") == want:
            print(f"{major} alias now redirects to {tag}")
            return 0
        time.sleep(3)
    print(f"::error::{probe} does not redirect to {want} (last: {status} "
          f"{headers.get('location')!r})")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-images")
    p.add_argument("--export", default="dist/registry.json")
    p = sub.add_parser("verify-root")
    p.add_argument("--base-url", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--dist", default="dist")
    p = sub.add_parser("flip-alias")
    p.add_argument("--base-url", required=True)
    p.add_argument("--major", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--versions", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--rule-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "check-images":
        return check_images(args.export)
    if args.command == "verify-root":
        return verify_root(args.base_url, args.tag, args.dist)
    return flip_alias(args.base_url, args.major, args.tag, args.versions, args.zone, args.rule_id)


if __name__ == "__main__":
    sys.exit(main())
