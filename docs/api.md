# The URL contract

The registry is published as one object per thing, so that every card, printing, slug and set has an address and the common questions are one HTTP request each. The objects are produced from the export by `python -m registry.publish` (into `dist/`, never committed) and uploaded at release time; a CDN in front of the bucket serves them. There is no server: every answer was computed at release time.

**Status: the objects are produced; hosting is not yet switched on.** When it is, the base URL and the version scheme will be documented here and in the README.

All paths below are relative to a version root. The plan is two roots per major version: an immutable one per release (`/v2.0.0/…`, cache forever) and a moving one (`/v2/…`, the latest v2.x, short cache). A consumer picks how much change they want to see; a breaking change is a new major root, so nothing breaks in place.

## Discovery

`index.json` - what exists: `schema_version`, `dataset_version`, record counts, and the `endpoints` map below with `{placeholder}` templates. Fetch this first.

## The export itself

- `registry.json` - the full export, byte for byte the committed file, so its checksum holds for the served copy.
- `registry.json.sha256` - `sha256sum` format. Poll this (~80 bytes) to learn whether the export changed.
- `schema.json` - the export's JSON Schema (draft 2020-12).

## One object per thing

| Path | Answers | Shape |
|---|---|---|
| `cards/{codex_id}.json` | one card | the card record from the export (including `default_printing_id`), plus `printings` (a summary of each: `printing_id`, `slug`, `set_code`, `set_name`, `released_at`, `product`, `finish`, `printed_as_current`, `retired_at`), `name_history` and `card_history` (that card's rows) |
| `printings/{printing_id}.json` | one physical print | the printing record, plus `slug_history` (that printing's rows) |
| `slugs/{slug}.json` | "what is this slug?" for **any slug that has ever existed** | `slug`, `printing_id`, `codex_id`, `card_name`, `current_slug`, `is_current`, `valid_from`, `valid_to`, `set_code`, `set_name`, `product`, `finish`, `retired_at` |
| `sets.json` | the set catalogue | the export's `sets` section |
| `sets/{set_code}.json` | one set and everything in it | the set entry, plus `cards`: `{codex_id, name, printing_ids}` for every card in the set, ordered by name (the official data has no collector numbers), with only that set's printings |

A slug object exists for every slug in `slug_history`, current or superseded. That is the migration path as a URL: a tool holding a pre-rename slug fetches `slugs/{old}.json` and receives the permanent ids and the current slug. A 404 here means the slug never existed in the registry under any naming convention.

## Indexes, for client-side search

The registry has ~1,100 cards; filtering them in the client is a millisecond. These are the compact lists to do it with:

- `index/cards.json` - `codex_id`, `name`, `type`, `category`, `rarity`, `elements`, `keywords`, `subtypes`, `cost`, `errata`, `set_codes`, `default_printing_id` per card (~220 KB).
- `index/printings.json` - `printing_id`, `codex_id`, `slug`, `set_code`, `product`, `finish`, `printed_as_current`, `retired_at` per printing (~500 KB).
- `index/slugs.json` - `{slug: printing_id}` for every slug ever (~100 KB).

## History, whole

`history/slugs.json`, `history/names.json`, `history/cards.json` - the export's three history sections, for consumers that want them all at once. `history/cards.json` is every state every card's gameplay face has been in; a printing released within a row's dates was printed with that row's values.

## Guarantees carried over

Everything the README guarantees about the export holds here, because the objects are derived from it and from nothing else: ids never change or vanish, a slug never refers to a different printing (the publisher refuses to run on an export where one does), and the served `registry.json` matches its published checksum.
