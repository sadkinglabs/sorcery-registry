"""One-off migration of the committed database from schema v10 to v11.

    python -m registry.migrate_v11 [--db registry.sqlite]

v11 adds card_history.source: 'api' for a face observed in the official
API (every existing row), 'card' for a face recorded by hand from what is
printed on the card (see data/errata.json and registry/errata.py).
"""

import argparse
import sys

from . import SCHEMA_VERSION
from .db import get_meta, open_db, set_meta


def migrate(con):
    found = get_meta(con, "schema_version")
    if found != "10":
        raise ValueError(f"expected a v10 database, found schema_version {found!r}")
    columns = {row["name"] for row in con.execute("PRAGMA table_info(card_history)")}
    if "source" not in columns:
        con.execute("ALTER TABLE card_history ADD COLUMN source TEXT NOT NULL DEFAULT 'api'")
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
