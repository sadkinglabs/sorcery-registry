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

  Every entry needs a `reason` - it is the audit trail. A field is written wherever the record has that column: `life` is a card fact, `artist` a printing fact. The registry-owned `errata` flag is corrected the same way (`"set_fields": { "errata": true }`), since upstream has no value for it, and so is a card's representative printing: `"set_fields": { "default_printing_id": "P000937" }` pins that printing as the card's `default_printing_id` instead of the rule's choice (the validator insists it is one of the card's own printings). Add `"set_name"` inside `match` to restrict the fix to one set's printings; without it the fix applies to the card and all its printings. `"back.rules_text"` (any `back.<field>`) corrects one field of a back face and leaves the rest of it as upstream serves it. When upstream later fixes the error, the sync flags the entry as matching nothing and it gets removed.

  **`"retroactive": true`** says the card *always* had this value and the registry's record was wrong. Without it a correction reads as a change the card underwent: a new `card_history` row opens today and every printing released before it is reported as showing older values - right when the card really did change and upstream has it wrong (Druid's rules text), and wrong when only our record ever said otherwise (Rubble was recorded as a Token; the printed card is a Site and always was). A retroactive correction is written into every history row instead, opens none, and leaves `printed_as_current` alone. It may only name card fields, since a printing keeps no history of its own. The audit trail is the entry itself, with its reason, in git.

- If the registry disagrees with the API for no documented reason: that is a bug in the pipeline. Open an issue with both values.

## Running the pipeline

Requirements: Python 3.10+, `pip install -r requirements.txt` (requests, and mcp for the MCP server; both pinned). The workflows and the test suite install `requirements-ci.txt`, which adds jsonschema, pillow and google-auth, also pinned. Dependabot opens a pull request when a pin or a pinned Action has a newer release; the Actions are pinned to commits, with the version in a comment beside each.

```bash
python -m unittest discover -s tests   # test suite
python -m registry.sync --dry-run      # fetch the API, snapshot the payload, show what would change
python -m registry.sync --from-file review/upstream-snapshot.json   # apply those exact bytes, after confirmation
python -m registry.validate            # check every invariant
python -m registry.publish             # write dist/: one object per card, printing, slug and set (see docs/api.md)
python -m registry.types               # regenerate schema/registry.d.ts from the schema (CI checks it matches)
```

Every fetch over the network writes the raw payload to `review/upstream-snapshot.json` (a local working file, never committed) before anything is diffed. Applying from that file rather than fetching a second time is the recommended flow: the apply then acts on exactly the bytes the dry run showed you, not on whatever upstream is serving a minute later. Plain `python -m registry.sync` still works and simply fetches afresh.

There is also [`mcp_server.py`](mcp_server.py), a read-only MCP server over the export for AI agents (see the README). A checkout carries a project-level `.mcp.json`, so opening the repository in Claude Code (or any client that reads it) attaches the server automatically and the seven tools answer from the working tree's `export/registry.json`. Outside a checkout it resolves the newest verified release through `api.kairosarchive.net/versions.json` (`latest.v3` and its digest), fetches `registry.json` from that immutable release root, falls back to the same tag on `raw.githubusercontent.com` (or, without `versions.json`, to the `releases/latest` redirect - never `main`), verifies the bytes against the digest, caches them verbatim and revalidates the cache with one small request; the loader is unit-tested with a mocked HTTP layer (`mcp_server._get`). It contains no logic of its own beyond loading, indexing and querying the export - if the export is right, the server is right. Its query layer is unit-tested in `tests/test_mcp.py`; running the server itself additionally needs `pip install mcp`. If you change the export's shape, update the server's `Registry` class, its tests, and `schema/registry.schema.json` in the same PR, then run `python -m registry.types` and commit `schema/registry.d.ts` - CI validates the export against the schema, checks the declarations match it, and type-checks a slice of the export against them (`tests/ts/`). Note also `name_history` and `card_history`, the card-name and gameplay-face counterparts of `slug_history`: every card rename, and every change to a gameplay field, must close the old row and open one for the new value, which the pipeline does automatically.

