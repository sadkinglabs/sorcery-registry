"""Sync the registry against the official API.

    python -m registry.sync                  # fetch, diff, confirm, apply
    python -m registry.sync --yes            # no prompt (CI)
    python -m registry.sync --dry-run        # show the plan, change nothing
    python -m registry.sync --interactive    # resolve ambiguities at the prompt
    python -m registry.sync --from-file X    # use a saved API response

Every run that goes to the network saves the raw payload to
review/upstream-snapshot.json before doing anything with it, so a later
--from-file run can act on the exact same bytes. Runs that already read
--from-file leave the snapshot alone.

Exit codes: 0 clean (applied, or nothing to do), 2 ambiguous cases were
quarantined to review/pending.json, 1 error.

Ambiguity flow: the run writes review/pending.json describing each case
and its candidates, and exits 2 without applying anything from those cases.
A human writes review/decisions.json (see CONTRIBUTING.md), re-runs the
sync, and the decisions are validated against the live diff, applied, and
archived to review/archive/. Unambiguous changes are applied either way.
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from .db import (CARD_FIELDS, ERRATA_FIELDS, HISTORY_FIELDS, PRINTING_FIELDS,
                 allocate_id, decode_field, encode_field, face_of, get_meta,
                 init_db, load_registry_state, open_db)
from .diff import diff, is_noop, summarise
from .export import write_export
from .ids import id_number
from .fetch import apply_overrides, build_snapshot, fetch_api, load_api_file, load_overrides

PENDING_PATH = Path("review") / "pending.json"
DECISIONS_PATH = Path("review") / "decisions.json"
ARCHIVE_DIR = Path("review") / "archive"
SNAPSHOT_PATH = Path("review") / "upstream-snapshot.json"
OVERRIDES_PATH = Path("data") / "overrides.json"


def apply_plan(con, plan, as_of, retroactive=None):
    """Write an unambiguous plan to the database in one transaction.
    Ids are only ever allocated here, from the meta counters, and no
    branch updates or deletes an id: the schema's triggers would abort
    the transaction if one tried.

    `retroactive` names, per card, the fields an override corrected
    retroactively: the card always had that value and the registry's
    record was wrong. Those are written to every history row rather than
    opening a new one, because a card whose classification we mis-recorded
    did not change - and a new row would report every printing of it as
    showing older values, which is a claim about the printed card.

    Any failure rolls the whole thing back before re-raising: the caller
    keeps the connection, and a half-applied transaction left open on it
    would otherwise still be visible to every later read."""
    try:
        _apply_plan(con, plan, as_of, retroactive or {})
    except Exception:
        con.rollback()
        raise


def _apply_plan(con, plan, as_of, retroactive=None):
    cur = con.cursor()
    retroactive = retroactive or {}

    for rename in plan["card_renames"]:
        cur.execute("UPDATE cards SET name = ? WHERE card_id = ?",
                    (rename["new_name"], rename["card_id"]))
        cur.execute(
            "UPDATE name_history SET valid_to = ? "
            "WHERE card_id = ? AND name = ? AND valid_to IS NULL",
            (as_of, rename["card_id"], rename["old_name"]))
        cur.execute(
            "INSERT INTO name_history (name, card_id, valid_from, valid_to) "
            "VALUES (?, ?, ?, NULL)",
            (rename["new_name"], rename["card_id"], as_of))

    for card in plan["new_cards"]:
        card_id = allocate_id(con, "next_card_id")
        columns = ", ".join(CARD_FIELDS)
        holes = ", ".join("?" for _ in CARD_FIELDS)
        cur.execute(
            f"INSERT INTO cards (card_id, {columns}) VALUES (?, {holes})",
            [card_id] + [encode_field(field, card.get(field)) for field in CARD_FIELDS])
        cur.execute(
            "INSERT INTO name_history (name, card_id, valid_from, valid_to) "
            "VALUES (?, ?, ?, NULL)",
            (card["name"], card_id, as_of))
        cur.execute(
            "INSERT INTO card_history (card_id, valid_from, valid_to, face) "
            "VALUES (?, ?, NULL, ?)",
            (card_id, as_of, face_of(card)))

    card_ids = {row["name"]: row["card_id"]
                for row in con.execute("SELECT name, card_id FROM cards")}

    # A manual card upstream now serves keeps its id and becomes upstream's
    # to speak for. Its face row, read from the card, stays as the record
    # of what was read; a difference upstream closes it below like any
    # other change.
    for confirm in plan.get("confirm_cards", []):
        cur.execute("UPDATE cards SET origin = 'api', confirmed_at = ?, withdrawn_at = NULL, "
                    "withdrawn_reason = NULL WHERE card_id = ? AND origin = 'manual'",
                    (as_of, confirm["card_id"]))
        if cur.rowcount != 1:
            raise ValueError(f"confirm: card {confirm['card_id']} is not a manual card")

    for update in plan["card_updates"]:
        for field, change in update["changes"].items():
            value = change["new"]
            if field == "default_printing_id" and value is not None:
                value = id_number(value)
            cur.execute(f"UPDATE cards SET {field} = ? WHERE card_id = ?",
                        (encode_field(field, value), update["card_id"]))
        changed = set(update["changes"])
        # A retroactively corrected field was never otherwise: rewrite it into
        # every face already recorded, and leave it out of what counts as a
        # change the card underwent.
        corrected = changed & set(retroactive.get(update.get("name"), ()))
        if corrected:
            for row in con.execute(
                    "SELECT rowid, face FROM card_history WHERE card_id = ?",
                    (update["card_id"],)).fetchall():
                face = json.loads(row["face"])
                for field in corrected:
                    face[field] = update["changes"][field]["new"]
                cur.execute("UPDATE card_history SET face = ? WHERE rowid = ?",
                            (face_of(face), row["rowid"]))
        changed -= corrected
        if changed & set(HISTORY_FIELDS):
            row = con.execute("SELECT * FROM cards WHERE card_id = ?",
                              (update["card_id"],)).fetchone()
            face = face_of({f: decode_field(f, row[f]) for f in HISTORY_FIELDS})
            open_row = con.execute(
                "SELECT rowid, valid_from, source FROM card_history "
                "WHERE card_id = ? AND valid_to IS NULL", (update["card_id"],)).fetchone()
            # History is kept by date. A second change on the same day rewrites
            # that day's row rather than closing it at its own start, which
            # would claim a face was current from a day to the same day - never.
            # A row transcribed from the printed card is never rewritten here:
            # a sync only ever speaks for what the API serves.
            if open_row and open_row["valid_from"] == as_of and open_row["source"] == "api":
                cur.execute("UPDATE card_history SET face = ? WHERE rowid = ?",
                            (face, open_row["rowid"]))
            else:
                cur.execute(
                    "UPDATE card_history SET valid_to = ? "
                    "WHERE card_id = ? AND valid_to IS NULL",
                    (as_of, update["card_id"]))
                cur.execute(
                    "INSERT INTO card_history (card_id, valid_from, valid_to, face) "
                    "VALUES (?, ?, NULL, ?)",
                    (update["card_id"], as_of, face))
        if changed & set(ERRATA_FIELDS):
            cur.execute("UPDATE cards SET errata = 1 WHERE card_id = ?",
                        (update["card_id"],))

    for printing in plan["new_printings"]:
        printing_id = allocate_id(con, "next_printing_id")
        columns = ", ".join(PRINTING_FIELDS)
        holes = ", ".join("?" for _ in PRINTING_FIELDS)
        cur.execute(
            f"INSERT INTO printings (printing_id, card_id, {columns}) VALUES (?, ?, {holes})",
            [printing_id, card_ids[printing["card_name"]]]
            + [encode_field(field, printing.get(field)) for field in PRINTING_FIELDS])
        cur.execute(
            "INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
            "VALUES (?, ?, ?, NULL)",
            (printing["slug"], printing_id, as_of))

    # A manual printing upstream now serves takes upstream's slug, which
    # from today is owned like any other; its predicted slug owned nothing
    # and leaves no history.
    for confirm in plan.get("confirm_printings", []):
        cur.execute("UPDATE printings SET slug = ?, origin = 'api', confirmed_at = ?, "
                    "withdrawn_at = NULL, withdrawn_reason = NULL "
                    "WHERE printing_id = ? AND origin = 'manual'",
                    (confirm["new_slug"], as_of, confirm["printing_id"]))
        if cur.rowcount != 1:
            raise ValueError(f"confirm: printing {confirm['printing_id']} is not a manual printing")
        cur.execute(
            "INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
            "VALUES (?, ?, ?, NULL)",
            (confirm["new_slug"], confirm["printing_id"], as_of))

    for rename in plan["printing_renames"]:
        cur.execute("UPDATE printings SET slug = ? WHERE printing_id = ?",
                    (rename["new_slug"], rename["printing_id"]))
        cur.execute(
            "UPDATE slug_history SET valid_to = ? "
            "WHERE printing_id = ? AND slug = ? AND valid_to IS NULL",
            (as_of, rename["printing_id"], rename["old_slug"]))
        cur.execute(
            "INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
            "VALUES (?, ?, ?, NULL)",
            (rename["new_slug"], rename["printing_id"], as_of))

    for update in plan["printing_updates"]:
        for field, change in update["changes"].items():
            cur.execute(f"UPDATE printings SET {field} = ? WHERE printing_id = ?",
                        (encode_field(field, change["new"]), update["printing_id"]))

    for retire in plan["retire_printings"]:
        cur.execute("UPDATE printings SET retired_at = ? WHERE printing_id = ?",
                    (as_of, retire["printing_id"]))

    for unretire in plan["unretire_printings"]:
        cur.execute("UPDATE printings SET retired_at = NULL WHERE printing_id = ?",
                    (unretire["printing_id"],))

    con.commit()


def save_snapshot(raw, path=SNAPSHOT_PATH):
    """Keep the raw payload of a network fetch on disk, so the same run can
    be repeated exactly with --from-file. It is a working artifact, not a
    committed one: .gitignore excludes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(raw, ensure_ascii=False) + "\n")


