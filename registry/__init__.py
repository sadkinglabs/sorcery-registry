"""Sorcery: Contested Realm card identifier registry.

Stable integer identifiers for cards and printings, with the official
API slug demoted to an ordinary, mutable column.
"""

# v2: printings.card_number (INTEGER) became set_number (TEXT). The slug's
# leading digits are the set's number (001 = Alpha, 006 = Gothic), not a
# collector number - the official data has no within-set serialisation.
# v3: printings.set_code removed - it was a registry-invented slugification
# of the set name; sets are identified by their official facts, set_number
# and set_name. The export gains derived card_name on each printing.
# v4: export gains a derived top-level "sets" catalogue and per-card
# "set_numbers", a .sha256 checksum file, and a published JSON Schema
# (schema/registry.schema.json). No database changes.
# v5: slug ownership enforced - a slug can never move to a different
# printing_id (engine triggers + planner + validator).
# v6: name_history - card renames tracked like slug renames, seeded with
# current names; export gains the section; release manifest tooling added.
# v7: the upstream API was rebuilt and the registry mirrors its new shape.
# Cards gain category, slot, keywords, umbrellas and an optional back face;
# subtypes and elements are arrays; defence is spelled defense. Printings
# carry physical facts only (gameplay columns removed - the current rules
# belong to the card), set_number is set_code (it is a code, not a number),
# type_text is typeline, artist gains artist_slug, and released_at is the
# printing's own date. Upstream no longer marks errata (the UPDATED: text
# prefix is gone), so errata is now a registry-owned stored flag: seeded
# from the old marker, set whenever a sync observes a card's text change,
# and rules_history records every such change alongside it.
SCHEMA_VERSION = 7
API_URL = "https://api.sorcerytcg.com/api/cards"
