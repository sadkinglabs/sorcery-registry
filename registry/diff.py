"""Diff the registry against an API snapshot and classify every difference.

Pure functions: no database, no network, no clock. Input is two snapshots
(the registry's current state and the API's), output is a plan describing
what would change. sync.py applies plans; this module only decides.

The rule that matters most: a rename is only recognised when the pairing
is unambiguous. Cards pair on their full gameplay fingerprint (rules text,
type, stats, thresholds). Printings pair within a card on set, product and
finish. Anything that does not
resolve to exactly one-to-one is quarantined for human review, because a
wrong guess silently forks one card into two identities, which is the exact
failure this registry exists to prevent. The printing layer also honours slug
ownership: once a slug has referred to a printing it may never refer to a
different one, so any classification that would hand a historically owned slug
to another printing is quarantined instead (a human decision that demands it
is an error, not a quarantine).

The card layer additionally fails closed: if any card disappeared from the
API while any unexplained new card name appeared in the same sync, none of
them is classified automatically. A card can be renamed AND have its
gameplay attributes changed at once, in which case the fingerprint cannot
pair them - so issuing the newcomer a fresh id would fork the identity.
Both sides go to quarantine instead. Disappearances alone still retire
their printings, and newcomers alone are still genuinely new.

Manual records (origin "manual", see registry/manual.py) are the
registry's own: upstream not serving them is their normal state, so they
never count as vanished and are never retired, and their predicted slugs
own nothing. When upstream starts serving something that looks like one -
a card with the same or a close name, a printing of the same card with
the same product and finish, or the predicted slug itself - it is never
matched automatically and never minted a second id. It goes to review,
and a human either confirms the match (confirm_cards, confirm_printings:
the record keeps its id, takes upstream's name, slug and values, and
becomes origin "api") or says it is something else (new_cards,
new_printings).
"""

import difflib
import json
import re
import unicodedata

from .db import CARD_FIELDS, CARD_OWNED_FIELDS, PRINTING_FIELDS

# Fields compared for "attributes changed" on matched records.
# image_hash is registry-owned, never sourced from the API, so it is
# excluded from printing comparison. The CARD_OWNED_FIELDS are registry-
# owned too: an API snapshot carries no value for them, so they only ever
# change through an override, which sets them on the snapshot explicitly.
CARD_COMPARE = [f for f in CARD_FIELDS if f != "name"]
PRINTING_COMPARE = [f for f in PRINTING_FIELDS if f not in ("slug", "image_hash")]
OWNED_COMPARE = list(CARD_OWNED_FIELDS)

# Fields that upstream may serve as null without meaning "there is none":
# a null here leaves the registry's value alone rather than erasing it.
# flavour_text is served null for every printing since the API rebuild
# while the cards themselves still carry flavour text.
FROZEN_WHEN_NULL = {"flavour_text"}

# Fields that do not define a card's gameplay identity: rarity and slot
# are distribution facts, subtypes a classification that gets revised.
NOT_FINGERPRINT = {"rarity", "slot", "subtypes"}

EMPTY_DECISIONS = {
    "card_renames": [],      # {"card_id": int, "new_name": str}
    "new_cards": [],         # [card name, ...] force "genuinely new"
    "printing_renames": [],  # {"printing_id": int, "new_slug": str}
    "new_printings": [],     # [slug, ...] force "genuinely new"
    "retire_printings": [],  # [printing_id, ...] force "really gone"
    "confirm_cards": [],     # {"card_id": int, "name": str} manual card = upstream card
    "confirm_printings": [], # {"printing_id": int, "slug": str} manual printing = upstream printing
}


