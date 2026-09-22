"""What changed between two releases: changes.json at every release root.

    python -m registry.changes --previous prev/registry.json --current export/registry.json \
        --from v3.3.0 --to v3.3.1 [--out changes.json]
    python -m registry.changes --check changes.json

A consumer moving from one release to the next wants to know, before
downloading anything, whether it matters to them: which cards changed and
in which fields, which printings are new, which images were replaced, and
that no identifier they store has gone away. The release workflow builds
this document from the previous release's export (from git, by tag) and
the one being released, and publishes it next to index.json:

    {
      "from": "v3.3.0", "to": "v3.3.1",
      "schema_version": {"from": 12, "to": 12},
      "summary": {"cards_added": 0, "cards_changed": 2, "cards_removed": 0,
                  "printings_added": 4, "printings_changed": 1, "printings_removed": 0,
                  "sets_added": 0, "images_added": 3, "images_replaced": 1,
                  "history_rows_added": 2, "notes_added": 1, "notes_removed": 0,
                  "manual_added": 4, "manual_confirmed": 0, "manual_withdrawn": 0,
                  "identifiers_removed": 0},
      "cards": {"added": [], "changed": [{"codex_id": "C000230", "name": "Polar Bears",
                                          "fields": ["rules_text", "errata"]}], "removed": []},
      "printings": {"added": ["P003089"], "changed": [{"printing_id": ..., "codex_id": ...,
                                                       "fields": ["slug"]}], "removed": []},
      "sets": {"added": []},
      "images": {"added": ["P003089"], "replaced": ["P000937"]},
      "history": {"added": [{"codex_id": "C000230", "valid_from": "2026-09-15", "source": "card"}]},
      "notes": {"added": [{"id": "P001640", "text": ..., "source": ..., "recorded": "2026-09-22"}],
                "removed": []},
      "manual": {"added": ["C001101", "P003089", "P003090", "P003091"],
                 "confirmed": [], "withdrawn": []}
    }

Notes have a section of their own: a note added to a printing is
reported there, not as the printing changing. `manual` names the records
added by hand (also counted as added above), the manual records upstream
now serves and a person confirmed, and those withdrawn as wrong.

A release that adds a field changes the shape, which schema_version
reports, not every record that carries it: a new field counts only on
records where it says something beyond its default, such as a promo's
released_with recorded by hand.

`identifiers_removed` is the sum of removed cards and printings and is
zero by the registry's first rule: ids are permanent. `--check` enforces
it, failing when a release within the same major removes one, so the
guarantee is proven at release time rather than promised.

With no previous export (the first release ever) `from` is null and
everything counts as added. Pure: the same two exports give the same
document, key order included.
"""

import argparse
import json
import sys
from pathlib import Path

from .versions import parse_tag


def _by(records, key):
    return {r[key]: r for r in records}


# Fields reported in their own section rather than as a change to the
# record, so a note added to a printing reads as a note, not as the
# printing changing.
_OWN_SECTION = ("notes",)


def _says_nothing(record, key, value):
    """Whether a field that is new in this release holds only what every
    record gets by default, rather than something learned about this one.
    Empty is always a default. A new field whose default is not empty is
    listed here: origin is "api" for every record the API serves, and
    released_with repeats the printing's own set unless it was recorded
    by hand for a promo or curio."""
    if value in (None, [], {}, ""):
        return True
    if key == "origin":
        return value == "api"
    if key == "released_with":
        return value == record.get("set_code")
    return False


def _changed_fields(before, after):
    """Field names whose values differ, in the record's own key order.
    A field this release adds counts only where it says something about
    this record (_says_nothing): adding a field changes the shape, which
    schema_version reports, not every record that carries it - but a
    value recorded for one record is news about that record. A field this
    release drops is a change of shape alone."""
    fields = []
    for key in after:
        if key in _OWN_SECTION:
            continue
        if key not in before:
            if not _says_nothing(after, key, after[key]):
                fields.append(key)
        elif before[key] != after[key]:
            fields.append(key)
    return fields


def _notes(export):
    """Every note in an export as (record id, note), cards then printings.
    An export from before notes existed has none."""
    out = []
    if not export:
        return out
    for section, key in (("cards", "codex_id"), ("printings", "printing_id")):
        for record in export[section]:
            for note in record.get("notes") or []:
                out.append((record[key], note))
    return out


def _note_key(record_id, note):
    return (record_id, note["text"])


