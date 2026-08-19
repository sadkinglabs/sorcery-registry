"""Deterministic JSON export.

One file, fixed key order, records sorted by id. The same database always
produces byte-identical output, so an unchanged card contributes zero diff
lines in git. Deliberately no timestamp in the header: a timestamp would
put a diff line on every no-op run.
"""

import json
from pathlib import Path

from . import API_URL, SCHEMA_VERSION
from .db import CARD_FIELDS, PRINTING_FIELDS, open_db

EXPORT_PATH = Path("export") / "registry.json"


def build_export(con):
    # Derived at export time from the printings table, never stored: the
    # reverse card -> printings link cannot drift from the forward one.
    printing_ids_by_card = {}
    for row in con.execute("SELECT card_id, printing_id FROM printings ORDER BY printing_id"):
        printing_ids_by_card.setdefault(row["card_id"], []).append(row["printing_id"])

    cards = []
    for row in con.execute("SELECT * FROM cards ORDER BY card_id"):
        record = {"card_id": row["card_id"]}
        for field in CARD_FIELDS:
            record[field] = row[field]
        record["printing_ids"] = printing_ids_by_card.get(row["card_id"], [])
        cards.append(record)

    printings = []
    for row in con.execute("SELECT * FROM printings ORDER BY printing_id"):
        record = {"printing_id": row["printing_id"], "card_id": row["card_id"]}
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
            "printing_id": row["printing_id"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
        })

    return {
        "header": {
            "schema_version": SCHEMA_VERSION,
            "source": API_URL,
            "cards": len(cards),
            "printings": len(printings),
            "slug_history": len(slug_history),
        },
        "cards": cards,
        "printings": printings,
        "slug_history": slug_history,
    }


def render(export):
    return json.dumps(export, indent=2, ensure_ascii=False) + "\n"


def write_export(con, path=EXPORT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(build_export(con)), encoding="utf-8", newline="\n")


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
