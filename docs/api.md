# The URL contract

New here? [`docs/quickstart.md`](quickstart.md) is a shorter, example-driven walkthrough of the ids, discovery flow and images; this page is the complete reference.

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

`index.json` - what exists: `schema_version`, `dataset_version`, record counts, the `endpoints` map below with `{placeholder}` templates, and where things live: `base_url` (the absolute root these objects were uploaded to, an immutable release root) and `latest_url` (the moving major alias that always redirects to the newest release); both null in a local `dist/`. `manifest` names `manifest.json` when the release manifest was copied into the root; `terms` links the usage terms ([`docs/usage.md`](usage.md): what is free to use, what belongs to Erik's Curiosa, what we ask of clients). Fetch this first.

## Records carry their own addresses

Every card, printing and set record - in the export and in the per-object files - carries `api_url` and `kairos_url`, and cards and printings carry `image_urls` and `image_status`; slug objects carry the resolved printing's `api_url` and `kairos_url`. `api_url` is the **current-record URL**: it points at the moving major alias, so a consumer reading a historical release root and following `api_url` leaves that snapshot by design and lands on the current record; a consumer that wants the snapshot uses the paths of the root it fetched. The addresses are derived from the ids at export time and CI proves each one names the record it sits on. The indexes leave them out to stay small: both are a template away from the id each index record already carries (`{base}/v3/cards/{codex_id}.json` and `https://kairosarchive.net/cards/{codex_id}`, and the same shapes with `printings`/`{printing_id}`), and carrying them would add 38% to the card index and 56% to the printing index.

## The export itself

- `registry.json` - the full export, byte for byte the committed file, so its checksum holds for the served copy (~6.4 MB, ~380 KB over the wire: the edge serves it Brotli-compressed to any client that asks).
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

## When something goes wrong

Every failure answers with the same small JSON envelope, so a client can tell
a missing card from a blocked request without parsing prose:

```json
{"error": "not_found",
 "status": 404,
 "message": "No object at this path.",
 "docs": "https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/api.md",
 "discovery": "https://api.kairosarchive.net/versions.json"}
```

| Status | `error` | When | What to do |
|---|---|---|---|
| `404` | `not_found` | No object at that path: an id that does not exist, a slug never issued, a typo, a path outside any release root | Check the id against `index/` or `versions.json`. Under a pinned root a 404 is permanent; under `/v3/` a later release may add the object |
| `403` | `no_user_agent` | The request carried no `User-Agent` header at all | Send one naming your project and a contact, e.g. `my-deck-tool/1.2 (me@example.com)`. This identifies clients; it is not a security control |
| `405` | `method_not_allowed` | Anything but `GET`, `HEAD` or `OPTIONS` | The archive is read-only. The `Allow` header lists what is accepted |
| `429` | `rate_limited` | The per-IP rate limit tripped | Back off for the `Retry-After` seconds, then fetch `registry.json` or an `index/` file once instead of crawling object by object |
| `5xx` | `unavailable` | The origin or the edge is having trouble | Retry with backoff. Every release is also on GitHub: `raw.githubusercontent.com/sadkinglabs/sorcery-registry/<tag>/export/registry.json`. A pinned root's bytes never change, so a copy you already hold stays valid |

Error responses carry `Access-Control-Allow-Origin: *` like every other
response, so a browser can read the status rather than seeing an opaque
network failure. `GET https://api.kairosarchive.net/` redirects to
`versions.json`, which is where a client should start anyway.

`OPTIONS` is answered with the CORS preflight a conditional cross-origin
`GET` needs, so a browser can revalidate a cached object with `If-None-Match`
and get a 304 rather than re-downloading it.

The zone-side configuration behind this section is written down in
[`docs/error-responses.md`](error-responses.md), with the exact rules and bodies.

## Images

Card images live under `https://api.kairosarchive.net/images/`, hosted by the registry as the publisher's guidance asks. Every printing carries `image_urls` for its front face (and `back.image_urls` for a double-faced printing), every card its default printing's, and `image_status` says what to expect:

| `image_status` | meaning |
|---|---|
| `missing` | the registry holds no image for this face; `image_urls` is null |
| `lowres` | held and served in every rendition, but the publisher's file was too small for the large rendition and was upscaled (the early sets ship at 380×531) |
| `ok` | held at full size (744×1039 sources) |

Image addresses are permanent and independent of where the registry obtained a file: the name carries the printing id and an art-version key, never the source. The publisher's folder is one intake among possible others; if their distribution changes, new files get new keys and new addresses, and every address already published keeps serving the same bytes.

Renditions, Scryfall's vocabulary and sizes: `small` 146×204, `normal` 488×680, `large` 672×936, all WebP with the aspect preserved (a rendition may be a pixel narrower than nominal), plus `original`, the publisher's file untouched in its own format. Object names are self-describing and permanent: `{printing_id}.{key}.{rendition}.{ext}`, with `.back` before the rendition for a back face (`P000937.ab12cd34ef56.normal.webp`, `P001762.9f8e7d6c5b4a.back.large.webp`). `key` is the art-version key, sha256(original bytes ‖ encoding recipe) truncated to 12 hex, published on the printing as `image_hash`: new art or a new recipe is a new key and a new address, and the bytes at an old address never change, so cache them forever. The indexes carry `image_hash` and `image_status` so a client can build any address from the scheme above without fetching the printing object. The renditions keep whatever the publisher's file has at the corners: WebP carries transparency, so transparent rounded corners survive into every size, and square corners stay square. Either way, display cards with a radius proportional to the card, `border-radius: 4.75% / 3.5%` (the same advice Scryfall gives), which lands on the physical corner at any rendition size. Images are © Erik's Curiosa, served for archive, identification and site function; hotlinking is allowed, with credit ([usage terms](usage.md)).

## Indexes, for client-side search

The registry has ~1,100 cards; filtering them in the client is a millisecond. These are the compact lists to do it with:

- `index/cards.json` - `codex_id`, `name`, `type`, `category`, `rarity`, `elements`, `keywords`, `subtypes`, `cost`, `attack`, `defense`, `power`, `life`, `errata`, `set_codes`, `default_printing_id`, `image_status` per card (~330 KB).
- `index/printings.json` - `printing_id`, `codex_id`, `slug`, `set_code`, `product`, `finish`, `printed_as_current`, `retired_at`, `image_hash`, `image_status` per printing (~690 KB).
- `index/slugs.json` - `{slug: printing_id}` for every slug ever (~100 KB, 3,088 entries).

## History, whole

`history/slugs.json`, `history/names.json`, `history/cards.json` - the export's three history sections, for consumers that want them all at once. `history/cards.json` is every state every card's gameplay face has been in; a printing released within a row's dates was printed with that row's values. Each row says where it came from: `source` is `api` when the registry observed the face in the official API, and `card` when the face was recorded by hand from what is printed on the card - the case of a card the publisher changed after printing, whose earlier face the API never served. Such a row runs from the day the first printing carrying it reached the public until the current face took over, so those printings show `printed_as_current: false` and a later reprint with the corrected text shows `true`; a printing that shows no face at all (a textless promo) reports `null`.

## Guarantees carried over

Everything the README guarantees about the export holds here, because the objects are derived from it and from nothing else: ids never change or vanish, a slug never refers to a different printing (the publisher refuses to run on an export where one does), and the served `registry.json` matches its published checksum.
