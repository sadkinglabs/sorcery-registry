"""SQLite schema and helpers.

The invariants are enforced in the schema itself, not by convention:
- triggers abort any UPDATE of card_id / printing_id and any DELETE,
  so no code path can move or remove an identifier;
- id assignment reads high-water counters in the meta table that only
  ever increase, so retired numbers are never handed out again.
"""

import sqlite3

from . import SCHEMA_VERSION

DB_FILENAME = "registry.sqlite"

CARD_FIELDS = [
    "name", "type", "rarity", "subtypes", "elements",
    "cost", "attack", "defence", "life",
    "thr_air", "thr_earth", "thr_fire", "thr_water",
    "rules_text",
]

PRINTING_FIELDS = [
    "set_name", "released_at", "set_number",
    "product", "finish", "slug",
    "artist", "flavour_text", "type_text",
    "rarity", "type", "rules_text",
    "cost", "attack", "defence", "life",
    "thr_air", "thr_earth", "thr_fire", "thr_water",
    "image_hash",
]

DDL = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE cards (
    card_id    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    type       TEXT,
    rarity     TEXT,
    subtypes   TEXT,
    elements   TEXT,
    cost       INTEGER,
    attack     INTEGER,
    defence    INTEGER,
    life       INTEGER,
    thr_air    INTEGER NOT NULL DEFAULT 0,
    thr_earth  INTEGER NOT NULL DEFAULT 0,
    thr_fire   INTEGER NOT NULL DEFAULT 0,
    thr_water  INTEGER NOT NULL DEFAULT 0,
    rules_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE printings (
    printing_id  INTEGER PRIMARY KEY,
    card_id      INTEGER NOT NULL REFERENCES cards(card_id),
    set_name     TEXT NOT NULL,
    released_at  TEXT,
    set_number   TEXT,
    product      TEXT,
    finish       TEXT,
    slug         TEXT NOT NULL UNIQUE,
    artist       TEXT,
    flavour_text TEXT,
    type_text    TEXT,
    rarity       TEXT,
    type         TEXT,
    rules_text   TEXT,
    cost         INTEGER,
    attack       INTEGER,
    defence      INTEGER,
    life         INTEGER,
    thr_air      INTEGER NOT NULL DEFAULT 0,
    thr_earth    INTEGER NOT NULL DEFAULT 0,
    thr_fire     INTEGER NOT NULL DEFAULT 0,
    thr_water    INTEGER NOT NULL DEFAULT 0,
    image_hash   TEXT,
    retired_at   TEXT
);

CREATE INDEX idx_printings_card_id ON printings(card_id);

CREATE TABLE slug_history (
    slug        TEXT NOT NULL,
    printing_id INTEGER NOT NULL REFERENCES printings(printing_id),
    valid_from  TEXT NOT NULL,
    valid_to    TEXT,
    UNIQUE (printing_id, slug, valid_from)
);

CREATE INDEX idx_slug_history_slug ON slug_history(slug);

-- Identifier immutability, enforced at the engine level.
CREATE TRIGGER cards_no_delete BEFORE DELETE ON cards
BEGIN SELECT RAISE(ABORT, 'cards are append only: DELETE is forbidden'); END;

CREATE TRIGGER cards_id_immutable BEFORE UPDATE OF card_id ON cards
BEGIN SELECT RAISE(ABORT, 'card_id is immutable'); END;

CREATE TRIGGER printings_no_delete BEFORE DELETE ON printings
BEGIN SELECT RAISE(ABORT, 'printings are append only: DELETE is forbidden'); END;

CREATE TRIGGER printings_id_immutable BEFORE UPDATE OF printing_id ON printings
BEGIN SELECT RAISE(ABORT, 'printing_id is immutable'); END;

CREATE TRIGGER printings_card_immutable BEFORE UPDATE OF card_id ON printings
BEGIN SELECT RAISE(ABORT, 'a printing never moves to a different card'); END;
"""


def open_db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(con):
    con.executescript(DDL)
    con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.execute("INSERT INTO meta VALUES ('next_card_id', '1')")
    con.execute("INSERT INTO meta VALUES ('next_printing_id', '1')")
    con.commit()


def get_meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(con, key, value):
    con.execute(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def allocate_id(con, counter_key):
    """Hand out the next id and advance the high-water mark. The counter
    never decreases, which is what makes 'never reuse an id' checkable."""
    next_id = int(get_meta(con, counter_key))
    set_meta(con, counter_key, next_id + 1)
    return next_id


def load_registry_state(con):
    """Load the registry into the same snapshot shape fetch.build_snapshot
    produces, with ids attached, for diffing."""
    cards = {}
    card_names = {}
    for row in con.execute("SELECT * FROM cards"):
        record = {field: row[field] for field in CARD_FIELDS}
        record["card_id"] = row["card_id"]
        cards[row["name"]] = record
        card_names[row["card_id"]] = row["name"]

    printings = {}
    for row in con.execute("SELECT * FROM printings"):
        record = {field: row[field] for field in PRINTING_FIELDS}
        record["printing_id"] = row["printing_id"]
        record["card_name"] = card_names[row["card_id"]]
        record["retired_at"] = row["retired_at"]
        printings[row["slug"]] = record

    return {"cards": cards, "printings": printings}
