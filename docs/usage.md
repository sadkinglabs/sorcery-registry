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
- **Identify automated clients** with a `User-Agent` that names your project and a way to reach you.
- **Availability is best effort.** The domain is a CDN in front of object storage and has no maintenance windows, but it has no SLA either. Every release is mirrored on GitHub as a tagged release (`raw.githubusercontent.com/sadkinglabs/sorcery-registry/<tag>/export/registry.json`), and the registry's own guarantee - identifiers never change - means a copy you hold is never wrong, only older.
- **Rate limits** exist only as a tripwire against runaway crawlers (a generous per-address limit at the edge). A well-behaved client will never meet it.

## Links to sellers

Kairos Archive may link card printings to third-party sellers, including through affiliate programmes; such links are ordinary outbound links, disclosed on the site, and the registry data never carries seller information.

## Contact

Data errors: open an issue on [github.com/sadkinglabs/sorcery-registry](https://github.com/sadkinglabs/sorcery-registry) (see [CONTRIBUTING](../CONTRIBUTING.md) for what a good report looks like). Anything about the publisher's content: [Erik's Curiosa](https://sorcerytcg.com).
