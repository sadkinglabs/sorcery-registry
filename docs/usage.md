# Using Kairos Archive

Kairos Archive (`kairosarchive.net`, `api.kairosarchive.net`) is a stable-identifier registry for *Sorcery: Contested Realm* cards and printings. This page says what you may do with it, what belongs to whom, and what we ask of automated clients. No keys, no signup, no terms to accept: the requests below are requests.

## What Kairos Archive created, and how it is licensed

Everything the registry adds on top of the official data is free for any use, including commercial use, without permission:

- the identifiers (`codex_id`, `printing_id`) and the guarantee that they never change or move;
- the record structure: the card/printing split, `sets`, `slug_history`, `name_history`, `card_history`, the derived fields (`default_printing_id`, `printed_as_current`, `errata`, `image_status`, the `api_url`/`kairos_url`/`image_urls` addresses), `versions.json`, the JSON Schema;
- the data corrections in `data/overrides.json` and their documented reasons;
- the code: the pipeline, the publisher, the MCP server, the website.

Identifiers, structure and derived data are released under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (public domain dedication); code under the [MIT licence](../LICENSE). Attribution is requested, not required: *"Identifiers and structure: Kairos Archive (kairosarchive.net)"*, with a link where a link fits.

## What belongs to Erik's Curiosa

Card names, rules text, typelines, flavour text, artist credits, set and product names, and every card image are © [Erik's Curiosa](https://sorcerytcg.com) and are used here with credit. The registry republishes the card data the official public API already serves, restructured for stability, and hosts card images itself as that API's own guidance asks (*"Host images yourself ... Download released card images from the public image folder"*, [api.sorcerytcg.com](https://api.sorcerytcg.com)). Both are displayed and served as part of the archive's function: keeping the record, matching printings to identifiers, and showing what an identifier refers to.

This page says nothing about commercial use of Erik's Curiosa's content, either way, because that is not ours to grant or to withhold. If your use of their names, text or images needs permission, the publisher is who can give it.

## Practical requests

- **Fetch in bulk, not object by object.** `registry.json` (one file, everything) or the `index/` lists are the right shape for a client that wants many records; the per-object files are for one lookup at a time. Please do not crawl the object tree.
- **Cache what you fetch.** Release roots (`/v3.1.0/…`) are immutable - cache them forever. To learn when a new release exists, poll `versions.json` (60-second cache; once an hour is plenty, the data changes a few times a year) and read the digest before re-downloading.
- **Hotlinking images is allowed**, with credit to Erik's Curiosa. The addresses in `image_urls` are permanent and immutable (new art gets a new address), so link to them directly rather than copying them.
- **Automated clients must send a `User-Agent`** that names the project and a way to reach you, for example `my-deck-tool/1.2 (contact@example.com)`. Requests with no `User-Agent` at all are blocked at the edge. This is not a security measure and does not pretend to be one: it is how we see who is here, so we can reach a client before a problem becomes a block, and thank the ones that name themselves. Browsers send one automatically, so pages and hotlinked images are unaffected.
- **The query API is for lookups, not for your backend.** `query.kairosarchive.net/cards?q=…` answers the site's search syntax for a bot, a script or a page doing lookups. It is not a search engine for another service to sit on: one address gets 60 requests a minute, then `429` for the rest of it, and a request without a `User-Agent` gets `403`. An application that answers searches should download `registry.json` (one file, a few times a year) and query it locally; the [search grammar](https://github.com/sadkinglabs/kairos-archive/tree/main/src/search) is open source.
- **Availability is best effort.** The domain is a CDN in front of object storage and has no maintenance windows, but it has no SLA either. Every release is mirrored on GitHub as a tagged release (`raw.githubusercontent.com/sadkinglabs/sorcery-registry/<tag>/export/registry.json`), and the registry's own guarantee - identifiers never change - means a copy you hold is never wrong, only older.
- **Rate limit:** 30 requests per 10 seconds per address on JSON objects, at the edge; a `429` clears after 10 seconds. Images are not limited. It is a tripwire against per-object crawling, not a quota: a client that fetches `registry.json` or the indexes never meets it, and one that looks up a card at a time never meets it either. What does meet it is resolving a whole deck by fetching each card's object in parallel from one server - fetch the index instead, it is one request. If you were blocked and believe you should not have been, open an issue with your `User-Agent` and the time.

## Links to sellers

Kairos Archive may link card printings to third-party sellers, including through affiliate programmes; such links are ordinary outbound links, disclosed on the site, and the registry data never carries seller information.

## Contact

Data errors: open an issue on [github.com/sadkinglabs/sorcery-registry](https://github.com/sadkinglabs/sorcery-registry) (see [CONTRIBUTING](../CONTRIBUTING.md) for what a good report looks like). Anything about the publisher's content: [Erik's Curiosa](https://sorcerytcg.com).
