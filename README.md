# Sorcery Card Registry

Stable numeric identifiers for every card and printing in **Sorcery: Contested Realm**, published as a single JSON file anyone can build on.

## The problem

The identifier most tools work from is the official API slug, e.g. `004-witch-b-s`. That slug is a composite key: set, collector number and printing are all packed into one string, in a format that was never publicly specified. Worse, the slug is derived from data that shifts - when the naming convention changed, every slug in a set changed with it, and every database keyed on those slugs broke at once.

There is also nothing in the official data linking two printings of the same card to each other except the card's name, so every tool reconstructs that link by name-matching, which breaks at the first inconsistency.

## The fix

This registry follows the pattern that already works elsewhere - Konami's passcodes for Yu-Gi-Oh, Scryfall's oracle IDs for Magic. Two identifiers, both plain integers, both permanent:

- **`card_id`** - one per card, shared by every printing of it. Alpha, Beta and promo Apprentice Wizard all carry the same `card_id`. Use it when you mean "this card as a game object": decklists, rulings, collection grouping.
- **`printing_id`** - one per physical print (a specific set, product and finish). Use it when you mean "this exact piece of cardboard": inventories, pricing, scans.

Set, collector number, product and finish sit in ordinary columns *next to* the IDs, not inside them. When the official naming convention changes again, the slug column updates, a row is added to `slug_history`, and **the IDs do not move**. Nothing downstream needs remapping, ever.

Three guarantees, enforced by CI on every commit, not by promise:

1. An ID never changes value and never disappears.
2. A retired ID is never reused. Assignment is append only.
3. A slug change never causes a new ID. Old slugs stay resolvable through `slug_history`.

## What this is not

- Not a hosted service. There is no server and no endpoint; you consume a file.
- No images, no prices, no rulings, no legality data.
- Not a second opinion on card data. Attributes mirror the official API, with a short, public list of corrections for confirmed upstream errors (see [`data/overrides.json`](data/overrides.json)).

## Using the data

Everything you need is one file: [`export/registry.json`](export/registry.json). Grab it, vendor it, or read it straight from the repo. It is deterministically ordered, so diffing two versions shows you exactly what changed and nothing else.

```jsonc
{
  "header":       { "schema_version": 1, "source": "...", "cards": 1100, ... },
  "cards":        [ { "card_id": 1, "name": "Apprentice Wizard", "type": "Minion", ... } ],
  "printings":    [ { "printing_id": 1, "card_id": 1, "slug": "001-apprentice_wizard-b-s",
                      "set_code": "alpha", "card_number": 1, "product": "Booster",
                      "finish": "Standard", ... } ],
  "slug_history": [ { "slug": "...", "printing_id": 1, "valid_from": "2026-08-19", "valid_to": null } ]
}
```

Practical notes:

- **Key on the IDs, treat everything else as data.** `slug`, `set_code`, `card_number` are conveniences that can change; `card_id` and `printing_id` cannot.
- **Migrating existing data keyed on slugs:** look each slug up in `slug_history`, which maps every slug that has ever existed (current and superseded) to its `printing_id`. Do it once and the next naming convention change costs you nothing.
- **Retired printings** (removed upstream) keep their rows and IDs, marked with a `retired_at` date, so old references never dangle. Cards are never removed at all.
- **Text is canonicalised**: `\n` line endings, no trailing whitespace, one line per ability. The official API is inconsistent about all three; the registry is not.
- The SQLite database (`registry.sqlite`) is also committed, for anyone who prefers SQL. It and the JSON always agree - CI fails if they drift.

## Data corrections

The official API occasionally ships errors (at the time of writing, 17 Gothic cards carry a `life` value only Avatars should have). Corrections live in [`data/overrides.json`](data/overrides.json), each with a written reason. They are applied on top of the API data during sync, so the registry holds the corrected values while the correction itself stays visible and reviewable in git. Spotted an error? Open an issue or a PR against that file - see [CONTRIBUTING.md](CONTRIBUTING.md).

## How updates happen

A sync script fetches the official API, diffs it against the registry, and classifies every difference. New cards get new IDs. Attribute changes update in place. Slug renames are matched conservatively (name, rules text, set, product, finish, collector number) - and anything that does not resolve to an unambiguous one-to-one match is quarantined for human review instead of guessed at, because a wrong guess would silently fork one card into two IDs. Syncs are run manually (or via the manually-triggered GitHub Action) and land as pull requests, never as direct pushes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for running the pipeline yourself and for how ambiguous cases are resolved.

## Licence

The code in this repository is MIT licensed. The card data itself belongs to Erik's Curiositea; this project only republishes what the official public API already serves, restructured for stability.
