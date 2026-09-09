# Migrating from v1.x to v2.0

Registry release v2.0.0 (export `schema_version` 7) changed the shape of every record. **No identifier changed**: every `codex_id` and `printing_id` from v1.x is present in v2.0 and refers to the same card or printing, and CI proved append-only across the change. If you key on the ids, as the README asks, migration is renaming fields and, in three places, changing a type.

Why it happened: the official API was rebuilt on a new backend in August 2026 and the registry now mirrors its shape - gameplay data on the card, physical facts on the printing, the upstream field names and spellings. See the [v2.0.0 release notes](https://github.com/sadkinglabs/sorcery-registry/releases/tag/v2.0.0) for the full story.

Pin either version while you migrate:

- v1.5.0 (schema 6): `https://raw.githubusercontent.com/sadkinglabs/sorcery-registry/v1.5.0/export/registry.json`
- v2.0.0 (schema 7): `https://raw.githubusercontent.com/sadkinglabs/sorcery-registry/v2.0.0/export/registry.json`

## Header

| v1.x | v2.0 |
|---|---|
| `schema_version: 6` | `schema_version: 7` |
| - | `rules_history` (count of the new section) |

## Cards

| v1.x | v2.0 | Note |
|---|---|---|
| `defence` | `defense` | Upstream spelling. |
| `subtypes` (string, comma-joined: `"Beast, Spirit"`) | `subtypes` (array: `["Beast", "Spirit"]`) | **Type change.** Upstream's order. |
| `elements` (string, comma-joined: `"Earth, Water"`) | `elements` (array: `["Earth", "Water"]`) | **Type change.** Upstream's order, which differs from v1's fixed order. `["None"]` means colourless. |
| `set_numbers` | `set_codes` | Same values (`"001"`). |
| `errata` | `errata` | Same field, new source: the registry now maintains it itself (see below). |
| - | `category` | `Spell`, `Site`, `Avatar` or `Token`. |
| - | `slot` | The rarity slot the card is distributed in; equals `rarity` except for Avatars and some tokens, where one of the two is null. |
| - | `keywords` | Array of ability keywords (`Airborne`, `Genesis`, ...). |
| - | `umbrellas` | Array of cross-subtype groups (`Evil`, `Knight`, `Royalty`). |
| - | `back` | Gameplay data of the reverse face for double-faced cards; `null` for all but two cards (Druid, Foot Soldier). |

Unchanged: `codex_id`, `name`, `type`, `rarity`, `cost`, `attack`, `life`, `thr_air`, `thr_earth`, `thr_fire`, `thr_water`, `rules_text`, `printing_ids`.

`rules_text` lost the `UPDATED: ` prefix that the old API put on errata'd cards; 28 cards changed text for that reason alone. The meaning moved into `errata` and `rules_history`.

## Printings

| v1.x | v2.0 | Note |
|---|---|---|
| `set_number` | `set_code` | Same values. It is a code, not a number: `003` is unused. |
| `type_text` | `typeline` | Upstream name. |
| `artist` | `artist` + `artist_slug` | `artist_slug` is new (`jeff_a_menges`). |
| `released_at` | `released_at` | Same field, new meaning: the date **this printing** reached the public, from upstream's `printedAt`. Alpha printings moved from `2023-04-19` to `2023-06-22`, Beta from `2023-11-10` to `2023-10-06`; promos carry their individual dates. |
| `product` | `product` | Same field, upstream's new spelling: `Box_Topper` → `BoxTopper`, `Organized_Play` → `OrganizedPlay`, `Preconstructed_Deck` → `PreconstructedDeck`, `Draft_Kit` → `DraftKit`, `Alpha_Investments` → `AlphaInvestments`, `Welcome_Kit` → `WelcomeKit`, `Team_Covenant` → `TeamCovenant`, `Star_City_Games` → `StarCityGames`. `Booster`, `Dust`, `Kickstarter` unchanged. Strip underscores to compare. |
| `rarity`, `type`, `rules_text`, `cost`, `attack`, `defence`, `life`, `thr_*` | **removed** | Gameplay data is on the card. Join through `codex_id`; every printing carries it. |
| - | `back` | `artist`, `artist_slug`, `flavour_text`, `typeline` of the reverse face for three double-faced printings; `null` otherwise. |

Unchanged: `printing_id`, `codex_id`, `card_name`, `set_name`, `finish`, `slug`, `flavour_text`, `image_hash`, `retired_at`.

`set_name` for set `999` changed from `Promotional` to `Promo` (upstream renamed it).

## Sets

| v1.x | v2.0 | Note |
|---|---|---|
| `set_number` | `set_code` | |
| `released_at` | `released_at` | Now the earliest `released_at` among the set's printings. |

## New section: `rules_history`

```json
{ "rules_text": "Lance\nThe first time Sir Lancelot fights each turn, untap him.",
  "codex_id": "C000572", "valid_from": "2026-09-09", "valid_to": "2026-09-09" }
```

One row per text a card has played by. Every card has exactly one open row (`valid_to: null`) equal to its `rules_text`; a closed row is a wording the card used to have. Seeded on 2026-09-09 with every card's text at that date, so history before that is not recorded.

## `errata` changed source, not shape

In v1.x the flag was derived from the old API's `UPDATED:` text prefix. The rebuilt API has no such marker and no replacement. In v2.0 the registry maintains the flag itself: seeded from the old marker (28 cards), set whenever a sync observes a card's `rules_text` change (29 cards at v2.0.0), corrected only through `data/overrides.json`. Read it as "this card's text has been updated since it was printed".

## What to change in your code

Old field names:

```python
card["defense"]        # was card["defence"]
card["set_codes"]      # was card["set_numbers"]
printing["set_code"]   # was printing["set_number"]
printing["typeline"]   # was printing["type_text"]
```

Strings that became arrays:

```python
# v1.x
"Water" in card["elements"].split(", ")
# v2.0
"Water" in card["elements"]
```

Gameplay data you read from a printing now lives on its card:

```python
cards = {c["codex_id"]: c for c in registry["cards"]}
rules = cards[printing["codex_id"]]["rules_text"]   # was printing["rules_text"]
```

Product comparisons, if you hard-coded v1 spellings:

```python
def product_key(value):
    return value.replace("_", "").lower()
product_key(printing["product"]) == product_key("Box_Topper")
```

Set dates: if you displayed `released_at` as "the set's release", read it from the `sets` section (earliest printing) rather than from an arbitrary printing, and expect Alpha and Beta to differ from v1.

## MCP server

Same seven tools. `search_cards` gained `keyword` and `category` filters and keeps `errata`; results carry `category` and `keywords`. Every set is addressed by `set_code` (`"006"`) or name, as before. `search_printings` accepts product names in any of the spellings (`box topper`, `box_topper`, `BoxTopper`). Responses that carried `set_number` now carry `set_code`.

## Validation

The v2.0 export validates against [`schema/registry.schema.json`](../schema/registry.schema.json) (JSON Schema 2020-12). Validating your own copy against it is the fastest way to find a field you missed.
