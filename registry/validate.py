"""Invariant validation. Intended as the CI gate on every commit.

    python -m registry.validate                    # internal + export checks
    python -m registry.validate --against REF      # also append-only vs a git ref

Checks, in order:
 1. schema_version in the database matches the code.
 2. Foreign keys hold and the immutability triggers are still installed.
 3. No id exceeds its high-water counter (ids are only ever handed out
    by the counters, so an id above the counter means someone bypassed them).
 4. Every printing's current slug has exactly one open slug_history row,
    and that row agrees with the slug column.
 5. Regenerating the export from the database is byte-identical to the
    committed export file (nobody edited one without the other), and each
    card's derived printing_ids list agrees with the printings table.
 6. With --against REF: every card_id and printing_id present in that
    commit's export still exists, printings still point at the same
    card_id, no card's printing_ids list shrank, the counters have not
    decreased, and any slug that changed is explained by slug_history.
    This is the append-only guarantee, checked against history rather
    than promised.

Exit code 0 when every check passes, 1 with a list of violations otherwise.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import SCHEMA_VERSION
from .db import get_meta, open_db
from .export import EXPORT_PATH, build_export, render

REQUIRED_TRIGGERS = {
    "cards_no_delete", "cards_id_immutable",
    "printings_no_delete", "printings_id_immutable", "printings_card_immutable",
}


def check_internal(con, errors):
    if get_meta(con, "schema_version") != str(SCHEMA_VERSION):
        errors.append(f"schema_version mismatch: db={get_meta(con, 'schema_version')} code={SCHEMA_VERSION}")

    if con.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("foreign_key_check reports dangling references")

    triggers = {row["name"] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    for missing in sorted(REQUIRED_TRIGGERS - triggers):
        errors.append(f"immutability trigger missing: {missing}")

    for table, counter in (("cards", "next_card_id"), ("printings", "next_printing_id")):
        id_column = "card_id" if table == "cards" else "printing_id"
        top = con.execute(f"SELECT max({id_column}) AS m FROM {table}").fetchone()["m"] or 0
        next_id = int(get_meta(con, counter))
        if top >= next_id:
            errors.append(f"{table}: max {id_column} {top} >= counter {counter} {next_id}; "
                          f"an id was assigned without the counter")

    rows = con.execute("""
        SELECT p.printing_id, p.slug,
               (SELECT count(*) FROM slug_history h
                 WHERE h.printing_id = p.printing_id AND h.valid_to IS NULL) AS open_rows,
               (SELECT h.slug FROM slug_history h
                 WHERE h.printing_id = p.printing_id AND h.valid_to IS NULL) AS open_slug
        FROM printings p""").fetchall()
    for row in rows:
        if row["open_rows"] != 1:
            errors.append(f"printing {row['printing_id']}: {row['open_rows']} open slug_history rows, expected 1")
        elif row["open_slug"] != row["slug"]:
            errors.append(f"printing {row['printing_id']}: slug column {row['slug']!r} "
                          f"disagrees with open history row {row['open_slug']!r}")


def check_export_matches(con, export_path, errors):
    export_path = Path(export_path)
    if not export_path.exists():
        errors.append(f"export file missing: {export_path}")
        return
    regenerated = render(build_export(con))
    committed = export_path.read_text(encoding="utf-8")
    if regenerated != committed:
        errors.append(f"{export_path} is not what the database produces; "
                      f"regenerate it with python -m registry.export")

    # Both directions of the card <-> printing link must tell the same story.
    export = build_export(con)
    forward = {}
    for printing in export["printings"]:
        forward.setdefault(printing["card_id"], set()).add(printing["printing_id"])
    for card in export["cards"]:
        listed = set(card["printing_ids"])
        actual = forward.get(card["card_id"], set())
        if listed != actual:
            errors.append(f"card {card['card_id']}: printing_ids {sorted(listed)} "
                          f"disagrees with the printings table {sorted(actual)}")
        if card["printing_ids"] != sorted(card["printing_ids"]):
            errors.append(f"card {card['card_id']}: printing_ids is not sorted")


def check_against_ref(con, ref, export_path, errors):
    result = subprocess.run(
        ["git", "show", f"{ref}:{Path(export_path).as_posix()}"],
        capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"note: no export at {ref}, skipping history check (first commit?)")
        return
    old = json.loads(result.stdout)
    new = build_export(con)

    new_cards = {c["card_id"]: c for c in new["cards"]}
    for card in old["cards"]:
        current = new_cards.get(card["card_id"])
        if current is None:
            errors.append(f"card_id {card['card_id']} ({card['name']!r}) existed at {ref} and is gone")
            continue
        # printing_ids may only ever grow ("printing_ids" guard: exports
        # from before the field existed have nothing to compare against).
        lost = set(card.get("printing_ids", [])) - set(current["printing_ids"])
        if lost:
            errors.append(f"card_id {card['card_id']}: printings {sorted(lost)} "
                          f"were listed at {ref} and are gone")

    new_printings = {p["printing_id"]: p for p in new["printings"]}
    history = {}
    for row in new["slug_history"]:
        history.setdefault(row["printing_id"], set()).add(row["slug"])
    for printing in old["printings"]:
        pid = printing["printing_id"]
        current = new_printings.get(pid)
        if current is None:
            errors.append(f"printing_id {pid} ({printing['slug']!r}) existed at {ref} and is gone")
            continue
        if current["card_id"] != printing["card_id"]:
            errors.append(f"printing_id {pid} moved from card {printing['card_id']} "
                          f"to card {current['card_id']}")
        if current["slug"] != printing["slug"] and printing["slug"] not in history.get(pid, set()):
            errors.append(f"printing_id {pid} slug changed {printing['slug']!r} -> "
                          f"{current['slug']!r} without a slug_history record")

    for key in ("cards", "printings", "slug_history"):
        if new["header"][key] < old["header"][key]:
            errors.append(f"{key} count decreased: {old['header'][key]} -> {new['header'][key]}")


def main():
    parser = argparse.ArgumentParser(description="Validate registry invariants.")
    parser.add_argument("--db", default="registry.sqlite")
    parser.add_argument("--export", default=str(EXPORT_PATH))
    parser.add_argument("--against", help="git ref whose export must be a subset of the current one")
    args = parser.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"database not found: {args.db}")
    con = open_db(args.db)

    errors = []
    check_internal(con, errors)
    check_export_matches(con, args.export, errors)
    if args.against:
        check_against_ref(con, args.against, args.export, errors)

    if errors:
        print(f"FAIL: {len(errors)} invariant violation(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("OK: all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