**Conduct toward the publisher.** Their API page says it plainly: requests are rate limited (30 a minute on `/api/cards`), and the API is not meant to be anyone's live backend - "poll intermittently, compare each response with your previous import, and host the data your application needs". The registry is that guidance made concrete, so it must stay on the right side of it: a sync is one request, the weekly drift check is one request, and nothing in this repository may loop over upstream - no per-card fetches, no retries in a loop, no polling inside a workflow. Every request identifies itself with the `User-Agent` in `registry/fetch.py` (`sorcery-registry/schemaN (+https://kairosarchive.net; ...)`). The same courtesy applies to their public image folder when the image pipeline lands.

**The upstream contract.** Their page also publishes the response type, `CardAPIDTO`; it is checked in verbatim as [`schema/upstream-cards.dto.ts`](schema/upstream-cards.dto.ts) so a diff of their page is a diff of ours, and [`schema/upstream-cards.schema.json`](schema/upstream-cards.schema.json) is our machine-checked reading of it: structure and types only, every vocabulary left open. `build_snapshot` checks every payload against it before reading a field (`registry/contract.py`, stdlib-only) and stops with the path of the first field that no longer fits - a missing `engine.rules`, a `set` that became a string - rather than flattening a changed shape into wrong data; a new field or a new product spelling passes through as data. When their page changes, update both files in one PR and let the tests say what the adapter must do about it.

The registry mirrors the shape the official API serves today: its field names (`set_code`, `defense`, `typeline`, `BoxTopper`), its list fields in its order, and its split between gameplay data on the card and physical facts on the printing. When upstream changes shape again, the adapter in `registry/fetch.py` is the one place that knows the upstream layout; everything downstream works from the snapshot it builds. A schema bump that restructures the database ships with a migration script (see `registry/migrate_v8.py`) that rebuilds the file from the new DDL and proves every id survived; a bump that only adds derived export fields ships a script that just records the new version (`registry/migrate_v12.py`), because the validator insists the database and the code agree on it. The previous bump's script is removed once it has run, since it can never run again.

A sync PR should contain: the updated `registry.sqlite`, the regenerated `export/registry.json`, and nothing hand-written except (when relevant) override or decision files. Run `python -m registry.validate --against origin/main` before pushing; CI runs the same check.

## Shipping a set release (the runbook)

When a new set drops, this is the whole flow. Existing IDs never change; a set release is pure append.