def _name_key(name):
    """A card name reduced to what two spellings of one card share: case,
    accents, punctuation and a leading "The" do not tell cards apart."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    plain = re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()
    return re.sub(r"^the ", "", plain)


def close_names(a, b):
    """Whether two card names could be the same card spelled twice: equal
    once reduced, one contained in the other, or nearly the same letters.
    Deliberately generous - a false alarm costs a look, a miss costs a
    duplicate card."""
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # Short names differ by a letter and are still different cards (Frog,
    # Fog); only longer ones are compared by likeness.
    if min(len(ka), len(kb)) < 6:
        return False
    if ka in kb or kb in ka:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.85


def _hashable(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return value


def card_fingerprint(card):
    """Everything that defines a card's gameplay identity except its name.
    Used to recognise 'same card, new name'."""
    return tuple(_hashable(card.get(field)) for field in CARD_COMPARE
                 if field not in NOT_FINGERPRINT)


def field_changes(old, new, fields):
    changes = {}
    for field in fields:
        if field in FROZEN_WHEN_NULL and new.get(field) is None:
            continue
        if old.get(field) != new.get(field):
            changes[field] = {"old": old.get(field), "new": new.get(field)}
    return changes


def _one_to_one(pairs_by_key):
    """Keep only keys where exactly one old and one new record met."""
    matched = []
    for key, (olds, news) in pairs_by_key.items():
        if len(olds) == 1 and len(news) == 1:
            matched.append((olds[0], news[0]))
    return matched


def _card_summary(card):
    """A card as a human reviewer needs to see it in a quarantine case:
    the name plus the fingerprint fields they must compare by eye."""
    return {
        "name": card["name"],
        "rules_text": card.get("rules_text"),
        "type": card.get("type"),
        "cost": card.get("cost"),
        "attack": card.get("attack"),
        "defense": card.get("defense"),
        "life": card.get("life"),
        "thresholds": {element: card.get(f"thr_{element}")
                       for element in ("air", "earth", "fire", "water")},
    }


def _case_names(plan):
    """Card names already spoken for by a card-kind quarantine case.
    Entries are either bare names or _card_summary dicts."""
    names = set()
    for case in plan["ambiguous"]:
        if case["kind"] not in ("card", "manual-card"):
            continue
        for entry in case["missing"] + case["candidates"]:
            names.add(entry if isinstance(entry, str) else entry["name"])
    return names


def _group(missing, added, key_fn_old, key_fn_new):
    groups = {}
    for record in missing:
        groups.setdefault(key_fn_old(record), ([], []))[0].append(record)
    for record in added:
        groups.setdefault(key_fn_new(record), ([], []))[1].append(record)
    return groups


def diff(registry, api, decisions=None):
    decisions = {**EMPTY_DECISIONS, **(decisions or {})}
    plan = {
        "new_cards": [],
        "card_renames": [],
        "card_updates": [],
        "new_printings": [],
        "printing_renames": [],
        "printing_updates": [],
        "retire_printings": [],
        "unretire_printings": [],
        "confirm_cards": [],
        "confirm_printings": [],
        "ambiguous": [],
        "notes": [],
    }

    # Manual records take no part in the matching below: they are not
    # upstream's, so they cannot vanish from it. They meet upstream only
    # through review (_manual_cards, _manual_printings).
    manual_cards = {n: c for n, c in registry["cards"].items() if c.get("origin") == "manual"}
    reg_cards = {n: c for n, c in registry["cards"].items() if c.get("origin", "api") == "api"}
    api_cards = api["cards"]

    # ---- Card layer -----------------------------------------------------
    # rename_map: registry card name -> API card name, for renamed cards,
    # so the printing layer can compare ownership under the new names.
    rename_map = {}
    card_id_by_name = {c["name"]: c["card_id"] for c in reg_cards.values()}

    missing_names = [n for n in reg_cards if n not in api_cards]
    added_names = [n for n in api_cards if n not in reg_cards]
    _manual_cards(plan, manual_cards, api_cards, added_names, rename_map, decisions)

    # Human decisions consume candidates before any automatic pairing.
    for decision in decisions["card_renames"]:
        old_name = next((n for n, c in reg_cards.items()
                         if c["card_id"] == decision["card_id"]), None)
        new_name = decision["new_name"]
        if old_name is None or old_name not in missing_names or new_name not in added_names:
            raise ValueError(f"card_renames decision does not match the current diff: {decision!r}")
        missing_names.remove(old_name)
        added_names.remove(new_name)
        rename_map[old_name] = new_name
        plan["card_renames"].append(
            {"card_id": decision["card_id"], "old_name": old_name,
             "new_name": new_name, "decided_by": "human"})
    for name in decisions["new_cards"]:
        if name not in added_names:
            raise ValueError(f"new_cards decision does not match the current diff: {name!r}")

    # Automatic card rename pairing on the gameplay fingerprint.
    groups = _group(
        [reg_cards[n] for n in missing_names],
        [api_cards[n] for n in added_names if n not in decisions["new_cards"]],
        card_fingerprint, card_fingerprint)
    for old_card, new_card in _one_to_one(groups):
        rename_map[old_card["name"]] = new_card["name"]
        missing_names.remove(old_card["name"])
        added_names.remove(new_card["name"])
        plan["card_renames"].append(
            {"card_id": old_card["card_id"], "old_name": old_card["name"],
             "new_name": new_card["name"], "decided_by": "fingerprint"})
    for key, (olds, news) in groups.items():
        if olds and news and not (len(olds) == 1 and len(news) == 1):
            plan["ambiguous"].append({
                "kind": "card",
                "problem": "several vanished and new cards share one gameplay fingerprint",
                "missing": sorted(c["name"] for c in olds),
                "candidates": sorted(c["name"] for c in news),
            })

    # Fail closed: a card can be renamed AND have its attributes changed in
    # the same sync, which defeats fingerprint pairing. If anything vanished
    # while anything unexplained appeared, no side is classified
    # automatically - handing the newcomer a fresh id would fork the card.
    forced_new_cards = set(decisions["new_cards"])
    leftover_missing = [n for n in missing_names if n not in _case_names(plan)]
    leftover_added = [n for n in added_names
                      if n not in _case_names(plan) and n not in forced_new_cards]
    if leftover_missing and leftover_added:
        plan["ambiguous"].append({
            "kind": "card",
            "problem": "cards disappeared and appeared in the same sync and could not be matched confidently",
            "missing": [_card_summary(reg_cards[n]) for n in sorted(leftover_missing)],
            "candidates": [_card_summary(api_cards[n]) for n in sorted(leftover_added)],
        })

    ambiguous_card_names = _case_names(plan)

    # Cards still missing pair with nothing: the card stays in the registry
    # (cards never retire) and its printings will retire below.
    for name in missing_names:
        if name not in ambiguous_card_names:
            plan["notes"].append(f"card no longer in API, kept with retired printings: {name!r}")
    for name in added_names:
        if name not in ambiguous_card_names:
            plan["new_cards"].append(dict(api_cards[name]))

    # Attribute updates on cards present in both (including renamed ones).
    for old_name, card in reg_cards.items():
        api_name = rename_map.get(old_name, old_name)
        if api_name not in api_cards or old_name in ambiguous_card_names:
            continue
        changes = field_changes(card, api_cards[api_name], CARD_COMPARE)
        api_card = api_cards[api_name]
        for field in OWNED_COMPARE:
            if field in api_card and api_card[field] != card.get(field):
                changes[field] = {"old": card.get(field), "new": api_card[field]}
        if changes:
            plan["card_updates"].append(
                {"card_id": card["card_id"], "name": api_name, "changes": changes})

    # ---- Printing layer -------------------------------------------------
    manual_printings = [p for p in registry["printings"].values() if p.get("origin") == "manual"]
    reg_printings = {s: p for s, p in registry["printings"].items()
                     if p.get("origin", "api") == "api"}
    api_printings = api["printings"]
    printing_by_id = {p["printing_id"]: p for p in reg_printings.values()}

    def effective_card(reg_printing):
        return rename_map.get(reg_printing["card_name"], reg_printing["card_name"])

    missing = []   # active registry printings whose slug left the API
    for slug, printing in reg_printings.items():
        if slug in api_printings:
            api_printing = api_printings[slug]
            if effective_card(printing) != api_printing["card_name"]:
                plan["ambiguous"].append({
                    "kind": "printing",
                    "problem": "slug kept but now belongs to a different card",
                    "missing": [{"printing_id": printing["printing_id"], "slug": slug,
                                 "card": printing["card_name"]}],
                    "candidates": [{"slug": slug, "card": api_printing["card_name"]}],
                })
                continue
            if printing["retired_at"]:
                plan["unretire_printings"].append(
                    {"printing_id": printing["printing_id"], "slug": slug})
            changes = field_changes(printing, api_printing, PRINTING_COMPARE)
            if changes:
                plan["printing_updates"].append(
                    {"printing_id": printing["printing_id"], "slug": slug,
                     "changes": changes})
        elif not printing["retired_at"]:
            if printing["card_name"] not in ambiguous_card_names:
                missing.append(printing)

    added = [p for p in api_printings.values()
             if p["slug"] not in reg_printings
             and p["card_name"] not in ambiguous_card_names]
    _manual_printings(plan, manual_printings, added, effective_card, decisions)

    # Human decisions first, exactly as at the card layer.
    for decision in decisions["printing_renames"]:
        old = printing_by_id.get(decision["printing_id"])
        new = api_printings.get(decision["new_slug"])
        if old is None or old not in missing or new is None or new not in added:
            raise ValueError(f"printing_renames decision does not match the current diff: {decision!r}")
        missing.remove(old)
        added.remove(new)
        _record_rename(plan, old, new, "human")
    for slug in decisions["new_printings"]:
        if not any(p["slug"] == slug for p in added):
            raise ValueError(f"new_printings decision does not match the current diff: {slug!r}")
    for printing_id in decisions["retire_printings"]:
        old = printing_by_id.get(printing_id)
        if old is None or old not in missing:
            raise ValueError(f"retire_printings decision does not match the current diff: {printing_id!r}")
        missing.remove(old)
        plan["retire_printings"].append(
            {"printing_id": printing_id, "slug": old["slug"], "decided_by": "human"})

    forced_new = set(decisions["new_printings"])
    pairable_added = [p for p in added if p["slug"] not in forced_new]

    # Pairing key: card + set + product + finish. Deliberately NOT the
    # slug's set_code - that changes when the sets are renumbered, which
    # is exactly the event a rename has to be matched across. (There is no
    # collector number in the official data to tiebreak on.)
    def pair_key_old(p):
        return (effective_card(p), p["set_name"], p["product"], p["finish"])

    def pair_key_new(p):
        return (p["card_name"], p["set_name"], p["product"], p["finish"])

    groups = _group(missing, pairable_added, pair_key_old, pair_key_new)
    for old, new in _one_to_one(groups):
        missing.remove(old)
        pairable_added.remove(new)
        added.remove(new)
        _record_rename(plan, old, new, "set+product+finish")

    # Whatever is left: if a card has both vanished and appeared printings
    # they could not be paired cleanly, so quarantine the lot. Vanished-only
    # printings retire; appeared-only printings are genuinely new.
    left_missing_by_card = {}
    for printing in missing:
        left_missing_by_card.setdefault(effective_card(printing), []).append(printing)
    left_added_by_card = {}
    for printing in pairable_added:
        left_added_by_card.setdefault(printing["card_name"], []).append(printing)

    for card_name, olds in left_missing_by_card.items():
        news = left_added_by_card.pop(card_name, None)
        if news:
            plan["ambiguous"].append({
                "kind": "printing",
                "problem": "vanished and new printings of the same card do not pair one to one",
                "card": card_name,
                "missing": [{"printing_id": p["printing_id"], "slug": p["slug"],
                             "set_name": p["set_name"], "product": p["product"],
                             "finish": p["finish"]}
                            for p in olds],
                "candidates": [{"slug": p["slug"], "set_name": p["set_name"],
                                "product": p["product"], "finish": p["finish"]}
                               for p in news],
            })
            for p in news:
                added.remove(p)
        else:
            for p in olds:
                plan["retire_printings"].append(
                    {"printing_id": p["printing_id"], "slug": p["slug"],
                     "decided_by": "no candidate"})

    for printing in added:
        plan["new_printings"].append(dict(printing))

    _enforce_slug_ownership(plan, registry.get("slug_owners", {}) or {}, decisions)

    return plan


def _manual_cards(plan, manual_cards, api_cards, added_names, rename_map, decisions):
    """Upstream cards that may be manual cards the registry already holds.
    A confirmed pair keeps the manual card's id and takes upstream's name
    and values; anything else that resembles a manual card waits for a
    human. Consumes what it handles from added_names."""
    by_id = {c["card_id"]: c for c in manual_cards.values()}
    for decision in decisions["confirm_cards"]:
        card = by_id.get(decision["card_id"])
        name = decision["name"]
        if card is None or name not in added_names:
            raise ValueError(f"confirm_cards decision does not match the current diff: {decision!r}")
        added_names.remove(name)
        del by_id[decision["card_id"]]
        plan["confirm_cards"].append({"card_id": card["card_id"], "old_name": card["name"],
                                      "new_name": name})
        if card["name"] != name:
            rename_map[card["name"]] = name
            plan["card_renames"].append({"card_id": card["card_id"], "old_name": card["name"],
                                         "new_name": name, "decided_by": "human"})
        changes = field_changes(card, api_cards[name], CARD_COMPARE)
        if changes:
            plan["card_updates"].append({"card_id": card["card_id"], "name": name,
                                         "changes": changes})
    forced_new = set(decisions["new_cards"])
    for name in list(added_names):
        alike = sorted(c["name"] for c in by_id.values() if close_names(c["name"], name))
        if not alike:
            continue
        if name in forced_new:
            if any(a.casefold() == name.casefold() for a in alike):
                raise ValueError(f"new_cards decision: {name!r} is the name of manual card "
                                 f"{alike}; confirm it, or rename the manual entry first")
            continue
        added_names.remove(name)
        plan["ambiguous"].append({
            "kind": "manual-card",
            "problem": "the official API now serves a card like one the registry recorded by "
                       "hand; confirm it (confirm_cards) or say it is a different card (new_cards)",
            "missing": alike,
            "candidates": [name],
        })


def _manual_printings(plan, manual_printings, added, effective_card, decisions):
    """Upstream printings that may be manual printings the registry
    already holds: same card with the same product and finish, or the
    predicted slug itself. Confirmed pairs keep the manual id; the rest
    wait for a human. Consumes what it handles from `added`."""
    by_id = {p["printing_id"]: p for p in manual_printings}
    by_slug = {p["slug"]: p for p in added}
    for decision in decisions["confirm_printings"]:
        old = by_id.get(decision["printing_id"])
        new = by_slug.get(decision["slug"])
        if old is None or new is None or new not in added \
                or effective_card(old) != new["card_name"]:
            raise ValueError(f"confirm_printings decision does not match the current diff: "
                             f"{decision!r}")
        added.remove(new)
        del by_id[decision["printing_id"]]
        plan["confirm_printings"].append({"printing_id": old["printing_id"],
                                          "old_slug": old["slug"], "new_slug": new["slug"]})
        changes = field_changes(old, new, PRINTING_COMPARE)
        if changes:
            plan["printing_updates"].append({"printing_id": old["printing_id"],
                                             "slug": new["slug"], "changes": changes})
    forced_new = set(decisions["new_printings"])
    for new in list(added):
        alike = [p for p in by_id.values()
                 if p["slug"] == new["slug"]
                 or (effective_card(p) == new["card_name"]
                     and (p["product"], p["finish"]) == (new["product"], new["finish"]))]
        if not alike:
            continue
        if new["slug"] in forced_new:
            if any(p["slug"] == new["slug"] for p in alike):
                raise ValueError(f"new_printings decision: {new['slug']!r} is the predicted slug "
                                 f"of a manual printing; confirm it, or give that entry another "
                                 f"slug in data/manual.json first")
            continue
        added.remove(new)
        plan["ambiguous"].append({
            "kind": "manual-printing",
            "problem": "the official API now serves a printing like one the registry recorded "
                       "by hand; confirm it (confirm_printings) or say it is a different "
                       "printing (new_printings)",
            "card": new["card_name"],
            "missing": [{"printing_id": p["printing_id"], "slug": p["slug"],
                         "set_name": p["set_name"], "product": p["product"],
                         "finish": p["finish"], "released_at": p["released_at"]}
                        for p in sorted(alike, key=lambda p: p["printing_id"])],
            "candidates": [{"slug": new["slug"], "set_name": new["set_name"],
                            "product": new["product"], "finish": new["finish"],
                            "released_at": new["released_at"]}],
        })


def _enforce_slug_ownership(plan, slug_owners, decisions):
    """A slug belongs permanently to the first printing that carried it.
    Sweep the classified plan and quarantine anything that would hand a
    historically owned slug to a different printing. A rename back to a
    printing's own former slug (A -> B -> A) is untouched: it is still
    owned by that same printing."""
    kept_renames = []
    for rename in plan["printing_renames"]:
        owner = slug_owners.get(rename["new_slug"])
        if owner is None or owner == rename["printing_id"]:
            kept_renames.append(rename)
            continue
        if rename["decided_by"] == "human":
            raise ValueError(
                f"printing_renames decision would reassign slug "
                f"{rename['new_slug']!r} away from printing {owner}")
        plan["printing_updates"] = [
            update for update in plan["printing_updates"]
            if update["printing_id"] != rename["printing_id"]]
        plan["ambiguous"].append({
            "kind": "printing",
            "problem": "historical slug would be reassigned to a different printing",
            "slug": rename["new_slug"],
            "owned_by_printing_id": owner,
            "attempted_printing_id": rename["printing_id"],
        })
    plan["printing_renames"] = kept_renames

    forced_new = set(decisions["new_printings"])
    kept_new = []
    for printing in plan["new_printings"]:
        owner = slug_owners.get(printing["slug"])
        if owner is None:
            kept_new.append(printing)
            continue
        if printing["slug"] in forced_new:
            raise ValueError(
                f"new_printings decision would reassign slug "
                f"{printing['slug']!r} away from printing {owner}")
        plan["ambiguous"].append({
            "kind": "printing",
            "problem": "historical slug would be reassigned to a different printing",
            "slug": printing["slug"],
            "owned_by_printing_id": owner,
            "attempted_printing_id": None,
        })
    plan["new_printings"] = kept_new


def _record_rename(plan, old, new, decided_by):
    plan["printing_renames"].append({
        "printing_id": old["printing_id"],
        "old_slug": old["slug"],
        "new_slug": new["slug"],
        "decided_by": decided_by,
    })
    changes = field_changes(old, new, PRINTING_COMPARE)
    if changes:
        plan["printing_updates"].append(
            {"printing_id": old["printing_id"], "slug": new["slug"],
             "changes": changes})


def is_noop(plan):
    return not any(plan[key] for key in (
        "new_cards", "card_renames", "card_updates", "new_printings",
        "printing_renames", "printing_updates", "retire_printings",
        "unretire_printings", "confirm_cards", "confirm_printings", "ambiguous"))


def summarise(plan):
    lines = []
    counts = [
        ("new cards", len(plan["new_cards"])),
        ("card renames", len(plan["card_renames"])),
        ("card attribute updates", len(plan["card_updates"])),
        ("new printings", len(plan["new_printings"])),
        ("printing slug renames", len(plan["printing_renames"])),
        ("printing attribute updates", len(plan["printing_updates"])),
        ("printings retired", len(plan["retire_printings"])),
        ("printings unretired", len(plan["unretire_printings"])),
        ("manual cards confirmed upstream", len(plan["confirm_cards"])),
        ("manual printings confirmed upstream", len(plan["confirm_printings"])),
        ("AMBIGUOUS, needs human review", len(plan["ambiguous"])),
    ]
    for label, count in counts:
        if count:
            lines.append(f"  {count:5d}  {label}")
    for note in plan["notes"]:
        lines.append(f"  note: {note}")
    if not lines:
        lines.append("  no changes")
    return "\n".join(lines)
