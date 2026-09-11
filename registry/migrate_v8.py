"""One-off migration of the committed database from schema v7 to v8.

    python -m registry.migrate_v8 [--db registry.sqlite]

Structural only: the new database is built from the v8 DDL and every row
is copied across with its id. rules_history becomes card_history: each
old row held one text the card played by; the new row holds the whole
gameplay face at that time. Under v7 nothing but rules_text was ever
recorded as changing, so a v7 row's face is the card's current face with
that row's text in place of the current one - exact, not reconstructed.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import SCHEMA_VERSION
from .db import HISTORY_FIELDS, decode_field, face_of, get_meta, init_db, open_db, set_meta

COPIED_TABLES = (
    ("cards", None),
    ("printings", None),
    ("slug_history", "slug, printing_id, valid_from, valid_to"),
    ("name_history", "name, card_id, valid_from, valid_to"),
)


def migrate(old, new):
    if get_meta(old, "schema_version") != "7":
        raise ValueError(f"expected a v7 database, found schema_version "
                         f"{get_meta(old, 'schema_version')!r}")
    init_db(new)
    for key in ("next_card_id", "next_printing_id"):
        set_meta(new, key, get_meta(old, key))

    for table, columns in COPIED_TABLES:
        if columns is None:
            names = [r["name"] for r in old.execute(f"PRAGMA table_info({table})")]
            columns = ", ".join(names)
        holes = ", ".join("?" for _ in columns.split(","))
        for row in old.execute(f"SELECT {columns} FROM {table}"):
            new.execute(f"INSERT INTO {table} ({columns}) VALUES ({holes})", tuple(row))

    faces = {}
    for row in old.execute("SELECT * FROM cards"):
        faces[row["card_id"]] = {f: decode_field(f, row[f]) for f in HISTORY_FIELDS}
    for row in old.execute(
            "SELECT rules_text, card_id, valid_from, valid_to FROM rules_history"):
        face = dict(faces[row["card_id"]])
        face["rules_text"] = row["rules_text"]
        new.execute(
            "INSERT INTO card_history (card_id, valid_from, valid_to, face) "
            "VALUES (?, ?, ?, ?)",
            (row["card_id"], row["valid_from"], row["valid_to"], face_of(face)))
    new.commit()

    for table in ("cards", "printings", "slug_history", "name_history"):
        before = old.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        after = new.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        if before != after:
            raise ValueError(f"{table}: {before} rows before, {after} after")
    before = old.execute("SELECT count(*) AS n FROM rules_history").fetchone()["n"]
    after = new.execute("SELECT count(*) AS n FROM card_history").fetchone()["n"]
    if before != after:
        raise ValueError(f"history: {before} rows before, {after} after")
    for table, column in (("cards", "card_id"), ("printings", "printing_id")):
        before = [r[0] for r in old.execute(f"SELECT {column} FROM {table} ORDER BY 1")]
        after = [r[0] for r in new.execute(f"SELECT {column} FROM {table} ORDER BY 1")]
        if before != after:
            raise ValueError(f"{table}: the set of {column}s changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="registry.sqlite")
    args = parser.parse_args()

    db_path = Path(args.db)
    fresh_path = db_path.with_name(db_path.name + ".v8")
    if fresh_path.exists():
        fresh_path.unlink()
    old = open_db(db_path)
    new = open_db(fresh_path)
    try:
        migrate(old, new)
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
