# Contributing

Thanks for helping keep the registry accurate. Two ground rules before anything else:

1. **Never edit an ID.** Not in the database, not in the JSON. The whole value of this project is that `codex_id` and `printing_id` never move. The database schema physically refuses ID updates and deletions, and CI checks every commit against the previous export.
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

  Every entry needs a `reason` - it is the audit trail. A field is written wherever the record has that column: `life` is a card fact, `artist` a printing fact. The registry-owned `errata` flag is corrected the same way (`"set_fields": { "errata": true }`), since upstream has no value for it. Add `"set_name"` inside `match` to restrict the fix to one set's printings; without it the fix applies to the card and all its printings. When upstream later fixes the error, the sync flags the entry as matching nothing and it gets removed.

- If the registry disagrees with the API for no documented reason: that is a bug in the pipeline. Open an issue with both values.

## Running the pipeline

Requirements: Python 3.10+, `pip install requests`. No other dependencies.

```bash
python -m unittest discover -s tests   # test suite
python -m registry.sync --dry-run      # fetch the API, snapshot the payload, show what would change
python -m registry.sync --from-file review/upstream-snapshot.json   # apply those exact bytes, after confirmation
python -m registry.validate            # check every invariant
```

Every fetch over the network writes the raw payload to `review/upstream-snapshot.json` (a local working file, never committed) before anything is diffed. Applying from that file rather than fetching a second time is the recommended flow: the apply then acts on exactly the bytes the dry run showed you, not on whatever upstream is serving a minute later. Plain `python -m registry.sync` still works and simply fetches afresh.

There is also [`mcp_server.py`](mcp_server.py), a read-only MCP server over the export for AI agents (see the README). It contains no logic of its own beyond indexing and querying `export/registry.json` - if the export is right, the server is right. Its query layer is unit-tested in `tests/test_mcp.py`; running the server itself additionally needs `pip install mcp`. If you change the export's shape, update the server's `Registry` class, its tests, and `schema/registry.schema.json` in the same PR - CI validates the export against the schema. Note also `name_history` and `rules_history`, the card-name and rules-text counterparts of `slug_history`: every card rename or rewording must close the old row and open one for the new value, which the pipeline does automatically.

The registry mirrors the shape the official API serves today: its field names (`set_code`, `defense`, `typeline`, `BoxTopper`), its list fields in its order, and its split between gameplay data on the card and physical facts on the printing. When upstream changes shape again, the adapter in `registry/fetch.py` is the one place that knows the upstream layout; everything downstream works from the snapshot it builds. A schema bump that restructures the database ships with a migration script (see `registry/migrate_v7.py`) that rebuilds the file from the new DDL and proves every id survived.

A sync PR should contain: the updated `registry.sqlite`, the regenerated `export/registry.json`, and nothing hand-written except (when relevant) override or decision files. Run `python -m registry.validate --against origin/main` before pushing; CI runs the same check.

## Shipping a set release (the runbook)

When a new set drops, this is the whole flow. Existing IDs never change; a set release is pure append.

1. Branch: `git checkout -b sync/<set-name>`.
2. `python -m registry.sync --dry-run` - fetches, snapshots the payload to `review/upstream-snapshot.json`, and prints the plan. The expected shape is boring: N new cards, M new printings, possibly attribute updates (errata waves are normal), zero renames, zero retirals, zero ambiguity.
3. If the plan is NOT boring, that is the guardrails working, not an error: read `review/pending.json`, write `review/decisions.json` (next section), and re-run against the same snapshot until the plan is clean.
4. Watch the override notes: if upstream fixed an error we correct in `data/overrides.json`, the sync reports the entry as matching nothing - delete it in this same PR.
5. Apply against the exact reviewed bytes: `python -m registry.sync --from-file review/upstream-snapshot.json`.
6. `python -m registry.validate --against origin/main`, then push and open the PR. CI re-proves everything, including that every pre-existing ID survived.
7. After merge: tag a data release. Bump the minor version for data (a new set, corrections); bump the major version when the export's shape changes (a `schema_version` bump). Write the release notes as the tag message and push the tag:

   ```bash
   git tag -a vX.Y.0 -m "<what changed, for consumers>"
   git push origin vX.Y.0
   ```

   The `release` workflow does the rest: it re-runs the tests and invariants on the tagged commit, builds the manifest (`python -m registry.manifest --dataset-version vX.Y.0 --out manifest.json`, should you want it locally), and publishes the GitHub release with the tag message as its notes and the manifest attached.

8. Never run the first sync of a new set through the GitHub Action - it applies with `--yes`. The Action is for routine re-syncs once the drop has been reviewed by a human once.

## When a sync is ambiguous

The sync auto-applies only what is unambiguous. If a slug vanished and a new one appeared and they cannot be paired with certainty (same card, same set, product, finish - or for whole cards, the full gameplay fingerprint), the case is written to `review/pending.json` and the run exits with code 2. Likewise, any sync in which existing cards disappear while new card names appear quarantines the unmatched remainder rather than issuing new IDs, because a card that was renamed and reworded in the same sync is indistinguishable from a removal plus an unrelated newcomer. **This is by design.** A wrong automatic guess would fork one card into two IDs, which is the one failure this project exists to prevent.

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
- every printing's slug agrees with its open `slug_history` row, and every card's name and rules text agree with their open history rows,
- the committed JSON is byte-identical to what the committed database generates,
- and against the base branch: every ID that existed before still exists, printings still point at the same card, counters never decreased, and every slug change is explained by `slug_history`.

If any of those fail, the PR does not merge. There is deliberately no way to "fix up" a violation in place; revert and redo the change through the pipeline.

## Style

Plain Python, standard library plus `requests`. Correctness beats cleverness: the sync logic is meant to be read and reviewed by strangers. If you add classification behaviour, add a test for it, especially anything touching rename matching.
