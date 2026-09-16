# Sorcery Card Registry

Stable identifiers for every card and printing in **Sorcery: Contested Realm** - `C000042` for cards, `P000042` for printings - published as a single JSON file anyone can build on.

## The problem

The identifier most tools work from is the official API slug, e.g. `004-witch-b-s`. That slug is a composite key: set code, card name, product and finish all packed into one string, in a format that was never publicly specified. Worse, the slug is derived from data that shifts - when the sets were renumbered, every slug changed with it, and every database keyed on those slugs broke at once. (And note what the slug does *not* contain: a collector number. Cards have no official serialisation within a set at all.)

The only durable handle the official data gives a card is its name, so every tool that wants to link two printings of the same card ends up name-matching, which breaks at the first inconsistency.

The official API was rebuilt in 2026 and now serves an `id` on every card and printing. Those are the backend's own minting ids: its developers confirm they are regenerated whenever the data is re-imported, so they are not stable either. The registry does not store them.

## The fix

This registry follows the pattern that already works elsewhere - Konami's passcodes for Yu-Gi-Oh, Scryfall's oracle IDs for Magic. Two identifiers, both permanent, both in a fixed shape: a one-letter prefix naming the ID space, then six zero-padded digits.

- **`codex_id`** (`C000001`) - one per card, shared by every printing of it. Alpha, Beta and promo Apprentice Wizard all carry the same `codex_id`. Use it when you mean "this card as a game object": decklists, rulings, collection grouping.
- **`printing_id`** (`P000001`) - one per physical print (a specific set, product and finish). Use it when you mean "this exact piece of cardboard": inventories, pricing, scans.

The prefix means a card ID can never be confused with a printing ID, and the fixed width keeps IDs intact in spreadsheets (no stripped leading zeros) and aligned in diffs. Treat the whole string as opaque; if you need a plain number, the digits after the prefix are one (`int("C000042"[1:]) == 42`). Six digits leaves room for a million of each, which will never run out.

## Which ID am I after?

Ask one question: **does my feature care which physical version of the card it is?**

If yes - the copy matters - you want the **printing**. If any copy of the card would do - the game object matters - you want the **card**.

| You're building… | Key on | Why |
|---|---|---|
| A collection tracker or inventory | `printing_id` | People own specific printings: an Alpha Foil is not a Beta Standard. |
| Pricing, trades, scans, condition tracking | `printing_id` | Value and identity are per physical print. |
| A decklist format | `codex_id` | A deck runs "4x Apprentice Wizard"; any printing fills the slot. |
| Deck buildability ("can I build this from my collection?") | Both | The deck wants `codex_id`s; you own `printing_id`s; every printing carries its `codex_id`, so the join is one lookup. |
| Rulings, errata, card search, game databases | `codex_id` | Rules apply to the card, all printings at once. |
| "Show all versions of this card" | `codex_id` → `printing_ids` | Each card record lists its printings. |

If you know Yu-Gi-Oh or Magic tooling, this is the same two-level split you already use. Konami's printed passcode is a *card*-level ID (every reprint of a card shares it), while the printing level in Yu-Gi-Oh is the set number (`LOB-001`) - a composite of set code and collector number, which is precisely the kind of derived key that breaks when naming changes. Scryfall gives Magic stable IDs at both levels (`oracle_id` for the card, a per-printing id for the print). This registry does what Scryfall did: both levels, both stable, so nobody has to reconstruct either one by string-matching.

Set code, set name, product and finish sit in ordinary columns *next to* the IDs, not inside them. When the official naming convention changes again, the slug column updates, a row is added to `slug_history`, and **the IDs do not move**. Nothing downstream needs remapping, ever.

Five guarantees, enforced by CI on every commit, not by promise:

1. An ID never changes value and never disappears.
2. A retired ID is never reused. Assignment is append only.
3. A slug change never causes a new ID. Old slugs stay resolvable through `slug_history`.
4. A card that vanishes upstream while new names appear is never automatically issued a new ID. Unmatched disappearances and newcomers stop the sync for human review.
5. Once a slug has referred to a printing, it can never refer to a different one. The database engine itself rejects such a write, the sync quarantines it, and the resolver refuses to guess if it ever meets corrupt data.

## Tokens and play pieces

The registry covers every game piece the official API publishes - which already includes most tokens, since they exist as physical printings (the box-topper tokens, for instance). Game pieces that have never appeared in the official data - app-local token definitions - are deliberately out of scope: the registry never invents records.

