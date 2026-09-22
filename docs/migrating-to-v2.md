# Migrating to v3.2 (from v3.1), v3.1 (from v3.0), v3.0 (from v2.0) and v2.0 (from v1.x)

## v3.3 to v3.4 (schema 12)

Additive; **no identifier changed** and no existing value changed.

| New | On | Value |
|---|---|---|
| `notes` | cards and printings, after `image_status` | a list of `{text, source, recorded}`: what the registry knows that the official API does not say, with where it came from and the day it was recorded. Empty for almost every record |
| `gaps` | a new top-level section, after `card_history`, and `gaps.json` at every release root | cards and printings known to exist that the registry does not record: `{name, codex_id, text, source, recorded}`, with `codex_id` null for an unrecorded card |
| `header.gaps` | the header, and `counts.gaps` in `index.json` | the number of gaps |
| `notes_*`, `gaps_*` | `changes.json` summary, and `notes`/`gaps` sections | notes added and removed, gaps recorded and closed |

A strict parser that rejects unknown keys needs the new schema; everything else reads v3.4 unchanged. `changes.json` also stops counting a field a release adds as a change to records where that field is empty.


## v3.2 to v3.3 (schema 11)

Additive. `card_history` rows gain `source`: `"api"` for a face the registry observed in the official API (every row until now), `"card"` for a face recorded by hand from what is printed on the card (`data/errata.json`). Cards the publisher changed after printing gain a closed `"card"` row holding their printed face, dated from the first printing that carries it, and their older printings' `printed_as_current` becomes `false`; `errata` was already `true` for them. Nothing else changes.


## v3.1 → v3.2 (schema 9 → 10)

Additive; **no identifier changed**. The images landed and one derived value was added:

| New | On | Value |
|---|---|---|
| `power` | cards, `back` faces, `card_history` rows (after `defense`) | Sorcery's derived power: equal to attack when attack equals defense, otherwise ⌊(attack + defense) / 2⌋; null when either is null |
| `image_status: "lowres"` | cards, printings | a third value: held and served in every rendition, but the publisher's file was upscaled to fill the large rendition (the early sets ship at 380×531) |
| `image_urls` filled in | printings, `back` faces, cards | the fields existed since v3.1 as null; they now carry addresses under `api.kairosarchive.net/images/` for every printing the publisher's folder has a file for |
| `image_hash` filled in | printings | the art-version key behind those addresses |

`index/cards.json` gains `attack`, `defense`, `power`, `life` and `image_status`; `index/printings.json` gains `image_hash` and `image_status`. A consumer validating against the schema needs the v3.2 schema; one reading fields by name needs nothing.


## v3.0 → v3.1 (schema 8 → 9)

Purely additive; nothing was renamed, removed or retyped, and **no identifier changed**. Every card, printing and set gained the addresses it lives at, derived from its id:

| New field | On | Value |
|---|---|---|
| `api_url` | cards, printings, sets | the record's own JSON object on `api.kairosarchive.net`, under the moving `/v3/` alias (always the newest verified v3.x release) |
| `kairos_url` | cards, printings, sets | the record's page on `kairosarchive.net` |
| `image_urls` | cards, printings, `printings[].back` | `{small, normal, large, original}` renditions, or `null` while the registry holds no image (all null in v3.1; the image pipeline fills them in a later minor release without changing the shape) |
| `image_status` | cards, printings | `missing`, `lowres` or `ok` (`lowres` added in v3.2 when the images landed); a card carries its `default_printing_id`'s |

A consumer that validates against the schema needs the v3.1 schema (the fields are required); one that reads fields by name needs nothing. Published objects: `index.json` gained `base_url`, `latest_url` and `manifest`; slug objects gained the resolved printing's `api_url` and `kairos_url`.

v3.1.0 is also the first release served from the domain. Where to pin, from that release on:

| | GitHub (every release) | Domain (v3.1.0 onward) |
|---|---|---|
| the export, pinned | `https://raw.githubusercontent.com/sadkinglabs/sorcery-registry/v3.1.0/export/registry.json` | `https://api.kairosarchive.net/v3.1.0/registry.json` |
| its checksum | `…/v3.1.0/export/registry.json.sha256` | `https://api.kairosarchive.net/v3.1.0/registry.json.sha256` |
| the schema | `…/v3.1.0/schema/registry.schema.json` | `https://api.kairosarchive.net/v3.1.0/schema.json` |
| newest of the major | `github.com/sadkinglabs/sorcery-registry/releases/latest` (redirect) | `https://api.kairosarchive.net/versions.json` → `latest.v3`, or the `/v3/` alias |

## v2.0 → v3.0 (schema 7 → 8)

Prompted by upstream announcing reprints that change existing cards' cost and power. v2.0 recorded only rules-text changes; v3.0 records the whole gameplay face. **No identifier changed.**

| v2.0 | v3.0 | Note |
|---|---|---|
| `rules_history` section: `{rules_text, codex_id, valid_from, valid_to}` | `card_history` section: `{codex_id, valid_from, valid_to, type, category, rarity, slot, subtypes, elements, keywords, umbrellas, cost, attack, defense, life, thr_*, rules_text, back}` | **Renamed and widened.** One row per state of the face, oldest first, open row last. The v2.0 rows carry over exactly: under v2.0 only text ever changed, so each old row's face is the current face with that row's text. |
| `header.rules_history` | `header.card_history` | |
| `errata` = text changed | `errata` = any gameplay field changed (`type`, `elements`, `cost`, `attack`, `defense`, `life`, thresholds, `rules_text`, `back`) | Same values today (29 cards); the meaning widened. Classification changes (`rarity`, `slot`, `subtypes`, `keywords`, `umbrellas`) are recorded in `card_history` but do not set `errata`. |
| - | `cards[].default_printing_id` | New, derived: not retired, showing the card's current face over older values, Booster over other products, Standard over other finishes, most recent release, lowest id. |
| - | `printings[].printed_as_current` | New, derived: whether the printing's physical values equal the card's current face - `true` when released on or after the current face's `valid_from`, or when the face never changed; `null` without a release date. |

Code: `export["rules_history"]` → `export["card_history"]`; read `row["rules_text"]` as before, and now also `row["cost"]` etc. Published objects: `history/rules.json` → `history/cards.json`; `cards/{id}.json` carries `card_history` instead of `rules_history`.

## v1.x → v2.0 (schema 6 → 7)

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

## New section: `rules_history` (v2.0; widened into `card_history` in v3.0)

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