1. Branch: `git checkout -b sync/<set-name>`.
2. `python -m registry.sync --dry-run` - fetches, snapshots the payload to `review/upstream-snapshot.json`, and prints the plan. The expected shape is boring: N new cards, M new printings, possibly attribute updates (errata waves are normal), zero renames, zero retirals, zero ambiguity.
3. If the plan is NOT boring, that is the guardrails working, not an error: read `review/pending.json`, write `review/decisions.json` (next section), and re-run against the same snapshot until the plan is clean.
4. Watch the override notes: if upstream fixed an error we correct in `data/overrides.json`, the sync reports the entry as matching nothing - delete it in this same PR.
5. Apply against the exact reviewed bytes: `python -m registry.sync --from-file review/upstream-snapshot.json`.
6. `python -m registry.validate --against origin/main`, then push and open the PR. CI re-proves everything, including that every pre-existing ID survived.
7. After merge: tag a data release. Bump the minor version for data (a new set, corrections) and for additive shape changes: a `schema_version` bump that only adds fields or sections is a minor release, as v3.1 through v3.4 were. Bump the major version only for a breaking change, one that removes or renames a field or changes what a value means. Write the release notes as the tag message and push the tag:

   ```bash
   git tag -a vX.Y.0 -m "<what changed, for consumers>"
   git push origin vX.Y.0
   ```

   The `release` workflow does the rest: it re-runs the tests and invariants on the tagged commit, builds the manifest (`python -m registry.manifest --dataset-version vX.Y.0 --out manifest.json`, should you want it locally) and the published objects (`python -m registry.publish`; among them `changes.json`, the way from the previous release tag's export, which the workflow reads from git, and a step that refuses the release if it removes an identifier within a major), uploads the release to its own immutable root on `api.kairosarchive.net`, verifies byte for byte what the CDN serves, marks the root `RELEASED`, lists it in `versions.json`, points the `/vX/` alias at it, and only then publishes the GitHub release with the tag message as its notes and the manifest attached (see "Hosting" below for what each step guarantees). Where pushing tags is not possible (some tooling can push branches but not tags), run the same workflow by hand from the Actions tab with the version and the notes as inputs: it creates the annotated tag on `main` itself and continues identically.

8. Never run the first sync of a new set through the GitHub Action - it applies with `--yes`. The Action is for routine re-syncs once the drop has been reviewed by a human once.

## Hosting

The domain is Cloudflare only: an R2 bucket (`sorcery-registry`) with the custom domain `api.kairosarchive.net` as its only public door - the bucket's `r2.dev` public-development URL is disabled and stays disabled, because that hostname is outside the zone and so outside every rule below (a client on it would meet no cache, no rate rule and no User-Agent check; the data is public anyway, the controls are what it would bypass) - a Single Redirect rule for the major alias (`/v3/*` → `/v3.1.0/*`, whose target the workflow rewrites on every release), one rate limiting rule (the Free plan allows exactly one per zone, so it guards both hosts: `(http.host eq "kairosarchive.net" and http.request.uri.path eq "/random") or (http.host eq "api.kairosarchive.net" and not (http.request.uri.path contains "/images/"))`, 30 requests per 10 seconds per IP, block for 10 seconds - `/random` is a Pages Function whose invocations are the account's one finite resource, and the API clause is a tripwire against per-object crawling; images are excluded because a results grid is 60 of them and the image workflows verify thousands, and a cached image costs the origin nothing after its first read), a cache rule that also ignores the query string on the API host (so `?anything=` cannot turn a cached read into an origin read; `check-domain` proves this weekly, and that a browser's request still carries the CORS header), a custom WAF rule that blocks requests with an empty `User-Agent` (`http.host eq "api.kairosarchive.net" and http.user_agent eq ""`; browsers always send one, so only deliberately anonymous clients are affected - the usage terms say so), no Bot Fight Mode (it challenges API clients), and one account-level billing alert at USD 10 - about 28 million billable reads past the free tier, so if it fires something is wrong rather than busy. It is a notification, not a cap: nothing on the account can stop spend on its own, and a spent Function allowance on the Free plan bills nothing and simply stops `/random` for the day (the site's `_routes.json` keeps it to that). Every client this repository ships identifies itself: the pipeline (`registry/fetch.py`), the release checks (`registry/hosted.py`), the MCP server. The `aws` CLI in the workflow is only the standard S3-compatible client for R2; there is no AWS account or service anywhere.

The workflow switches the hosted steps on when these repository **variables** exist: `R2_BUCKET` (`sorcery-registry`), `REGISTRY_BASE_URL` (`https://api.kairosarchive.net`), `CF_ZONE_ID`; optionally `CF_REDIRECT_RULE_ID` (the alias rule's internal id) - without it the workflow finds the rule by the name it was given in the dashboard, `v3 alias`. It needs these **secrets**: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID` (an R2 API token with Object Read & Write on the bucket), `CF_API_TOKEN` (a Cloudflare API token scoped to the zone with *Single Redirect: Edit*), and optionally `PAGES_DEPLOY_HOOK` (the website's deploy hook, rebuilt after every release). Without `R2_BUCKET` the workflow warns and releases to GitHub only.

What the hosted steps guarantee, in order (`.github/workflows/release.yml`, helpers in `registry/hosted.py`):

1. **Immutability.** A root already marked `RELEASED` is not re-uploaded (a re-run after a GitHub-side failure only redoes the pointers and the release). A root that already carries a *different* `registry.json.sha256` fails the run: bytes at a published path never change - release a new version instead.
2. **Upload** of `dist/` to `/<tag>/` with a one-year immutable cache header. It also tries to set the bucket's CORS policy, which an *Object Read & Write* token is not allowed to do; that is a warning, not a failure, because CORS is a one-off bucket setting made in the dashboard (R2 → bucket → Settings → CORS policy) with this JSON: `[{"AllowedOrigins": ["*"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 86400}]`. The verification step reports whether the CDN sends the permissive header.
3. **Every `image_urls` value in the export answers `HEAD 200`** through the CDN, so no published record ever references an image that is not served. Only the addresses the previous release did not already publish are asked: an image object is content-addressed - its name carries the art key - so an address that answered when that release published cannot have come to mean other bytes, and a release touching no images costs no requests (v3.3.1 spent 14 of its 16 minutes here, re-checking all 12,348 for a two-line text fix). What that no longer catches is an object deleted from the bucket behind an address nothing changed; the weekly image sync re-verifies every held object, which is where that would surface.
4. **Verification by bytes**: the served `registry.json` hashes to the committed digest, `index.json` names the tag and the root, a card object and a slug object answer as JSON. Only then is `RELEASED` written (the digest and the run URL).
5. **`versions.json`** is rebuilt from the one currently served plus this release (`python -m registry.versions`; newest first, `latest` per major only moves forward, a changed digest for a listed tag is refused) and uploaded with a 60-second cache.
6. **The alias flips** - the redirect rule's target becomes `/<tag>/` - only if `versions.json` now names this tag as the newest of its major, and the workflow confirms the alias redirects there before continuing. One operation, so no client ever sees a mixed dataset.
7. The GitHub release, then the website rebuild.

