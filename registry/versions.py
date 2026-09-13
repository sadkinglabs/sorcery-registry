"""The discovery document: versions.json at the domain root.

    python -m registry.versions --add v3.1.0 --sha256 <digest> \
        --base-url https://api.kairosarchive.net \
        [--previous versions.json] [--export export/registry.json] \
        [--released-at 2026-09-14] [--out versions.json]

One small file (served with a short cache) tells a consumer which
releases the domain serves and which is the newest of each major:

    {
      "base_url": "https://api.kairosarchive.net",
      "latest": {"v3": "v3.1.0"},
      "releases": [
        {"tag": "v3.1.0", "schema_version": 9, "released_at": "2026-09-14",
         "sha256": "<registry.json digest>"}
      ]
    }

A release is listed here only after the release workflow has verified,
byte for byte, that the CDN serves it - never from a bucket listing,
which an interrupted upload would also leave behind. The document is
rebuilt from the previous one plus the release being added, by a pure
function with three rules: releases are ordered newest first by semantic
version; `latest` for a major only ever moves forward (re-running an
older release never points consumers back); adding a tag that is already
listed is a no-op, unless its digest differs, which is refused - the
bytes behind a published tag never change.
"""

import argparse
import datetime
import json
import re
from pathlib import Path

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_tag(tag):
    """'v3.1.0' -> (3, 1, 0). Only plain semantic versions are release
    tags; anything else is refused rather than sorted by guesswork."""
    match = TAG_RE.match(tag or "")
    if not match:
        raise ValueError(f"not a release tag: {tag!r} (expected vMAJOR.MINOR.PATCH)")
    return tuple(int(part) for part in match.groups())


def major_of(tag):
    return f"v{parse_tag(tag)[0]}"


def empty(base_url):
    return {"base_url": base_url.rstrip("/"), "latest": {}, "releases": []}


def add_release(previous, tag, schema_version, released_at, sha256, base_url=None):
    """Return the document with `tag` listed. Pure; `previous` is not
    modified. `previous` may be None (first release ever)."""
    parse_tag(tag)
    if previous is None:
        if base_url is None:
            raise ValueError("the first versions.json needs a base_url")
        previous = empty(base_url)
    doc = {"base_url": (base_url or previous["base_url"]).rstrip("/"),
           "latest": dict(previous.get("latest", {})),
           "releases": [dict(r) for r in previous.get("releases", [])]}

    entry = {"tag": tag, "schema_version": schema_version,
             "released_at": released_at, "sha256": sha256}
    listed = next((r for r in doc["releases"] if r["tag"] == tag), None)
    if listed is not None:
        if listed["sha256"] != sha256:
            raise ValueError(f"{tag} is already published with digest {listed['sha256']}; "
                             f"refusing to relist it with {sha256}")
        return doc  # idempotent re-run: nothing to add
    doc["releases"].append(entry)
    doc["releases"].sort(key=lambda r: parse_tag(r["tag"]), reverse=True)

    # latest per major is the newest tag listed for it - so it never moves
    # backwards, whatever order releases are (re)run in.
    latest = {}
    for release in doc["releases"]:
        latest.setdefault(major_of(release["tag"]), release["tag"])
    doc["latest"] = dict(sorted(latest.items(), key=lambda kv: parse_tag(kv[1])))
    return doc


def latest_tag(doc, major):
    """The newest listed release of a major ('v3'), or None."""
    return doc.get("latest", {}).get(major)


def render(doc):
    return json.dumps(doc, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--add", required=True, metavar="TAG")
    parser.add_argument("--sha256", required=True, help="digest of the release's registry.json")
    parser.add_argument("--base-url", help="domain root; required for the first document")
    parser.add_argument("--previous", help="the versions.json currently served (omit on first run)")
    parser.add_argument("--export", default="export/registry.json",
                        help="where to read schema_version from")
    parser.add_argument("--schema-version", type=int, help="override the export's header")
    parser.add_argument("--released-at", help="YYYY-MM-DD; default today (UTC)")
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args()

    previous = None
    if args.previous:
        text = Path(args.previous).read_text(encoding="utf-8").strip()
        previous = json.loads(text) if text else None
    schema_version = args.schema_version
    if schema_version is None:
        header = json.loads(Path(args.export).read_text(encoding="utf-8"))["header"]
        schema_version = header["schema_version"]
    released_at = args.released_at or datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    doc = add_release(previous, args.add, schema_version, released_at, args.sha256, args.base_url)
    rendered = render(doc)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
