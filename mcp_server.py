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
    3. the published export on GitHub, cached for 24h in ~/.cache/sorcery-registry
       (a stale cache is used when offline rather than failing)
"""

import hashlib
import json
import os
import time
from pathlib import Path

EXPORT_URL = ("https://raw.githubusercontent.com/sadkinglabs/sorcery-registry"
              "/main/export/registry.json")
CACHE_PATH = Path.home() / ".cache" / "sorcery-registry" / "registry.json"
CACHE_TTL_SECONDS = 24 * 3600


# --------------------------------------------------------------------------
# Data loading and indexing (no MCP dependency; unit-tested directly)
# --------------------------------------------------------------------------

def load_registry():
    override = os.environ.get("SORCERY_REGISTRY_JSON")
    if override:
        if override.startswith(("http://", "https://")):
            return _fetch(override)
        return json.loads(Path(override).read_text(encoding="utf-8"))

    local = Path("export") / "registry.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))

    if CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if CACHE_PATH.exists() and _cache_still_current():
        # The published .sha256 matches our cached bytes: revalidated with
        # a ~100 byte fetch instead of re-downloading the whole export.
        os.utime(CACHE_PATH)
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    try:
        data = _fetch(EXPORT_URL)
    except Exception:
        if CACHE_PATH.exists():  # offline: stale beats nothing
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        raise
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _fetch(url):
    import requests
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def _cache_still_current():
    try:
        import requests
        response = requests.get(EXPORT_URL + ".sha256", timeout=10)
        response.raise_for_status()
        published = response.text.split()[0]
        cached = hashlib.sha256(CACHE_PATH.read_bytes()).hexdigest()
        return published == cached
    except Exception:
        return False


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
        card.setdefault("errata", (card.get("rules_text") or "").startswith("UPDATED"))
    for printing in data["printings"]:
        printing["printing_id"] = printing_ref(printing["printing_id"])
        printing["codex_id"] = card_ref(printing.pop("card_id", None) or printing["codex_id"])
        # Older exports called the slug's set number "card_number" (a
        # mislabel: the digits are the set's number, e.g. 006 = Gothic)
        # and carried a registry-invented "set_code".
        printing.pop("card_number", None)
        printing.pop("set_code", None)
        printing.setdefault("set_number", printing["slug"].split("-")[0])
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
            "set_number": printing["set_number"],
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
            {k: p[k] for k in ("printing_id", "slug", "set_name", "set_number",
                               "product", "finish", "artist", "retired_at")}
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
        """Match a printing against a set given as its official number
        ('006', '6', 6) or its name ('Gothic'), case-insensitively."""
        text = str(wanted).strip()
        if text.isdigit():
            return printing["set_number"] == text.zfill(3)
        return (printing["set_name"] or "").lower() == text.lower()

    def search_cards(self, name=None, type=None, element=None, rarity=None,
                     card_set=None, errata=None, limit=20):
        results = []
        name_lower = name.lower() if name else None
        for card in self.cards.values():
            if name_lower and name_lower not in card["name"].lower():
                continue
            if type and (card["type"] or "").lower() != type.lower():
                continue
            if element and element.lower() not in (card["elements"] or "").lower():
                continue
            if rarity and (card["rarity"] or "").lower() != rarity.lower():
                continue
            if errata is not None and card["errata"] != errata:
                continue
            if card_set and not any(
                    self._in_set(p, card_set)
                    for p in self.printings_by_card.get(card["codex_id"], [])):
                continue
            results.append({k: card[k] for k in
                            ("codex_id", "name", "type", "rarity", "elements",
                             "cost", "errata", "rules_text")})
        results.sort(key=lambda c: c["codex_id"])
        return {"total_matches": len(results), "returned": min(len(results), limit),
                "cards": results[:limit]}

    def set_contents(self, card_set):
        # Ordered by name: the official data has no collector numbers, so
        # there is no official ordering of cards within a set to follow.
        entries = {}
        set_name = set_number = None
        for p in self.printings.values():
            if not self._in_set(p, card_set):
                continue
            set_name, set_number = p["set_name"], p["set_number"]
            key = p["codex_id"]
            entry = entries.setdefault(key, {
                "codex_id": key,
                "name": self.cards[key]["name"],
                "printing_ids": [],
            })
            entry["printing_ids"].append(p["printing_id"])
        cards = sorted(entries.values(), key=lambda e: e["name"])
        return {"set_name": set_name, "set_number": set_number,
                "distinct_cards": len(cards),
                "total_printings": sum(len(c["printing_ids"]) for c in cards),
                "cards": cards}

    def search_printings(self, name=None, card_set=None, product=None,
                         finish=None, limit=50):
        # product matching tolerates spaces for underscores ("Box Topper"
        # finds Box_Topper); the values are the API's own product names.
        def norm(text):
            return str(text).strip().lower().replace(" ", "_")

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
                             "set_name", "set_number", "product", "finish",
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
            entry = sets.setdefault(p["set_number"], {
                "set_number": p["set_number"], "set_name": p["set_name"],
                "released_at": p["released_at"], "cards": set(), "printings": 0})
            entry["cards"].add(p["codex_id"])
            entry["printings"] += 1
            products[p["product"]] = products.get(p["product"], 0) + 1
        set_list = [{**s, "cards": len(s["cards"])}
                    for s in sorted(sets.values(),
                                    key=lambda s: (s["released_at"] or "", s["set_number"] or ""))]
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
            "for resolve_slug, never as an identifier. Set codes, collector "
            "numbers, and names are plain data columns, also unsafe as keys. "
            "Only Avatars have a life value; the registry corrects known upstream "
            "data errors, with every correction documented in the repo."
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
        finish) with its per-set data and current slug."""
        return registry.get_printing(printing_id)

    def search_cards(name: str = None, type: str = None, element: str = None,
                     rarity: str = None, card_set: str = None,
                     errata: bool = None, limit: int = 20) -> dict:
        """Search cards. name is a case-insensitive substring; type (Minion,
        Magic, Site, Artifact, Aura, Avatar), element (Air, Earth, Fire, Water,
        None) and rarity (Ordinary, Elite, Exceptional, Unique) are exact;
        card_set restricts to cards printed in a set, given as its official
        number ('006') or name ('Gothic'); errata=true finds cards whose rules
        text has been officially updated since printing."""
        return registry.search_cards(name, type, element, rarity, card_set,
                                     errata, limit)

    def set_contents(card_set: str) -> dict:
        """List every distinct card in a set with its printing_ids, ordered by
        name (the official data has no collector numbers, so cards have no
        official order within a set). The set is given as its official number
        ('006') or name ('Gothic'). This is the authoritative answer to 'how
        many cards are in set X', which the official data states nowhere."""
        return registry.set_contents(card_set)

    def search_printings(name: str = None, card_set: str = None,
                         product: str = None, finish: str = None,
                         limit: int = 50) -> dict:
        """Search physical printings. name is a case-insensitive substring of
        the card's name; card_set is a set's official number ('006') or name
        ('Gothic'); product is the official product line exactly as the API
        names it (Booster, Welcome_Kit, Organized_Play, Box_Topper, Dust,
        Preconstructed_Deck, Draft_Kit, Kickstarter, Alpha_Investments,
        Team_Covenant, Star_City_Games - spaces work too, e.g. 'Box Topper');
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
