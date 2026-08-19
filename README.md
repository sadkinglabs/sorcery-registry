# Sorcery Card Registry

Stable identifiers for every card and printing in **Sorcery: Contested Realm** - `C000042` for cards, `P000042` for printings - published as a single JSON file anyone can build on.

## The problem

The identifier most tools work from is the official API slug, e.g. `004-witch-b-s`. That slug is a composite key: set number, card name, product and finish all packed into one string, in a format that was never publicly specified. Worse, the slug is derived from data that shifts - when the sets were renumbered, every slug changed with it, and every database keyed on those slugs broke at once. (And note what the slug does *not* contain: a collector number. Cards have no official serialisation within a set at all.)

There is also nothing in the official data linking two printings of the same card to each other except the card's name, so every tool reconstructs that link by name-matching, which breaks at the first inconsistency.

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

Set, set number, product and finish sit in ordinary columns *next to* the IDs, not inside them. When the official naming convention changes again, the slug column updates, a row is added to `slug_history`, and **the IDs do not move**. Nothing downstream needs remapping, ever.

Three guarantees, enforced by CI on every commit, not by promise:

1. An ID never changes value and never disappears.
2. A retired ID is never reused. Assignment is append only.
3. A slug change never causes a new ID. Old slugs stay resolvable through `slug_history`.

## What this is not

- Not a hosted service. There is no server and no endpoint; you consume a file (or run the bundled MCP server locally - see below).
- No images, no prices, no rulings, no legality data.
- Not a second opinion on card data. Attributes mirror the official API, with a short, public list of corrections for confirmed upstream errors (see [`data/overrides.json`](data/overrides.json)).

## Using the data

Everything you need is one file: [`export/registry.json`](export/registry.json). Grab it, vendor it, or read it straight from the repo. It is deterministically ordered, so diffing two versions shows you exactly what changed and nothing else.

```jsonc
{
  "header":       { "schema_version": 1, "source": "...", "cards": 1100, ... },
  "cards":        [ { "codex_id": "C000001", "name": "Apprentice Wizard", "type": "Minion", ...,
                      "errata": false,
                      "printing_ids": ["P000001", "P000002", "P000003", "P000004", "P000005", "P000006"] } ],
  "printings":    [ { "printing_id": "P000001", "codex_id": "C000001", "card_name": "Apprentice Wizard",
                      "slug": "001-apprentice_wizard-b-s", "set_name": "Alpha", "set_number": "001",
                      "product": "Booster", "finish": "Standard", ... } ],
  "slug_history": [ { "slug": "...", "printing_id": "P000001", "valid_from": "2026-08-19", "valid_to": null } ]
}
```

Practical notes:

- **Key on the IDs, treat everything else as data.** `slug`, `set_number`, `set_name`, `card_name` are conveniences that can change; `codex_id` and `printing_id` cannot. Sets are identified by their two official facts: `set_number`, the numbering parsed from the slug (001 = Alpha, 002 = Beta, 006 = Gothic), and `set_name`, the official display name. Both are published exactly as upstream states them - the registry invents no codes of its own.
- **Printings are readable on their own.** Each printing carries `card_name`, derived at export time from the card its `codex_id` points at, so a printing record never needs a join just to be understood. It's a convenience copy: the card record stays the source of truth for card-level data.
- **Each card lists its printings.** `printing_ids` on a card is the reverse of each printing's `codex_id` - derived at export time from the printings table, so the two can never disagree, and CI proves it. The list is sorted and only ever grows.
- **`errata` marks officially updated cards.** The upstream convention is that an errata'd card's rules text begins with `UPDATED`; the registry publishes that as a boolean so you don't have to know the convention. The flag is derived from the rules text at export time - if upstream changes how it marks errata, the flag follows the data. The rules text itself is always published verbatim (canonicalised, never rewritten).
- **Migrating existing data keyed on slugs:** look each slug up in `slug_history`, which maps every slug that has ever existed (current and superseded) to its `printing_id`. Do it once and the next naming convention change costs you nothing.
- **Retired printings** (removed upstream) keep their rows and IDs, marked with a `retired_at` date, so old references never dangle. Cards are never removed at all.
- **Text is canonicalised**: `\n` line endings, no trailing whitespace, one line per ability. The official API is inconsistent about all three; the registry is not.
- The SQLite database (`registry.sqlite`) is also committed, for anyone who prefers SQL. It and the JSON always agree - CI fails if they drift.

## For AI agents (MCP)

The registry ships an [MCP](https://modelcontextprotocol.io) server, so AI assistants (Claude, Cursor, and anything else that speaks MCP) can query it directly instead of guessing at slugs or parsing them with string logic. It runs locally on your machine - there is still no hosted service - and reads the published export, so answers always reflect the current registry.

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

Without uv: clone the repo, `pip install mcp requests`, and use `python mcp_server.py` as the command instead.

Six tools, each returning a small, focused answer rather than the whole database:

| Tool | What it answers |
|---|---|
| `resolve_slug` | Any slug - current **or from an older naming convention** - to its permanent `printing_id` and `codex_id`. This is how a tool holding pre-rename slugs migrates itself. |
| `get_card` | One card by `codex_id`, with its gameplay data and every printing of it. |
| `get_printing` | One physical print by `printing_id`, with its set, product, finish and current slug. |
| `search_cards` | Cards by name, type, element, rarity, or set. |
| `set_contents` | Every distinct card in a set - the authoritative answer to "how many cards are in set X", which the official data states nowhere. |
| `registry_stats` | Totals and per-set counts. |

The server also teaches connected agents the ground rules (key on the IDs, never on slugs; only Avatars have life), so tools built with AI assistance inherit correct usage by default.

## Data corrections

The official API occasionally ships errors (at the time of writing, 17 Gothic cards carry a `life` value only Avatars should have). Corrections live in [`data/overrides.json`](data/overrides.json), each with a written reason. They are applied on top of the API data during sync, so the registry holds the corrected values while the correction itself stays visible and reviewable in git. Spotted an error? Open an issue or a PR against that file - see [CONTRIBUTING.md](CONTRIBUTING.md).

## How updates happen

A sync script fetches the official API, diffs it against the registry, and classifies every difference. New cards get new IDs. Attribute changes update in place. Slug renames are matched conservatively (name, rules text, set, product, finish) - and anything that does not resolve to an unambiguous one-to-one match is quarantined for human review instead of guessed at, because a wrong guess would silently fork one card into two IDs. Syncs are run manually (or via the manually-triggered GitHub Action) and land as pull requests, never as direct pushes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for running the pipeline yourself and for how ambiguous cases are resolved.

## Licence

The code in this repository is MIT licensed. The card data itself belongs to Erik's Curiosa; this project only republishes what the official public API already serves, restructured for stability.
