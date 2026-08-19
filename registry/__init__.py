"""Sorcery: Contested Realm card identifier registry.

Stable integer identifiers for cards and printings, with the official
API slug demoted to an ordinary, mutable column.
"""

# v2: printings.card_number (INTEGER) became set_number (TEXT). The slug's
# leading digits are the set's number (001 = Alpha, 006 = Gothic), not a
# collector number - the official data has no within-set serialisation.
SCHEMA_VERSION = 2
API_URL = "https://api.sorcerytcg.com/api/cards"
