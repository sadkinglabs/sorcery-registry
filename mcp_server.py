# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp", "requests"]
# ///
"""MCP server for the Sorcery Card Registry.

Gives AI agents direct, always-current access to the registry's stable
identifiers without loading the full 3MB export into context. Read-only:
it serves the published export/registry.json, nothing more.

Run it locally (each user runs their own copy; there is no hosted service):

    uv run mcp_server.py                       # from a checkout
    uv run https://raw.githubusercontent.com/sadkinglabs/sorcery-registry/main/mcp_server.py
    pip install mcp requests && python mcp_server.py

Data source, first match wins:
    1. $SORCERY_REGISTRY_JSON - a local path or URL to a registry.json
    2. ./export/registry.json - when run from a repo checkout
    3. the newest verified release on api.kairosarchive.net: versions.json
       names it (latest.v3) with the digest of its registry.json, which is
       fetched from that release's immutable root. If the domain is
       unreachable, the same release (or, without versions.json, the
       newest GitHub release) is read from raw.githubusercontent.com -
       always a tagged release, never main. Cached in
       ~/.cache/sorcery-registry: trusted for 24h, then revalidated
       against the published digest (one small request) and only
       re-downloaded when it changed; a stale cache beats nothing when
       offline. Downloaded bytes are verified against the digest before
       they are cached.
"""

import hashlib
import json
import os
import time
from pathlib import Path

REPO = "sadkinglabs/sorcery-registry"
REGISTRY_BASE = "https://api.kairosarchive.net"
VERSIONS_URL = f"{REGISTRY_BASE}/versions.json"
MAJOR = "v3"  # the export shape this server understands
LATEST_RELEASE_URL = f"https://github.com/{REPO}/releases/latest"
RAW_EXPORT_URL = f"https://raw.githubusercontent.com/{REPO}/{{tag}}/export/registry.json"
CACHE_PATH = Path.home() / ".cache" / "sorcery-registry" / "registry.json"
CACHE_TTL_SECONDS = 24 * 3600


# --------------------------------------------------------------------------
# Data loading and indexing (no MCP dependency; unit-tested directly)
# --------------------------------------------------------------------------

def load_registry():
    override = os.environ.get("SORCERY_REGISTRY_JSON")
    if override:
        if override.startswith(("http://", "https://")):
            return json.loads(_fetch_bytes(override))
        return json.loads(Path(override).read_text(encoding="utf-8"))

    local = Path("export") / "registry.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))

    return json.loads(load_published(CACHE_PATH))


def load_published(cache_path=CACHE_PATH):
    """Return the bytes of the newest released export: from the cache when
    it is fresh or still matches the published digest, otherwise from the
    first source that serves bytes matching that digest. The cache holds
    the served bytes verbatim - never a re-serialisation - so its SHA-256
    is comparable with what the registry publishes."""
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_SECONDS:
        return cache_path.read_bytes()
    cached = hashlib.sha256(cache_path.read_bytes()).hexdigest() if cache_path.exists() else None
    failures = []
    for url, digest in _sources(failures):
        try:
            if digest is None:  # GitHub only publishes a checksum file
                digest = _published_digest(url)
            if cached is not None and cached == digest:
                # Revalidated with a small request instead of a download.
                os.utime(cache_path)
                return cache_path.read_bytes()
            data = _fetch_bytes(url)
            actual = hashlib.sha256(data).hexdigest()
            if actual != digest:
                raise RuntimeError(f"{url} served digest {actual}, expected {digest}")
        except Exception as error:  # try the next source
            failures.append(f"{url}: {error}")
            continue
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        return data
    if cache_path.exists():  # offline: stale beats nothing
        return cache_path.read_bytes()
    raise RuntimeError("no source served the registry export:\n  " + "\n  ".join(failures))


def _sources(failures):
    """(url, digest-or-None) for the newest release, most authoritative
    first: the domain's verified release root, then the same tag on
    GitHub; without versions.json, GitHub's newest release. Lazy, so a
    source that answers is the only one contacted; resolution failures
    are appended to `failures` for the final error message."""
    tag = None
    try:
        tag, base_url, digest = resolve_latest()
        yield f"{base_url}/{tag}/registry.json", digest
    except Exception as error:
        failures.append(f"{VERSIONS_URL}: {error}")
    if tag is None:
        try:
            tag = latest_release_tag()
        except Exception as error:
            failures.append(f"{LATEST_RELEASE_URL}: {error}")
            return
    yield export_url(tag), None