def _flipped(prev, cur):
    """Records in both exports that went from manual to api (confirmed
    upstream), and manual records newly marked withdrawn."""
    confirmed, withdrawn = [], []
    for record_id in sorted(i for i in cur if i in prev):
        before, after = prev[record_id], cur[record_id]
        if before.get("origin") == "manual" and after.get("origin") == "api":
            confirmed.append(record_id)
        was = (before.get("manual") or {}).get("withdrawn")
        now = (after.get("manual") or {}).get("withdrawn")
        if now and not was:
            withdrawn.append(record_id)
    return confirmed, withdrawn


def _front_key(printing):
    return printing.get("image_hash")


def _back_key(printing):
    back = printing.get("back") or {}
    urls = back.get("image_urls") or {}
    return urls.get("original")


def _image_change(before, after):
    """'added', 'replaced' or None for one printing, across both faces."""
    verdict = None
    for read in (_front_key, _back_key):
        old, new = read(before) if before else None, read(after)
        if old is None and new is not None:
            verdict = verdict or "added"
        elif old is not None and new is not None and old != new:
            verdict = "replaced"
    return verdict


def _history_key(row):
    return (row["codex_id"], row["valid_from"], row.get("source"))


def diff_exports(previous, current, from_tag, to_tag):
    """The changes document for `previous` -> `current`. `previous` may be
    None. Pure."""
    prev_cards = _by(previous["cards"], "codex_id") if previous else {}
    cur_cards = _by(current["cards"], "codex_id")
    prev_prints = _by(previous["printings"], "printing_id") if previous else {}
    cur_prints = _by(current["printings"], "printing_id")
    prev_sets = {s["set_code"] for s in previous["sets"]} if previous else set()

    cards_added = sorted(i for i in cur_cards if i not in prev_cards)
    cards_removed = sorted(i for i in prev_cards if i not in cur_cards)
    cards_changed = []
    for codex_id in sorted(i for i in cur_cards if i in prev_cards):
        fields = _changed_fields(prev_cards[codex_id], cur_cards[codex_id])
        if fields:
            cards_changed.append({"codex_id": codex_id, "name": cur_cards[codex_id]["name"],
                                  "fields": fields})

    prints_added = sorted(i for i in cur_prints if i not in prev_prints)
    prints_removed = sorted(i for i in prev_prints if i not in cur_prints)
    prints_changed = []
    for printing_id in sorted(i for i in cur_prints if i in prev_prints):
        fields = _changed_fields(prev_prints[printing_id], cur_prints[printing_id])
        if fields:
            prints_changed.append({"printing_id": printing_id,
                                   "codex_id": cur_prints[printing_id]["codex_id"],
                                   "fields": fields})

    images_added, images_replaced = [], []
    for printing_id in sorted(cur_prints):
        verdict = _image_change(prev_prints.get(printing_id), cur_prints[printing_id])
        if verdict == "added":
            images_added.append(printing_id)
        elif verdict == "replaced":
            images_replaced.append(printing_id)

    sets_added = sorted(s["set_code"] for s in current["sets"] if s["set_code"] not in prev_sets)

    prev_rows = {_history_key(r) for r in previous["card_history"]} if previous else set()
    history_added = [
        {"codex_id": r["codex_id"], "valid_from": r["valid_from"], "source": r.get("source")}
        for r in current["card_history"] if _history_key(r) not in prev_rows]
    history_added.sort(key=lambda r: (r["codex_id"], r["valid_from"] or ""))

    cards_confirmed, cards_withdrawn = _flipped(prev_cards, cur_cards)
    prints_confirmed, prints_withdrawn = _flipped(prev_prints, cur_prints)
    manual_added = sorted(i for i in cards_added if cur_cards[i].get("origin") == "manual") + \
        sorted(i for i in prints_added if cur_prints[i].get("origin") == "manual")

    # A note is the same note while its record and text are; rewording one
    # reads as one removed and one added, which is what a consumer showing
    # notes needs to know.
    prev_notes = {_note_key(i, n) for i, n in _notes(previous)}
    cur_notes = {_note_key(i, n) for i, n in _notes(current)}
    notes_added = [{"id": i, **n} for i, n in _notes(current) if _note_key(i, n) not in prev_notes]
    notes_removed = [{"id": i, **n} for i, n in _notes(previous) if _note_key(i, n) not in cur_notes]

    return {
        "from": from_tag,
        "to": to_tag,
        "schema_version": {
            "from": previous["header"]["schema_version"] if previous else None,
            "to": current["header"]["schema_version"],
        },
        "summary": {
            "cards_added": len(cards_added),
            "cards_changed": len(cards_changed),
            "cards_removed": len(cards_removed),
            "printings_added": len(prints_added),
            "printings_changed": len(prints_changed),
            "printings_removed": len(prints_removed),
            "sets_added": len(sets_added),
            "images_added": len(images_added),
            "images_replaced": len(images_replaced),
            "history_rows_added": len(history_added),
            "notes_added": len(notes_added),
            "notes_removed": len(notes_removed),
            "manual_added": len(manual_added),
            "manual_confirmed": len(cards_confirmed) + len(prints_confirmed),
            "manual_withdrawn": len(cards_withdrawn) + len(prints_withdrawn),
            "identifiers_removed": len(cards_removed) + len(prints_removed),
        },
        "cards": {"added": cards_added, "changed": cards_changed, "removed": cards_removed},
        "printings": {"added": prints_added, "changed": prints_changed, "removed": prints_removed},
        "sets": {"added": sets_added},
        "images": {"added": images_added, "replaced": images_replaced},
        "history": {"added": history_added},
        "notes": {"added": notes_added, "removed": notes_removed},
        "manual": {"added": manual_added,
                   "confirmed": cards_confirmed + prints_confirmed,
                   "withdrawn": cards_withdrawn + prints_withdrawn},
    }


