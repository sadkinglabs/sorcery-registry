"""Art for hand-recorded printings: images the registry holds because the
owner supplied them, for printings the publisher's folder will never have.

    python -m registry.art strip  FILE [FILE ...]
    python -m registry.art render [--work work/images]
    python -m registry.art check

The publisher's image folder covers what the official API serves. A
printing recorded by hand (data/manual.json) - a store-kit prize, a
Kickstarter pledge card, a curio - has no file there, so its art comes
from the owner and lives in the repository:

    art/P001700.jpg      the front of printing P001700
    art/P001700-r.jpg    its back face, for a printing that has one

named by printing id, with the same "-r" suffix the publisher's folder
uses for a back face. PNG, JPEG or WebP; a phone's HEIC photo is
converted to one of those first.

Rules, enforced here and by the validator:

- Art is only for printings recorded by hand: one whose export record
  carries a manual block (origin "manual", or since confirmed upstream).
  The publisher's image is the only image of an official printing.
- The publisher's image wins. When the folder starts serving a file for
  a hand-recorded printing, the image sync replaces the art, and `render`
  leaves it alone from then on.
- No metadata. A phone photo carries EXIF - often the place it was taken
  - and the repository and the bucket are public. `check` refuses a file
  with EXIF, XMP or text chunks; `strip` rewrites one without them, with
  the camera's orientation applied to the pixels (so nothing depends on
  the tag it removes). Colour profiles are kept.

`render` is the image pipeline for these files: for every art file that
is new or changed (by MD5, or by a new rendition recipe) it renders the
same renditions as a publisher image, under the same content-addressed
names, into the work directory, and records the face in data/images.json
with the art file as its source. A held face whose art file is gone is
dropped, so the printing's image goes back to missing; the objects stay
in the bucket, immutable, as every published object does. The images
workflow runs it when art changes on main, then uploads, verifies and
opens the pull request, exactly as it does for the publisher's folder.
"""

import argparse
import datetime
import hashlib
import io
import json
import re
import sys
from pathlib import Path

from .images import (BACK_SUFFIX, IMAGES_PATH, RENDITION_RECIPE, WORK_PATH, art_key, is_lowres,
                     load_images, object_name, render, save_images)

ART_DIR = Path("art")
ART_NAME = re.compile(r"^(?P<pid>P\d{6})(?P<back>" + re.escape(BACK_SUFFIX) + r")?"
                      r"\.(?P<ext>png|jpg|jpeg|webp)$")
MAX_BYTES = 15 * 1024 * 1024  # R2's limit for one object is far above this; git's patience is not
EXPORT_PATH = Path("export") / "registry.json"


def art_files(art_dir=ART_DIR):
    """Every file under the art directory, sorted, as (path, match or None)."""
    art_dir = Path(art_dir)
    if not art_dir.exists():
        return []
    return [(path, ART_NAME.match(path.name))
            for path in sorted(p for p in art_dir.iterdir() if p.is_file())]


def source_name(path):
    """How data/images.json records an art file: its path in the repository."""
    return f"{ART_DIR.as_posix()}/{Path(path).name}"


def metadata(data):
    """The kinds of metadata an image file carries that must not be
    published: EXIF (camera, time, often location), XMP, and text chunks
    or comments. A colour profile is not metadata about the photograph and
    is kept."""
    from PIL import Image
    found = []
    with Image.open(io.BytesIO(data)) as image:
        if len(image.getexif()):
            found.append("EXIF")
        info = image.info
        if "xmp" in info or "XML:com.adobe.xmp" in info:
            found.append("XMP")
        if getattr(image, "text", None):
            found.append("text")
        if info.get("comment"):
            found.append("comment")
    return found