def resolve_latest(url=VERSIONS_URL, major=MAJOR):
    """(tag, base_url, sha256) of the newest verified release of `major`,
    from the domain's discovery document."""
    response = _get(url, timeout=10)
    response.raise_for_status()
    doc = response.json()
    tag = doc["latest"][major]
    release = next(r for r in doc["releases"] if r["tag"] == tag)
    return tag, doc["base_url"].rstrip("/"), release["sha256"]


def latest_release_tag():
    """The tag of the newest GitHub release, read from the redirect that
    github.com/<repo>/releases/latest answers with (no API, no token, and
    cacheable by any proxy). Raises when there is no release to point at."""
    response = _get(LATEST_RELEASE_URL, timeout=10, allow_redirects=False)
    location = response.headers.get("Location", "")
    marker = "/releases/tag/"
    if marker not in location:
        raise RuntimeError(f"no release found behind {LATEST_RELEASE_URL} "
                           f"(status {response.status_code}, location {location!r})")
    return location.rstrip("/").rsplit(marker, 1)[1]


def export_url(tag):
    return RAW_EXPORT_URL.format(tag=tag)


USER_AGENT = "sorcery-registry-mcp (+https://kairosarchive.net; github.com/sadkinglabs/sorcery-registry)"


def _get(url, **kwargs):
    """The one place HTTP happens, so tests can replace it. Every request
    identifies this server, as the registry's own usage terms require of
    automated clients."""
    import requests
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    return requests.get(url, headers=headers, **kwargs)


def _fetch_bytes(url):
    response = _get(url, timeout=60)
    response.raise_for_status()
    return response.content


def _published_digest(url):
    """The sha256sum-format checksum file published next to an export."""
    response = _get(url + ".sha256", timeout=10)
    response.raise_for_status()
    return response.text.split()[0]


ID_WIDTH = 6


def card_ref(value):
    """Normalise any card-id spelling ('C000042', '000042', 42) to C000042."""
    return f"C{_digits(value):0{ID_WIDTH}d}"


def printing_ref(value):
    """Normalise any printing-id spelling ('P000042', '000042', 42) to P000042."""
    return f"P{_digits(value):0{ID_WIDTH}d}"


def _digits(value):
    if isinstance(value, int):
        return value
    text = str(value)
    if text[:1].upper() in ("C", "P"):
        text = text[1:]
    return int(text)


def _normalise_ids(data):
    """Rewrite all ids in a loaded export to the current published form, in
    place. A no-op on current exports; converts older ones (bare-integer ids,
    and the card-level id under its former name card_id)."""
    for card in data["cards"]:
        card["codex_id"] = card_ref(card.pop("card_id", None) or card["codex_id"])
        if "printing_ids" in card:
            card["printing_ids"] = [printing_ref(p) for p in card["printing_ids"]]
        # Exports before v7 carried subtypes and elements as comma-joined
        # strings.
        for field in ("subtypes", "elements"):
            if isinstance(card.get(field), str):
                card[field] = [v.strip() for v in card[field].split(",") if v.strip()]
    for printing in data["printings"]:
        printing["printing_id"] = printing_ref(printing["printing_id"])
        printing["codex_id"] = card_ref(printing.pop("card_id", None) or printing["codex_id"])
        # Older exports called the set code "set_number" or "card_number".
        printing.pop("card_number", None)
        legacy = printing.pop("set_number", None)
        printing.setdefault("set_code", legacy or printing["slug"].split("-")[0])
        if "typeline" not in printing:
            printing["typeline"] = printing.pop("type_text", None)
    names = {c["codex_id"]: c["name"] for c in data["cards"]}
    for printing in data["printings"]:
        printing.setdefault("card_name", names.get(printing["codex_id"]))
    for row in data["slug_history"]:
        row["printing_id"] = printing_ref(row["printing_id"])
    return data