To see what the domain serves right now - digest, index, objects, CORS, the alias redirect, and whether an anonymous request is refused - run the `check-domain` workflow from the Actions tab (read-only, no credentials; optionally name a tag).

A failed run leaves at most a partial root without `RELEASED` - never listed, never aliased, harmless - and re-running the same tag resumes it (same bytes, objects compared by size so the resume is quick) or refuses it (different bytes). A dispatched re-run reuses the tag it already created; if `main` has moved since (a workflow fix merged in between), the tag is moved to the new commit as long as nothing was published under it - no GitHub release and no `RELEASED` root - because until then it marks an attempt, not a release. Two releases cannot interleave: the workflow runs in a concurrency group.

## Images

The publisher's guidance is "host images yourself; download released card images from the public image folder" (a Google Drive folder, 3,090 PNGs named by API slug, a `-r` suffix for the reverse of a double-faced card). The `images` workflow (Actions tab) has three modes:

- **list** - lists the folder through the Drive API as a service account (secret `GDRIVE_SERVICE_ACCOUNT`, the JSON key of a robot identity in the owner's Google Cloud project; it can only read what is already public, but its requests count against the project's quota instead of the abuse filter Google applies to anonymous traffic from cloud addresses) or, failing that, with a plain API key (secret `GDRIVE_API_KEY`), maps every file to a printing and face through `slug_history` plus [`data/image-decisions.json`](data/image-decisions.json), and commits `review/image-listing.json` to the branch `review/image-listing` with a summary: folders, formats, dimensions, unmapped files, faces claimed twice, printings and back faces without a file. Read-only.
- **sync** - lists, then downloads what changed (a new file, a new MD5, a new recipe), renders the renditions, uploads them to the bucket's `images/` prefix, verifies every held object through the CDN, regenerates the export and opens a pull request. Nothing reaches `main` except through that PR (which the workflow also asks CI to check, by dispatching `validate` on the branch: a pull request opened by a workflow's token does not trigger CI by itself). With a service account a run takes everything that changed, within a four-hour fetch budget (a GitHub job dies at six hours with everything on its disk, so the fetch stops in time to upload, verify and open the PR, and the next run continues from `data/images.json`); anonymously, `limit` caps it at 1,500 images: Google refuses an address after roughly 1,600 anonymous downloads in a day (an HTML "Sorry" page, not an API error), and the fetch stops on its own after a few refusals in a row, publishing what it got; the next run - weekly on Tuesdays, or dispatched - resumes where `data/images.json` says, so the folder is covered in two or three runs.
- **art** - the same upload, verification and pull request for the owner's art of hand-recorded printings (`art/`, next to the manual entries below). It runs by itself when a change to `art/` reaches `main`; nothing is fetched from Drive.

**Two sources, two watches.** The registry follows the publisher on two channels and treats each as a diff against what it already holds: the official API for the data (the `drift` workflow, Mondays: one request, a dry-run sync, an issue when anything changed) and the public image folder for the pictures (the `images` workflow, Tuesdays: one listing, then only the files whose MD5 or name is new). Each weekly listing is also a drift check on the folder itself: files the naming rule cannot place, one face claimed by two files, or files named by slugs the API no longer uses are raised as warnings on the run page, and the mapping summary lands in the run's step summary. A file that vanishes from the folder changes nothing here - the objects already served stay served, because image addresses are permanent - it only shows up as one more "printing without a file" in the summary. If Erik's Curiosa ever changes how they distribute images (another host, another naming scheme, a different structure), the only code that knows about the folder is the intake in `registry/images.py` (`list_folder`, `map_listing`, `DriveAuth`); keys, object names, `data/images.json` and every published address are independent of where a file came from, so the adaptation is a new intake and a run, never a change to what is already published.

