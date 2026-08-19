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
from .db import CARD_FIELDS, PRINTING_FIELDS, open_db
from .ids import format_card_id, format_printing_id

EXPORT_PATH = Path("export") / "registry.json"
SCHEMA_PATH = Path("schema") / "registry.schema.json"


def checksum_path(export_path):
    export_path = Path(export_path)
    return export_path.with_name(export_path.name + ".sha256")


def build_export(con):
    # Derived at export time from the printings table, never stored: the
    # reverse card -> printings link cannot drift from the forward one.
    printing_ids_by_card = {}
    set_numbers_by_card = {}
    for row in con.execute(
            "SELECT card_id, printing_id, set_number FROM printings ORDER BY printing_id"):
        printing_ids_by_card.setdefault(row["card_id"], []).append(
            format_printing_id(row["printing_id"]))
        if row["set_number"] is not None:
            set_numbers_by_card.setdefault(row["card_id"], set()).add(row["set_number"])

    # Derived set catalogue: the six sets themselves, with counts - the
    # answer to "what sets exist and how big are they", which the official
    # data states nowhere.
    set_agg = {}
    for row in con.execute(
            "SELECT set_number, set_name, released_at, card_id FROM printings"):
        entry = set_agg.setdefault(row["set_number"], {
            "set_number": row["set_number"], "set_name": row["set_name"],
            "released_at": row["released_at"], "card_ids": set(), "printings": 0})
        entry["card_ids"].add(row["card_id"])
        entry["printings"] += 1
    sets = []
    for key in sorted(set_agg, key=lambda k: (k is None, k)):
        entry = set_agg[key]
        sets.append({"set_number": entry["set_number"],
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
            record[field] = row[field]
        # Derived: upstream marks errata'd cards by starting the rules text
        # with "UPDATED". The registry publishes that convention as a flag
        # rather than expecting every consumer to rediscover it.
        record["errata"] = (row["rules_text"] or "").startswith("UPDATED")
        record["set_numbers"] = sorted(set_numbers_by_card.get(row["card_id"], set()))
        record["printing_ids"] = printing_ids_by_card.get(row["card_id"], [])
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
            record[field] = row[field]
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

    return {
        "header": {
            "schema_version": SCHEMA_VERSION,
            "source": API_URL,
            "sets": len(sets),
            "cards": len(cards),
            "printings": len(printings),
            "slug_history": len(slug_history),
            "name_history": len(name_history),
        },
        "sets": sets,
        "cards": cards,
        "printings": printings,
        "slug_history": slug_history,
        "name_history": name_history,
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
