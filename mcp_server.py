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
    """Rewrite all ids in a loaded export to the prefixed form, in place.
    A no-op on current exports; converts pre-C/P exports (bare integers)."""
    for card in data["cards"]:
        card["card_id"] = card_ref(card["card_id"])
        if "printing_ids" in card:
            card["printing_ids"] = [printing_ref(p) for p in card["printing_ids"]]
    for printing in data["printings"]:
        printing["printing_id"] = printing_ref(printing["printing_id"])
        printing["card_id"] = card_ref(printing["card_id"])
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
        self.cards = {c["card_id"]: c for c in data["cards"]}
        self.printings = {p["printing_id"]: p for p in data["printings"]}
        self.printings_by_card = {}
        for p in data["printings"]:
            self.printings_by_card.setdefault(p["card_id"], []).append(p)
        # Every slug that has ever existed resolves to its printing.
        # Current slugs are included in slug_history by construction, but
        # index them explicitly so a lookup never depends on that.
        self.slug_to_printing = {}
        for row in data["slug_history"]:
            self.slug_to_printing[row["slug"]] = row["printing_id"]
        for p in data["printings"]:
            self.slug_to_printing[p["slug"]] = p["printing_id"]

    # -- queries ----------------------------------------------------------

    def resolve_slug(self, slug):
        printing_id = self.slug_to_printing.get(slug)
        if printing_id is None:
            return {"found": False, "slug": slug,
                    "note": "This slug has never existed in the registry, "
                            "under any naming convention it has seen."}
        printing = self.printings[printing_id]
        card = self.cards[printing["card_id"]]
        return {
            "found": True,
            "printing_id": printing_id,
            "card_id": card["card_id"],
            "card_name": card["name"],
            "current_slug": printing["slug"],
            "queried_slug_is_current": printing["slug"] == slug,
            "set_code": printing["set_code"],
            "card_number": printing["card_number"],
            "product": printing["product"],
            "finish": printing["finish"],
            "retired_at": printing["retired_at"],
        }

    def get_card(self, card_id):
        card_id = card_ref(card_id)
        card = self.cards.get(card_id)
        if card is None:
            return {"found": False, "card_id": card_id}
        printings = [
            {k: p[k] for k in ("printing_id", "slug", "set_code", "set_name",
                               "card_number", "product", "finish", "artist",
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
        card = self.cards[printing["card_id"]]
        return {"found": True, **printing, "card_name": card["name"]}

    def search_cards(self, name=None, type=None, element=None, rarity=None,
                     set_code=None, limit=20):
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
            if set_code and not any(
                    p["set_code"] == set_code.lower()
                    for p in self.printings_by_card.get(card["card_id"], [])):
                continue
            results.append({k: card[k] for k in
                            ("card_id", "name", "type", "rarity", "elements",
                             "cost", "rules_text")})
        results.sort(key=lambda c: c["card_id"])
        return {"total_matches": len(results), "returned": min(len(results), limit),
                "cards": results[:limit]}

    def set_contents(self, set_code):
        entries = {}
        for p in self.printings.values():
            if p["set_code"] != set_code.lower():
                continue
            key = p["card_id"]
            entry = entries.setdefault(key, {
                "card_id": key,
                "name": self.cards[key]["name"],
                "card_number": p["card_number"],
                "printing_ids": [],
            })
            entry["printing_ids"].append(p["printing_id"])
            if p["card_number"] is not None and (
                    entry["card_number"] is None or p["card_number"] < entry["card_number"]):
                entry["card_number"] = p["card_number"]
        cards = sorted(entries.values(),
                       key=lambda e: (e["card_number"] is None, e["card_number"], e["name"]))
        return {"set_code": set_code.lower(), "distinct_cards": len(cards),
                "total_printings": sum(len(c["printing_ids"]) for c in cards),
                "cards": cards}

    def stats(self):
        sets = {}
        for p in self.printings.values():
            entry = sets.setdefault(p["set_code"], {
                "set_code": p["set_code"], "set_name": p["set_name"],
                "released_at": p["released_at"], "cards": set(), "printings": 0})
            entry["cards"].add(p["card_id"])
            entry["printings"] += 1
        set_list = [{**s, "cards": len(s["cards"])}
                    for s in sorted(sets.values(),
                                    key=lambda s: (s["released_at"] or "", s["set_code"]))]
        return {**self.header, "sets": set_list}


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
            "card_id (C000042) identifies a card across all its reprints (like a "
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
        printing_id and card_id. This is how data keyed on slugs survives naming
        convention changes: old slugs keep resolving forever."""
        return registry.resolve_slug(slug)

    def get_card(card_id: str) -> dict:
        """Fetch one card by its permanent card_id (e.g. 'C000042'; a bare
        number is accepted too), with its full gameplay data and every
        printing of it (all sets, products and finishes)."""
        return registry.get_card(card_id)

    def get_printing(printing_id: str) -> dict:
        """Fetch one printing by its permanent printing_id (e.g. 'P000042'; a
        bare number is accepted too): the exact physical print (set, product,
        finish) with its per-set data and current slug."""
        return registry.get_printing(printing_id)

    def search_cards(name: str = None, type: str = None, element: str = None,
                     rarity: str = None, set_code: str = None, limit: int = 20) -> dict:
        """Search cards. name is a case-insensitive substring; type (Minion,
        Magic, Site, Artifact, Aura, Avatar), element (Air, Earth, Fire, Water,
        None) and rarity (Ordinary, Elite, Exceptional, Unique) are exact;
        set_code restricts to cards printed in that set (e.g. 'alpha', 'beta',
        'gothic', 'arthurian_legends')."""
        return registry.search_cards(name, type, element, rarity, set_code, limit)

    def set_contents(set_code: str) -> dict:
        """List every distinct card in a set with its collector number and
        printing_ids. This is the authoritative answer to 'how many cards are
        in set X', which the official data does not state anywhere."""
        return registry.set_contents(set_code)

    def registry_stats() -> dict:
        """Registry totals and the list of known sets with per-set card and
        printing counts."""
        return registry.stats()

    for tool in (resolve_slug, get_card, get_printing, search_cards,
                 set_contents, registry_stats):
        mcp.tool()(tool)
    return mcp


if __name__ == "__main__":
    build_server().run()
