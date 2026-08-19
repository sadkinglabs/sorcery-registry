"""Fetch the official API and flatten it into a snapshot.

The API is card-grained: each entry carries a card-level `guardian` block,
then per-set metadata copies, then per-variant slugs. We flatten that to
two dictionaries mirroring the registry tables:

    snapshot["cards"][card_name]  -> card-level fields (from guardian)
    snapshot["printings"][slug]   -> one record per variant slug

Data corrections from data/overrides.json are applied here, after
canonicalisation and before anything is diffed or stored, so the registry
holds the corrected truth and the corrections themselves live in git.
"""

import json

from . import API_URL
from .canon import canon_text, parse_slug, released_date, set_code

CARD_NUMERIC = ["cost", "attack", "defence", "life"]
THRESHOLD_KEYS = [("thr_air", "air"), ("thr_earth", "earth"),
                  ("thr_fire", "fire"), ("thr_water", "water")]


def fetch_api(url=API_URL):
    import requests
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.json()


def load_api_file(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_overrides(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _metadata_fields(metadata):
    """Shared shape of the guardian block and per-set metadata blocks."""
    fields = {
        "type": canon_text(metadata.get("type")),
        "rarity": canon_text(metadata.get("rarity")),
        "rules_text": canon_text(metadata.get("rulesText")) or "",
    }
    for key in CARD_NUMERIC:
        fields[key] = metadata.get(key)
    thresholds = metadata.get("thresholds") or {}
    for column, key in THRESHOLD_KEYS:
        fields[column] = thresholds.get(key) or 0
    return fields


def build_snapshot(raw_cards):
    """Flatten the raw API list. Fails loudly on duplicate card names or
    duplicate slugs: both would undermine identity matching, so a run must
    stop rather than pick a winner silently."""
    cards = {}
    printings = {}
    for entry in raw_cards:
        name = canon_text(entry["name"])
        if name in cards:
            raise ValueError(f"duplicate card name in API data: {name!r}")
        card = {"name": name}
        card.update(_metadata_fields(entry["guardian"]))
        card["subtypes"] = canon_text(entry.get("subTypes"))
        card["elements"] = canon_text(entry.get("elements"))
        cards[name] = card

        for set_entry in entry["sets"]:
            set_name = canon_text(set_entry["name"])
            set_meta = _metadata_fields(set_entry["metadata"])
            for variant in set_entry["variants"]:
                slug = variant["slug"]
                if slug in printings:
                    raise ValueError(f"duplicate slug in API data: {slug!r}")
                card_number, _, _, _ = parse_slug(slug)
                printing = {
                    "card_name": name,
                    "set_code": set_code(set_name),
                    "set_name": set_name,
                    "released_at": released_date(set_entry.get("releasedAt")),
                    "card_number": card_number,
                    "product": canon_text(variant.get("product")),
                    "finish": canon_text(variant.get("finish")),
                    "slug": slug,
                    "artist": canon_text(variant.get("artist")),
                    "flavour_text": canon_text(variant.get("flavorText")),
                    "type_text": canon_text(variant.get("typeText")),
                    "image_hash": None,
                }
                printing.update(set_meta)
                printings[slug] = printing
    return {"cards": cards, "printings": printings}


def apply_overrides(snapshot, overrides):
    """Apply data corrections to the snapshot in place.

    Each override entry:
        match.card_name  required, the card to correct
        match.set_name   optional, restricts the fix to that set's printings
        set_fields       column -> corrected value
        reason           required free text, the audit trail

    Without set_name the fields are applied to the card record and every
    printing of it; with set_name, only to printings from that set.
    Returns the list of entries that matched nothing, so the sync report
    can flag corrections the upstream has since fixed.
    """
    unmatched = []
    for entry in overrides:
        if not entry.get("reason"):
            raise ValueError(f"override without a reason: {entry!r}")
        card_name = canon_text(entry["match"]["card_name"])
        set_name = canon_text(entry["match"].get("set_name"))
        fields = entry["set_fields"]
        hit = False
        if set_name is None and card_name in snapshot["cards"]:
            snapshot["cards"][card_name].update(fields)
            hit = True
        for printing in snapshot["printings"].values():
            if printing["card_name"] != card_name:
                continue
            if set_name is not None and printing["set_name"] != set_name:
                continue
            printing.update(fields)
            hit = True
        if not hit:
            unmatched.append(entry)
    return unmatched