def resolve_interactively(plan):
    """Walk each quarantined case at the prompt and build a decisions dict."""
    decisions = {"card_renames": [], "new_cards": [], "printing_renames": [],
                 "new_printings": [], "retire_printings": [], "confirm_cards": [],
                 "confirm_printings": []}
    for case in plan["ambiguous"]:
        print(f"\nAmbiguous ({case['kind']}): {case['problem']}")
        print(json.dumps({k: v for k, v in case.items() if k not in ("kind", "problem")},
                         indent=2, ensure_ascii=False))
        if case["kind"] != "printing" or "card" not in case:
            print("This case cannot be resolved interactively; edit review/decisions.json instead.")
            continue
        for old in case["missing"]:
            options = case["candidates"]
            print(f"\nRegistry printing {old['printing_id']} ({old['slug']}) vanished. Candidates:")
            for index, candidate in enumerate(options, 1):
                print(f"  {index}. {candidate['slug']}")
            print("  r. it was really removed (retire it)")
            print("  s. skip, leave for review/decisions.json")
            choice = input("> ").strip().lower()
            if choice == "r":
                decisions["retire_printings"].append(old["printing_id"])
            elif choice.isdigit() and 1 <= int(choice) <= len(options):
                decisions["printing_renames"].append(
                    {"printing_id": old["printing_id"],
                     "new_slug": options[int(choice) - 1]["slug"]})
    return decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="registry.sqlite")
    parser.add_argument("--from-file", help="read the API response from a JSON file instead of the network")
    parser.add_argument("--yes", action="store_true", help="apply without prompting for confirmation")
    parser.add_argument("--dry-run", action="store_true", help="show the plan and change nothing")
    parser.add_argument("--interactive", action="store_true", help="resolve ambiguous cases at the prompt")
    parser.add_argument("--as-of", default=None, help="date stamp for history rows, default today (UTC)")
    parser.add_argument("--init", action="store_true", help="create a fresh empty database first")
    args = parser.parse_args()

    as_of = args.as_of or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(as_of)  # fail fast on a malformed --as-of

    db_path = Path(args.db)
    con = open_db(db_path)
    if args.init:
        has_schema = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'meta'").fetchone()[0]
        if has_schema:
            sys.exit("--init refused: the database already has a schema")
        init_db(con)
    if get_meta(con, "schema_version") != str(SCHEMA_VERSION):
        sys.exit(f"database schema version does not match code ({SCHEMA_VERSION}); refusing to run")

    print("fetching API data..." if not args.from_file else f"reading {args.from_file}...")
    if args.from_file:
        raw = load_api_file(args.from_file)
    else:
        raw = fetch_api()
        save_snapshot(raw)
        print(f"snapshot saved to {SNAPSHOT_PATH.as_posix()} "
              f"(re-run with --from-file to act on these exact bytes)")
    snapshot = build_snapshot(raw)
    if OVERRIDES_PATH.exists():
        unmatched, retroactive = apply_overrides(snapshot, load_overrides(OVERRIDES_PATH))
        for entry in unmatched:
            print(f"note: override no longer matches anything (upstream fixed?): "
                  f"{entry['match']} - {entry['reason']}")

    decisions = None
    if DECISIONS_PATH.exists():
        decisions = json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))
        print(f"applying human decisions from {DECISIONS_PATH}")

    registry = load_registry_state(con)
    try:
        plan = diff(registry, snapshot, decisions)
    except ValueError as error:
        sys.exit(f"error: {error}")

    if plan["ambiguous"] and args.interactive:
        extra = resolve_interactively(plan)
        merged = {key: (decisions or {}).get(key, []) + extra.get(key, [])
                  for key in set(extra) | set(decisions or {})}
        plan = diff(registry, snapshot, merged)
        decisions = merged

    print(f"\nplan against {len(snapshot['cards'])} API cards / "
          f"{len(snapshot['printings'])} API printings:")
    print(summarise(plan))

    if is_noop(plan):
        print("\nnothing to do")
        return 0

    if plan["ambiguous"]:
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        PENDING_PATH.write_text(
            json.dumps({"as_of": as_of, "ambiguous": plan["ambiguous"]},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"\n{len(plan['ambiguous'])} ambiguous case(s) written to {PENDING_PATH}")
        print("resolve them in review/decisions.json and re-run (see CONTRIBUTING.md)")

    if args.dry_run:
        print("\ndry run: nothing applied")
        return 2 if plan["ambiguous"] else 0

    if not args.yes:
        answer = input("\napply the unambiguous changes above? [y/N] ").strip().lower()
        if answer != "y":
            print("aborted, nothing applied")
            return 2 if plan["ambiguous"] else 0

    apply_plan(con, plan, as_of, retroactive)
    write_export(con)
    print("\napplied and exported")

    if decisions is not None and DECISIONS_PATH.exists():
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        DECISIONS_PATH.rename(ARCHIVE_DIR / f"{as_of}-decisions.json")
        print(f"decisions archived to {ARCHIVE_DIR}")
    if not plan["ambiguous"] and PENDING_PATH.exists():
        PENDING_PATH.unlink()

    return 2 if plan["ambiguous"] else 0


if __name__ == "__main__":
    sys.exit(main())