**The identity toward Google.** The service account in the owner's Google Cloud project has no roles and no shares: it can read exactly what any anonymous visitor can read, the publisher's public folder, and nothing else. What it buys is that requests are attributed to the project and counted against its Drive API quota, instead of being judged by the abuse filter Google applies to anonymous traffic from cloud address ranges (which stopped the GitHub runners after a few dozen to a few hundred files a day). To rotate it: Google Cloud console, IAM & Admin, Service Accounts, the account, Keys, add a new JSON key, replace the `GDRIVE_SERVICE_ACCOUNT` secret with the new file's contents, then delete the old key. If the account is ever deleted or the secret removed, the workflow falls back to `GDRIVE_API_KEY` and its 1,500-a-run cap, and says which identity it used at the top of the log (`Drive identity: ...`).

**A killed run loses nothing.** A GitHub job that dies (the six-hour limit, a runner failure) after its upload but before its pull request leaves every rendition in the bucket under its content-addressed name, and only the bookkeeping on the runner's disk. The next sync's first step, `python -m registry.images adopt`, lists the bucket and records those faces without downloading anything, as long as the evidence is unambiguous: exactly one complete set of renditions for the face, every object uploaded after the source file was last modified, the original the same size and extension as the file, and no recipe bump since the state was last rendered (after a bump the bucket's keys belong to the old recipe and are re-rendered). Everything else goes to the fetch as usual, and the CDN verification checks adopted objects like any other.

What the registry holds is [`data/images.json`](data/images.json), registry-owned data in git like the overrides: per printing and face, the Drive file (id, name, MD5, dimensions), the art-version key, the recipe, and the size of every rendered object. The export derives `image_hash`, `image_urls` and `image_status` from it; the database is not involved, and the validator checks that every held face is exactly what the export publishes. Files the naming rule cannot place are decided by hand in `data/image-decisions.json` (`assign` a file to a printing and face, or `ignore` it), each with a reason - the registry never guesses, and never invents a printing from an image file.

**Running the image sync from your own machine** (the whole folder at once, no per-address limit): in the Drive web UI, download the publisher's folder as a zip and unpack it anywhere. Then, in a checkout of this repository with Python 3.10+:

```bash
pip install -r requirements-ci.txt
export GDRIVE_SERVICE_ACCOUNT="$(cat kairos-images-key.json)"             # or GDRIVE_API_KEY=... for the anonymous fallback
python -m registry.images list --out review/image-listing.json          # a handful of requests
python -m registry.images fetch --listing review/image-listing.json \
    --source-dir "/path/to/unpacked folder" --limit 0                   # reads the files instead of downloading them
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=auto \
       AWS_ENDPOINT_URL=https://<account id>.r2.cloudflarestorage.com \
       AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
python -m registry.images upload --work work/images --bucket sorcery-registry   # aws CLI, as the R2 client
python -m registry.images verify                                                # every held object served through the CDN
python -m registry.export && python -m registry.validate --against origin/main
git checkout -b images/$(date -u +%Y-%m-%d) && git add data/images.json export/ && git commit -m "images: ..." && git push -u origin HEAD
```

Then open the pull request. The R2 values are the same ones the release workflow holds as secrets; keep them in your shell session only. `data/images.json` is saved after every file, so an interrupted run resumes. The fetch prints a `progress:` line every 100 files (handled, fetched, failed, rate, time left); on GitHub the same counts land in the run's step summary.

The renditions are Scryfall's (small 146×204, normal 488×680, large 672×936 WebP, plus the untouched original). Everything that determines the rendered bytes is `RENDITION_RECIPE` in `registry/images.py`: bump it when the sizes, quality or resampling change, and the next sync re-renders every image under a new key, so no published address ever changes bytes. The publisher's files come in two resolutions; the low one (380×531) is upscaled by decision and flagged `image_status: lowres`.

## When the printed card and the API disagree

The official API serves a card's current face. For a card the publisher changed after it was printed - the cards the old API marked `UPDATED` - the printed card is the only record of the earlier face, and the registry never observed it, so every printing would claim to show current values. [`data/errata.json`](data/errata.json) records the printed face by hand, one entry per card:

```json
{
  "codex_id": "C000002",
  "printed": { "rules_text": "The text as printed on the card." },
  "current_since": "2026-09-15",
  "current_printings": [],
  "source": { "printing_id": "P000007" },
  "reason": "Read from the Alpha printing's image; the API's text differs. Checked by the registry owner, 2026-09-15."
}
```

`printed` names the gameplay fields as printed (any of the face fields: `rules_text`, `cost`, `attack`, ...); `current_since` is the date from which the current face applies; `current_printings` lists printings that already carry the current face (a reprint with the corrected text), which must have been released on or after that date, while every other printing must predate it; `unknown_printings` lists printings that show no face at all (a textless promo), which the export reports as `printed_as_current: null` since no date can say what they show. `python -m registry.errata apply` turns the entry into history: a closed `card_history` row with `source: "card"` from the day the first printing carrying the printed face reached the public until `current_since`, and the observed face from then on. The export then derives `printed_as_current: false` for the older printings. The command is idempotent; re-running it after correcting a transcription (the same dates, a differently spelled `printed`) updates the recorded row in place, because that row is the registry's reading of the card rather than an observation. It refuses an entry whose dates no longer match what is recorded, and the validator (`registry.validate`, in CI) checks that the file and the history agree, that `current_since` really separates the printings as claimed, and that no `card` row exists without an entry. To read what a card says, the `review-images` workflow (Actions tab) copies the served images of the printings of any cards into the branch `review/images` with an index. Like the overrides, every entry carries a reason, and the pull request that adds one shows the transcription for review; on the site, the card page's "As printed" panel shows the two faces side by side once the release is out.

## When a sync is ambiguous

The sync auto-applies only what is unambiguous. If a slug vanished and a new one appeared and they cannot be paired with certainty (same card, same set, product, finish - or for whole cards, the full gameplay fingerprint), the case is written to `review/pending.json` and the run exits with code 2. Likewise, any sync in which existing cards disappear while new card names appear quarantines the unmatched remainder rather than issuing new IDs, because a card that was renamed and reworded in the same sync is indistinguishable from a removal plus an unrelated newcomer. **This is by design.** A wrong automatic guess would fork one card into two IDs, which is the one failure this project exists to prevent.

To resolve a case, write `review/decisions.json`:

```json
{
  "printing_renames": [ { "printing_id": 812, "new_slug": "004-witch-b-s" } ],
  "new_printings":    [ "091-some_genuinely_new-b-s" ],
  "retire_printings": [ 640 ],
  "card_renames":     [ { "card_id": 77, "new_name": "Witch" } ],
  "new_cards":        [ "Some Genuinely New Card" ],
  "confirm_cards":     [ { "card_id": 1101, "name": "The Champion" } ],
  "confirm_printings": [ { "printing_id": 3089, "slug": "999-the_champion-op-f" } ]
}
```

Each entry answers one pending question: *this* vanished printing is now *that* slug (`printing_renames`), *this* new slug really is a new printing (`new_printings`), *this* printing really was removed (`retire_printings`), and likewise for cards. `confirm_cards` and `confirm_printings` answer the cases the sync raises when upstream starts serving something that looks like a manual record (next section): *this* manual record is *that* upstream card or slug. Re-run the sync; decisions are validated against the live diff (a stale decision is an error, never a silent guess), applied, and archived to `review/archive/` so every human judgement stays on record. Alternatively `python -m registry.sync --interactive` walks the same choices at the prompt.

Include the pending file, your decisions and your reasoning in the PR so reviewers can check the pairing.

## Recording cards the API does not serve

Store-kit prize cards, Kickstarter pledge cards and curios were printed but are not in the official API. [`data/manual.json`](data/manual.json) records them with the same fields as every other card and printing, and they get real ids from the same counters.

- **A new card** goes under `cards`, with every card field and its printings nested inside it. Leave `codex_id` and each `printing_id` null.
- **A new printing of a card the registry already holds**, such as a store-kit foil of an official card, goes under `printings` with that card's `codex_id`. Its gameplay text comes from the card, so it lists only physical facts.
- **A curio is always a card of its own.** Curios are collectibles, never played, so none is recorded as a printing of a card that is. Each goes under `cards` with its printing(s) in set `CUR`, and its name is the printed name followed by ` (Curio)`: `Bosk Troll (Curio)` beside the official `Bosk Troll`, because card names are unique and the sync identifies cards by name. Where several curios share a printed name, a qualifier goes before the suffix, so the name still ends in ` (Curio)`: `Bosk Troll, variant 2 (Curio)`. Game fields it does not have are null, `rules_text` is `""` when there is none, and notes say what sets it apart and which card it relates to. A note on the official card can say that a curio of it exists. The validator enforces all of this: a card with a `CUR` printing has only `CUR` printings and a `(Curio)` name, and only such a card has one.
- **Null means unknown.** Never copy a value from another printing. An alternate art usually means a different artist, typeline or flavour text.
- **Every entry needs a `source` and a `recorded` date.** Sources name a place, never a person, as for notes. The bar for minting an id is a photo of the card or a public source that shows it: ids are permanent, so a record minted in error can only be withdrawn, never taken back.
- **Set codes.** A promo goes in the publisher's set `999` with the product it was distributed as (`OrganizedPlay`, `Kickstarter`). A printing upstream will never serve and that belongs to no set of theirs goes in a set of the registry's own, whose code is three capital letters (`CUR`, "Curios"), so it can never collide with a publisher's code. A set code must be classified in [`data/sets.json`](data/sets.json) before any entry uses it: a new set of ours gets `"kind": "registry"` there.
- **`released_with`** names the set release a promo or curio belongs to, such as `002` for a Beta store-kit card. Leave it null for a printing in a set of kind `release`. For official promos, which the API serves under 999, record it in [`data/released-with.json`](data/released-with.json) instead, with a source.

Then run:

```bash
python -m registry.manual apply     # mints ids and writes them into data/manual.json
python -m registry.export
python -m registry.validate --against origin/main
```

`apply` can be re-run safely. It mints ids only for entries without one, and updates the others in place: until upstream serves a record, it is our reading of the card, so a corrected reading replaces the old one. Commit `data/manual.json` with the ids it wrote, the database and the export together.

**Slugs.** Each manual printing gets a slug predicted in the publisher's own shape (`999-the_champion-op-f`), so it reads like the rest and is the first hint when upstream starts serving it. The prediction owns nothing and has no slug history. Where two predictions would collide, the second takes its `released_with` as a suffix (`999-the_champion_004-op-f`); an entry may also give its own `slug`.

**Never remove an entry.** `apply` refuses a manual record with no entry. To retract one found to be wrong, add `"withdrawn": {"on": "YYYY-MM-DD", "reason": "..."}` to it. A withdrawn printing is never a card's default, and the export shows why it was withdrawn.

**When upstream starts serving one.** The sync never retires a manual record, since upstream not serving it is its normal state. When upstream serves a card with the same or a close name, or a printing of the same card with the same product and finish, or the predicted slug itself, the sync neither matches nor mints. It writes a case to `review/pending.json`, and you answer it in `review/decisions.json`:
- `confirm_cards` or `confirm_printings` says it is the same record. The record keeps its id, takes upstream's name, slug and values where they differ, and becomes origin `api`, with `manual.confirmed_at` set to that day. Its entry stays in `data/manual.json` as the record of where it started.
- `new_cards` or `new_printings` says it is something else.

A curio in a set of the registry's own will never be confirmed, and stays manual for good.

**Art.** The publisher's folder has no file for a printing recorded by hand, so its image comes from the owner and lives in [`art/`](art/), named by printing id: `art/P001700.jpg` for the front, `art/P001700-r.jpg` for the back face of a printing that has one. PNG, JPEG or WebP (convert a phone's HEIC first). Art is only for printings recorded by hand - the validator refuses a file for an official printing, whose only image is the publisher's - and when the publisher's folder later serves a file for a confirmed record, the image sync replaces the art and `render` leaves it alone.

