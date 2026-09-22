"""One-off migration of the committed database to schema v12.

    python -m registry.migrate_v12 [--db registry.sqlite]

v12 adds notes on cards and printings (read at export time from
data/notes.json, no database change) and manual entries: cards and
printings the registry records by hand (data/manual.json). For those,
cards and printings gain origin ('api' for every existing record),
manual_source, manual_recorded, confirmed_at, withdrawn_at and
withdrawn_reason, and printings gain released_with.

v12 was built in two steps before it was released, so this accepts a v11
database or a v12 one that predates the manual columns, and adds only the
columns that are missing. Adding a column keeps every row and every id.
"""

import argparse
import sys

from . import SCHEMA_VERSION
from .db import get_meta, open_db, set_meta

ADDED = {
    "cards": [
        ("origin", "TEXT NOT NULL DEFAULT 'api'"),
        ("manual_source", "TEXT"),
        ("manual_recorded", "TEXT"),
        ("confirmed_at", "TEXT"),
        ("withdrawn_at", "TEXT"),
        ("withdrawn_reason", "TEXT"),
    ],
    "printings": [
        ("released_with", "TEXT"),
        ("origin", "TEXT NOT NULL DEFAULT 'api'"),
        ("manual_source", "TEXT"),
        ("manual_recorded", "TEXT"),
        ("confirmed_at", "TEXT"),
        ("withdrawn_at", "TEXT"),
        ("withdrawn_reason", "TEXT"),
    ],
}


def migrate(con):
    found = get_meta(con, "schema_version")
    if found not in ("11", "12"):
        raise ValueError(f"expected a v11 database, found schema_version {found!r}")
    added = 0
    for table, columns in ADDED.items():
        have = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                added += 1
    if found == "12" and not added:
        raise ValueError("the database is already at v12 with every column; nothing to do")
    set_meta(con, "schema_version", SCHEMA_VERSION)
    con.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="registry.sqlite")
    args = parser.parse_args()
    con = open_db(args.db)
    migrate(con)
    con.close()
    print(f"{args.db}: migrated to schema v{SCHEMA_VERSION}")


if __name__ == "__main__":
    sys.exit(main())
