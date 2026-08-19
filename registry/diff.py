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
failure this registry exists to prevent.
"""

from .db import CARD_FIELDS, PRINTING_FIELDS

# Fields compared for "attributes changed" on matched records.
# image_hash is registry-owned, never sourced from the API, so it is
# excluded from printing comparison.
CARD_COMPARE = [f for f in CARD_FIELDS if f != "name"]
PRINTING_COMPARE = [f for f in PRINTING_FIELDS if f not in ("slug", "image_hash")]

EMPTY_DECISIONS = {
    "card_renames": [],      # {"card_id": int, "new_name": str}
    "new_cards": [],         # [card name, ...] force "genuinely new"
    "printing_renames": [],  # {"printing_id": int, "new_slug": str}
    "new_printings": [],     # [slug, ...] force "genuinely new"
    "retire_printings": [],  # [printing_id, ...] force "really gone"
}


def card_fingerprint(card):
    """Everything that defines a card's gameplay identity except its name.
    Used to recognise 'same card, new name'."""
    return tuple(card.get(field) for field in CARD_COMPARE
                 if field not in ("rarity", "subtypes"))


def field_changes(old, new, fields):
    changes = {}
    for field in fields:
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
        "ambiguous": [],
        "notes": [],
    }

    reg_cards = registry["cards"]
    api_cards = api["cards"]

    # ---- Card layer -----------------------------------------------------
    # rename_map: registry card name -> API card name, for renamed cards,
    # so the printing layer can compare ownership under the new names.
    rename_map = {}
    card_id_by_name = {c["name"]: c["card_id"] for c in reg_cards.values()}

    missing_names = [n for n in reg_cards if n not in api_cards]
    added_names = [n for n in api_cards if n not in reg_cards]

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

    ambiguous_card_names = {n for a in plan["ambiguous"]
                            for n in a["missing"] + a["candidates"]}

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
        if changes:
            plan["card_updates"].append(
                {"card_id": card["card_id"], "name": api_name, "changes": changes})

    # ---- Printing layer -------------------------------------------------
    reg_printings = registry["printings"]
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
    # slug's set_number - that changes when the sets are renumbered, which
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

    return plan


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
        "unretire_printings", "ambiguous"))


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
