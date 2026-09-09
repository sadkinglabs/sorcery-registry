"""One-off migration of the committed database from schema v6 to v7.

    python -m registry.migrate_v7 [--db registry.sqlite] [--as-of YYYY-MM-DD]

Structural only: the new database is built from the v7 DDL and every row
is copied across with its id, so no identifier changes and every history
row survives. Columns the new schema does not have are dropped (the
per-printing gameplay copies); columns upstream now serves but the old
data never had (category, slot, keywords, umbrellas, faces, artist_slug)
start null and are filled by the first sync, where the change is visible
in the plan rather than invented here.

Two values are rewritten, both artefacts of the old API rather than card
data: "defence" is spelled "defense" and comma-joined subtypes/elements
become lists (both are pure re-encodings); and the "UPDATED: " prefix on
rules text is removed - it was the old API's errata marker, dropped by
the rebuild, and leaving it would seed rules_history with text no card
has ever carried.

rules_history is seeded with each card's current text, valid from the
migration date, mirroring how name_history was seeded at v6.
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

from . import SCHEMA_VERSION
from .db import encode_field, get_meta, init_db, open_db, set_meta

UPDATED_PREFIX = "UPDATED: "


def _split(text):
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def migrate(old, new, as_of):
    if get_meta(old, "schema_version") != "6":
        raise ValueError(f"expected a v6 database, found schema_version "
                         f"{get_meta(old, 'schema_version')!r}")
    init_db(new)
    for key in ("next_card_id", "next_printing_id"):
        set_meta(new, key, get_meta(old, key))

    for row in old.execute("SELECT * FROM cards ORDER BY card_id"):
        rules = row["rules_text"] or ""
        if rules.startswith(UPDATED_PREFIX):
            rules = rules[len(UPDATED_PREFIX):]
        new.execute(
            "INSERT INTO cards (card_id, name, type, rarity, subtypes, elements, "
            "cost, attack, defense, life, thr_air, thr_earth, thr_fire, thr_water, "
            "rules_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["card_id"], row["name"], row["type"], row["rarity"],
             encode_field("subtypes", _split(row["subtypes"])),
             encode_field("elements", _split(row["elements"])),
             row["cost"], row["attack"], row["defence"], row["life"],
             row["thr_air"], row["thr_earth"], row["thr_fire"], row["thr_water"],
             rules))
        new.execute(
            "INSERT INTO rules_history (rules_text, card_id, valid_from, valid_to) "
            "VALUES (?, ?, ?, NULL)", (rules, row["card_id"], as_of))

    for row in old.execute("SELECT * FROM printings ORDER BY printing_id"):
        new.execute(
            "INSERT INTO printings (printing_id, card_id, set_name, set_code, "
            "released_at, product, finish, slug, artist, flavour_text, typeline, "
            "image_hash, retired_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["printing_id"], row["card_id"], row["set_name"], row["set_number"],
             row["released_at"], row["product"], row["finish"], row["slug"],
             row["artist"], row["flavour_text"], row["type_text"],
             row["image_hash"], row["retired_at"]))

    for table, columns in (("slug_history", "slug, printing_id, valid_from, valid_to"),
                           ("name_history", "name, card_id, valid_from, valid_to")):
        for row in old.execute(f"SELECT {columns} FROM {table}"):
            new.execute(f"INSERT INTO {table} ({columns}) VALUES (?, ?, ?, ?)", tuple(row))
    new.commit()

    for table in ("cards", "printings", "slug_history", "name_history"):
        before = old.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        after = new.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        if before != after:
            raise ValueError(f"{table}: {before} rows before, {after} after")
    for table, column in (("cards", "card_id"), ("printings", "printing_id")):
        before = [r[0] for r in old.execute(f"SELECT {column} FROM {table} ORDER BY 1")]
        after = [r[0] for r in new.execute(f"SELECT {column} FROM {table} ORDER BY 1")]
        if before != after:
            raise ValueError(f"{table}: the set of {column}s changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="registry.sqlite")
    parser.add_argument("--as-of", default=None, help="valid_from for the seeded rules_history rows")
    args = parser.parse_args()
    as_of = args.as_of or date.today().isoformat()
    date.fromisoformat(as_of)

    db_path = Path(args.db)
    fresh_path = db_path.with_name(db_path.name + ".v7")
    if fresh_path.exists():
        fresh_path.unlink()
    old = open_db(db_path)
    new = open_db(fresh_path)
    try:
        migrate(old, new, as_of)
    except Exception:
        new.close()
        fresh_path.unlink(missing_ok=True)
        raise
    old.close()
    new.close()
    shutil.move(fresh_path, db_path)
    print(f"{db_path}: migrated to schema v{SCHEMA_VERSION}")


if __name__ == "__main__":
    sys.exit(main())
