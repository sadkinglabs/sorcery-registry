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
SCHEMA_VERSION = 3
API_URL = "https://api.sorcerytcg.com/api/cards"
