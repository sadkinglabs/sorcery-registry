# Contributing

Thanks for helping keep the registry accurate. Two ground rules before anything else:

1. **Never edit an ID.** Not in the database, not in the JSON. The whole value of this project is that `card_id` and `printing_id` never move. The database schema physically refuses ID updates and deletions, and CI checks every commit against the previous export.
2. **Never hand-edit `export/registry.json` or `registry.sqlite`.** Both are produced by the pipeline. Change the inputs (API data arrives via sync, corrections via `data/overrides.json`) and let the scripts regenerate the outputs. CI fails if the two files disagree.

## Reporting a data error

Found a card whose registry data is wrong?

- If the **official API** is also wrong: that is an upstream error we paper over. Open an issue with the card, the field, the wrong value and the right one (a photo of the physical card is ideal evidence), or go straight to a PR adding an entry to `data/overrides.json`:

```json
{
  "match": { "card_name": "Accursed Tower" },
  "set_fields": { "life": null },
  "reason": "API data error: only Avatars have a life value. Confirmed against the printed card."
}
```

  Every entry needs a `reason` - it is the audit trail. Add `"set_name"` inside `match` to restrict the fix to one set's printings; without it the fix applies to the card and all its printings. When upstream later fixes the error, the sync flags the entry as matching nothing and it gets removed.

- If the registry disagrees with the API for no documented reason: that is a bug in the pipeline. Open an issue with both values.

## Running the pipeline

Requirements: Python 3.10+, `pip install requests`. No other dependencies.

```bash
python -m unittest discover -s tests   # test suite
python -m registry.sync --dry-run      # fetch the API, show what would change
python -m registry.sync                # same, then apply after confirmation
python -m registry.validate            # check every invariant
```

A sync PR should contain: the updated `registry.sqlite`, the regenerated `export/registry.json`, and nothing hand-written except (when relevant) override or decision files. Run `python -m registry.validate --against origin/main` before pushing; CI runs the same check.

## When a sync is ambiguous

The sync auto-applies only what is unambiguous. If a slug vanished and a new one appeared and they cannot be paired with certainty (same card, same set, product, finish, with collector number as tiebreaker - or for whole cards, the full gameplay fingerprint), the case is written to `review/pending.json` and the run exits with code 2. **This is by design.** A wrong automatic guess would fork one card into two IDs, which is the one failure this project exists to prevent.

To resolve a case, write `review/decisions.json`:

```json
{
  "printing_renames": [ { "printing_id": 812, "new_slug": "004-witch-b-s" } ],
  "new_printings":    [ "091-some_genuinely_new-b-s" ],
  "retire_printings": [ 640 ],
  "card_renames":     [ { "card_id": 77, "new_name": "Witch" } ],
  "new_cards":        [ "Some Genuinely New Card" ]
}
```

Each entry answers one pending question: *this* vanished printing is now *that* slug (`printing_renames`), *this* new slug really is a new printing (`new_printings`), *this* printing really was removed (`retire_printings`), and likewise for cards. Re-run the sync; decisions are validated against the live diff (a stale decision is an error, never a silent guess), applied, and archived to `review/archive/` so every human judgement stays on record. Alternatively `python -m registry.sync --interactive` walks the same choices at the prompt.

Include the pending file, your decisions and your reasoning in the PR so reviewers can check the pairing.

## What runs in CI

Every push and PR: the test suite, then `registry.validate`, which checks that

- the database's invariant triggers are intact and all foreign keys hold,
- no ID exceeds its allocation counter (nothing bypassed ID assignment),
- every printing's slug agrees with its open `slug_history` row,
- the committed JSON is byte-identical to what the committed database generates,
- and against the base branch: every ID that existed before still exists, printings still point at the same card, counters never decreased, and every slug change is explained by `slug_history`.

If any of those fail, the PR does not merge. There is deliberately no way to "fix up" a violation in place; revert and redo the change through the pipeline.

## Style

Plain Python, standard library plus `requests`. Correctness beats cleverness: the sync logic is meant to be read and reviewed by strangers. If you add classification behaviour, add a test for it, especially anything touching rename matching.
