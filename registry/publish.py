"""Publish the export as one object per thing: the files behind the API's URLs.

    python -m registry.publish [--export export/registry.json] [--out dist]
                               [--dataset-version v3.1.0]
                               [--base-url https://api.kairosarchive.net/v3.1.0]
                               [--latest-url https://api.kairosarchive.net/v3]
                               [--manifest manifest.json]

The registry's data is a few thousand records with permanent keys that
change a handful of times a year, so the API needs no server: every
answer is computed here, once, at release time, and written as a file
whose path is its URL. Step 2 of the release uploads `dist/` to object
storage; a CDN in front of it does the rest.

Input is only the published export - never the database - so the objects
can never say anything the export does not. Output is deterministic: the
same export produces byte-identical files.

Layout (every path relative to the version root the uploader chooses):

    index.json                  discovery: versions, counts, endpoint patterns,
                                where this root and the moving alias live,
                                and the usage terms
    manifest.json               the release manifest, when --manifest is given
    registry.json               the full export, byte for byte, + .sha256
    schema.json                 the export's JSON Schema
    cards/{codex_id}.json       the card, its printings (summaries), its history
    printings/{printing_id}.json  the printing and its slug history
    slugs/{slug}.json           any slug that has ever existed -> its ids
    sets.json                   the set catalogue
    sets/{set_code}.json        one set and every card in it
    index/cards.json            compact card list for client-side search
    index/printings.json        compact printing list
    index/slugs.json            slug -> printing_id, every slug ever
    history/slugs.json, history/names.json, history/cards.json
"""

import argparse
import json
import re
import shutil
from pathlib import Path

from . import TERMS_URL
from .export import EXPORT_PATH, SCHEMA_PATH, checksum_path

DIST_PATH = Path("dist")

PRINTING_SUMMARY = ("printing_id", "slug", "set_code", "set_name", "released_at",
                    "product", "finish", "printed_as_current", "retired_at")
CARD_INDEX = ("codex_id", "name", "type", "category", "rarity", "elements",
              "keywords", "subtypes", "cost", "errata", "set_codes",
              "default_printing_id", "image_status")
PRINTING_INDEX = ("printing_id", "codex_id", "slug", "set_code", "product",
                  "finish", "printed_as_current", "retired_at", "image_hash",
                  "image_status")

# Object keys become URL path segments; anything outside this set would
# need escaping, and a key that needs escaping is not a stable address.
SAFE_KEY = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")

ENDPOINTS = {
    "export": "registry.json",
    "checksum": "registry.json.sha256",
    "schema": "schema.json",
    "card": "cards/{codex_id}.json",
    "printing": "printings/{printing_id}.json",
    "slug": "slugs/{slug}.json",
    "sets": "sets.json",
    "set": "sets/{set_code}.json",
    "cards_index": "index/cards.json",
    "printings_index": "index/printings.json",
    "slugs_index": "index/slugs.json",
    "slug_history": "history/slugs.json",
    "name_history": "history/names.json",
    "card_history": "history/cards.json",
}


