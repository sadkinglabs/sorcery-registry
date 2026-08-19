"""Invariant validation. Intended as the CI gate on every commit.

    python -m registry.validate                    # internal + export checks
    python -m registry.validate --against REF      # also append-only vs a git ref

Checks, in order:
 1. schema_version in the database matches the code.
 2. Foreign keys hold and the immutability triggers are still installed.
 3. No id exceeds its high-water counter (ids are only ever handed out
    by the counters, so an id above the counter means someone bypassed them).
 4. Every printing's current slug has exactly one open slug_history row,
    and that row agrees with the slug column, and no slug - current or
    historical - has ever referred to more than one printing.
 5. Regenerating the export from the database is byte-identical to the
    committed export file (nobody edited one without the other), and each
    card's derived printing_ids list agrees with the printings table.
 6. With --against REF: every codex_id and printing_id present in that
    commit's export still exists, printings still point at the same
    card, no card's printing_ids list shrank, the counters have not
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
from .export import EXPORT_PATH, SCHEMA_PATH, build_export, checksum_path, render
from .ids import id_number

REQUIRED_TRIGGERS = {
    "cards_no_delete", "cards_id_immutable",
    "printings_no_delete", "printings_id_immutable", "printings_card_immutable",
    "slug_history_no_reassign", "printings_slug_owned_insert",
    "printings_slug_owned_update",
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

    # A slug belongs permanently to one printing: every slug the registry has
    # ever used, current or historical, must name exactly one printing_id.
    owners = {}
    for row in con.execute("""
        SELECT slug, printing_id FROM slug_history
        UNION
        SELECT slug, printing_id FROM printings"""):
        owners.setdefault(row["slug"], set()).add(row["printing_id"])
    for slug in sorted(owners):
        if len(owners[slug]) > 1:
            errors.append(f"slug {slug!r} refers to more than one printing: "
                          f"{sorted(owners[slug])}")


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

    sha_path = checksum_path(export_path)
    if not sha_path.exists():
        errors.append(f"checksum file missing: {sha_path}")
    else:
        import hashlib
        actual = hashlib.sha256(committed.encode("utf-8")).hexdigest()
        stated = sha_path.read_text(encoding="utf-8").split()[0]
        if actual != stated:
            errors.append(f"{sha_path} does not match {export_path}; "
                          f"regenerate both with python -m registry.export")

    # Schema validation runs when the jsonschema package is available
    # (CI installs it); locally it degrades to a note, keeping the
    # pipeline stdlib-only.
    if SCHEMA_PATH.exists():
        try:
            import jsonschema
        except ImportError:
            print("note: jsonschema not installed, skipping schema validation")
        else:
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            validator = jsonschema.Draft202012Validator(schema)
            for error in validator.iter_errors(json.loads(committed)):
                path = "/".join(str(p) for p in error.absolute_path)
                errors.append(f"schema violation at /{path}: {error.message[:120]}")
                if len(errors) > 20:
                    break
    else:
        errors.append(f"schema file missing: {SCHEMA_PATH}")

    # Both directions of the card <-> printing link must tell the same story.
    export = build_export(con)
    forward = {}
    for printing in export["printings"]:
        forward.setdefault(printing["codex_id"], set()).add(printing["printing_id"])
    for card in export["cards"]:
        listed = set(card["printing_ids"])
        actual = forward.get(card["codex_id"], set())
        if listed != actual:
            errors.append(f"card {card['codex_id']}: printing_ids {sorted(listed)} "
                          f"disagrees with the printings table {sorted(actual)}")
        if card["printing_ids"] != sorted(card["printing_ids"]):
            errors.append(f"card {card['codex_id']}: printing_ids is not sorted")


def check_against_ref(con, ref, export_path, errors):
    result = subprocess.run(
        ["git", "show", f"{ref}:{Path(export_path).as_posix()}"],
        capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"note: no export at {ref}, skipping history check (first commit?)")
        return
    old = json.loads(result.stdout)
    new = build_export(con)

    # Ids are compared by their number so the check spans format changes
    # (exports from before the C/P-prefixed form carry bare integers), and
    # the card-level id is read under both its old name (card_id) and its
    # current one (codex_id).
    def codex_id_of(record):
        return record["codex_id"] if "codex_id" in record else record["card_id"]

    new_cards = {id_number(codex_id_of(c)): c for c in new["cards"]}
    for card in old["cards"]:
        current = new_cards.get(id_number(codex_id_of(card)))
        if current is None:
            errors.append(f"codex_id {codex_id_of(card)} ({card['name']!r}) existed at {ref} and is gone")
            continue
        # printing_ids may only ever grow ("printing_ids" guard: exports
        # from before the field existed have nothing to compare against).
        lost = ({id_number(p) for p in card.get("printing_ids", [])}
                - {id_number(p) for p in current["printing_ids"]})
        if lost:
            errors.append(f"codex_id {codex_id_of(card)}: printings {sorted(lost)} "
                          f"were listed at {ref} and are gone")

    new_printings = {id_number(p["printing_id"]): p for p in new["printings"]}
    history = {}
    for row in new["slug_history"]:
        history.setdefault(id_number(row["printing_id"]), set()).add(row["slug"])
    for printing in old["printings"]:
        pid = id_number(printing["printing_id"])
        current = new_printings.get(pid)
        if current is None:
            errors.append(f"printing_id {pid} ({printing['slug']!r}) existed at {ref} and is gone")
            continue
        if id_number(codex_id_of(current)) != id_number(codex_id_of(printing)):
            errors.append(f"printing_id {pid} moved from card {codex_id_of(printing)} "
                          f"to card {codex_id_of(current)}")
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
