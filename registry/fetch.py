"""Fetch the official API and flatten it into a snapshot.

The API is card-grained: each entry carries a card-level `engine` block
(the gameplay data, with an optional `back` face for double-faced cards)
and a flat `printings` list, each printing naming its set and carrying a
`meta` block of physical facts. We flatten that to two dictionaries
mirroring the registry tables:

    snapshot["cards"][card_name]  -> card-level fields (from engine)
    snapshot["printings"][slug]   -> one record per printing

Upstream also serves an `id` on every card and printing. Those are the
backend's own minting ids, regenerated whenever the data is re-imported
(confirmed by its developers), so they are neither stored nor matched on.

Data corrections from data/overrides.json are applied here, after
canonicalisation and before anything is diffed or stored, so the registry
holds the corrected truth and the corrections themselves live in git.
"""

import json

from . import API_URL
from .canon import canon_text, released_date
from .db import CARD_FIELDS, FACE_FIELDS, PRINTING_FIELDS

CARD_NUMERIC = ["cost", "attack", "defense", "life"]
THRESHOLD_KEYS = [("thr_air", "air"), ("thr_earth", "earth"),
                  ("thr_fire", "fire"), ("thr_water", "water")]
LIST_KEYS = [("subtypes", "subtypes"), ("elements", "elements"),
             ("keywords", "keywords"), ("umbrellas", "umbrellas")]


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


def _canon_list(values):
    """Lists are kept in upstream's own order: the API's order is the
    canonical one, and a reordering upstream is a data change like any
    other."""
    return [canon_text(value) for value in (values or [])]


def _face_fields(engine):
    """Gameplay fields of one face: the engine block, or its `back`."""
    fields = {
        "type": canon_text(engine.get("type")),
        "category": canon_text(engine.get("category")),
        "rarity": canon_text(engine.get("rarity")),
        "slot": canon_text(engine.get("slot")),
    }
    for column, key in LIST_KEYS:
        fields[column] = _canon_list(engine.get(key))
    for key in CARD_NUMERIC:
        fields[key] = engine.get(key)
    for column, key in THRESHOLD_KEYS:
        fields[column] = engine.get(key) or 0
    fields["rules_text"] = canon_text(engine.get("rules")) or ""
    return {field: fields[field] for field in FACE_FIELDS}


def _printing_face_fields(meta):
    artist = meta.get("artist") or {}
    return {
        "artist": canon_text(artist.get("name")),
        "artist_slug": canon_text(artist.get("slug")),
        "flavour_text": canon_text(meta.get("flavor")),
        "typeline": canon_text(meta.get("typeline")),
    }


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
        engine = entry["engine"]
        card = {"name": name}
        card.update(_face_fields(engine))
        card["back"] = _face_fields(engine["back"]) if engine.get("back") else None
        cards[name] = card

        for upstream in entry["printings"]:
            slug = upstream["slug"]
            if slug in printings:
                raise ValueError(f"duplicate slug in API data: {slug!r}")
            set_entry = upstream["set"]
            meta = upstream["meta"]
            # set.releasedAt is the timestamp the set row was created in the
            # upstream database, not a release date; printedAt is the date
            # this printing reached the public, so that is the one kept.
            printing = {
                "card_name": name,
                "set_name": canon_text(set_entry["name"]),
                "set_code": canon_text(set_entry.get("code")),
                "released_at": released_date(upstream.get("printedAt")),
                "product": canon_text(meta.get("product")),
                "finish": canon_text(meta.get("finish")),
                "slug": slug,
                "image_hash": None,
            }
            printing.update(_printing_face_fields(meta))
            back = meta.get("back")
            printing["back"] = _printing_face_fields(back) if back else None
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
    printing of it; with set_name, only to printings from that set. A field
    is only ever written where the record has that column (life is a card
    fact, artist a printing fact), so one entry can name fields of either.
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
        card_fields = {k: v for k, v in fields.items() if k in CARD_FIELDS}
        printing_fields = {k: v for k, v in fields.items() if k in PRINTING_FIELDS}
        hit = False
        if set_name is None and card_fields and card_name in snapshot["cards"]:
            snapshot["cards"][card_name].update(card_fields)
            hit = True
        for printing in snapshot["printings"].values():
            if printing["card_name"] != card_name or not printing_fields:
                continue
            if set_name is not None and printing["set_name"] != set_name:
                continue
            printing.update(printing_fields)
            hit = True
        if not hit:
            unmatched.append(entry)
    return unmatched
