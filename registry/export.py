"""Deterministic JSON export.

One file, fixed key order, records sorted by id. The same database always
produces byte-identical output, so an unchanged card contributes zero diff
lines in git. Deliberately no timestamp in the header: a timestamp would
put a diff line on every no-op run.
"""

import hashlib
import json
from pathlib import Path

from . import API_BASE, API_URL, IMAGE_BASE, SCHEMA_VERSION, SITE_BASE
from .errata import load_errata, unknown_printings
from .images import load_images
from .notes import load_notes
from .db import (CARD_FIELDS, FACE_FIELDS, HISTORY_FIELDS, PRINTING_FACE_FIELDS,
                 PRINTING_FIELDS, decode_field, open_db)
from .ids import format_card_id, format_printing_id

EXPORT_PATH = Path("export") / "registry.json"
SCHEMA_PATH = Path("schema") / "registry.schema.json"


def checksum_path(export_path):
    export_path = Path(export_path)
    return export_path.with_name(export_path.name + ".sha256")


def power(attack, defense):
    """Sorcery's derived power: equal to attack when attack equals defense,
    otherwise the floor of their mean; null when either is null. Published
    so every consumer computes the same number - the site's pow: search,
    a deck tool's sort - and none has to know the rule."""
    if attack is None or defense is None:
        return None
    return attack if attack == defense else (attack + defense) // 2


def with_power(record):
    """The same record with power inserted after defense."""
    out = {}
    for key, value in record.items():
        out[key] = value
        if key == "defense":
            out["power"] = power(record.get("attack"), record.get("defense"))
    return out


def _face(value, fields):
    """A back face in fixed key order, or None. Stored JSON has sorted
    keys; the export reads in the same order as the front face."""
    if value is None:
        return None
    return with_power({field: value.get(field) for field in fields})


# Renditions of a card image: the three sized ones are WebP, "original"
# is the publisher's file untouched (its own extension). Object names are
# self-describing - {printing_id}.{key}.{rendition}.{ext}, with ".back"
# before the rendition for a back face - where key is the art-version key
# recorded in data/images.json by the image pipeline. The names never
# carry a slug, and the bytes at a name never change: new art or a new
# encoding recipe gets a new key.
RENDITIONS = ("small", "normal", "large", "original")


def card_urls(codex_id):
    return {"api_url": f"{API_BASE}/cards/{codex_id}.json",
            "kairos_url": f"{SITE_BASE}/cards/{codex_id}"}


def printing_urls(printing_id):
    return {"api_url": f"{API_BASE}/printings/{printing_id}.json",
            "kairos_url": f"{SITE_BASE}/printings/{printing_id}"}


def set_urls(set_code):
    if set_code is None:
        return {"api_url": None, "kairos_url": None}
    return {"api_url": f"{API_BASE}/sets/{set_code}.json",
            "kairos_url": f"{SITE_BASE}/sets/{set_code}"}


def image_urls(printing_id, image_key, back=False, original_ext="png"):
    """The addresses of one face's image renditions, or None while the
    registry holds no image for it."""
    if image_key is None:
        return None
    face = ".back" if back else ""
    return {rendition: f"{IMAGE_BASE}/{printing_id}.{image_key}{face}.{rendition}."
                       f"{original_ext if rendition == 'original' else 'webp'}"
            for rendition in RENDITIONS}


def face_urls(printing_id, held, back=False):
    """image_urls for one face as data/images.json records it (or None)."""
    if not held:
        return None
    return image_urls(printing_id, held["key"], back, held.get("original_ext", "png"))


def image_status(held):
    """missing: no image held; lowres: held, but the source was too small
    for the large rendition and was upscaled; ok: held at full size."""
    if not held:
        return "missing"
    return "lowres" if held.get("lowres") else "ok"


def default_printing(printings):
    """The representative printing of a card, by a fixed rule so every
    consumer picks the same one: not retired; showing the card's current
    face (printed_as_current) over one with older values; Booster over other
    products; Standard over other finishes; most recently released; lowest
    id. So a reprint that changed the card's stats becomes the default even
    when it is a promo, and otherwise a promo never outranks a Booster."""
    live = [p for p in printings if p["retired_at"] is None] or list(printings)
    if not live:
        return None
    return min(live, key=lambda p: (p.get("printed_as_current") is not True,
                                    p["product"] != "Booster",
                                    p["finish"] != "Standard",
                                    "" if p["released_at"] is None else
                                    "".join(chr(255 - ord(c)) for c in p["released_at"]),
                                    p["printing_id"]))["printing_id"]


