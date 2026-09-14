"""Card images: discovering the publisher's public folder.

    python -m registry.images list [--out review/image-listing.json]
                                   [--export export/registry.json]

The official API guidance is "host images yourself; download released
card images from the public image folder" - a Google Drive folder whose
files are reported to be named by API slug (004-witch-b-s.png). This
module lists that folder through the Drive API (a plain API key, which
can only read what is public; GDRIVE_API_KEY in the environment), maps
every file to a printing through slug_history - so a file named with a
superseded slug still finds its printing - and writes one JSON document
with the raw listing, the mapping, and a summary a human can read before
any image is downloaded. Nothing here fetches an image.

Requests are sequential and identified; a listing is a handful of
requests, not a crawl. The API key never appears in output: URLs are
redacted before they reach an error message.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from . import SCHEMA_VERSION, SITE_BASE

DRIVE_FOLDER = "17IrJkRGmIU9fDSTU2JQEU9JlFzb5liLJ"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
LIST_FIELDS = ("nextPageToken,files(id,name,mimeType,md5Checksum,size,modifiedTime,"
               "imageMediaMetadata(width,height))")
USER_AGENT = f"sorcery-registry-images/schema{SCHEMA_VERSION} (+{SITE_BASE})"
LISTING_PATH = Path("review") / "image-listing.json"


# --------------------------------------------------------------------------
# Drive (standard library only; the fetcher is injectable for tests)
# --------------------------------------------------------------------------

def _redact(url):
    return re.sub(r"([?&]key=)[^&]+", r"\1REDACTED", url)


def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(f"Drive API {error.code} for {_redact(url)}: {body}") from None


def list_folder(folder_id, api_key, get=_get_json, pause=0.2):
    """Every file under the folder, subfolders included, each with the
    folder path it sits in (relative to the root). Sequential, paged."""
    files = []
    pending = [(folder_id, "")]
    while pending:
        current, path = pending.pop(0)
        token = None
        while True:
            params = {
                "q": f"'{current}' in parents and trashed = false",
                "fields": LIST_FIELDS,
                "pageSize": 1000,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "key": api_key,
            }
            if token:
                params["pageToken"] = token
            page = get(f"{DRIVE_FILES}?{urllib.parse.urlencode(params)}")
            for entry in page.get("files", []):
                if entry.get("mimeType") == FOLDER_MIME:
                    pending.append((entry["id"], f"{path}{entry['name']}/"))
                    continue
                meta = entry.get("imageMediaMetadata") or {}
                files.append({
                    "id": entry["id"],
                    "name": entry["name"],
                    "path": path,
                    "mime_type": entry.get("mimeType"),
                    "size": int(entry["size"]) if entry.get("size") is not None else None,
                    "md5": entry.get("md5Checksum"),
                    "modified": entry.get("modifiedTime"),
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                })
            token = page.get("nextPageToken")
            if not token:
                break
            time.sleep(pause)
    files.sort(key=lambda f: (f["path"], f["name"]))
    return files


# --------------------------------------------------------------------------
# Mapping and summary (pure)
# --------------------------------------------------------------------------

def slug_owners(export):
    """slug -> printing_id for every slug that has ever existed, plus
    which slug is current for each printing."""
    owners = {}
    for row in export["slug_history"]:
        owners[row["slug"]] = row["printing_id"]
    current = {}
    for printing in export["printings"]:
        owners.setdefault(printing["slug"], printing["printing_id"])
        current[printing["printing_id"]] = printing["slug"]
    return owners, current


def file_key(name):
    """The part of a filename that should be a slug: the stem, lower-cased.
    Everything else about the naming is a finding, not an assumption."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.strip().lower()


def map_listing(files, export):
    """Attach a printing to every file whose stem is a slug the registry
    has ever issued. Unmapped files and printings claimed by more than
    one file are reported, never guessed at."""
    owners, current = slug_owners(export)
    mapped, unmapped = [], []
    claims = {}
    for entry in files:
        key = file_key(entry["name"])
        printing_id = owners.get(key)
        record = dict(entry)
        if printing_id is None:
            unmapped.append(record)
            continue
        record["printing_id"] = printing_id
        record["slug"] = key
        record["slug_is_current"] = current.get(printing_id) == key
        mapped.append(record)
        claims.setdefault(printing_id, []).append(entry["name"])
    duplicates = {pid: names for pid, names in sorted(claims.items()) if len(names) > 1}
    covered = set(claims)
    missing = sorted(pid for pid in current if pid not in covered)
    return {"mapped": mapped, "unmapped": unmapped, "duplicates": duplicates,
            "printings_without_file": missing}


def summarize(files, mapping):
    ext = Counter((f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else "")
                  for f in files)
    dims = Counter(f"{f['width']}x{f['height']}" for f in files
                   if f.get("width") and f.get("height"))
    folders = Counter(f["path"] or "/" for f in files)
    sizes = [f["size"] for f in files if f.get("size") is not None]
    return {
        "files": len(files),
        "folders": dict(sorted(folders.items())),
        "extensions": dict(ext.most_common()),
        "mime_types": dict(Counter(f["mime_type"] for f in files).most_common()),
        "dimensions": dict(dims.most_common(12)),
        "bytes_total": sum(sizes),
        "bytes_largest": max(sizes) if sizes else 0,
        "mapped": len(mapping["mapped"]),
        "mapped_to_superseded_slug": sum(1 for m in mapping["mapped"] if not m["slug_is_current"]),
        "unmapped": len(mapping["unmapped"]),
        "printings_claimed_twice": len(mapping["duplicates"]),
        "printings_without_file": len(mapping["printings_without_file"]),
        "unmapped_examples": [f["path"] + f["name"] for f in mapping["unmapped"][:25]],
    }


def render_summary(summary):
    lines = [f"files: {summary['files']} in {len(summary['folders'])} folder(s), "
             f"{summary['bytes_total'] / 1e6:.0f} MB total, largest {summary['bytes_largest'] / 1e6:.1f} MB"]
    for name, count in summary["folders"].items():
        lines.append(f"  folder {name!r}: {count}")
    lines.append("extensions: " + ", ".join(f"{k or '(none)'}={v}" for k, v in summary["extensions"].items()))
    lines.append("dimensions: " + ", ".join(f"{k}={v}" for k, v in summary["dimensions"].items()))
    lines.append(f"mapped to a printing: {summary['mapped']} "
                 f"({summary['mapped_to_superseded_slug']} via a superseded slug); "
                 f"unmapped: {summary['unmapped']}; printings claimed twice: "
                 f"{summary['printings_claimed_twice']}; printings without a file: "
                 f"{summary['printings_without_file']}")
    for example in summary["unmapped_examples"]:
        lines.append(f"  unmapped: {example}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list", help="list the public folder and map files to printings")
    p.add_argument("--folder", default=DRIVE_FOLDER)
    p.add_argument("--export", default="export/registry.json")
    p.add_argument("--out", default=str(LISTING_PATH))
    args = parser.parse_args(argv)

    api_key = os.environ.get("GDRIVE_API_KEY")
    if not api_key:
        print("::error::GDRIVE_API_KEY is not set")
        return 1
    export = json.loads(Path(args.export).read_text(encoding="utf-8"))
    files = list_folder(args.folder, api_key)
    mapping = map_listing(files, export)
    summary = summarize(files, mapping)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"folder": args.folder, "summary": summary,
                               "mapping": mapping, "files": files},
                              indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")
    print(render_summary(summary))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
