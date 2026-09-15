"""Faces recorded from the printed card: data/errata.json.

The official API serves a card's current face. When the publisher changed
a card after it was printed, the printed card is the only record of the
earlier face, and the registry never observed it - so every printing of
such a card would claim to show current values, which is not true.

data/errata.json records the printed face by hand, one entry per card:

    {"codex_id": "C000002",
     "printed": {"rules_text": "..."},          # the fields as printed (any of HISTORY_FIELDS)
     "current_since": "2026-09-15",             # the current face applies from this date
     "current_printings": [],                   # printings that already carry the current face
     "source": {"printing_id": "P000007"},      # which printing the face was read from
     "reason": "..."}                           # why, and who checked it

`python -m registry.errata apply` turns an entry into history: a closed
card_history row holding the printed face (source "card") from the day
the first printing carrying it reached the public until current_since,
and the observed face from current_since on. Every printing released before current_since then
shows older values; one released on or after it - a reprint with the
corrected text - shows current values. The validator checks that the
file and the history agree, that current_since really separates the
printings listed as current from the rest, and that no "card" row exists
without an entry. Like data/overrides.json, every entry carries a reason.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from .db import HISTORY_FIELDS, decode_field, face_of, open_db
from .ids import format_card_id, format_printing_id, id_number

ERRATA_PATH = Path("data") / "errata.json"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_errata(path=ERRATA_PATH):
    """The entries, shape-checked: a mistake in the file is an error here,
    never a silent no-op."""
    path = Path(path)
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a list of entries")
    seen = set()
    for entry in entries:
        codex = entry.get("codex_id")
        if not isinstance(codex, str) or not codex.startswith("C"):
            raise ValueError(f"{path}: entry without a codex_id: {entry!r}")
        if codex in seen:
            raise ValueError(f"{path}: {codex} appears twice")
        seen.add(codex)
        printed = entry.get("printed")
        if not isinstance(printed, dict) or not printed:
            raise ValueError(f"{path}: {codex}: 'printed' must name at least one field as printed")
        unknown = set(printed) - set(HISTORY_FIELDS)
        if unknown:
            raise ValueError(f"{path}: {codex}: not gameplay fields: {sorted(unknown)}")
        since = entry.get("current_since")
        if not isinstance(since, str) or not DATE.match(since):
            raise ValueError(f"{path}: {codex}: current_since must be a YYYY-MM-DD date")
        if not entry.get("reason"):
            raise ValueError(f"{path}: {codex}: every entry needs a reason")
        if not isinstance(entry.get("source", {}), dict) or not entry.get("source", {}).get("printing_id"):
            raise ValueError(f"{path}: {codex}: source.printing_id names the printing the face was read from")
        if not isinstance(entry.get("current_printings", []), list):
            raise ValueError(f"{path}: {codex}: current_printings must be a list of printing ids")
    return entries


def printed_face(current, printed):
    """The face as printed: the current face with the printed fields in
    place of the current ones."""
    face = {field: current.get(field) for field in HISTORY_FIELDS}
    face.update(printed)
    return face


def _history(con, card_id):
    return [dict(row) for row in con.execute(
        "SELECT rowid, valid_from, valid_to, face, source FROM card_history "
        "WHERE card_id = ? ORDER BY valid_from, valid_to IS NULL, face", (card_id,))]


def apply_errata(con, entries, log=print):
    """Record every entry's printed face in card_history. Idempotent: an
    entry already recorded (same face, same dates) is left alone; one
    whose dates no longer match what is recorded is an error to resolve
    by hand, never a silent rewrite of history."""
    applied = unchanged = 0
    for entry in entries:
        codex = entry["codex_id"]
        card_id = id_number(codex)
        if con.execute("SELECT 1 FROM cards WHERE card_id = ?", (card_id,)).fetchone() is None:
            raise ValueError(f"{codex}: no such card")
        rows = _history(con, card_id)
        open_rows = [r for r in rows if r["valid_to"] is None]
        if len(open_rows) != 1:
            raise ValueError(f"{codex}: {len(open_rows)} open history rows, expected 1")
        current_row = open_rows[0]
        current = json.loads(current_row["face"])
        face = printed_face(current, entry["printed"])
        face_json = face_of(face)
        if face_json == face_of(current):
            raise ValueError(f"{codex}: the printed face equals the current face; nothing differs")
        since = entry["current_since"]
        recorded = [r for r in rows if r["source"] == "card" and r["face"] == face_json]
        if recorded:
            if recorded[0]["valid_to"] != since or current_row["valid_from"] != since:
                raise ValueError(f"{codex}: recorded with current_since {recorded[0]['valid_to']}, "
                                 f"the file says {since}; history is not rewritten")
            unchanged += 1
            continue
        if len(rows) != 1:
            raise ValueError(f"{codex}: has {len(rows)} history rows; a printed face is recorded "
                             f"only while the card's history holds its single observed face")
        # The printed face has been in force since the first printing that
        # carries it reached the public - which may be years before the
        # registry began recording - and until the current face took over.
        current_ids = {id_number(pid) for pid in entry.get("current_printings", [])}
        printed_from = min([current_row["valid_from"]] + [
            row["released_at"] for row in con.execute(
                "SELECT printing_id, released_at FROM printings WHERE card_id = ?", (card_id,))
            if row["released_at"] and row["printing_id"] not in current_ids])
        if since <= printed_from:
            raise ValueError(f"{codex}: current_since {since} must be after the printed face "
                             f"first reached the public ({printed_from})")
        con.execute(
            "INSERT INTO card_history (card_id, valid_from, valid_to, face, source) "
            "VALUES (?, ?, ?, ?, 'card')",
            (card_id, printed_from, since, face_json))
        con.execute("UPDATE card_history SET valid_from = ? WHERE rowid = ?",
                    (since, current_row["rowid"]))
        con.execute("UPDATE cards SET errata = 1 WHERE card_id = ?", (card_id,))
        log(f"{codex}: printed face recorded; current face from {since}")
        applied += 1
    con.commit()
    return {"applied": applied, "unchanged": unchanged}


def check_errata(con, entries, errors):
    """The validator's view: the file and the history agree, the date
    separates the printings as claimed, and no hand-recorded row lacks
    an entry."""
    by_card = {id_number(e["codex_id"]): e for e in entries}
    for card_id, entry in by_card.items():
        codex = entry["codex_id"]
        rows = _history(con, card_id)
        if not rows:
            errors.append(f"errata {codex}: no such card")
            continue
        current_row = next((r for r in rows if r["valid_to"] is None), None)
        if current_row is None:
            errors.append(f"errata {codex}: no open history row")
            continue
        face_json = face_of(printed_face(json.loads(current_row["face"]), entry["printed"]))
        recorded = [r for r in rows if r["source"] == "card" and r["face"] == face_json]
        if not recorded:
            errors.append(f"errata {codex}: printed face not recorded in card_history "
                          f"(run python -m registry.errata apply)")
            continue
        since = entry["current_since"]
        if recorded[0]["valid_to"] != since or current_row["valid_from"] != since:
            errors.append(f"errata {codex}: current_since {since} disagrees with the history "
                          f"({recorded[0]['valid_to']} / {current_row['valid_from']})")
        current_ids = set(entry.get("current_printings", []))
        for row in con.execute("SELECT printing_id, released_at FROM printings WHERE card_id = ?",
                               (card_id,)):
            pid = format_printing_id(row["printing_id"])
            released = row["released_at"]
            if pid in current_ids:
                if released is None or released < since:
                    errors.append(f"errata {codex}: {pid} is listed as carrying the current face "
                                  f"but was released {released}, before {since}")
            elif released is not None and released >= since:
                errors.append(f"errata {codex}: {pid} was released {released}, on or after "
                              f"{since}, so it counts as current; list it in current_printings "
                              f"or move current_since")
        for pid in current_ids:
            owner = con.execute("SELECT card_id FROM printings WHERE printing_id = ?",
                                (id_number(pid),)).fetchone()
            if owner is None or owner["card_id"] != card_id:
                errors.append(f"errata {codex}: {pid} is not one of its printings")
    for row in con.execute("SELECT card_id FROM card_history WHERE source = 'card'"):
        if row["card_id"] not in by_card:
            errors.append(f"card_history: {format_card_id(row['card_id'])} has a face recorded "
                          f"from the card with no entry in data/errata.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record printed faces from data/errata.json.")
    parser.add_argument("command", choices=["apply", "check"])
    parser.add_argument("--db", default="registry.sqlite")
    parser.add_argument("--errata", default=str(ERRATA_PATH))
    args = parser.parse_args(argv)
    entries = load_errata(args.errata)
    con = open_db(args.db)
    if args.command == "apply":
        counts = apply_errata(con, entries)
        print(f"{counts['applied']} recorded, {counts['unchanged']} already recorded")
        return 0
    errors = []
    check_errata(con, entries, errors)
    for error in errors:
        print(f"  - {error}")
    print("OK" if not errors else f"FAIL: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