def printed_as_current(released_at, history, added_on=None):
    """Whether a printing shows the card's current face.

    The card's first history row stands for everything before the registry
    started recording, so under it every printing is current. Otherwise a
    printing is current when it was released on or after the current face
    took effect - or when it entered the registry on or after that date
    (`added_on`, its first slug_history row): a face is recorded on the
    date of the sync that saw it, which is normally after the reprint that
    carries it reached the public, and a printing first seen alongside or
    after the new face was necessarily printed with it. That shortcut holds
    only for faces the registry observed in the API: a face recorded by
    hand from the printed card (source "card") carries a hand-set date,
    and then only the release date decides."""
    if not history:
        return None
    current = history[-1]
    if len(history) == 1:
        return True
    observed = all(row.get("source", "api") == "api" for row in history)
    if observed and added_on is not None and added_on >= current["valid_from"]:
        return True
    if released_at is None:
        return None
    return released_at >= current["valid_from"]


def build_export(con, images=None, errata=None, notes=None):
    """The export, from the database plus data/images.json (what images the
    registry holds), data/errata.json (printed faces recorded by hand) and
    data/notes.json (what the official API does not say about a record) -
    registry-owned data kept in git, like overrides."""
    held_images = (images if images is not None else load_images()).get("printings", {})
    notes = notes if notes is not None else load_notes()
    # Every record carries its notes as a list, empty for almost all of
    # them: a consumer never has to ask whether the field is there.
    card_notes, printing_notes = notes.get("cards", {}), notes.get("printings", {})
    # A textless promo shows no face, so no date can say whether it is
    # current: data/errata.json names such printings and they report null.
    faceless = unknown_printings(errata if errata is not None else load_errata())
    # Derived at export time from the printings table, never stored: the
    # reverse card -> printings link cannot drift from the forward one.
    printing_ids_by_card = {}
    set_codes_by_card = {}
    printings_by_card = {}
    for row in con.execute(
            "SELECT card_id, printing_id, set_code, released_at, product, finish, "
            "retired_at FROM printings ORDER BY printing_id"):
        printing_ids_by_card.setdefault(row["card_id"], []).append(
            format_printing_id(row["printing_id"]))
        printings_by_card.setdefault(row["card_id"], []).append(dict(row))
        if row["set_code"] is not None:
            set_codes_by_card.setdefault(row["card_id"], set()).add(row["set_code"])

    history_by_card = {}
    for row in con.execute(
            "SELECT card_id, valid_from, valid_to, face, source FROM card_history "
            "ORDER BY card_id, valid_from, valid_to IS NULL, face"):
        history_by_card.setdefault(row["card_id"], []).append(dict(row))
    added_on = {row["printing_id"]: row["first_seen"] for row in con.execute(
        "SELECT printing_id, min(valid_from) AS first_seen FROM slug_history "
        "GROUP BY printing_id")}
    def shows_current(row):
        if format_printing_id(row["printing_id"]) in faceless:
            return None
        return printed_as_current(row["released_at"], history_by_card.get(row["card_id"], []),
                                  added_on.get(row["printing_id"]))

    for entries in printings_by_card.values():
        for entry in entries:
            entry["printed_as_current"] = shows_current(entry)

    # Derived set catalogue: the sets themselves, with counts - the answer
    # to "what sets exist and how big are they", which the official data
    # states nowhere. A set's release date is the earliest date any of its
    # printings reached the public; upstream's own set timestamp is a
    # database artefact and is never read.
    set_agg = {}
    for row in con.execute(
            "SELECT set_code, set_name, released_at, card_id FROM printings"):
        entry = set_agg.setdefault(row["set_code"], {
            "set_code": row["set_code"], "set_name": row["set_name"],
            "released_at": None, "card_ids": set(), "printings": 0})
        if row["released_at"] is not None and (
                entry["released_at"] is None or row["released_at"] < entry["released_at"]):
            entry["released_at"] = row["released_at"]
        entry["card_ids"].add(row["card_id"])
        entry["printings"] += 1
    sets = []
    for key in sorted(set_agg, key=lambda k: (k is None, k)):
        entry = set_agg[key]
        sets.append({"set_code": entry["set_code"],
                     "set_name": entry["set_name"],
                     "released_at": entry["released_at"],
                     "cards": len(entry["card_ids"]),
                     "printings": entry["printings"],
                     **set_urls(entry["set_code"])})

    # The card-level id is published as "codex_id", after Codex, the game's
    # official rules authority - the same move as Scryfall's oracle_id.
    cards = []
    for row in con.execute("SELECT * FROM cards ORDER BY card_id"):
        record = {"codex_id": format_card_id(row["card_id"])}
        for field in CARD_FIELDS:
            record[field] = decode_field(field, row[field])
        record = with_power(record)
        record["back"] = _face(record["back"], FACE_FIELDS)
        record["errata"] = bool(row["errata"])
        record["set_codes"] = sorted(set_codes_by_card.get(row["card_id"], set()))
        record["printing_ids"] = printing_ids_by_card.get(row["card_id"], [])
        # A hand-picked default (through overrides) wins over the rule, but
        # only while it names one of the card's own printings.
        own = printings_by_card.get(row["card_id"], [])
        pinned = row["default_printing_id"]
        if pinned is not None and any(p["printing_id"] == pinned for p in own):
            chosen = pinned
        else:
            chosen = default_printing(own)
        record["default_printing_id"] = (format_printing_id(chosen)
                                         if chosen is not None else None)
        # Where the record lives, and the image of its representative
        # printing - so a card answers "show me this card" on its own.
        record.update(card_urls(record["codex_id"]))
        chosen_front = (held_images.get(record["default_printing_id"], {}).get("front")
                        if chosen is not None else None)
        record["image_urls"] = face_urls(record["default_printing_id"], chosen_front)
        record["image_status"] = image_status(chosen_front)
        record["notes"] = [dict(n) for n in card_notes.get(record["codex_id"], [])]
        cards.append(record)

    # card_name is derived from the cards table at export time, so a
    # printing record is readable on its own without a join; it can never
    # disagree with the card its codex_id points at.
    name_by_card = {row["card_id"]: row["name"]
                    for row in con.execute("SELECT card_id, name FROM cards")}
    printings = []
    for row in con.execute("SELECT * FROM printings ORDER BY printing_id"):
        record = {"printing_id": format_printing_id(row["printing_id"]),
                  "codex_id": format_card_id(row["card_id"]),
                  "card_name": name_by_card[row["card_id"]]}
        for field in PRINTING_FIELDS:
            record[field] = decode_field(field, row[field])
        record["back"] = _face(record["back"], PRINTING_FACE_FIELDS)
        record["printed_as_current"] = shows_current(row)
        record["retired_at"] = row["retired_at"]
        record.update(printing_urls(record["printing_id"]))
        # What the registry holds for this printing's faces, from
        # data/images.json; image_hash publishes the front's art-version key.
        faces = held_images.get(record["printing_id"], {})
        front, back = faces.get("front"), faces.get("back")
        record["image_hash"] = front["key"] if front else None
        record["image_urls"] = face_urls(record["printing_id"], front)
        record["image_status"] = image_status(front)
        if record["back"] is not None:
            record["back"]["image_urls"] = face_urls(record["printing_id"], back, back=True)
        record["notes"] = [dict(n) for n in printing_notes.get(record["printing_id"], [])]
        printings.append(record)

    slug_history = []
    for row in con.execute(
            "SELECT slug, printing_id, valid_from, valid_to FROM slug_history "
            "ORDER BY printing_id, valid_from, slug"):
        slug_history.append({
            "slug": row["slug"],
            "printing_id": format_printing_id(row["printing_id"]),
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
        })

    name_history = []
    for row in con.execute(
            "SELECT name, card_id, valid_from, valid_to FROM name_history "
            "ORDER BY card_id, valid_from, name"):
        name_history.append({
            "name": row["name"],
            "codex_id": format_card_id(row["card_id"]),
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
        })

    # One row per state of a card's gameplay face, oldest first, the open
    # row last; the face's fields are flattened into the row.
    card_history = []
    for card_id in sorted(history_by_card):
        for row in history_by_card[card_id]:
            face = json.loads(row["face"])
            entry = {"codex_id": format_card_id(card_id),
                     "valid_from": row["valid_from"],
                     "valid_to": row["valid_to"],
                     "source": row["source"]}
            for field in HISTORY_FIELDS:
                entry[field] = face.get(field)
            entry = with_power(entry)
            entry["back"] = _face(entry["back"], FACE_FIELDS)
            card_history.append(entry)

    return {
        "header": {
            "schema_version": SCHEMA_VERSION,
            "source": API_URL,
            "sets": len(sets),
            "cards": len(cards),
            "printings": len(printings),
            "slug_history": len(slug_history),
            "name_history": len(name_history),
            "card_history": len(card_history),
        },
        "sets": sets,
        "cards": cards,
        "printings": printings,
        "slug_history": slug_history,
        "name_history": name_history,
        "card_history": card_history,
    }


def render(export):
    return json.dumps(export, indent=2, ensure_ascii=False) + "\n"


def write_export(con, path=EXPORT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render(build_export(con))
    path.write_text(rendered, encoding="utf-8", newline="\n")
    # sha256sum-compatible checksum file: consumers verify integrity, and
    # the MCP server revalidates its cache with a tiny fetch instead of
    # re-downloading the whole export.
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    checksum_path(path).write_text(f"{digest}  {path.name}\n",
                                   encoding="utf-8", newline="\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Regenerate export/registry.json from the database.")
    parser.add_argument("--db", default="registry.sqlite")
    parser.add_argument("--out", default=str(EXPORT_PATH))
    args = parser.parse_args()
    con = open_db(args.db)
    write_export(con, args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
