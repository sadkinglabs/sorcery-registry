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

Every payload is checked against the publisher's published contract
(schema/upstream-cards.schema.json, their CardAPIDTO) before it is read:
a field the adapter depends on going missing or changing type stops the
sync with the path of the offending element, rather than being flattened
into wrong data. New fields and new vocabulary values pass through.

Conduct toward the publisher: their API is rate limited (30 requests a
minute on /api/cards) and their guidance is to poll intermittently and
host the data yourself, which is exactly what this registry is. Every
request identifies itself with USER_AGENT; nothing in this repository
loops over upstream - a sync is one request, the weekly drift check is
one request.
"""

import json
from typing import NamedTuple

from . import API_URL, SCHEMA_VERSION, SITE_BASE
from .canon import canon_text, released_date
from .contract import check_contract
from .db import CARD_FIELDS, CARD_OWNED_FIELDS, FACE_FIELDS, PRINTING_FIELDS

USER_AGENT = (f"sorcery-registry/schema{SCHEMA_VERSION} (+{SITE_BASE}; "
              f"https://github.com/sadkinglabs/sorcery-registry)")

CARD_NUMERIC = ["cost", "attack", "defense", "life"]
THRESHOLD_KEYS = [("thr_air", "air"), ("thr_earth", "earth"),
                  ("thr_fire", "fire"), ("thr_water", "water")]
LIST_KEYS = [("subtypes", "subtypes"), ("elements", "elements"),
             ("keywords", "keywords"), ("umbrellas", "umbrellas")]


def _get(url, **kwargs):
    """The one place HTTP happens, so tests can replace it."""
    import requests
    return requests.get(url, **kwargs)


def fetch_api(url=API_URL):
    response = _get(url, timeout=120, headers={"User-Agent": USER_AGENT,
                                                "Accept": "application/json"})
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
    check_contract(raw_cards)
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


class Applied(NamedTuple):
    """What applying the overrides left to say: the entries that matched
    nothing, and, per card, the fields corrected retroactively."""
    unmatched: list
    retroactive: dict


def apply_overrides(snapshot, overrides):
    """Apply data corrections to the snapshot in place.

    Each override entry:
        match.card_name  required, the card to correct
        match.set_name   optional, restricts the fix to that set's printings
        set_fields       column -> corrected value; "back.<field>" corrects
                         one field of the back face and leaves the rest of
                         that face as upstream serves it
        retroactive      optional: the card always had this value and the
                         registry's record was wrong, so the correction is
                         written to every history row instead of opening one
                         (see sync.apply_plan). Without it a correction reads
                         as a change the card underwent, which is right when
                         the card did change and upstream has it wrong, and
                         wrong when only our record ever said otherwise.
        reason           required free text, the audit trail

    Without set_name the fields are applied to the card record and every
    printing of it; with set_name, only to printings from that set. A field
    is only ever written where the record has that column (life is a card
    fact, artist a printing fact), so one entry can name fields of either.
    A field name the registry does not have is an error rather than a line
    that quietly corrects nothing. Returns the entries that matched nothing,
    so the sync report can flag corrections the upstream has since fixed,
    and which card fields were corrected retroactively, for apply_plan.
    """
    unmatched = []
    retroactive = {}
    for entry in overrides:
        if not entry.get("reason"):
            raise ValueError(f"override without a reason: {entry!r}")
        card_name = canon_text(entry["match"]["card_name"])
        set_name = canon_text(entry["match"].get("set_name"))
        fields = entry["set_fields"]
        card_fields = {k: v for k, v in fields.items()
                       if k in CARD_FIELDS or k in CARD_OWNED_FIELDS}
        printing_fields = {k: v for k, v in fields.items() if k in PRINTING_FIELDS}
        back_fields = {}
        for column, value in fields.items():
            if column in card_fields or column in printing_fields:
                continue
            face_field = column[len("back."):] if column.startswith("back.") else ""
            if face_field not in FACE_FIELDS:
                raise ValueError(
                    f"override names {column!r}, which is not a field of a card, "
                    f"a printing, or (as back.<field>) a back face")
            if set_name is not None:
                raise ValueError(
                    f"override sets {column!r} for one set: a back face is a card "
                    f"fact, the same in every printing")
            back_fields[face_field] = value
        if not isinstance(entry.get("retroactive", False), bool):
            raise ValueError(f"override: retroactive must be true or false: {entry!r}")
        if entry.get("retroactive") and (printing_fields or set_name is not None):
            raise ValueError(
                f"override for {card_name!r} is retroactive but names printing fields: "
                f"only a card's own history is rewritten, a printing has none")
        hit = False
        card = snapshot["cards"].get(card_name)
        if set_name is None and (card_fields or back_fields) and card is not None:
            card.update(card_fields)
            if back_fields:
                if card.get("back") is None:
                    raise ValueError(
                        f"override corrects the back face of {card_name!r}, "
                        f"which upstream serves with no back face")
                card["back"].update(back_fields)
            if entry.get("retroactive"):
                corrected = set(card_fields) | ({"back"} if back_fields else set())
                retroactive.setdefault(card_name, set()).update(corrected)
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
    return Applied(unmatched, retroactive)