class Registry:
    """Indexed view over the export. All lookups are O(1) dict hits.

    Ids are handled in their published prefixed form (C000042 / P000042).
    Loaded data is normalised first, so exports predating that form (bare
    integers) still work."""

    def __init__(self, data):
        data = _normalise_ids(data)
        self.header = data["header"]
        self.cards = {c["codex_id"]: c for c in data["cards"]}
        self.printings = {p["printing_id"]: p for p in data["printings"]}
        self.printings_by_card = {}
        for p in data["printings"]:
            self.printings_by_card.setdefault(p["codex_id"], []).append(p)
        # Every slug that has ever existed resolves to its printing.
        # Current slugs are included in slug_history by construction, but
        # index them explicitly so a lookup never depends on that.
        # A slug belongs permanently to one printing; if the loaded data
        # ever says otherwise it is corrupt, so record the conflict and
        # refuse to serve that slug rather than let the last row win.
        self.slug_to_printing = {}
        self.conflicted_slugs = set()
        pairs = ([(row["slug"], row["printing_id"]) for row in data["slug_history"]]
                 + [(p["slug"], p["printing_id"]) for p in data["printings"]])
        for slug, printing_id in pairs:
            known = self.slug_to_printing.get(slug)
            if known is not None and known != printing_id:
                self.conflicted_slugs.add(slug)
                continue
            self.slug_to_printing[slug] = printing_id

    # -- queries ----------------------------------------------------------

    def resolve_slug(self, slug):
        if slug in self.conflicted_slugs:
            return {"found": False, "slug": slug,
                    "error": "registry invariant violated: this slug maps to "
                             "more than one printing in the loaded data; "
                             "refusing to guess"}
        printing_id = self.slug_to_printing.get(slug)
        if printing_id is None:
            return {"found": False, "slug": slug,
                    "note": "This slug has never existed in the registry, "
                            "under any naming convention it has seen."}
        printing = self.printings[printing_id]
        card = self.cards[printing["codex_id"]]
        return {
            "found": True,
            "printing_id": printing_id,
            "codex_id": card["codex_id"],
            "card_name": card["name"],
            "current_slug": printing["slug"],
            "queried_slug_is_current": printing["slug"] == slug,
            "set_name": printing["set_name"],
            "set_code": printing["set_code"],
            "product": printing["product"],
            "finish": printing["finish"],
            "retired_at": printing["retired_at"],
        }

    def get_card(self, card_id):
        card_id = card_ref(card_id)
        card = self.cards.get(card_id)
        if card is None:
            return {"found": False, "codex_id": card_id}
        printings = [
            {k: p.get(k) for k in ("printing_id", "slug", "set_name", "set_code",
                                   "released_at", "product", "finish", "artist",
                                   "retired_at")}
            for p in sorted(self.printings_by_card.get(card_id, []),
                            key=lambda p: p["printing_id"])
        ]
        return {"found": True, **card, "printings": printings}

    def get_printing(self, printing_id):
        printing_id = printing_ref(printing_id)
        printing = self.printings.get(printing_id)
        if printing is None:
            return {"found": False, "printing_id": printing_id}
        card = self.cards[printing["codex_id"]]
        return {"found": True, **printing, "card_name": card["name"]}

    @staticmethod
    def _in_set(printing, wanted):
        """Match a printing against a set given as its official code
        ('006', '6', 6) or its name ('Gothic'), case-insensitively."""
        text = str(wanted).strip()
        if text.isdigit():
            return printing["set_code"] == text.zfill(3)
        return (printing["set_name"] or "").lower() == text.lower()

    def search_cards(self, name=None, type=None, element=None, rarity=None,
                     card_set=None, keyword=None, category=None, errata=None,
                     limit=20):
        def has(values, wanted):
            return wanted.lower() in [v.lower() for v in (values or [])]

        results = []
        name_lower = name.lower() if name else None
        for card in self.cards.values():
            if name_lower and name_lower not in card["name"].lower():
                continue
            if type and (card["type"] or "").lower() != type.lower():
                continue
            if category and (card.get("category") or "").lower() != category.lower():
                continue
            if element and not has(card["elements"], element):
                continue
            if rarity and (card["rarity"] or "").lower() != rarity.lower():
                continue
            if keyword and not has(card.get("keywords"), keyword):
                continue
            if errata is not None and bool(card.get("errata")) != errata:
                continue
            if card_set and not any(
                    self._in_set(p, card_set)
                    for p in self.printings_by_card.get(card["codex_id"], [])):
                continue
            results.append({k: card.get(k) for k in
                            ("codex_id", "name", "type", "category", "rarity",
                             "elements", "keywords", "cost", "attack", "defense",
                             "power", "errata", "rules_text")})
        results.sort(key=lambda c: c["codex_id"])
        return {"total_matches": len(results), "returned": min(len(results), limit),
                "cards": results[:limit]}

    def set_contents(self, card_set):
        # Ordered by name: the official data has no collector numbers, so
        # there is no official ordering of cards within a set to follow.
        entries = {}
        set_name = set_code = None
        for p in self.printings.values():
            if not self._in_set(p, card_set):
                continue
            set_name, set_code = p["set_name"], p["set_code"]
            key = p["codex_id"]
            entry = entries.setdefault(key, {
                "codex_id": key,
                "name": self.cards[key]["name"],
                "printing_ids": [],
            })
            entry["printing_ids"].append(p["printing_id"])
        cards = sorted(entries.values(), key=lambda e: e["name"])
        return {"set_name": set_name, "set_code": set_code,
                "distinct_cards": len(cards),
                "total_printings": sum(len(c["printing_ids"]) for c in cards),
                "cards": cards}

    def search_printings(self, name=None, card_set=None, product=None,
                         finish=None, limit=50):
        # product matching ignores case, spaces and underscores, so "Box
        # Topper", "box_topper" and the official BoxTopper all match.
        def norm(text):
            return str(text).strip().lower().replace(" ", "").replace("_", "")

        results = []
        name_lower = name.lower() if name else None
        for p in self.printings.values():
            if name_lower and name_lower not in (p["card_name"] or "").lower():
                continue
            if card_set and not self._in_set(p, card_set):
                continue
            if product and norm(p["product"] or "") != norm(product):
                continue
            if finish and (p["finish"] or "").lower() != finish.lower():
                continue
            results.append({k: p[k] for k in
                            ("printing_id", "codex_id", "card_name", "slug",
                             "set_name", "set_code", "product", "finish",
                             "retired_at")})
        results.sort(key=lambda p: p["printing_id"])
        return {"total_matches": len(results),
                "distinct_cards": len({p["codex_id"] for p in results}),
                "returned": min(len(results), limit),
                "printings": results[:limit]}

    def stats(self):
        sets = {}
        products = {}
        for p in self.printings.values():
            entry = sets.setdefault(p["set_code"], {
                "set_code": p["set_code"], "set_name": p["set_name"],
                "released_at": p["released_at"], "cards": set(), "printings": 0})
            if p["released_at"] and (entry["released_at"] is None
                                     or p["released_at"] < entry["released_at"]):
                entry["released_at"] = p["released_at"]
            entry["cards"].add(p["codex_id"])
            entry["printings"] += 1
            products[p["product"]] = products.get(p["product"], 0) + 1
        set_list = [{**s, "cards": len(s["cards"])}
                    for s in sorted(sets.values(),
                                    key=lambda s: (s["released_at"] or "", s["set_code"] or ""))]
        product_list = [{"product": name, "printings": count}
                        for name, count in sorted(products.items(),
                                                  key=lambda kv: -kv[1])]
        return {**self.header, "sets": set_list, "products": product_list}


