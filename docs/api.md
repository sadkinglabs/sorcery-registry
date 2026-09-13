# The URL contract

The registry is published as one object per thing, so that every card, printing, slug and set has an address and the common questions are one HTTP request each. The objects are produced from the export by `python -m registry.publish` (into `dist/`, never committed) and uploaded at release time; a CDN in front of the bucket serves them. There is no server: every answer was computed at release time.

## Where it lives

Base URL: **`https://api.kairosarchive.net`** (Cloudflare R2 behind the zone's CDN; no server, no keys, no signup). Every release is also mirrored on GitHub as a tagged release (`raw.githubusercontent.com/sadkinglabs/sorcery-registry/<tag>/export/registry.json`).

| URL | What | Cache |
|---|---|---|
| `/versions.json` | the discovery document: `base_url`, `latest` per major (`{"v3": "v3.1.0"}`), and every `releases[]` entry with its `tag`, `schema_version`, `released_at` and the `sha256` of its `registry.json` | 60 s |
| `/v3.1.0/…` (one root per release) | the objects below, **immutable**: the bytes at a published path never change, so pin a root and cache it forever | 1 year, `immutable` |
| `/v3/…` (one alias per major) | a `302` to the same path under the newest verified v3.x root | the redirect target changes on release; the target is immutable |

Pick how much change you want to see. **Pin a release** (`/v3.1.0/`) and nothing you fetch ever changes under you; **follow the alias** (`/v3/`) and you always get the newest data of a shape you already understand; **poll `versions.json`** (≤ hourly is plenty; the data changes a few times a year) to learn when a new release exists and what its digest is. A breaking change to the shape is a new major - a new alias (`/v4/`) - so nothing breaks in place, and the previous major keeps its alias and its roots.

A release appears in `versions.json`, and the alias moves, only after the release workflow has verified byte for byte that the CDN serves the new root; `latest` only ever moves forward. Each root also carries `manifest.json` (the release manifest: counts, artifact digests) and a `RELEASED` marker (the `registry.json` digest and the workflow run that verified it), so a root without the marker is a partial upload, never a release. CORS allows `GET`/`HEAD` from any origin, so a browser can fetch the objects directly.

All paths below are relative to a release root or the major alias.

## Discovery

`index.json` - what exists: `schema_version`, `dataset_version`, record counts, the `endpoints` map below with `{placeholder}` templates, and where things live: `base_url` (the absolute root these objects were uploaded to, an immutable release root) and `latest_url` (the moving major alias that always redirects to the newest release); both null in a local `dist/`. `manifest` names `manifest.json` when the release manifest was copied into the root. Fetch this first.

## Records carry their own addresses

Every card, printing and set record - in the export, in the per-object files, in the indexes - carries `api_url` and `kairos_url`, and cards and printings carry `image_urls` and `image_status`; slug objects carry the resolved printing's `api_url` and `kairos_url`. `api_url` is the **current-record URL**: it points at the moving major alias, so a consumer reading a historical release root and following `api_url` leaves that snapshot by design and lands on the current record; a consumer that wants the snapshot uses the paths of the root it fetched. The addresses are derived from the ids at export time and CI proves each one names the record it sits on.

## The export itself

- `registry.json` - the full export, byte for byte the committed file, so its checksum holds for the served copy.
- `registry.json.sha256` - `sha256sum` format. Poll this (~80 bytes) to learn whether the export changed.
- `schema.json` - the export's JSON Schema (draft 2020-12).

## One object per thing

| Path | Answers | Shape |
|---|---|---|
| `cards/{codex_id}.json` | one card | the card record from the export (including `default_printing_id`), plus `printings` (a summary of each: `printing_id`, `slug`, `set_code`, `set_name`, `released_at`, `product`, `finish`, `printed_as_current`, `retired_at`), `name_history` and `card_history` (that card's rows) |
| `printings/{printing_id}.json` | one physical print | the printing record, plus `slug_history` (that printing's rows) |
| `slugs/{slug}.json` | "what is this slug?" for **any slug that has ever existed** | `slug`, `printing_id`, `codex_id`, `card_name`, `current_slug`, `is_current`, `valid_from`, `valid_to`, `set_code`, `set_name`, `product`, `finish`, `retired_at`, `api_url`, `kairos_url` (the printing's) |
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
