"""One-off migration of the committed database from schema v11 to v12.

    python -m registry.migrate_v12 [--db registry.sqlite]

v12 adds notes on cards and printings and the gaps section, both read at
export time from data/notes.json and data/gaps.json. The database does
not change; this only records the new version, because the validator
insists the database and the code agree on it.
"""

import argparse
import sys

from . import SCHEMA_VERSION
from .db import get_meta, open_db, set_meta


def migrate(con):
    found = get_meta(con, "schema_version")
    if found != "11":
        raise ValueError(f"expected a v11 database, found schema_version {found!r}")
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
