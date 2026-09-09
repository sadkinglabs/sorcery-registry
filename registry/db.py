"""SQLite schema and helpers.

The invariants are enforced in the schema itself, not by convention:
- triggers abort any UPDATE of card_id / printing_id and any DELETE,
  so no code path can move or remove an identifier;
- id assignment reads high-water counters in the meta table that only
  ever increase, so retired numbers are never handed out again;
- triggers abort any write that would make a slug refer to a printing
  other than the one it has always referred to.
"""

import json
import sqlite3

from . import SCHEMA_VERSION

DB_FILENAME = "registry.sqlite"

CARD_FIELDS = [
    "name", "type", "category", "rarity", "slot",
    "subtypes", "elements", "keywords", "umbrellas",
    "cost", "attack", "defense", "life",
    "thr_air", "thr_earth", "thr_fire", "thr_water",
    "rules_text", "back",
]

# Registry-owned card columns: stored on the card, published with it, but
# never read from upstream and never compared against it.
CARD_OWNED_FIELDS = ["errata"]

# The gameplay fields a back face carries: the card fields minus name and
# minus the face itself.
FACE_FIELDS = [f for f in CARD_FIELDS if f not in ("name", "back")]

PRINTING_FIELDS = [
    "set_name", "set_code", "released_at",
    "product", "finish", "slug",
    "artist", "artist_slug", "flavour_text", "typeline", "back",
    "image_hash",
]

# A printing's back face: the physical facts that differ per face.
PRINTING_FACE_FIELDS = ["artist", "artist_slug", "flavour_text", "typeline"]

# Lists and objects live as JSON text in SQLite and as Python values
# everywhere else (snapshots, plans, the export).
JSON_FIELDS = {"subtypes", "elements", "keywords", "umbrellas", "back"}

DDL = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE cards (
    card_id    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    type       TEXT,
    category   TEXT,
    rarity     TEXT,
    slot       TEXT,
    subtypes   TEXT,
    elements   TEXT,
    keywords   TEXT,
    umbrellas  TEXT,
    cost       INTEGER,
    attack     INTEGER,
    defense    INTEGER,
    life       INTEGER,
    thr_air    INTEGER NOT NULL DEFAULT 0,
    thr_earth  INTEGER NOT NULL DEFAULT 0,
    thr_fire   INTEGER NOT NULL DEFAULT 0,
    thr_water  INTEGER NOT NULL DEFAULT 0,
    rules_text TEXT NOT NULL DEFAULT '',
    back       TEXT,
    -- Registry-owned: true once the card's text has been updated since it
    -- was printed. Seeded from the old upstream UPDATED: marker, set by
    -- every observed rules_text change, corrected only through overrides.
    errata     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE printings (
    printing_id  INTEGER PRIMARY KEY,
    card_id      INTEGER NOT NULL REFERENCES cards(card_id),
    set_name     TEXT NOT NULL,
    set_code     TEXT,
    released_at  TEXT,
    product      TEXT,
    finish       TEXT,
    slug         TEXT NOT NULL UNIQUE,
    artist       TEXT,
    artist_slug  TEXT,
    flavour_text TEXT,
    typeline     TEXT,
    back         TEXT,
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

-- Card names get the same history treatment as slugs, so a decklist
-- written against an old name still resolves. Deliberately no ownership
-- trigger: unlike slugs, two different cards may legitimately hold the
-- same name at different times. Names are lookup history, not identity.
CREATE TABLE name_history (
    name       TEXT NOT NULL,
    card_id    INTEGER NOT NULL REFERENCES cards(card_id),
    valid_from TEXT NOT NULL,
    valid_to   TEXT,
    UNIQUE (card_id, name, valid_from)
);

CREATE INDEX idx_name_history_name ON name_history(name);

-- Every text a card has played by. Upstream publishes only the current
-- text and no longer marks errata, so the registry records what it
-- observes: a sync that changes a card's rules_text closes the open row
-- and opens a new one. Consumers see that a card's wording changed, and
-- when, without the registry ruling on why.
CREATE TABLE rules_history (
    rules_text TEXT NOT NULL,
    card_id    INTEGER NOT NULL REFERENCES cards(card_id),
    valid_from TEXT NOT NULL,
    valid_to   TEXT,
    UNIQUE (card_id, rules_text, valid_from)
);

CREATE INDEX idx_rules_history_card ON rules_history(card_id);

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

-- Slug ownership: once a slug has referred to a printing it may never
-- refer to a different one. A slug may return to its original printing
-- after an intervening rename (A -> B -> A), which these allow.
CREATE TRIGGER slug_history_no_reassign BEFORE INSERT ON slug_history
WHEN EXISTS (SELECT 1 FROM slug_history
              WHERE slug = NEW.slug AND printing_id != NEW.printing_id)
BEGIN SELECT RAISE(ABORT, 'slug already belongs to a different printing'); END;

CREATE TRIGGER printings_slug_owned_insert BEFORE INSERT ON printings
WHEN EXISTS (SELECT 1 FROM slug_history
              WHERE slug = NEW.slug AND printing_id != NEW.printing_id)
BEGIN SELECT RAISE(ABORT, 'slug already belongs to a different printing'); END;

CREATE TRIGGER printings_slug_owned_update BEFORE UPDATE OF slug ON printings
WHEN EXISTS (SELECT 1 FROM slug_history
              WHERE slug = NEW.slug AND printing_id != NEW.printing_id)
BEGIN SELECT RAISE(ABORT, 'slug already belongs to a different printing'); END;
"""


def encode_field(field, value):
    """Python value -> SQLite cell. Lists and objects become JSON text with
    sorted keys, so the same value always produces the same bytes."""
    if field in JSON_FIELDS and value is not None:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def decode_field(field, value):
    if field in JSON_FIELDS and value is not None:
        return json.loads(value)
    return value


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
        record = {field: decode_field(field, row[field]) for field in CARD_FIELDS}
        record["card_id"] = row["card_id"]
        record["errata"] = bool(row["errata"])
        cards[row["name"]] = record
        card_names[row["card_id"]] = row["name"]

    printings = {}
    for row in con.execute("SELECT * FROM printings"):
        record = {field: decode_field(field, row[field]) for field in PRINTING_FIELDS}
        record["printing_id"] = row["printing_id"]
        record["card_name"] = card_names[row["card_id"]]
        record["retired_at"] = row["retired_at"]
        printings[row["slug"]] = record

    # Every slug that has ever referred to a printing, and the printing it
    # belongs to permanently. History first, current slugs overlaid: the two
    # can never disagree while the ownership invariant holds.
    slug_owners = {}
    for row in con.execute("SELECT slug, printing_id FROM slug_history"):
        slug_owners[row["slug"]] = row["printing_id"]
    for row in con.execute("SELECT slug, printing_id FROM printings"):
        slug_owners[row["slug"]] = row["printing_id"]

    return {"cards": cards, "printings": printings, "slug_owners": slug_owners}
