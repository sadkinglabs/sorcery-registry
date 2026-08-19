"""First-run population: create the schema and load every card from the
official API, assigning ids sequentially. Refuses to touch a database that
already has data; after the first run, use registry.sync.

    python -m registry.populate [--from-file X] [--as-of YYYY-MM-DD]
"""

import sys
from pathlib import Path

from .db import open_db


def main():
    db_path = Path("registry.sqlite")
    if db_path.exists():
        con = open_db(db_path)
        count = con.execute(
            "SELECT count(*) AS n FROM sqlite_master WHERE name = 'cards'").fetchone()["n"]
        if count:
            sys.exit("populate refused: registry.sqlite already exists with a schema. "
                     "Use python -m registry.sync instead.")
        con.close()

    from .sync import main as sync_main
    sys.argv = [sys.argv[0], "--init", "--yes"] + sys.argv[1:]
    return sync_main()


if __name__ == "__main__":
    sys.exit(main())
