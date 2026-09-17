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
import sys
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


def validate(doc):
    """Raise ValueError unless `doc` has the shape of a discovery document:
    a base_url, a latest map and a releases list whose entries carry a
    release tag and a digest. Guards the workflow against publishing from
    a truncated or foreign file."""
    if not isinstance(doc, dict):
        raise ValueError("not a JSON object")
    if not isinstance(doc.get("base_url"), str) or not doc["base_url"]:
        raise ValueError("base_url missing")
    if not isinstance(doc.get("latest"), dict) or not isinstance(doc.get("releases"), list):
        raise ValueError("latest or releases missing")
    for entry in doc["releases"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise ValueError("a release entry lacks a digest")
        parse_tag(entry.get("tag"))
    for major, tag in doc["latest"].items():
        if major_of(tag) != major or tag not in {r["tag"] for r in doc["releases"]}:
            raise ValueError(f"latest.{major} = {tag} is not a listed release of that major")
    return doc


def read_previous(path):
    """The document at `path`, validated. A missing, empty or malformed
    file is an error: the workflow decides separately, and explicitly,
    that there is no previous document (scripts/fetch_versions.sh)."""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"--previous {path}: no such file")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"--previous {path}: empty file")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(f"--previous {path}: not JSON ({err})") from None
    try:
        return validate(doc)
    except ValueError as err:
        raise ValueError(f"--previous {path}: {err}") from None


def latest_tag(doc, major):
    """The newest listed release of a major ('v3'), or None."""
    return doc.get("latest", {}).get(major)


def render(doc):
    return json.dumps(doc, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--add", metavar="TAG")
    parser.add_argument("--validate", metavar="PATH",
                        help="check that PATH is a discovery document and exit (0 yes, 2 no)")
    parser.add_argument("--sha256", help="digest of the release's registry.json")
    parser.add_argument("--base-url", help="domain root; required for the first document")
    parser.add_argument("--previous", help="the versions.json currently served (omit on first run)")
    parser.add_argument("--export", default="export/registry.json",
                        help="where to read schema_version from")
    parser.add_argument("--schema-version", type=int, help="override the export's header")
    parser.add_argument("--released-at", help="YYYY-MM-DD; default today (UTC)")
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args()

    if args.validate:
        try:
            read_previous(args.validate)
        except ValueError as err:
            print(f"versions: {err}", file=sys.stderr)
            sys.exit(2)
        return
    if not args.add or not args.sha256:
        parser.error("--add TAG and --sha256 DIGEST are required (or --validate PATH)")

    previous = None
    if args.previous:
        try:
            previous = read_previous(args.previous)
        except ValueError as err:
            print(f"versions: {err}", file=sys.stderr)
            sys.exit(2)
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