If you need local play pieces, mint IDs in your own namespace and **do not use the `C` or `P` prefixes**, so a local ID can never collide with a registry ID. If such a piece later appears officially, resolve it by name and attributes once, then store the registry ID alongside your local one.

## What this is not

- Not a live service. The data is files - one export, or one object per thing on `api.kairosarchive.net` - computed at release time; there is no query endpoint (run the bundled MCP server locally for questions, see below).
- No prices, no rulings, no legality data. Card images are served from `api.kairosarchive.net/images/` as the publisher's API guidance asks ("host images yourself"); every printing and card carries `image_urls` and `image_status` (see the practical notes).
- Not a second opinion on card data. Attributes mirror the official API, with a short, public list of corrections for confirmed upstream errors (see [`data/overrides.json`](data/overrides.json)).

## Using the data

Everything you need is one file: [`export/registry.json`](export/registry.json). Grab it, vendor it, or read it straight from the repo. It is deterministically ordered, so diffing two versions shows you exactly what changed and nothing else.

### Hosted

The same data is served, one object per thing, from **`https://api.kairosarchive.net`**. The web documentation at **[kairosarchive.net/docs](https://kairosarchive.net/docs)** is the place to start: the rules of the road, a page per record type generated from the schema, and a changelog of releases. In this repository, [`docs/api.md`](docs/api.md) is the complete URL contract and [`docs/quickstart.md`](docs/quickstart.md) walks through the ids, discovery and images with working examples. The short version:

- **Pin a release** and it never changes: `https://api.kairosarchive.net/v3.1.0/registry.json` (and `cards/C000230.json`, `printings/P000937.json`, `slugs/{slug}.json`, `sets/006.json`, the indexes) - immutable, cache forever.
- **Follow the major alias** for the newest data of a shape you understand: `https://api.kairosarchive.net/v3/…` redirects to the newest verified v3.x root. A breaking change is a new major alias; nothing breaks in place.
- **Poll `https://api.kairosarchive.net/versions.json`** (60 s cache; hourly is plenty) to learn when a release exists and the digest of its `registry.json`; a release is listed only after the CDN has been verified serving it.
- Every release is also mirrored here as a tagged GitHub release, so `raw.githubusercontent.com/sadkinglabs/sorcery-registry/<tag>/export/registry.json` is the same bytes.

No keys, no signup. Two courtesies are required of automated clients: send a `User-Agent` that names your project (requests without one are blocked at the edge), and fetch `registry.json` or the indexes rather than crawling objects one by one; a generous per-address rate limit is the only other tripwire. Details in [`docs/usage.md`](docs/usage.md).

```jsonc
{
  "header":        { "schema_version": 10, "source": "...", "sets": 6, "cards": 1100, ... },
  "sets":          [ { "set_code": "001", "set_name": "Alpha", "released_at": "2023-06-22",
                       "cards": 407, "printings": 817,
                       "api_url": "https://api.kairosarchive.net/v3/sets/001.json",
                       "kairos_url": "https://kairosarchive.net/sets/001" }, ... ],
  "cards":         [ { "codex_id": "C000001", "name": "Apprentice Wizard",
                       "type": "Minion", "category": "Spell", "rarity": "Ordinary", "slot": "Ordinary",
                       "subtypes": ["Mortal"], "elements": ["Air"], "keywords": ["Genesis", "Spellcaster"],
                       "umbrellas": [], "cost": 3, "attack": 1, "defense": 1, "power": 1, "life": null,
                       "thr_air": 1, "thr_earth": 0, "thr_fire": 0, "thr_water": 0,
                       "rules_text": "Spellcaster\nGenesis → Draw a spell.", "back": null, "errata": false,
                       "set_codes": ["001", "002", "999"],
                       "printing_ids": ["P000001", "P000002", "P000003", "P000004", "P000005", "P000006"],
                       "default_printing_id": "P000002",
                       "api_url": "https://api.kairosarchive.net/v3/cards/C000001.json",
                       "kairos_url": "https://kairosarchive.net/cards/C000001",
                       "image_urls": null, "image_status": "missing" } ],
  "printings":     [ { "printing_id": "P000001", "codex_id": "C000001", "card_name": "Apprentice Wizard",
                       "set_name": "Alpha", "set_code": "001", "released_at": "2023-06-22",
                       "product": "Booster", "finish": "Standard", "slug": "001-apprentice_wizard-b-s",
                       "artist": "Ossi Hiekkala", "artist_slug": "ossi_hiekkala", "flavour_text": "",
                       "typeline": "An Ordinary Mortal new to power", "back": null,
                       "printed_as_current": true, "retired_at": null,
                       "api_url": "https://api.kairosarchive.net/v3/printings/P000001.json",
                       "kairos_url": "https://kairosarchive.net/printings/P000001",
                       "image_urls": null, "image_status": "missing" } ],
  "slug_history":  [ { "slug": "...", "printing_id": "P000001", "valid_from": "2026-08-19", "valid_to": null } ],
  "name_history":  [ { "name": "...", "codex_id": "C000001", "valid_from": "2026-08-19", "valid_to": null } ],
  "card_history":  [ { "codex_id": "C000001", "valid_from": "2026-09-09", "valid_to": null,
                       "type": "Minion", ..., "cost": 3, "attack": 1, ..., "rules_text": "...", "back": null } ]
}
```

The file's exact shape is formally described by [`schema/registry.schema.json`](schema/registry.schema.json) (JSON Schema, draft 2020-12) - CI validates every commit's export against it. Built on a v1.x export? [`docs/migrating-to-v2.md`](docs/migrating-to-v2.md) maps every field that changed in v2.0. A [`registry.json.sha256`](export/registry.json.sha256) checksum sits next to the export for integrity checks and cheap freshness polling (fetch the ~80 byte checksum; re-download only when it changed).

Practical notes:

- **Key on the IDs, treat everything else as data.** `slug`, `set_code`, `set_name`, `card_name` are conveniences that can change; `codex_id` and `printing_id` cannot. Sets are identified by their two official facts: `set_code` (001 = Alpha, 002 = Beta, 006 = Gothic; 003 is deliberately unused, so the codes are labels, not an order) and `set_name`, the official display name. Both are published exactly as upstream states them - the registry invents no codes of its own.
- **Gameplay data lives on the card; physical facts live on the printing.** This is how the official API is organised, and the registry mirrors it. `rules_text`, stats, thresholds, `keywords` and the rest describe the card and apply to every printing of it; a printing carries set, product, finish, slug, artist, typeline and flavour text. There is no such thing as "the text printed in Alpha": a card plays by its current text wherever it was printed.
- **`power` is derived, on every face.** Sorcery's power equals attack when attack and defense are equal, otherwise ⌊(attack + defense) / 2⌋ (null when either is null). The registry publishes it on cards, back faces and every `card_history` row so every consumer computes the same number and none has to know the rule.
- **Lists are lists.** `subtypes`, `elements`, `keywords` and `umbrellas` are arrays, in upstream's own order. `["None"]` in `elements` means colourless. `slot` is the rarity slot a card is distributed in and agrees with `rarity` except where nothing is printed on the card (Avatars, some tokens). `umbrellas` are the cross-subtype groups rules text refers to (Evil, Knight, Royalty).
- **Double-faced cards have a `back`.** For the two physically double-faced cards (Druid, Foot Soldier) the card's `back` carries the full gameplay data of the reverse face, and each printing's `back` its artist and typeline. Everything else is a front; `back` is `null` for every other card.
- **Printings are readable on their own.** Each printing carries `card_name`, derived at export time from the card its `codex_id` points at, so a printing record never needs a join just to be understood. It's a convenience copy: the card record stays the source of truth for card-level data.
- **Each card lists its printings.** `printing_ids` on a card is the reverse of each printing's `codex_id` - derived at export time from the printings table, so the two can never disagree, and CI proves it. The list is sorted and only ever grows.
- **The `sets` section is the set catalogue.** One record per set with its official code, name, release date, and distinct-card and printing counts - the authoritative answer to "how many cards are in set X", which the official data states nowhere. A set's `released_at` is the earliest date any of its printings reached the public; each printing carries its own. Each card also lists its `set_codes`; for products and finishes, follow its `printing_ids`.
- **`card_history` and `errata` are the registry's own record of updated cards.** Upstream publishes only the current values and marks nothing (the old API prefixed updated text with `UPDATED:`; the rebuilt one does not). So the registry records what it observes: `card_history` holds every state a card's gameplay face has been in - type, elements, cost, attack, defense, life, thresholds, rules text, keywords, back face - one row per state with `valid_from`/`valid_to`. A reprint that changes a card's cost or power is recorded exactly like a rewording: the old face closes, the new face opens. Every card has exactly one open row, which equals the card record. `errata` is `true` once any *gameplay* field has changed since the card was printed (a re-tag of keywords, subtypes, rarity or slot is history but not errata) - seeded from the old marker, set by observed changes, corrected only through [`data/overrides.json`](data/overrides.json). Each row says where its face came from: `source` is `api` for a face the registry observed upstream, and `card` for one transcribed from the printed card - the 28 cards the publisher changed after printing, whose earlier text the API never served. Those transcriptions live in [`data/errata.json`](data/errata.json) with the printing each was read from, and CI checks they agree with the history and the release dates.
- **Which printings show the current values?** Each printing carries `printed_as_current`: `true` when it was released on or after the current face took effect - or entered the registry with it, as a reprint carrying a change does - and `false` for a printing that physically shows older values (`true` for everything while a card's face has never changed). It is `null` when no answer is possible: a printing with no release date, or one that shows no rules text at all, as the textless promos do. Each card carries `default_printing_id`, a representative printing chosen by a fixed rule - not retired, showing the card's current face over older values, Booster over other products, Standard over other finishes, most recent release, lowest id - so every consumer picks the same one; a reprint that changed a card's stats becomes its default even if it is a promo, and otherwise a promo never outranks a Booster printing. Want a different policy? The full `printings` list is there; ignore the field. Think the rule picked wrong for one card? Pin another of its printings through [`data/overrides.json`](data/overrides.json) with a reason, and the pin wins.
- **Every record says where it lives.** Cards, printings and sets carry `api_url` (the record's own JSON object on `api.kairosarchive.net`) and `kairos_url` (its page on `kairosarchive.net`), derived from the id at export time so they can never name a different record - CI checks that too. `api_url` points at the moving major alias (`/v3/`), which always redirects to the newest verified v3.x release: follow it from any copy, however old, and you reach the current record. A consumer that wants the snapshot it fetched uses that release root's paths instead. Printings (and cards, through their default printing) also carry `image_urls` - the four renditions `small` (146×204), `normal` (488×680), `large` (672×936) as WebP, and `original`, the publisher's file untouched - and `image_status`: `missing` while the registry holds no image, `lowres` when the publisher's file (380×531 for the early sets) was upscaled to fill the large rendition, `ok` at full size. Image addresses are permanent: the name carries the printing id and an art-version key (`P000937.ab12cd34ef56.normal.webp`), and new art or a new encoding gets a new name rather than new bytes at the old one. What the registry holds is recorded in [`data/images.json`](data/images.json), with the publisher's file each image came from. The committed `registry.json` therefore contains `kairosarchive.net` addresses, and the GitHub mirror points at the domain by design.
- **Migrating existing data keyed on slugs:** look each slug up in `slug_history`, which maps every slug that has ever existed (current and superseded) to its `printing_id`. Do it once and the next naming convention change costs you nothing.
- **Retired printings** (removed upstream) keep their rows and IDs, marked with a `retired_at` date, so old references never dangle. Cards are never removed at all.
- **Text is canonicalised**: `\n` line endings, no trailing whitespace, one line per ability. The official API is inconsistent about all three; the registry is not.
- **Flavour text is kept.** Since the API rebuild upstream serves `flavor` as null for every printing while the cards themselves still carry flavour text. The registry keeps the values it holds and treats an upstream null as "no information", never as an erasure.
- The committed SQLite database (`registry.sqlite`) is the **pipeline's internal working database**, not a second published artifact: it uses bare integer ids and internal column names, and lacks the derived fields. The JSON export is the only published contract; CI guarantees the JSON is exactly what the database produces.

## For AI agents (MCP)

The registry ships an [MCP](https://modelcontextprotocol.io) server, so AI assistants (Claude, Cursor, and anything else that speaks MCP) can query it directly instead of guessing at slugs or parsing them with string logic. It runs locally on your machine and reads the **newest verified release**: `api.kairosarchive.net/versions.json` names it and the digest of its `registry.json`, which the server fetches from that release's immutable root (falling back to the same release on GitHub, or to GitHub's newest release when the domain is unreachable - always a tagged release, never `main`). Outside a checkout the export is cached in `~/.cache/sorcery-registry` for 24 hours, then revalidated against the published digest with one small request and only re-downloaded when it changed; downloaded bytes are verified against the digest before they are cached, and when offline the stale cache is served rather than failing.

With [uv](https://docs.astral.sh/uv/) installed, add this to your MCP configuration (for Claude Desktop: `claude_desktop_config.json`; for Claude Code: `.mcp.json`) and you're done - no clone, no install:

```json
{
  "mcpServers": {
    "sorcery-registry": {
      "command": "uv",
      "args": ["run", "https://raw.githubusercontent.com/sadkinglabs/sorcery-registry/main/mcp_server.py"]
    }
  }
}
```

Without uv: clone the repo, `pip install -r requirements.txt`, and use `python mcp_server.py` as the command instead.

Seven tools, each returning a small, focused answer rather than the whole database:

| Tool | What it answers |
|---|---|
| `resolve_slug` | Any slug - current **or from an older naming convention** - to its permanent `printing_id` and `codex_id`. This is how a tool holding pre-rename slugs migrates itself. |
| `get_card` | One card by `codex_id`, with its gameplay data and every printing of it. |
| `get_printing` | One physical print by `printing_id`, with its set, product, finish and current slug. |
| `search_cards` | Cards by name, type, category, element, rarity, keyword, set, or errata status. |
| `search_printings` | Physical printings by card name, set, product line (Booster, BoxTopper, Dust, ...) or finish - "what's in the Arthurian Legends box topper" is one call. |
| `set_contents` | Every distinct card in a set - the authoritative answer to "how many cards are in set X", which the official data states nowhere. |
| `registry_stats` | Totals, per-set counts, and the product lines with printing counts. |

The server also teaches connected agents the ground rules (key on the IDs, never on slugs or upstream's own ids; gameplay data is on the card; only Avatars have life), so tools built with AI assistance inherit correct usage by default.

## Data corrections

The official API occasionally ships errors (at the time of writing, 17 Gothic cards carry a `life` value only Avatars should have). Corrections live in [`data/overrides.json`](data/overrides.json), each with a written reason. They are applied on top of the API data during sync, so the registry holds the corrected values while the correction itself stays visible and reviewable in git. Spotted an error? Open an issue or a PR against that file - see [CONTRIBUTING.md](CONTRIBUTING.md).

**A correction is read as a change the card underwent.** That is right when the card really did change and upstream has it wrong: a gameplay field that differs from the printed card opens a new `card_history` row, sets `errata`, and every printing released earlier reports `printed_as_current: false`, because those printings do show older values. It is wrong when only the registry's record was ever mistaken - a classification we took from upstream and later corrected - since that card never changed, and dating its printings would be a claim about the printed cards. An entry may say `"retroactive": true` for that case: the correction is written into every history row, none opens, no `errata` is set and no printing is dated by it. It may only name card fields, a printing keeping no history of its own. No entry uses it at the time of writing; it exists because the first correction to reach a record after import would otherwise have made the registry say something false about five printed cards.

## How updates happen

A sync script fetches the official API, diffs it against the registry, and classifies every difference. New cards get new IDs. Attribute changes update in place (a change to any gameplay field of a card also lands in `card_history`). Slug renames are matched conservatively (name, rules text, set, product, finish) - and anything that does not resolve to an unambiguous one-to-one match is quarantined for human review instead of guessed at, because a wrong guess would silently fork one card into two IDs. Every fetch over the network is snapshotted locally before anything is diffed, so the dry run that shows the plan and the run that applies it can be guaranteed to have seen identical data. Syncs are run manually (or via the manually-triggered GitHub Action) and land as pull requests, never as direct pushes.

A weekly workflow dry-runs the sync against the live API and files an `upstream-drift` issue the moment the official data stops matching the registry - new cards, changed attributes, ambiguous renames, or a payload the adapter can no longer read. It applies nothing; it exists so that an upstream change is a notification, not a surprise. Every payload is first checked against the publisher's own published response contract ([`schema/upstream-cards.dto.ts`](schema/upstream-cards.dto.ts), machine-read as [`schema/upstream-cards.schema.json`](schema/upstream-cards.schema.json)): a field the adapter depends on going missing or changing type stops the sync at the offending path, while new fields and new vocabulary pass through as data.

The publisher asks API consumers to poll intermittently and host the data themselves rather than treat their API as a live backend, and rate-limits it at 30 requests a minute. The registry is that guidance in practice: one request per sync, one per weekly check, every request identified by its `User-Agent`, and nothing that loops over upstream.

See [CONTRIBUTING.md](CONTRIBUTING.md) for running the pipeline yourself and for how ambiguous cases are resolved.

## Licence

The code in this repository is MIT licensed; the identifiers, structure and derived data the registry adds are CC0 - free for any use, including commercial, attribution requested. The card data itself belongs to Erik's Curiosa; this project republishes what the official public API already serves, restructured for stability, and hosts the card images as that API's guidance asks, with credit. [`docs/usage.md`](docs/usage.md) spells out what is whose and what we ask of automated clients (fetch in bulk, cache, poll `versions.json`, identify yourself).
