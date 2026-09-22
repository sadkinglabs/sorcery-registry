"""Notes and the gap register: what the registry knows that the official
API does not say. data/notes.json and data/gaps.json.

The official API describes a card and its printings and nothing else. The
people who play the game know more - that one foil was prize support in
the Arthurian Legends store kit, three to a kit; that a card was printed
and never served at all. Both files hold that knowledge, one fact per
entry, and every entry says where it came from and when it was written
down, because the only thing that separates a record from a rumour is
being able to ask "says who?".

data/notes.json attaches notes to records the registry already holds:

    {"cards":     {"C000403": [NOTE, ...]},
     "printings": {"P001640": [NOTE, ...]}}

    NOTE = {"text": "...", "source": "...", "recorded": "2026-09-22"}

A note is for what no field can say. A fact that fits a field - the
artist, the finish, a date - is a correction, and belongs in
data/overrides.json, where it changes the field and leaves a trail;
otherwise the note and the field would quietly disagree.

data/gaps.json lists what is known to exist but is not recorded:

    {"gaps": [{"name": "The Champion", "codex_id": null,
               "text": "...", "source": "...", "recorded": "2026-09-22"}]}

codex_id is the card a missing printing belongs to, or null when the
card itself is unrecorded. A gap is closed by removing it once the thing
it describes has a record; the release diff says so.

Sources name where a fact came from, not who said it: every release is
immutable, so a person named in one is named in it for good. Write
"community report, Sorcery Discord, confirmed by photo" and name someone
only if they have asked to be credited. The files are read as they are;
nothing here can check that rule, so CONTRIBUTING.md states it.

Both files are shape-checked on load - a mistake is an error, never a
silently dropped note - and check_notes() is the validator's view: every
id exists, and a gap agrees with the card it names.
"""

import datetime
import json
import re
from pathlib import Path

from .ids import format_card_id, format_printing_id

NOTES_PATH = Path("data") / "notes.json"
GAPS_PATH = Path("data") / "gaps.json"

NOTE_KEYS = ("text", "source", "recorded")
GAP_KEYS = ("name", "codex_id", "text", "source", "recorded")
CARD_ID = re.compile(r"^C\d{6}$")
PRINTING_ID = re.compile(r"^P\d{6}$")


def _text(where, entry, key):
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {key} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{where}: {key} has leading or trailing whitespace")
    return value


def _date(where, entry):
    value = entry.get("recorded")
    try:
        datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: recorded must be a date, YYYY-MM-DD") from None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{where}: recorded must be a date, YYYY-MM-DD")
    return value


def _keys(where, entry, allowed):
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object")
    extra = sorted(set(entry) - set(allowed))
    missing = [k for k in allowed if k not in entry]
    if extra:
        raise ValueError(f"{where}: unknown key(s) {', '.join(extra)}")
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")


def _note(where, entry):
    _keys(where, entry, NOTE_KEYS)
    # Rebuilt in fixed key order, so the export never depends on how the
    # file happened to be typed.
    return {"text": _text(where, entry, "text"),
            "source": _text(where, entry, "source"),
            "recorded": _date(where, entry)}


def load_notes(path=NOTES_PATH):
    """{"cards": {codex_id: [note]}, "printings": {printing_id: [note]}},
    in file order. A missing file means no notes."""
    path = Path(path)
    if not path.exists():
        return {"cards": {}, "printings": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object with cards and printings")
    extra = sorted(set(data) - {"_comment", "cards", "printings"})
    if extra:
        raise ValueError(f"{path}: unknown key(s) {', '.join(extra)}")
    out = {}
    for section, pattern in (("cards", CARD_ID), ("printings", PRINTING_ID)):
        entries = data.get(section, {})
        if not isinstance(entries, dict):
            raise ValueError(f"{path}: {section} must map ids to lists of notes")
        out[section] = {}
        for record_id, notes in entries.items():
            if not pattern.match(record_id):
                raise ValueError(f"{path}: {section}: {record_id!r} is not a {section[:-1]} id")
            if not isinstance(notes, list) or not notes:
                raise ValueError(f"{path}: {record_id}: expected a non-empty list of notes")
            cleaned = [_note(f"{path}: {record_id} note {i + 1}", n) for i, n in enumerate(notes)]
            texts = [n["text"] for n in cleaned]
            if len(set(texts)) != len(texts):
                raise ValueError(f"{path}: {record_id}: the same note appears twice")
            out[section][record_id] = cleaned
    return out


def load_gaps(path=GAPS_PATH):
    """The gap register as a list, in file order. A missing file means none."""
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("gaps"), list):
        raise ValueError(f"{path}: expected an object with a gaps list")
    extra = sorted(set(data) - {"_comment", "gaps"})
    if extra:
        raise ValueError(f"{path}: unknown key(s) {', '.join(extra)}")
    out, seen = [], set()
    for i, entry in enumerate(data["gaps"]):
        where = f"{path}: gap {i + 1}"
        _keys(where, entry, GAP_KEYS)
        codex_id = entry["codex_id"]
        if codex_id is not None and not (isinstance(codex_id, str) and CARD_ID.match(codex_id)):
            raise ValueError(f"{where}: codex_id must be a card id or null")
        gap = {"name": _text(where, entry, "name"),
               "codex_id": codex_id,
               "text": _text(where, entry, "text"),
               "source": _text(where, entry, "source"),
               "recorded": _date(where, entry)}
        # One entry per missing thing: the release diff tells gaps apart by
        # this pair, so a second entry would read as the same gap twice.
        key = gap_key(gap)
        if key in seen:
            raise ValueError(f"{where}: {gap['name']} is already in the register")
        seen.add(key)
        out.append(gap)
    return out


def gap_key(gap):
    """What makes a gap the same gap across releases: what it names, not how
    it is worded, so rewording an entry is not reported as closing one gap
    and opening another."""
    return (gap["name"], gap["codex_id"])


def check_notes(con, notes, gaps, errors):
    """The validator's view: every note is on a record that exists, and every
    gap agrees with the card it names - or names no card at all."""
    card_names = {format_card_id(row["card_id"]): row["name"]
                  for row in con.execute("SELECT card_id, name FROM cards")}
    printing_ids = {format_printing_id(row["printing_id"])
                    for row in con.execute("SELECT printing_id FROM printings")}
    for codex_id in notes.get("cards", {}):
        if codex_id not in card_names:
            errors.append(f"data/notes.json: a note on {codex_id}, which is not a card in the registry")
    for printing_id in notes.get("printings", {}):
        if printing_id not in printing_ids:
            errors.append(f"data/notes.json: a note on {printing_id}, which is not a printing in the registry")

    by_name = {}
    for codex_id, name in card_names.items():
        by_name.setdefault(name.casefold(), codex_id)
    for gap in gaps:
        if gap["codex_id"] is not None:
            name = card_names.get(gap["codex_id"])
            if name is None:
                errors.append(f"data/gaps.json: {gap['name']} names {gap['codex_id']}, "
                              f"which is not a card in the registry")
            elif name != gap["name"]:
                errors.append(f"data/gaps.json: {gap['codex_id']} is {name!r}, "
                              f"but the gap calls it {gap['name']!r}")
        else:
            # A gap with no card must be for a card the registry does not
            # hold. If it does, the gap is really a missing printing and
            # should say which card, or it is already closed.
            known = by_name.get(gap["name"].casefold())
            if known is not None:
                errors.append(f"data/gaps.json: {gap['name']!r} is recorded as {known}; "
                              f"give the gap that codex_id or remove it")
