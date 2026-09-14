"""One-off migration of the committed database from schema v9 to v10.

    python -m registry.migrate_v10 [--db registry.sqlite]

v10 adds nothing to the database: power is derived at export time and
image_status's new value comes from data/images.json. The migration only
records the new schema version, so the validator's "database matches the
code" check keeps its meaning.
"""

import argparse
import sys

from . import SCHEMA_VERSION
from .db import get_meta, open_db, set_meta


def migrate(con):
    found = get_meta(con, "schema_version")
    if found != "9":
        raise ValueError(f"expected a v9 database, found schema_version {found!r}")
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
