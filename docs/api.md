# The URL contract

New here? The web documentation at [kairosarchive.net/docs](https://kairosarchive.net/docs) is written for that, with a reference page per record type generated from the schema and a changelog of releases; [`docs/quickstart.md`](quickstart.md) is the same walkthrough in this repository. This page is the complete reference.

The registry is published as one object per thing, so that every card, printing, slug and set has an address and the common questions are one HTTP request each. The objects are produced from the export by `python -m registry.publish` (into `dist/`, never committed) and uploaded at release time; a CDN in front of the bucket serves them. There is no server: every answer was computed at release time.

## Where it lives

Base URL: **`https://api.kairosarchive.net`** (Cloudflare R2 behind the zone's CDN; no server, no keys, no signup). Every release is also mirrored on GitHub as a tagged release (`raw.githubusercontent.com/sadkinglabs/sorcery-registry/<tag>/export/registry.json`).

| URL | What | Cache |
|---|---|---|
| `/versions.json` | the discovery document: `base_url`, `latest` per major (`{"v3": "v3.1.0"}`), and every `releases[]` entry with its `tag`, `schema_version`, `released_at` and the `sha256` of its `registry.json` | 60 s |
| `/v3.1.0/…` (one root per release) | the objects below, **immutable**: the bytes at a published path never change, so pin a root and cache it forever | 1 year, `immutable` |
| `/v3/…` (one alias per major) | a `302` to the same path under the newest verified v3.x root | the redirect target changes on release; the target is immutable |

Pick how much change you want to see. **Pin a release** (`/v3.1.0/`) and nothing you fetch ever changes under you; **follow the alias** (`/v3/`) and you always get the newest data of a shape you already understand; **poll `versions.json`** (≤ hourly is plenty; the data changes a few times a year) to learn when a new release exists and what its digest is. A breaking change to the shape is a new major - a new alias (`/v4/`) - so nothing breaks in place, and the previous major keeps its alias and its roots.

A release appears in `versions.json`, and the alias moves, only after the release workflow has verified byte for byte that the CDN serves the new root; `latest` only ever moves forward. Each root also carries `changes.json` (what the release changed against the previous one, see below), `manifest.json` (the release manifest: counts, artifact digests) and a `RELEASED` marker (the `registry.json` digest and the workflow run that verified it), so a root without the marker is a partial upload, never a release. CORS allows `GET`/`HEAD` from any origin, so a browser can fetch the objects directly.

All paths below are relative to a release root or the major alias.

## Discovery

`index.json` - what exists: `schema_version`, `dataset_version`, record counts, the `endpoints` map below with `{placeholder}` templates, and where things live: `base_url` (the absolute root these objects were uploaded to, an immutable release root) and `latest_url` (the moving major alias that always redirects to the newest release); both null in a local `dist/`. `manifest` names `manifest.json` when the release manifest was copied into the root; `terms` links the usage terms ([`docs/usage.md`](usage.md): what is free to use, what belongs to Erik's Curiosa, what we ask of clients). Fetch this first.

## Records carry their own addresses

Every card, printing and set record - in the export and in the per-object files - carries `api_url` and `kairos_url`, and cards and printings carry `image_urls` and `image_status`; slug objects carry the resolved printing's `api_url` and `kairos_url`. `api_url` is the **current-record URL**: it points at the moving major alias, so a consumer reading a historical release root and following `api_url` leaves that snapshot by design and lands on the current record; a consumer that wants the snapshot uses the paths of the root it fetched. The addresses are derived from the ids at export time and CI proves each one names the record it sits on. The indexes leave them out to stay small: both are a template away from the id each index record already carries (`{base}/v3/cards/{codex_id}.json` and `https://kairosarchive.net/cards/{codex_id}`, and the same shapes with `printings`/`{printing_id}`), and carrying them would add 38% to the card index and 56% to the printing index.

## The export itself

- `registry.json` - the full export, byte for byte the committed file, so its checksum holds for the served copy (~6.4 MB, ~380 KB over the wire: the edge serves it Brotli-compressed to any client that asks).
- `registry.json.sha256` - `sha256sum` format. Poll this (~80 bytes) to learn whether the export changed.
- `schema.json` - the export's JSON Schema (draft 2020-12).
- `types.d.ts` - TypeScript declarations generated from that schema (see below).

## Typed records for TypeScript

Every release root carries `types.d.ts`, generated from `schema.json` at release time, so a TypeScript project gets typed records with one import and nothing to install. Every field carries the schema's description as JSDoc, and a field added to the schema appears in the types on the next release.

```sh
curl --fail --location -O 'https://api.kairosarchive.net/v3/types.d.ts'
curl --fail --location -O 'https://api.kairosarchive.net/v3/registry.json'
```

```ts
// registry.ts - next to the two files above
import { readFile } from "node:fs/promises";
import type { Registry, Card } from "./types";

const registry: Registry = JSON.parse(await readFile("registry.json", "utf8"));
const bears: Card | undefined = registry.cards.find((c) => c.name === "Polar Bears");
console.log(bears?.codex_id, bears?.power);
```

Run it with Node 22.18 or newer, which strips the types itself: `node registry.ts`. To type-check it, install the compiler and Node's own type definitions once (`npm i -D typescript @types/node`; the registry's types need nothing, these two are for `tsc` and for `node:fs`), then `npx tsc --strict --noEmit --target es2022 --module esnext --moduleResolution bundler --types node registry.ts`. In a browser or a Worker the same declarations apply to a fetch and need no Node types at all:

```ts
import type { Registry } from "./types";
const registry = await (await fetch("https://api.kairosarchive.net/v3/registry.json")).json() as Registry;
```

(type-check that one with `--lib es2022,dom` instead of `--types node`).

What the file declares: `Registry` (the whole export) and one interface per record, `Card`, `Printing`, `RegistrySet` (not `Set`, which would shadow the built-in), `CardHistoryRow`, `NameHistoryRow`, `SlugHistoryRow` and `Header`; the shared shapes `Face` (the gameplay fields; `Card` and `CardHistoryRow` extend it, so a function that takes a `Face` accepts either), `PrintingFace`, `ImageUrls` and `Note`; and the aliases `CodexId`, `PrintingId`, `SetCode`, `IsoDate`, `Threshold` and `ImageStatus` (`"missing" | "lowres" | "ok"`). Nothing is optional and no record has an index signature, because the schema lists every field and allows no others. The declarations are only a description: `JSON.parse` does not validate, so a file that is not a registry export is not caught here; validate against `schema.json` for that.

The same file is committed as [`schema/registry.d.ts`](../schema/registry.d.ts); CI fails if it drifts from the schema, and type-checks a slice of the real export against it under strict `tsc`, so the types are proven neither too loose nor too tight before a release.

## One object per thing

| Path | Answers | Shape |
|---|---|---|
| `cards/{codex_id}.json` | one card | the card record from the export (including `default_printing_id`), plus `printings` (a summary of each: `printing_id`, `slug`, `set_code`, `set_name`, `released_at`, `released_with`, `product`, `finish`, `printed_as_current`, `retired_at`, `origin`), `name_history` and `card_history` (that card's rows) |
| `printings/{printing_id}.json` | one physical print | the printing record, plus `slug_history` (that printing's rows) |
| `slugs/{slug}.json` | "what is this slug?" for **any slug that has ever existed** | `slug`, `printing_id`, `codex_id`, `card_name`, `current_slug`, `is_current`, `valid_from`, `valid_to`, `set_code`, `set_name`, `product`, `finish`, `retired_at`, `api_url`, `kairos_url` (the printing's) |
| `sets.json` | the set catalogue | the export's `sets` section |
| `sets/{set_code}.json` | one set and everything in it | the set entry, plus `cards`: `{codex_id, name, printing_ids}` for every card in the set, ordered by name (the official data has no collector numbers), with only that set's printings |

A slug object exists for every slug in `slug_history`, current or superseded. That is the migration path as a URL: a tool holding a pre-rename slug fetches `slugs/{old}.json` and receives the permanent ids and the current slug. A 404 here means the slug never existed in the registry under any naming convention.

## Query API

One part of the API computes, on its own hostname: `GET https://query.kairosarchive.net/cards?q=…` runs the site's search syntax ([kairosarchive.net/syntax](https://kairosarchive.net/syntax)) over the same compact records the search page uses and returns a page of card records as JSON, with `unique:`, `sort:` and `order:` honoured and `page` / `page_size` (up to 200) for paging. `/cards/named?exact=…` (or `fuzzy=…`), `/cards/random?q=…` and `/cards/autocomplete?q=…` answer the three questions a bot asks most; `/cards/C000230` redirects to the static object on `api.kairosarchive.net`. (It has its own hostname because `api.kairosarchive.net` is the bucket's custom domain, where the bucket answers before any Worker.) Every answer is JSON with an `object` field (`list`, `card`, `catalog` or `error`), allows any origin, and names the `release` it came from; a query the parser cannot read is a `400` with the parser's messages, an empty result a `200`, an unknown path a JSON `404`. The Worker lives in the site repository beside the grammar and reads the site's `/data/query/` payloads, so an answer and the search page agree to the card. Full reference: [kairosarchive.net/docs/query](https://kairosarchive.net/docs/query). For anything heavier than a lookup, fetch `registry.json` and query locally.

## When something goes wrong

**Read the status, not the body.** The status codes below are exact and are
what a client should branch on. The bodies are the CDN's own pages: HTML for
most, a line of plain text for one, and none of them JSON. Calling `.json()`
on a failed response will throw, so check `response.ok` first.

| Status | When | What to do |
|---|---|---|
| `404` | No object at that path: an id that does not exist, a slug never issued, a typo, a path outside any release root | Check the id against `index/` or `versions.json`. Under a pinned root a 404 is permanent; under `/v3/` a later release may add the object. The body is a 27 KB HTML page, so do not read it |
| `403` | Two different cases. Either the request carried no `User-Agent` at all, or it used a method other than `GET`, `HEAD` or `OPTIONS` | Send a `User-Agent` naming your project and a contact, e.g. `my-deck-tool/1.2 (me@example.com)`, and only read. The archive is read-only: there is no write API, and no credential that would make one work |
| `429` | The per-IP rate limit tripped | Back off, then fetch `registry.json` or an `index/` file once instead of crawling object by object |
| `5xx` | The origin or the edge is having trouble | Retry with backoff. Every release is also on GitHub: `raw.githubusercontent.com/sadkinglabs/sorcery-registry/<tag>/export/registry.json`. A pinned root's bytes never change, so a copy you already hold stays valid |

A write is refused with `403`, not `405`, and carries no `Allow` header. That
is a limitation of the zone's plan rather than a statement about the request:
the edge can refuse a method but cannot currently name the ones it accepts.
They are `GET`, `HEAD` and `OPTIONS`.

A 404 carries `Access-Control-Allow-Origin: *`, so a browser can read the
status rather than seeing an opaque network failure. A refusal from the edge
(either `403` case) does not, so a cross-origin caller sees a network error
instead of a status. Browsers send a `User-Agent` automatically and cannot
write, so this affects only non-browser clients, which can read the status
directly.

`GET https://api.kairosarchive.net/` redirects to `versions.json`, which is
where a client should start anyway.

`OPTIONS` is answered with the CORS preflight a conditional cross-origin
`GET` needs, so a browser can revalidate a cached object with `If-None-Match`
and get a 304 rather than re-downloading it.

What the zone is configured to do, what its plan prevents, and what an upgrade
would change, is recorded in [`docs/error-responses.md`](error-responses.md).

## Images

Card images live under `https://api.kairosarchive.net/images/`, hosted by the registry as the publisher's guidance asks. Every printing carries `image_urls` for its front face (and `back.image_urls` for a double-faced printing), every card its default printing's, and `image_status` says what to expect:

| `image_status` | meaning |
|---|---|
| `missing` | the registry holds no image for this face; `image_urls` is null |
| `lowres` | held and served in every rendition, but the publisher's file was too small for the large rendition and was upscaled (the early sets ship at 380×531) |
| `ok` | held at full size (744×1039 sources) |

Image addresses are permanent and independent of where the registry obtained a file: the name carries the printing id and an art-version key, never the source. The publisher's folder is one intake among possible others; if their distribution changes, new files get new keys and new addresses, and every address already published keeps serving the same bytes.

Renditions, Scryfall's vocabulary and sizes: `small` 146×204, `normal` 488×680, `large` 672×936, all WebP with the aspect preserved (a rendition may be a pixel narrower than nominal), plus `original`, the publisher's file untouched in its own format. Object names are self-describing and permanent: `{printing_id}.{key}.{rendition}.{ext}`, with `.back` before the rendition for a back face (`P000937.ab12cd34ef56.normal.webp`, `P001762.9f8e7d6c5b4a.back.large.webp`). `key` is the art-version key, sha256(original bytes ‖ encoding recipe) truncated to 12 hex, published on the printing as `image_hash`: new art or a new recipe is a new key and a new address, and the bytes at an old address never change, so cache them forever. The indexes carry `image_hash` and `image_status` so a client can build any address from the scheme above without fetching the printing object. The renditions keep whatever the publisher's file has at the corners: WebP carries transparency, so transparent rounded corners survive into every size, and square corners stay square. Either way, display cards with a radius proportional to the card, `border-radius: 4.75% / 3.5%` (the same advice Scryfall gives), which lands on the physical corner at any rendition size. Images are © Erik's Curiosa, served for archive, identification and site function; hotlinking is allowed, with credit ([usage terms](usage.md)).

## What changed since the previous release

`changes.json` at every release root (and attached to the GitHub release) describes the way from the previous release to this one, so a consumer can decide whether to move before downloading anything:

```json
{
  "from": "v3.3.3", "to": "v3.4.0",
  "schema_version": {"from": 11, "to": 12},
  "summary": {"cards_added": 0, "cards_changed": 0, "cards_removed": 0,
              "printings_added": 0, "printings_changed": 1, "printings_removed": 0,
              "sets_added": 0, "images_added": 0, "images_replaced": 0,
              "history_rows_added": 0, "notes_added": 1, "notes_removed": 0,
              "manual_added": 0, "manual_confirmed": 0, "manual_withdrawn": 0,
              "identifiers_removed": 0},
  "cards": {"added": [], "changed": [], "removed": []},
  "printings": {"added": [], "changed": [{"printing_id": "P001640", "codex_id": "C000403",
                                          "fields": ["released_with"]}], "removed": []},
  "sets": {"added": []},
  "images": {"added": [], "replaced": []},
  "history": {"added": []},
  "notes": {"added": [{"id": "P001640", "text": "Prize support in the Arthurian Legends store kit, ...",
                       "source": "Community report, Sorcery Discord. ...", "recorded": "2026-09-22"}],
            "removed": []},
  "manual": {"added": [], "confirmed": [], "withdrawn": []}
}
```

`cards.changed` and `printings.changed` name the fields that differ, in the record's own key order. `images` counts printings whose front or back art was added or replaced (a replaced image is a new address; the old one keeps serving). `history.added` lists the `card_history` rows new in this release, by card and start date. `notes.added` and `notes.removed` list whole notes with the `id` of the card or printing they sit on; a note is never counted as a change to its record, and rewording one reads as one removed and one added. A release that adds a field changes the shape, which `schema_version` reports, not every record that carries the field. A new field counts as a change only on records where it says something beyond its default: a promo's `released_with` recorded by hand, a record whose `origin` is `manual`. `manual.added` lists the cards and printings recorded by hand in this release, which are also counted as added. `manual.confirmed` lists manual records that upstream now serves and a person confirmed, and `manual.withdrawn` lists manual records marked as wrong. Documents from before schema 12 have no `notes_*` or `manual_*` keys; read them as 0. `identifiers_removed` is the sum of removed cards and printings and is always `0` within a major: the release workflow refuses to publish otherwise, so the promise that ids are permanent is checked at release time rather than merely stated. A new major may remove ids, and its `changes.json` says exactly which. The first release to carry the file is the one after v3.3.1; earlier roots have none.

## Indexes, for client-side search

The registry has ~1,100 cards; filtering them in the client is a millisecond. These are the compact lists to do it with:

- `index/cards.json` - `codex_id`, `name`, `type`, `category`, `rarity`, `elements`, `keywords`, `subtypes`, `cost`, `attack`, `defense`, `power`, `life`, `errata`, `set_codes`, `default_printing_id`, `image_status`, `origin` per card (~330 KB).
- `index/printings.json` - `printing_id`, `codex_id`, `slug`, `set_code`, `released_with`, `product`, `finish`, `printed_as_current`, `retired_at`, `image_hash`, `image_status`, `origin` per printing (~690 KB).
- `index/slugs.json` - `{slug: printing_id}` for every slug ever (~100 KB, 3,088 entries).

## History, whole

`history/slugs.json`, `history/names.json`, `history/cards.json` - the export's three history sections, for consumers that want them all at once. `history/cards.json` is every state every card's gameplay face has been in; a printing released within a row's dates was printed with that row's values. Each row says where it came from: `source` is `api` when the registry observed the face in the official API, and `card` when the face was recorded by hand from what is printed on the card - the case of a card the publisher changed after printing, whose earlier face the API never served. Such a row runs from the day the first printing carrying it reached the public until the current face took over, so those printings show `printed_as_current: false` and a later reprint with the corrected text shows `true`; a printing that shows no face at all (a textless promo) reports `null`.

## Notes

The official API describes a card and its printings and nothing else. People who play the game know more, such as that a promo was prize support in a particular store kit. The registry keeps that knowledge as notes, and every note says where it came from and when it was written down.

Every card and printing carries `notes`, a list that is empty for most records. Each note is `{text, source, recorded}`: the fact in plain words, where it came from, and the date the registry recorded it. That date is not when the fact became true. A note never contradicts a field. A fact that fits a field, such as an artist or a date, is a correction and changes the field instead, with its reason in the repo. Notes appear in the export, in `cards/{codex_id}.json` and `printings/{printing_id}.json`; the indexes leave them out.

A source names a place, not a person: "Community report, Sorcery Discord", "Arthurian Legends store kit insert, photographed". Releases are immutable, so a name printed in one stays in it for good; a person is credited only if they ask to be. Notes are reports, not official records. Quote the source with the fact.

## Manual records

The official API does not serve everything that was printed. Store-kit prize cards, Kickstarter pledge cards and curios exist only on tables and in binders. The registry records them by hand, with the same fields as every other record and ids from the same counters, so they can be stored, searched and linked like anything else.

Every card, printing and set says who stands behind it:

- `origin` is `api` when the official API serves the record, and `manual` when the registry recorded it by hand.
- `manual` is null for a record the registry only ever observed upstream. For a hand-recorded one it is `{source, recorded, confirmed_at, withdrawn}`: where the record came from, the day it was written down, the day upstream was confirmed to serve it, and whether it was withdrawn as wrong, with `{on, reason}`.
- A set's `origin` is `manual` when every printing in it is, as for a set code of the registry's own.

**Confirmation.** When the official API starts serving a manual record, a person confirms the match. The record keeps its id, takes upstream's name, slug and values where they differ, and becomes `api`, with `manual.confirmed_at` set. `changes.json` lists it under `manual.confirmed`. Nothing a consumer stored breaks: the id is the same.

**Withdrawal.** Ids are permanent, so a manual record found to be wrong stays, with `manual.withdrawn` set. A withdrawn printing is never a card's `default_printing_id`.

**Slugs of manual printings are predictions.** A manual printing carries a slug predicted in the publisher's own shape, such as `999-the_champion-op-f`. It has no row in `slug_history`. It resolves at `slugs/{slug}.json` while it is the printing's slug, with `valid_from` null. On confirmation it is replaced by upstream's slug, and if the two differ, the prediction stops resolving from the next release. As always, a slug is a lookup, never a key.

**Set codes and kinds.** A set code is a label, never a number: `6` is not `006`, and nothing about a set is read from its code. What a set is, is its `kind`, recorded by the registry: `release` for a set release, `promo` for the publisher's bucket for promos (999), and `registry` for a set of the registry's own. The publisher's codes are three digits. The registry's own codes are three capital letters, such as `CUR` for the curios, so the two can never collide. A registry set is served like any other, at `sets/CUR.json`. A set that appears upstream stops the next release until it is classified.

**`released_with`.** Every printing carries the set release it belongs to. For a printing in a set of kind `release` it is that set. For a promo, which upstream files under set 999 (kind `promo`), or a curio (kind `registry`), it is the release recorded by hand, and null until recorded.

Manual records are the registry's own reports, like notes. Say a record is manual when you cite it.

## Guarantees carried over

Everything the README guarantees about the export holds here, because the objects are derived from it and from nothing else: ids never change or vanish, a slug never refers to a different printing (the publisher refuses to run on an export where one does), and the served `registry.json` matches its published checksum.