The repository and the bucket are public, and a phone photo carries EXIF, often including where it was taken. The validator refuses an art file with EXIF, XMP or text chunks. Strip a file before committing it, because a pushed blob stays in the branch's history even if a later commit cleans it:

```bash
python -m registry.art strip art/P001700.jpg   # orientation applied to the pixels, metadata removed, colour profile kept
python -m registry.art check                   # the validator's checks on art/
```

Commit the art in the same pull request as its manual entry, or after it. When it reaches `main`, the `images` workflow (mode `art`) renders the same renditions as a publisher image under the same content-addressed names, uploads them, verifies them through the CDN and opens a pull request with `data/images.json`, which records the art file as the source. Replacing a file publishes new art under a new key; deleting one drops the image from the records (the objects stay in the bucket, as every published object does). As with the publisher's images, nothing reaches the domain until the next release.

## What each set is

A set code is a label, never a number, so nothing is read from it. [`data/sets.json`](data/sets.json) records what each set is:
- `release` is a set release, whose printings came out with it.
- `promo` is the publisher's bucket for promos, `999`. Its printings came out with many releases, and each one's release is recorded per printing.
- `registry` is a set of the registry's own, for printings the official API will never serve. Its code is three capital letters, so it can never collide with a publisher's.

When a sync brings a set the file does not list, the validator fails until you add it with its kind. That is deliberate: a new code could be a new release or a new promo bucket, and only a person can say which.