# --------------------------------------------------------------------------
# MCP wiring
# --------------------------------------------------------------------------

def build_server():
    try:  # mcp >= 2.0
        from mcp.server.mcpserver import MCPServer
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer

    mcp = MCPServer(
        "sorcery-registry",
        instructions=(
            "Stable identifiers for Sorcery: Contested Realm cards. "
            "codex_id (C000042) identifies a card across all its reprints (like a "
            "Scryfall oracle id); printing_id (P000042) identifies one physical "
            "print (set + product + finish). The C/P prefix names the id space, "
            "the digits are zero-padded to six, and these ids never change - "
            "they are the only safe keys to "
            "store. The official API slug (e.g. 004-witch-b-s) is mutable and has "
            "changed for entire sets in the past: treat any slug as a lookup input "
            "for resolve_slug, never as an identifier. Set codes, product names "
            "and card names are plain data columns, also unsafe as keys; so are "
            "the ids upstream itself serves, which are regenerated on re-import. "
            "Gameplay data (rules_text, stats, keywords) lives on the card and "
            "applies to every printing; a printing carries physical facts only. "
            "Only Avatars have a life value; the registry corrects known upstream "
            "data errors, with every correction documented in the repo. A card "
            "whose text or stats changed since printing carries errata=true and a "
            "closed row in the export's card_history; default_printing_id names "
            "its representative printing and each printing's printed_as_current "
            "says whether its printed values match the card's current face."
        ),
    )
    registry = Registry(load_registry())

    def resolve_slug(slug: str) -> dict:
        """Resolve any official-API slug, current or historical, to its permanent
        printing_id and codex_id. This is how data keyed on slugs survives naming
        convention changes: old slugs keep resolving forever."""
        return registry.resolve_slug(slug)

    def get_card(codex_id: str) -> dict:
        """Fetch one card by its permanent codex_id (e.g. 'C000042'; a bare
        number is accepted too), with its full gameplay data and every
        printing of it (all sets, products and finishes)."""
        return registry.get_card(codex_id)

    def get_printing(printing_id: str) -> dict:
        """Fetch one printing by its permanent printing_id (e.g. 'P000042'; a
        bare number is accepted too): the exact physical print (set, product,
        finish) with its physical facts and current slug."""
        return registry.get_printing(printing_id)

    def search_cards(name: str = None, type: str = None, element: str = None,
                     rarity: str = None, card_set: str = None,
                     keyword: str = None, category: str = None,
                     errata: bool = None, limit: int = 20) -> dict:
        """Search cards. name is a case-insensitive substring; type (Minion,
        Magic, Site, Artifact, Aura, Avatar), category (Spell, Site, Avatar,
        Token), rarity (Ordinary, Elite, Exceptional, Unique), element (Air,
        Earth, Fire, Water, None - a card with several elements matches each)
        and keyword (Airborne, Genesis, Spellcaster, Submerge, ...) are exact;
        card_set restricts to cards printed in a set, given as its official
        code ('006') or name ('Gothic'); errata=true finds cards whose text
        or stats have been updated since they were printed (the registry
        tracks this itself; card_history in the export has every state)."""
        return registry.search_cards(name, type, element, rarity, card_set,
                                     keyword, category, errata, limit)

    def set_contents(card_set: str) -> dict:
        """List every distinct card in a set with its printing_ids, ordered by
        name (the official data has no collector numbers, so cards have no
        official order within a set). The set is given as its official code
        ('006') or name ('Gothic'). This is the authoritative answer to 'how
        many cards are in set X', which the official data states nowhere."""
        return registry.set_contents(card_set)

    def search_printings(name: str = None, card_set: str = None,
                         product: str = None, finish: str = None,
                         limit: int = 50) -> dict:
        """Search physical printings. name is a case-insensitive substring of
        the card's name; card_set is a set's official code ('006') or name
        ('Gothic'); product is the official product line as the API names it
        (Booster, BoxTopper, Dust, OrganizedPlay, PreconstructedDeck, DraftKit,
        AlphaInvestments, WelcomeKit, Kickstarter, TeamCovenant, StarCityGames
        - case, spaces and underscores are ignored, so 'box topper' works);
        finish is Standard, Foil or Rainbow. Answers questions like 'what is
        in the Arthurian Legends box topper' or 'which cards are sold as
        Dust' in one call."""
        return registry.search_printings(name, card_set, product, finish, limit)

    def registry_stats() -> dict:
        """Registry totals, the list of known sets with per-set card and
        printing counts, and the list of product lines with printing counts."""
        return registry.stats()

    for tool in (resolve_slug, get_card, get_printing, search_cards, search_printings,
                 set_contents, registry_stats):
        mcp.tool()(tool)
    return mcp


if __name__ == "__main__":
    build_server().run()
