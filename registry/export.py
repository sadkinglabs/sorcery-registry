"""Deterministic JSON export.

One file, fixed key order, records sorted by id. The same database always
produces byte-identical output, so an unchanged card contributes zero diff
lines in git. Deliberately no timestamp in the header: a timestamp would
put a diff line on every no-op run.
"""

import hashlib
import json
from pathlib import Path

from . import API_URL, SCHEMA_VERSION
from .db import (CARD_FIELDS, FACE_FIELDS, HISTORY_FIELDS, PRINTING_FACE_FIELDS,
                 PRINTING_FIELDS, decode_field, open_db)
from .ids import format_card_id, format_printing_id

EXPORT_PATH = Path("export") / "registry.json"
SCHEMA_PATH = Path("schema") / "registry.schema.json"


def checksum_path(export_path):
    export_path = Path(export_path)
    return export_path.with_name(export_path.name + ".sha256")


def _face(value, fields):
    """A back face in fixed key order, or None. Stored JSON has sorted
    keys; the export reads in the same order as the front face."""
    if value is None:
        return None
    return {field: value.get(field) for field in fields}


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
    after the new face was necessarily printed with it."""
    if not history:
        return None
    current = history[-1]
    if len(history) == 1:
        return True
    if added_on is not None and added_on >= current["valid_from"]:
        return True
    if released_at is None:
        return None
    return released_at >= current["valid_from"]


def build_export(con):
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
            "SELECT card_id, valid_from, valid_to, face FROM card_history "
            "ORDER BY card_id, valid_from, valid_to IS NULL, face"):
        history_by_card.setdefault(row["card_id"], []).append(dict(row))
    added_on = {row["printing_id"]: row["first_seen"] for row in con.execute(
        "SELECT printing_id, min(valid_from) AS first_seen FROM slug_history "
        "GROUP BY printing_id")}
    for card_id, entries in printings_by_card.items():
        for entry in entries:
            entry["printed_as_current"] = printed_as_current(
                entry["released_at"], history_by_card.get(card_id, []),
                added_on.get(entry["printing_id"]))

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
                     "printings": entry["printings"]})

    # The card-level id is published as "codex_id", after Codex, the game's
    # official rules authority - the same move as Scryfall's oracle_id.
    cards = []
    for row in con.execute("SELECT * FROM cards ORDER BY card_id"):
        record = {"codex_id": format_card_id(row["card_id"])}
        for field in CARD_FIELDS:
            record[field] = decode_field(field, row[field])
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
        record["printed_as_current"] = printed_as_current(
            row["released_at"], history_by_card.get(row["card_id"], []),
            added_on.get(row["printing_id"]))
        record["retired_at"] = row["retired_at"]
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
                     "valid_to": row["valid_to"]}
            for field in HISTORY_FIELDS:
                entry[field] = face.get(field)
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
