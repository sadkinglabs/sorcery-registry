"""Notes: what the registry knows about a card or printing that the
official API does not say. data/notes.json.

The official API describes a card and its printings and nothing else. The
people who play the game know more - that one foil was prize support in
the Arthurian Legends store kit, three to a kit. The file holds that
knowledge, one fact per note, and every note says where it came from and
when it was written down, because the only thing that separates a record
from a rumour is being able to ask "says who?".

    {"cards":     {"C000403": [NOTE, ...]},
     "printings": {"P001640": [NOTE, ...]}}

    NOTE = {"text": "...", "source": "...", "recorded": "2026-09-22"}

A note is for what no field can say. A fact that fits a field - the
artist, the finish, a date - is a correction, and belongs in
data/overrides.json, where it changes the field and leaves a trail;
otherwise the note and the field would quietly disagree.

Sources name where a fact came from, not who said it: every release is
immutable, so a person named in one is named in it for good. Write
"community report, Sorcery Discord, confirmed by photo" and name someone
only if they have asked to be credited. The file is read as it is;
nothing here can check that rule, so CONTRIBUTING.md states it.

The file is shape-checked on load - a mistake is an error, never a
silently dropped note - and check_notes() is the validator's view: every
note sits on a record that exists.
"""

import datetime
import json
import re
from pathlib import Path

from .ids import format_card_id, format_printing_id

NOTES_PATH = Path("data") / "notes.json"

NOTE_KEYS = ("text", "source", "recorded")
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


def check_notes(con, notes, errors):
    """The validator's view: every note is on a record that exists."""
    card_ids = {format_card_id(row["card_id"])
                for row in con.execute("SELECT card_id FROM cards")}
    printing_ids = {format_printing_id(row["printing_id"])
                    for row in con.execute("SELECT printing_id FROM printings")}
    for codex_id in notes.get("cards", {}):
        if codex_id not in card_ids:
            errors.append(f"data/notes.json: a note on {codex_id}, which is not a card in the registry")
    for printing_id in notes.get("printings", {}):
        if printing_id not in printing_ids:
            errors.append(f"data/notes.json: a note on {printing_id}, which is not a printing in the registry")