def strip(data):
    """The same picture without metadata: the EXIF orientation applied to
    the pixels, then saved in the same format with only the colour profile
    carried over. JPEG and WebP are re-encoded at high quality; PNG is
    lossless."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as image:
        fmt = _plain(image.format)
        icc = image.info.get("icc_profile")
        upright = ImageOps.exif_transpose(image)
        upright.load()
    options = {"icc_profile": icc} if icc else {}
    if fmt == "JPEG":
        options.update(quality=95, subsampling=0)
        if upright.mode not in ("RGB", "L"):
            upright = upright.convert("RGB")
    elif fmt == "WEBP":
        options.update(quality=95, method=6)
    elif fmt != "PNG":
        raise ValueError(f"unsupported image format {fmt}; use PNG, JPEG or WebP")
    buffer = io.BytesIO()
    upright.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def check_art(export, art_dir, errors):
    """The validator's view of the art directory: every file is named for a
    printing recorded by hand (and a back only for a printing with a back
    face), is an image of the kind its name says, is not too large, and
    carries no metadata."""
    by_id = {p["printing_id"]: p for p in export["printings"]}
    for path, match in art_files(art_dir):
        where = f"{source_name(path)}"
        if match is None:
            errors.append(f"{where}: an art file is named <printing id>.png|jpg|jpeg|webp, "
                          f"or <printing id>{BACK_SUFFIX}.<ext> for a back face")
            continue
        printing = by_id.get(match["pid"])
        if printing is None:
            errors.append(f"{where}: no printing {match['pid']}")
            continue
        if not printing.get("manual"):
            errors.append(f"{where}: {match['pid']} is an official printing; its only image is "
                          f"the publisher's")
            continue
        if match["back"] and printing.get("back") is None:
            errors.append(f"{where}: {match['pid']} has no back face")
            continue
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            errors.append(f"{where}: {len(data) // (1024 * 1024)} MB; keep art under "
                          f"{MAX_BYTES // (1024 * 1024)} MB")
            continue
        try:
            found = metadata(data)
            fmt = _format(data)
        except Exception as error:  # noqa: BLE001 - any unreadable file is the same error
            errors.append(f"{where}: not a readable image ({error})")
            continue
        expected = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}[match["ext"]]
        if fmt != expected:
            errors.append(f"{where}: the file is {fmt}, but its name says {expected}")
        if found:
            errors.append(f"{where}: carries {', '.join(found)}; the repository is public, so art "
                          f"is published without metadata (python -m registry.art strip {where})")


def check_held_art(export, images, errors):
    """data/images.json may hold art only for printings recorded by hand."""
    by_id = {p["printing_id"]: p for p in export["printings"]}
    for pid, faces in images.get("printings", {}).items():
        for face, held in faces.items():
            if str(held.get("source", "")).startswith(f"{ART_DIR.as_posix()}/"):
                printing = by_id.get(pid)
                if printing is not None and not printing.get("manual"):
                    errors.append(f"data/images.json: {pid} {face} is held from "
                                  f"{held['source']}, but {pid} is an official printing")


def _plain(fmt):
    """A phone's JPEG often opens as MPO (a JPEG with a second, preview
    image appended); it is a JPEG, and is written back as one."""
    return "JPEG" if fmt == "MPO" else fmt


def _format(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        return _plain(image.format)


def plan_render(export, images, art_dir=ART_DIR):
    """What `render` would do: (to_render, to_drop, left_alone).

    to_render   [(path, printing id, face)] - new art, changed art, or art
                rendered under an older recipe
    to_drop     [(printing id, face)] - held from an art file that is gone
    left_alone  [(path, reason)] - art the publisher's image has replaced
    """
    held_faces = images.get("printings", {})
    to_render, left_alone, present = [], [], set()
    for path, match in art_files(art_dir):
        if match is None:
            continue
        pid, face = match["pid"], "back" if match["back"] else "front"
        present.add((pid, face))
        held = held_faces.get(pid, {}).get(face)
        if held and held.get("drive_id"):
            left_alone.append((path, "the publisher's image is held"))
            continue
        md5 = hashlib.md5(path.read_bytes()).hexdigest()
        if (held and held.get("source") == source_name(path) and held.get("md5") == md5
                and held.get("recipe") == images.get("recipe")):
            continue
        to_render.append((path, pid, face))
    to_drop = [(pid, face) for pid, faces in held_faces.items() for face, held in faces.items()
               if str(held.get("source", "")).startswith(f"{ART_DIR.as_posix()}/")
               and (pid, face) not in present]
    return to_render, sorted(to_drop), left_alone


def render_one(path, pid, face, work, state, today=None):
    """Render one art file into `work` and record it in `state` (not yet
    saved), in the same shape as a face from the publisher's folder, with
    the art file as its source and no Drive id."""
    original = Path(path).read_bytes()
    key = art_key(original, state["recipe"])
    renditions, (width, height) = render(original)
    ext = ART_NAME.match(Path(path).name)["ext"]
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
        "source": source_name(path),
        "md5": hashlib.md5(original).hexdigest(),
        "width": width,
        "height": height,
        "lowres": is_lowres(width),
        "original_ext": ext,
        "objects": objects,
        "fetched": today or datetime.date.today().isoformat(),
    }
    return key


def cmd_strip(args):
    for name in args.files:
        path = Path(name)
        data = path.read_bytes()
        before = metadata(data)
        path.write_bytes(strip(data))
        after = metadata(path.read_bytes())
        if after:
            print(f"::error::{path}: still carries {', '.join(after)} after stripping")
            return 1
        print(f"{path}: removed {', '.join(before) or 'nothing (none found)'}")
    return 0


def cmd_render(args):
    export = json.loads(Path(args.export).read_text(encoding="utf-8"))
    errors = []
    check_art(export, args.art, errors)
    if errors:
        print("::error::the art directory does not pass its checks:\n" + "\n".join(errors))
        return 1
    state = load_images(args.images)
    if state.get("recipe") != RENDITION_RECIPE:
        print(f"::error::data/images.json was rendered under recipe {state.get('recipe')}, the "
              f"code is at {RENDITION_RECIPE}; run the images sync first, which re-renders everything")
        return 1
    to_render, to_drop, left_alone = plan_render(export, state, args.art)
    for path, reason in left_alone:
        print(f"{source_name(path)}: left alone, {reason}")
    for pid, face in to_drop:
        print(f"{pid} {face}: its art file is gone; the image is dropped")
        del state["printings"][pid][face]
        if not state["printings"][pid]:
            del state["printings"][pid]
    for path, pid, face in to_render:
        key = render_one(path, pid, face, args.work, state)
        print(f"{source_name(path)} -> {pid} {face}, key {key}")
    if to_render or to_drop:
        save_images(state, args.images)
    print(f"rendered {len(to_render)}, dropped {len(to_drop)}, left alone {len(left_alone)}")
    return 0


def cmd_check(args):
    export = json.loads(Path(args.export).read_text(encoding="utf-8"))
    errors = []
    check_art(export, args.art, errors)
    for error in errors:
        print(error)
    if not errors:
        print(f"{len(art_files(args.art))} art file(s), all well formed")
    return 1 if errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("strip", help="rewrite image files without metadata, orientation applied")
    p.add_argument("files", nargs="+")
    p.set_defaults(run=cmd_strip)

    p = sub.add_parser("render", help="render new or changed art and record it in data/images.json")
    p.add_argument("--art", default=str(ART_DIR))
    p.add_argument("--export", default=str(EXPORT_PATH))
    p.add_argument("--images", default=str(IMAGES_PATH))
    p.add_argument("--work", default=str(WORK_PATH))
    p.set_defaults(run=cmd_render)

    p = sub.add_parser("check", help="the validator's checks on the art directory")
    p.add_argument("--art", default=str(ART_DIR))
    p.add_argument("--export", default=str(EXPORT_PATH))
    p.set_defaults(run=cmd_check)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
