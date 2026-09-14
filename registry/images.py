"""Card images: discovery, download, renditions, upload, verification.

    python -m registry.images list   [--out review/image-listing.json]
    python -m registry.images fetch  [--listing ...] [--limit N] [--work work/images]
    python -m registry.images upload [--work work/images] [--bucket ...]
    python -m registry.images verify [--work work/images]

The official API guidance is "host images yourself; download released
card images from the public image folder" - a Google Drive folder whose
files are named by API slug (004-witch-b-s.png; a "-r" suffix is the
reverse of a double-faced card). Everything the registry knows about
images lives in data/images.json, committed like data/overrides.json:
per printing and face, which Drive file (id, MD5, dimensions) the image
came from and the art-version key it was published under. The export
derives image_urls and image_status from that file; the database is not
involved.

list    lists the folder (a plain API key can only read what is public;
        GDRIVE_API_KEY), maps every file to a printing and face through
        every slug ever issued plus data/image-decisions.json, and writes
        a document with the raw listing, the mapping and a summary.
fetch   downloads what changed (a new file, a new MD5, a new recipe),
        sequentially and identified, renders the renditions and records
        the source in data/images.json after each file.
upload  copies the rendered objects into the bucket's images/ prefix.
        Names are content-addressed, so an existing object is never
        overwritten.
verify  every object named in data/images.json answers HEAD 200 through
        the CDN with the size that was rendered.

Renditions follow Scryfall's vocabulary and sizes: small 146x204,
normal 488x680, large 672x936 (WebP, aspect preserved, so a rendition
may be a pixel narrower than nominal), plus the untouched original. The
publisher's files come in two resolutions - 380x531 for the early sets,
744x1039 for the later ones - and the owner's decision is to upscale the
low one so every card has the same renditions, and to say so:
image_status "lowres" when the large rendition needed upscaling.

The art-version key is sha256(original bytes || recipe)[:12]: it changes
when the art changes or the encoding recipe changes, so the bytes at a
published name never change. The API key never appears in output.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from . import IMAGE_BASE, SCHEMA_VERSION, SITE_BASE

DRIVE_FOLDER = "17IrJkRGmIU9fDSTU2JQEU9JlFzb5liLJ"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
LIST_FIELDS = ("nextPageToken,files(id,name,mimeType,md5Checksum,size,modifiedTime,"
               "imageMediaMetadata(width,height))")
USER_AGENT = f"sorcery-registry-images/schema{SCHEMA_VERSION} (+{SITE_BASE})"
LISTING_PATH = Path("review") / "image-listing.json"
DECISIONS_PATH = Path("data") / "image-decisions.json"
IMAGES_PATH = Path("data") / "images.json"
WORK_PATH = Path("work") / "images"

# The recipe: everything that determines the rendered bytes. Bump it when
# any of this changes, and every image gets a new key (a new address).
RENDITION_RECIPE = 1
RENDITIONS = (("small", 146, 204), ("normal", 488, 680), ("large", 672, 936))
WEBP_QUALITY = 85
# A source narrower than the large rendition had to be upscaled for it.
LOWRES_BELOW_WIDTH = 672
BACK_SUFFIX = "-r"


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


class DriveError(RuntimeError):
    """An HTTP failure from Drive, with the reason Google gives (e.g.
    userRateLimitExceeded, downloadQuotaExceeded) when the body is JSON.
    Google's HTML "Sorry..." page - shown when it decides an address is
    sending automated traffic - is reported as reason "blocked"."""

    def __init__(self, code, url, body=b""):
        self.code = code
        self.reason = None
        text = body[:600].decode("utf-8", "replace")
        try:
            errors = json.loads(text).get("error", {}).get("errors", [])
            self.reason = errors[0].get("reason") if errors else None
        except (ValueError, AttributeError):
            if "<html" in text.lower() and code in (403, 429):
                self.reason = "blocked"
        detail = "" if self.reason == "blocked" else f": {text[:200]!r}"
        super().__init__(f"Drive API {code} ({self.reason or 'no reason given'}) for "
                         f"{_redact(url)}{detail}")


# Google throttles anonymous downloads; these come back on a good day
# after a pause. Anything else is a real answer.
RETRIABLE_REASONS = {"userRateLimitExceeded", "rateLimitExceeded", "quotaExceeded",
                     "backendError", "internalError"}
# The block page lasts longer than a throttle: one long wait, then give
# the file up so the run can decide to stop (see fetch_many).
BLOCK_WAIT_SECONDS = 90
# After this many files in a row refused, the address is blocked for the
# day: stop fetching and let the run publish what it has.
MAX_CONSECUTIVE_FAILURES = 5


def _get_bytes(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise DriveError(error.code, url, error.read() or b"") from None


def retriable(error):
    if isinstance(error, DriveError):
        return error.code == 429 or error.code >= 500 or (
            error.code == 403 and error.reason in RETRIABLE_REASONS)
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))


def get_with_retry(url, get_bytes=_get_bytes, attempts=6, sleep=time.sleep, log=print):
    """Fetch, backing off 2, 4, 8, 16, 32 seconds on throttling and
    transient failures; one long wait on Google's block page; anything
    else is raised at once."""
    for attempt in range(1, attempts + 1):
        try:
            return get_bytes(url)
        except Exception as error:
            if isinstance(error, DriveError) and error.reason == "blocked":
                if attempt > 1:
                    raise
                log(f"  blocked by Google; waiting {BLOCK_WAIT_SECONDS}s once: {error}", flush=True)
                sleep(BLOCK_WAIT_SECONDS)
                continue
            if not retriable(error) or attempt == attempts:
                raise
            delay = 2 ** attempt
            log(f"  retry {attempt}/{attempts - 1} in {delay}s: {error}", flush=True)
            sleep(delay)


def download_url(file_id, api_key):
    return f"{DRIVE_FILES}/{urllib.parse.quote(file_id)}?alt=media&key={urllib.parse.quote(api_key)}"


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


def load_decisions(path=DECISIONS_PATH):
    path = Path(path)
    if not path.exists():
        return {"assign": {}, "ignore": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    for name, entry in doc.get("assign", {}).items():
        if entry.get("face") not in ("front", "back") or not entry.get("printing_id") \
                or not entry.get("reason"):
            raise ValueError(f"image decision for {name!r} needs printing_id, face and reason")
    return {"assign": doc.get("assign", {}), "ignore": doc.get("ignore", {})}


def map_listing(files, export, decisions=None):
    """Attach a printing and a face to every file the rules can place:
    a stem that is a slug the registry has ever issued is that printing's
    front; the same stem with "-r" is its back, when the printing has one;
    data/image-decisions.json assigns or ignores the rest by hand. What is
    left is reported, never guessed at."""
    decisions = decisions or {"assign": {}, "ignore": {}}
    owners, current = slug_owners(export)
    has_back = {p["printing_id"]: p.get("back") is not None for p in export["printings"]}
    mapped, unmapped, ignored = [], [], []
    claims = {}
    for entry in files:
        record = dict(entry)
        name = entry["name"]
        if name in decisions["ignore"]:
            record["reason"] = decisions["ignore"][name]
            ignored.append(record)
            continue
        key = file_key(name)
        face = "front"
        if name in decisions["assign"]:
            decision = decisions["assign"][name]
            printing_id, face = decision["printing_id"], decision["face"]
            record["decided"] = decision["reason"]
            slug = current.get(printing_id)
        else:
            printing_id = owners.get(key)
            slug = key
            if printing_id is None and key.endswith(BACK_SUFFIX):
                front_key = key[:-len(BACK_SUFFIX)]
                if owners.get(front_key) is not None and has_back.get(owners[front_key]):
                    printing_id, face, slug = owners[front_key], "back", front_key
        if printing_id is None:
            unmapped.append(record)
            continue
        record["printing_id"] = printing_id
        record["face"] = face
        record["slug"] = slug
        record["slug_is_current"] = current.get(printing_id) == slug
        mapped.append(record)
        claims.setdefault((printing_id, face), []).append(name)
    duplicates = {f"{pid}/{face}": names for (pid, face), names in sorted(claims.items())
                  if len(names) > 1}
    covered = {pid for pid, face in claims if face == "front"}
    missing = sorted(pid for pid in current if pid not in covered)
    backs_missing = sorted(pid for pid, back in has_back.items()
                           if back and (pid, "back") not in claims)
    return {"mapped": mapped, "unmapped": unmapped, "ignored": ignored,
            "duplicates": duplicates, "printings_without_file": missing,
            "backs_without_file": backs_missing}


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
        "faces_claimed_twice": len(mapping["duplicates"]),
        "printings_without_file": len(mapping["printings_without_file"]),
        "backs_without_file": len(mapping.get("backs_without_file", [])),
        "ignored": len(mapping.get("ignored", [])),
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
                 f"unmapped: {summary['unmapped']}; ignored by decision: {summary['ignored']}; "
                 f"faces claimed twice: {summary['faces_claimed_twice']}; printings without "
                 f"a file: {summary['printings_without_file']}; back faces without a file: "
                 f"{summary['backs_without_file']}")
    for example in summary["unmapped_examples"]:
        lines.append(f"  unmapped: {example}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Renditions and the art-version key (pure)
# --------------------------------------------------------------------------

def art_key(original_bytes, recipe=RENDITION_RECIPE):
    """First 12 hex of sha256(original || recipe). New art or a new recipe
    is a new key, so the bytes behind a published name never change."""
    digest = hashlib.sha256()
    digest.update(original_bytes)
    digest.update(f"\nrecipe={recipe}\n".encode())
    return digest.hexdigest()[:12]


def render(original_bytes):
    """The renditions of one original: {name: (bytes, width, height)} plus
    the source's own size. Aspect preserved: the image is scaled to fit the
    nominal box (Lanczos), never distorted, upscaled when the source is
    smaller - the owner's decision, flagged through `lowres`."""
    from PIL import Image
    with Image.open(io.BytesIO(original_bytes)) as image:
        image.load()
        source = image.convert("RGBA") if image.mode not in ("RGB", "RGBA") else image.copy()
    width, height = source.size
    out = {}
    for name, box_w, box_h in RENDITIONS:
        scale = min(box_w / width, box_h / height)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = source.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=6)
        out[name] = (buffer.getvalue(), size[0], size[1])
    return out, (width, height)