## Notes

Some things the registry knows have no field and no place in the official API, such as that a promo was prize support in a particular store kit. [`data/notes.json`](data/notes.json) holds them, read at export time and never stored in the database. It attaches notes to cards (`cards`, keyed by `codex_id`) and printings (`printings`, keyed by `printing_id`). Each note is `{"text", "source", "recorded"}`: one fact in plain words, where it came from, and the day you wrote it down.

- **Sources name a place, never a person.** Write "Community report, Sorcery Discord" or "Arthurian Legends store kit insert, photographed". Every release is immutable, so a name written into one stays in it for good. Credit a person by name only if they have asked to be credited.
- **One fact per note.** Say how sure it is in the text, for example "reportedly". A single report is a reason to go and look, not a record.
- **A note never contradicts a field.** A fact that fits a field, such as an artist, a date or a finish, is a correction. Make it in [`data/overrides.json`](data/overrides.json), where it changes the field and carries a reason.
- **Adding a note is a data release.** Run `python -m registry.export` and commit the regenerated export with the note. `changes.json` reports notes in a section of their own, never as a change to the record.

The file is shape-checked on load, so a typo is an error rather than a silently dropped note, and the validator checks that every note sits on a record that exists.

## What runs in CI

Every push and PR: the test suite, then `registry.validate`, which checks that