def render(obj):
    """Compact JSON: these bytes are served, not diffed."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def build_objects(export, dataset_version=None, base_url=None, latest_url=None,
                  manifest=False):
    """Every object the API serves, as {path: json-able}. Pure: no files.

    base_url is the absolute root these objects are uploaded to (an
    immutable release root); latest_url the moving major alias that always
    redirects to the newest release. Both are advertised in index.json so a
    consumer holding any copy can find its origin and the current data."""
    cards = {c["codex_id"]: c for c in export["cards"]}
    printings_by_card = {}
    for printing in export["printings"]:
        printings_by_card.setdefault(printing["codex_id"], []).append(printing)
    slug_rows_by_printing = {}
    for row in export["slug_history"]:
        slug_rows_by_printing.setdefault(row["printing_id"], []).append(row)
    names_by_card = {}
    for row in export["name_history"]:
        names_by_card.setdefault(row["codex_id"], []).append(row)
    history_by_card = {}
    for row in export["card_history"]:
        history_by_card.setdefault(row["codex_id"], []).append(row)

    objects = {}

    for codex_id, card in cards.items():
        record = dict(card)
        record["printings"] = [
            {key: p[key] for key in PRINTING_SUMMARY}
            for p in sorted(printings_by_card.get(codex_id, []),
                            key=lambda p: p["printing_id"])]
        record["name_history"] = [
            {k: r[k] for k in ("name", "valid_from", "valid_to")}
            for r in names_by_card.get(codex_id, [])]
        record["card_history"] = [
            {k: v for k, v in r.items() if k != "codex_id"}
            for r in history_by_card.get(codex_id, [])]
        objects[f"cards/{codex_id}.json"] = record

    printing_by_id = {}
    for printing in export["printings"]:
        printing_by_id[printing["printing_id"]] = printing
        record = dict(printing)
        record["slug_history"] = [
            {k: r[k] for k in ("slug", "valid_from", "valid_to")}
            for r in slug_rows_by_printing.get(printing["printing_id"], [])]
        objects[f"printings/{printing['printing_id']}.json"] = record

    # Every slug that has ever existed resolves. A slug belongs to exactly
    # one printing; an export that says otherwise is corrupt and must not
    # be published as if one of the answers were right.
    owners = {}
    rows_by_slug = {}
    pairs = ([(r["slug"], r["printing_id"]) for r in export["slug_history"]]
             + [(p["slug"], p["printing_id"]) for p in export["printings"]])
    for slug, printing_id in pairs:
        if owners.setdefault(slug, printing_id) != printing_id:
            raise ValueError(f"slug {slug!r} refers to more than one printing")
    for row in export["slug_history"]:
        rows_by_slug.setdefault(row["slug"], []).append(row)
    for slug, printing_id in owners.items():
        printing = printing_by_id[printing_id]
        card = cards[printing["codex_id"]]
        rows = rows_by_slug.get(slug, [])
        objects[f"slugs/{slug}.json"] = {
            "slug": slug,
            "printing_id": printing_id,
            "codex_id": card["codex_id"],
            "card_name": card["name"],
            "current_slug": printing["slug"],
            "is_current": printing["slug"] == slug,
            "valid_from": rows[0]["valid_from"] if rows else None,
            "valid_to": rows[-1]["valid_to"] if rows else None,
            "set_code": printing["set_code"],
            "set_name": printing["set_name"],
            "product": printing["product"],
            "finish": printing["finish"],
            "retired_at": printing["retired_at"],
            "api_url": printing.get("api_url"),
            "kairos_url": printing.get("kairos_url"),
        }

    objects["sets.json"] = export["sets"]
    for set_entry in export["sets"]:
        code = set_entry["set_code"]
        members = {}
        for printing in export["printings"]:
            if printing["set_code"] != code:
                continue
            entry = members.setdefault(printing["codex_id"], {
                "codex_id": printing["codex_id"],
                "name": cards[printing["codex_id"]]["name"],
                "printing_ids": []})
            entry["printing_ids"].append(printing["printing_id"])
        record = dict(set_entry)
        # Ordered by name: the official data has no collector numbers.
        record["cards"] = sorted(members.values(), key=lambda m: (m["name"], m["codex_id"]))
        for entry in record["cards"]:
            entry["printing_ids"].sort()
        objects[f"sets/{code}.json"] = record

    objects["index/cards.json"] = [
        {key: c[key] for key in CARD_INDEX} for c in export["cards"]]
    objects["index/printings.json"] = [
        {key: p[key] for key in PRINTING_INDEX} for p in export["printings"]]
    objects["index/slugs.json"] = dict(sorted(owners.items()))
    objects["history/slugs.json"] = export["slug_history"]
    objects["history/names.json"] = export["name_history"]
    objects["history/cards.json"] = export["card_history"]

    header = export["header"]
    objects["index.json"] = {
        "schema_version": header["schema_version"],
        "dataset_version": dataset_version,
        "base_url": base_url,
        "latest_url": latest_url,
        "manifest": "manifest.json" if manifest else None,
        "source": header["source"],
        "terms": TERMS_URL,
        "counts": {k: header[k] for k in ("sets", "cards", "printings", "slug_history",
                                          "name_history", "card_history")},
        "endpoints": dict(ENDPOINTS),
    }

    for path in objects:
        if not SAFE_KEY.match(path):
            raise ValueError(f"object key is not URL-safe: {path!r}")
    return objects


def _looks_like_dist(path):
    return not any(path.iterdir()) or (path / "index.json").exists()


def write_dist(export_path=EXPORT_PATH, schema_path=SCHEMA_PATH, out=DIST_PATH,
               dataset_version=None, base_url=None, latest_url=None, manifest_path=None):
    export_path, schema_path, out = Path(export_path), Path(schema_path), Path(out)
    manifest_path = Path(manifest_path) if manifest_path else None
    export_bytes = export_path.read_bytes()
    objects = build_objects(json.loads(export_bytes.decode("utf-8")), dataset_version,
                            base_url, latest_url, manifest=manifest_path is not None)

    if out.exists():
        if not _looks_like_dist(out):
            raise ValueError(f"{out} exists and does not look like a dist directory; "
                             f"refusing to delete it")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for path, obj in sorted(objects.items()):
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(obj), encoding="utf-8", newline="\n")
    # The full export is copied byte for byte so its published checksum
    # holds for the served file too.
    (out / ENDPOINTS["export"]).write_bytes(export_bytes)
    (out / ENDPOINTS["checksum"]).write_bytes(checksum_path(export_path).read_bytes())
    (out / ENDPOINTS["schema"]).write_bytes(schema_path.read_bytes())
    if manifest_path is None:
        return len(objects) + 3
    (out / "manifest.json").write_bytes(manifest_path.read_bytes())
    return len(objects) + 4


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", default=str(EXPORT_PATH))
    parser.add_argument("--schema", default=str(SCHEMA_PATH))
    parser.add_argument("--out", default=str(DIST_PATH))
    parser.add_argument("--dataset-version", default=None)
    parser.add_argument("--base-url", default=None,
                        help="absolute URL of the root these objects are served from")
    parser.add_argument("--latest-url", default=None,
                        help="absolute URL of the moving major alias (e.g. .../v3)")
    parser.add_argument("--manifest", default=None,
                        help="release manifest to copy into the root as manifest.json")
    args = parser.parse_args()
    count = write_dist(args.export, args.schema, args.out, args.dataset_version,
                       args.base_url.rstrip("/") if args.base_url else None,
                       args.latest_url.rstrip("/") if args.latest_url else None,
                       args.manifest)
    print(f"wrote {count} objects to {args.out}")


if __name__ == "__main__":
    main()