def summary_line(changes):
    """One line for a log or a release note: '2 cards changed · 4 printings
    added · 1 image replaced · 0 identifiers removed'."""
    s = changes["summary"]
    parts = []
    for key, one, many in (("cards_added", "card added", "cards added"),
                           ("cards_changed", "card changed", "cards changed"),
                           ("printings_added", "printing added", "printings added"),
                           ("printings_changed", "printing changed", "printings changed"),
                           ("sets_added", "set added", "sets added"),
                           ("images_added", "image added", "images added"),
                           ("images_replaced", "image replaced", "images replaced"),
                           ("history_rows_added", "history row added", "history rows added"),
                           ("notes_added", "note added", "notes added"),
                           ("notes_removed", "note removed", "notes removed"),
                           ("manual_added", "record added by hand", "records added by hand"),
                           ("manual_confirmed", "manual record confirmed upstream",
                            "manual records confirmed upstream"),
                           ("manual_withdrawn", "manual record withdrawn",
                            "manual records withdrawn")):
        if s.get(key):
            parts.append(f"{s[key]} {one if s[key] == 1 else many}")
    parts.append(f"{s['identifiers_removed']} identifiers removed")
    return " · ".join(parts)


def check(changes):
    """Problems with a changes document, as strings: identifiers removed
    within a major. Empty when the release keeps the registry's promises."""
    removed = changes["summary"]["identifiers_removed"]
    if not removed:
        return []
    if changes["from"] and parse_tag(changes["from"])[0] != parse_tag(changes["to"])[0]:
        return []
    ids = changes["cards"]["removed"] + changes["printings"]["removed"]
    return [f"{removed} identifier(s) removed between {changes['from']} and {changes['to']}: "
            f"{', '.join(ids[:10])}{'...' if len(ids) > 10 else ''}. Ids are permanent; "
            f"a removal is a breaking change and needs a new major."]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--previous", help="the previous release's registry.json")
    parser.add_argument("--current", help="the export being released")
    parser.add_argument("--from", dest="from_tag", help="the previous release tag")
    parser.add_argument("--to", dest="to_tag", help="the tag being released")
    parser.add_argument("--out", help="where to write changes.json (default: stdout)")
    parser.add_argument("--check", help="a changes.json to print and check instead of building one")
    args = parser.parse_args()

    if args.check:
        changes = json.loads(Path(args.check).read_text(encoding="utf-8"))
        print(f"{changes['from'] or 'nothing'} -> {changes['to']}: {summary_line(changes)}")
        problems = check(changes)
        for problem in problems:
            print(f"::error::{problem}")
        return 1 if problems else 0

    if not args.current or not args.to_tag:
        parser.error("--current and --to are required to build a changes document")
    current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    previous = json.loads(Path(args.previous).read_text(encoding="utf-8")) if args.previous else None
    changes = diff_exports(previous, current, args.from_tag if previous else None, args.to_tag)
    text = json.dumps(changes, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"{changes['from'] or 'nothing'} -> {changes['to']}: {summary_line(changes)}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