def is_lowres(width):
    return width < LOWRES_BELOW_WIDTH


def object_name(printing_id, key, face, rendition, ext):
    back = ".back" if face == "back" else ""
    return f"{printing_id}.{key}{back}.{rendition}.{ext}"


def original_extension(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else "png"


# --------------------------------------------------------------------------
# data/images.json: what the registry holds
# --------------------------------------------------------------------------

def load_images(path=IMAGES_PATH):
    path = Path(path)
    if not path.exists():
        return {"recipe": RENDITION_RECIPE, "printings": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_images(state, path=IMAGES_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {"recipe": state["recipe"],
               "printings": {pid: {face: state["printings"][pid][face]
                                   for face in ("front", "back") if face in state["printings"][pid]}
                             for pid in sorted(state["printings"])}}
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def plan_fetch(mapping, state):
    """The mapped files whose image the registry does not hold yet: never
    fetched, a different MD5 upstream, or rendered under an older recipe."""
    todo = []
    for entry in mapping["mapped"]:
        held = state["printings"].get(entry["printing_id"], {}).get(entry["face"])
        if held and held.get("md5") == entry.get("md5") and held.get("recipe") == state.get("recipe"):
            continue
        todo.append(entry)
    return todo


def image_objects(state):
    """Every object name data/images.json implies, with the size rendered."""
    objects = []
    for pid, faces in state["printings"].items():
        for face, held in faces.items():
            for rendition, size in held["objects"].items():
                ext = held["original_ext"] if rendition == "original" else "webp"
                objects.append((object_name(pid, held["key"], face, rendition, ext), size))
    return objects


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def _need_key():
    api_key = os.environ.get("GDRIVE_API_KEY")
    if not api_key:
        print("::error::GDRIVE_API_KEY is not set")
        sys.exit(1)
    return api_key


def cmd_list(args):
    api_key = _need_key()
    export = json.loads(Path(args.export).read_text(encoding="utf-8"))
    files = list_folder(args.folder, api_key)
    mapping = map_listing(files, export, load_decisions(args.decisions))
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


def fetch_one(entry, api_key, work, state, get_bytes=_get_bytes, today=None, sleep=time.sleep,
              log=print):
    """Download one mapped file, render it, write the objects under `work`
    and record the source in `state` (not yet saved)."""
    original = get_with_retry(download_url(entry["id"], api_key), get_bytes, sleep=sleep, log=log)
    md5 = hashlib.md5(original).hexdigest()
    if entry.get("md5") and md5 != entry["md5"]:
        raise RuntimeError(f"{entry['name']}: downloaded MD5 {md5} differs from the listing's "
                           f"{entry['md5']}; the folder changed under us, list again")
    key = art_key(original, state["recipe"])
    renditions, (width, height) = render(original)
    ext = original_extension(entry["name"])
    pid, face = entry["printing_id"], entry["face"]
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    objects = {}
    (work / object_name(pid, key, face, "original", ext)).write_bytes(original)
    objects["original"] = len(original)
    for rendition, (data, _, _) in renditions.items():
        (work / object_name(pid, key, face, rendition, "webp")).write_bytes(data)
        objects[rendition] = len(data)
    state["printings"].setdefault(pid, {})[face] = {
        "key": key,
        "recipe": state["recipe"],
        "source": entry["name"],
        "drive_id": entry["id"],
        "md5": md5,
        "width": width,
        "height": height,
        "lowres": is_lowres(width),
        "original_ext": ext,
        "objects": objects,
        "fetched": today or datetime.date.today().isoformat(),
    }
    return key


def fetch_many(todo, api_key, work, state, images_path, get_bytes=_get_bytes,
               pause=0.5, sleep=time.sleep, log=print,
               max_consecutive_failures=MAX_CONSECUTIVE_FAILURES):
    """Fetch every entry in turn. A file that fails after its retries is
    recorded and skipped - the run goes on, the state holds only what
    succeeded, and the next run tries the failure again - because one
    bad answer from Google must not discard hundreds of good ones. When
    several files in a row are refused, the address is blocked for the
    day: the loop stops early and leaves the rest for the next run."""
    failures = []
    consecutive = 0
    for index, entry in enumerate(todo, 1):
        try:
            key = fetch_one(entry, api_key, work, state, get_bytes=get_bytes, sleep=sleep, log=log)
        except Exception as error:  # noqa: BLE001 - recorded, not swallowed
            failures.append({"name": entry["name"], "printing_id": entry["printing_id"],
                             "face": entry["face"], "error": str(error)})
            log(f"[{index}/{len(todo)}] FAILED {entry['name']}: {error}", flush=True)
            consecutive += 1
            if consecutive >= max_consecutive_failures:
                remaining = len(todo) - index
                log(f"::warning::{consecutive} files refused in a row; Google is refusing this "
                    f"address. Stopping with {remaining} left for the next run.", flush=True)
                break
            sleep(pause)
            continue
        consecutive = 0
        save_images(state, images_path)  # progress survives an interrupted run
        log(f"[{index}/{len(todo)}] {entry['printing_id']} {entry['face']} <- {entry['name']} -> {key}",
            flush=True)
        sleep(pause)
    return failures


def cmd_fetch(args):
    api_key = _need_key()
    listing = json.loads(Path(args.listing).read_text(encoding="utf-8"))
    state = load_images(args.images)
    if state.get("recipe") != RENDITION_RECIPE:
        print(f"recipe changed {state.get('recipe')} -> {RENDITION_RECIPE}: every image is re-rendered")
        state["recipe"] = RENDITION_RECIPE
    todo = plan_fetch(listing["mapping"], state)
    if args.only:
        todo = [e for e in todo if e["printing_id"] in args.only]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} image(s) to fetch", flush=True)
    failures = fetch_many(todo, api_key, args.work, state, args.images, pause=args.pause)
    fetched = len(todo) - len(failures)
    print(f"fetched {fetched}, failed {len(failures)}", flush=True)
    if failures:
        report = Path(args.failures)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")
        reasons = Counter(f["error"].split(" for ")[0] for f in failures)
        print("failures by kind: " + "; ".join(f"{k}: {n}" for k, n in reasons.most_common()))
        print(f"::warning::{len(failures)} image(s) could not be fetched this run (see {report}); "
              f"they are retried on the next run")
        if args.strict or fetched == 0:
            return 1
    return 0


def cmd_upload(args):
    """aws s3 sync of the work directory into images/: objects are
    content-addressed, so an existing name is the same bytes and sync
    (by size) leaves it alone. The aws CLI is only the S3 client for R2."""
    work = Path(args.work)
    if not work.exists() or not any(work.iterdir()):
        print("nothing to upload")
        return 0
    command = ["aws", "s3", "sync", str(work), f"s3://{args.bucket}/images/", "--no-progress",
               "--size-only", "--cache-control", "public, max-age=31536000, immutable"]
    print(" ".join(command))
    return subprocess.call(command)


def cmd_verify(args):
    from .hosted import _status
    state = load_images(args.images)
    problems = []
    checked = 0
    for name, size in image_objects(state):
        url = f"{IMAGE_BASE}/{name}"
        status, headers = _status(url)
        checked += 1
        if status != 200:
            problems.append(f"{status} {url}")
        elif headers.get("content-length") and int(headers["content-length"]) != size:
            problems.append(f"{url}: served {headers['content-length']} bytes, rendered {size}")
    if problems:
        print("::error::objects named in data/images.json are not served as rendered:")
        print("\n".join(problems[:50]))
        return 1
    print(f"verified {checked} image objects through the CDN")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list the public folder and map files to printings")
    p.add_argument("--folder", default=DRIVE_FOLDER)
    p.add_argument("--export", default="export/registry.json")
    p.add_argument("--decisions", default=str(DECISIONS_PATH))
    p.add_argument("--out", default=str(LISTING_PATH))
    p.set_defaults(run=cmd_list)

    p = sub.add_parser("fetch", help="download what changed and render the renditions")
    p.add_argument("--listing", default=str(LISTING_PATH))
    p.add_argument("--images", default=str(IMAGES_PATH))
    p.add_argument("--work", default=str(WORK_PATH))
    p.add_argument("--limit", type=int, default=1500,
                   help="at most N images this run (0: all). Google refuses an address after "
                        "roughly 1,600 anonymous downloads; the default stays under it")
    p.add_argument("--only", nargs="*", default=None, help="printing ids to restrict to")
    p.add_argument("--pause", type=float, default=0.5)
    p.add_argument("--failures", default="review/image-failures.json")
    p.add_argument("--strict", action="store_true", help="exit 1 if any file failed")
    p.set_defaults(run=cmd_fetch)

    p = sub.add_parser("upload", help="copy the rendered objects into the bucket")
    p.add_argument("--work", default=str(WORK_PATH))
    p.add_argument("--bucket", default=os.environ.get("R2_BUCKET", ""))
    p.set_defaults(run=cmd_upload)

    p = sub.add_parser("verify", help="every object in data/images.json is served")
    p.add_argument("--images", default=str(IMAGES_PATH))
    p.set_defaults(run=cmd_verify)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