- the database's invariant triggers are intact and all foreign keys hold,
- no ID exceeds its allocation counter (nothing bypassed ID assignment),
- every printing's slug agrees with its open `slug_history` row, and every card's name and rules text agree with their open history rows,
- the committed JSON is byte-identical to what the committed database generates, and every record's addresses name the record they sit on,
- every note sits on a card or printing that exists,
- and against the base branch: every ID that existed before still exists, printings still point at the same card, counters never decreased, and every slug change is explained by `slug_history`.

If any of those fail, the PR does not merge. There is deliberately no way to "fix up" a violation in place; revert and redo the change through the pipeline.

Every Wednesday (and on demand, with a tag), the `check-domain` workflow verifies the newest release root on the domain: the served export matches its digest, the index and sample objects answer, the `/v3/` alias redirects to it, CORS is open, an anonymous request is refused, and one served image of each resolution family is inspected. A failure is a hosting problem, not a data problem: nothing on `main` changes, and the run page says which step broke.

Every Monday (and on demand), the `drift` workflow fetches the official API and runs `python -m registry.sync --dry-run`. When the result is anything but "nothing to do" - attribute changes, new cards, an override that no longer matches, quarantined cases, or the adapter failing on a payload it cannot read - it opens one issue labelled `upstream-drift` (or comments on the open one) with the plan, and keeps the raw payload as a run artifact so you can reproduce the plan with `--from-file`. It never applies anything: the set-release runbook above is still how changes land. `Run workflow` with `force_report` files the report even when nothing changed, to test the plumbing.

## Style

Plain Python, standard library plus `requests`. Correctness beats cleverness: the sync logic is meant to be read and reviewed by strangers. If you add classification behaviour, add a test for it, especially anything touching rename matching.
