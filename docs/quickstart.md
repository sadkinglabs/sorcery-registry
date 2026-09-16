# Quickstart

A fast path to using the registry: what the ids mean, how to find the
newest release, what each endpoint answers, and three working examples.
For the full URL contract read [`docs/api.md`](api.md); for what you may
do with the data read [`docs/usage.md`](usage.md). The same material, with
a reference page per record type, is on the web at
[kairosarchive.net/docs](https://kairosarchive.net/docs).

## Two id spaces, one rule

- **`codex_id`** (`C000042`) - one card, across every reprint. Alpha,
  Beta and promo copies of Apprentice Wizard all share `C000001`.
  Gameplay data (`rules_text`, `cost`, `attack`, `defense`, `power`,
  `life`, thresholds, `keywords`, ...) lives here and applies to every
  printing of the card.
- **`printing_id`** (`P000042`) - one physical print: a specific set,
  product and finish. Physical facts (`set_code`, `product`, `finish`,
  `slug`, `artist`, `flavour_text`, `released_at`) live here.

Both are permanent: append-only, never reused, never changed. If your
feature cares which exact piece of cardboard it is, key on
`printing_id`; if any copy of the card would do, key on `codex_id`.

**Slugs are not ids.** The official API's `slug` (e.g. `004-witch-b-s`)
is a mutable lookup key - it has changed for entire sets before, and
carries no collector number. Use `slugs/{slug}.json` (or the MCP
`resolve_slug` tool) to turn any slug, current or historical, into its
permanent ids. Never store a slug as a foreign key.

## Finding the newest release

The domain publishes immutable release roots plus one discovery
document. To get today's data:

1. `GET https://api.kairosarchive.net/versions.json` - names the newest
   release of each major (`latest.v3`) and lists every release with its
   `tag`, `schema_version`, `released_at` and the `sha256` of that
   release's `registry.json`.
2. Read `latest.v3` (e.g. `"v3.2.0"`) and fetch that release's root:
   `https://api.kairosarchive.net/v3.2.0/registry.json`.
3. Fetch `https://api.kairosarchive.net/v3.2.0/registry.json.sha256`
   (an ~80-byte `sha256sum`-format file) and compare it against
   `versions.json`'s `sha256` for that release, or against the digest
   of the bytes you downloaded, before trusting them.

`versions.json` is cached 60 seconds at the edge; polling it hourly is
already more than the data changes (a few times a year).

### Pin a release vs. follow the alias

- **Pin** `https://api.kairosarchive.net/v3.2.0/…` and nothing you
  fetch under that root ever changes - cache it forever.
- **Follow** `https://api.kairosarchive.net/v3/…` when you want the
  newest data of a shape you already understand: it's a `302` to the
  same path under the newest *verified* v3.x root (a release is only
  listed, and the alias only moves, after the CDN has been proven to
  serve it byte for byte). A breaking change ships as a new major
  (`/v4/`) and its own alias, so `/v3/` never breaks in place.

## Per-object endpoints

All paths below are relative to a release root (`/v3.2.0/`) or the
major alias (`/v3/`) - both serve the same shapes.

| Path | Answers |
|---|---|
| `registry.json` | the full export - everything in one file |
| `registry.json.sha256` | that export's checksum, for freshness polling |
| `schema.json` | the export's JSON Schema (draft 2020-12) |
| `changes.json` | what this release changed against the previous one: counts, the ids behind them, and `identifiers_removed`, always 0 within a major |
| `cards/{codex_id}.json` | one card, plus its `printings` summary, `name_history` and `card_history` |
| `printings/{printing_id}.json` | one physical print, plus its `slug_history` |
| `slugs/{slug}.json` | what a slug (current or historical) resolves to |
| `sets.json` | the set catalogue |
| `sets/{set_code}.json` | one set and every card printed in it |
| `index/cards.json` | a compact list for client-side card search/filtering |
| `index/printings.json` | a compact list for client-side printing search |
| `index/slugs.json` | `{slug: printing_id}` for every slug that has ever existed |
| `history/slugs.json` | every slug ever issued, with its validity dates |
| `history/names.json` | every card name ever recorded |
| `history/cards.json` | every state of every card's gameplay face (the errata log) |

Every card, printing and set record carries its own `api_url` (the
moving `/v3/` alias - always leads to the current record) and
`kairos_url` (its page on `kairosarchive.net`).

No keys, no signup, no rate-limit headache for well-behaved clients -
see "User-Agent" below and [`docs/usage.md`](usage.md) for the rest.

## Images

Printings (and cards, via their `default_printing_id`) carry
`image_urls` and `image_status`. `image_status` is `"missing"` (no
image held, `image_urls` is `null`), `"lowres"` (held and served at
every size, but the publisher's source file was too small for the
`large` rendition and was upscaled), or `"ok"` (held at full size). A
double-faced printing's reverse face carries its own `image_urls` under
`back` (the status is the printing's).

`image_urls`, when not null, has four renditions, all WebP except
`original`: `small` 146×204, `normal` 488×680, `large` 672×936, and
`original` (the publisher's file, untouched, own format).

Image addresses are permanent - a name encodes the printing id and an
art-version key (`P000290.93dbd8484e16.normal.webp`), so cache them
forever and hotlink freely (with credit to Erik's Curiosa; see
[`docs/usage.md`](usage.md)). When you render a card, round the corners
proportionally: `border-radius: 4.75% / 3.5%` (Scryfall's own advice),
which lands on the physical card corner at any rendition size.

## The derived `power` field

Cards, `back` faces and every `card_history` row carry `power`, so
every consumer computes the same number: `power` equals `attack` when
`attack` equals `defense`, otherwise `floor((attack + defense) / 2)`;
`null` when either `attack` or `defense` is `null`. For example, Black
Knight (`attack: 5`, `defense: 3`) carries `power: 4`.

## The User-Agent requirement

Automated clients must send a `User-Agent` naming their project and a
way to reach you (e.g. `my-deck-tool/1.2 (contact@example.com)`).
**Requests with no `User-Agent` at all are refused at the edge** - this
is how the registry knows who is using it, not a security control.
Browsers send one automatically, so pages and hotlinked `<img>` tags
are unaffected. See [`docs/usage.md`](usage.md) for the rest of what's
asked of clients: fetch in bulk (`registry.json` or `index/`, not
object-by-object crawling), cache immutable roots forever, and poll
`versions.json` rather than the export itself.

## Example: curl

```sh
# 1. Discover the newest v3 release and its checksum.
curl -s -H 'User-Agent: my-deck-tool/1.0 (me@example.com)' \
  https://api.kairosarchive.net/versions.json
# => {"base_url": "...", "latest": {"v3": "v3.2.0"}, "releases": [...]}

# 2. Fetch that release's export and verify it against the checksum file.
curl -s -H 'User-Agent: my-deck-tool/1.0 (me@example.com)' \
  -o registry.json \
  https://api.kairosarchive.net/v3.2.0/registry.json
curl -s -H 'User-Agent: my-deck-tool/1.0 (me@example.com)' \
  https://api.kairosarchive.net/v3.2.0/registry.json.sha256
sha256sum registry.json   # compare against the line above

# 3. Look up one card, or resolve a slug you already have on file.
curl -s -H 'User-Agent: my-deck-tool/1.0 (me@example.com)' \
  https://api.kairosarchive.net/v3/cards/C000001.json
curl -s -H 'User-Agent: my-deck-tool/1.0 (me@example.com)' \
  https://api.kairosarchive.net/v3/slugs/004-witch-b-s.json
```

## Example: Python (stdlib only)

```python
import hashlib
import json
import urllib.request

BASE = "https://api.kairosarchive.net"
HEADERS = {"User-Agent": "my-deck-tool/1.0 (me@example.com)"}


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request) as response:
        return response.read()


# 1. Discover the newest v3 release.
versions = json.loads(fetch(f"{BASE}/versions.json"))
tag = versions["latest"]["v3"]
release = next(r for r in versions["releases"] if r["tag"] == tag)

# 2. Fetch its export and verify the digest before trusting it.
data = fetch(f"{BASE}/{tag}/registry.json")
digest = hashlib.sha256(data).hexdigest()
assert digest == release["sha256"], "checksum mismatch - re-fetch"
registry = json.loads(data)

print(tag, registry["header"]["cards"], "cards")
```

## Example: browser fetch()

```js
const BASE = "https://api.kairosarchive.net";
// Browsers set User-Agent automatically; no header to add here.

const versions = await (await fetch(`${BASE}/versions.json`)).json();
const tag = versions.latest.v3;

// Follow the alias when you don't need to pin a snapshot:
const card = await (await fetch(`${BASE}/v3/cards/C000001.json`)).json();
console.log(card.name, card.power, card.image_status);

if (card.image_urls) {
  const img = document.createElement("img");
  img.src = card.image_urls.normal;       // 488x680 WebP
  img.style.borderRadius = "4.75% / 3.5%"; // Scryfall's corner-radius trick
  document.body.appendChild(img);
}
```
