"""One-off migration of the committed database from schema v8 to v9.

    python -m registry.migrate_v9 [--db registry.sqlite]

v9 adds nothing to the database: the new export fields (api_url,
kairos_url, image_urls, image_status) are derived from ids at export time.
The migration only records the new schema version, so the validator's
"database matches the code" check keeps its meaning.
"""

import argparse
import sys

from . import SCHEMA_VERSION
from .db import get_meta, open_db, set_meta


def migrate(con):
    found = get_meta(con, "schema_version")
    if found != "8":
        raise ValueError(f"expected a v8 database, found schema_version {found!r}")
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
