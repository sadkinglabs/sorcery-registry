"""Notes and the gap register: data/notes.json and data/gaps.json are read
as they are typed, so every shape mistake must be an error on load, and
the validator must catch a note or gap that disagrees with the registry."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from registry.db import init_db, load_registry_state, open_db
from registry.diff import diff
from registry.export import build_export
from registry.fetch import build_snapshot
from registry.notes import check_notes, load_gaps, load_notes
from registry.publish import build_objects
from registry.sync import apply_plan

from test_pipeline import RAW_API

NOTE = {"text": "Prize support in a store kit.", "source": "Community report, Sorcery Discord",
        "recorded": "2026-09-22"}
GAP = {"name": "The Champion", "codex_id": None, "text": "Printed, never served.",
       "source": "Community reports, Sorcery Discord", "recorded": "2026-09-22"}


def populated():
    con = open_db(":memory:")
    init_db(con)
    apply_plan(con, diff(load_registry_state(con), build_snapshot(copy.deepcopy(RAW_API))),
               "2026-08-19")
    return con


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, data):
        path = Path(self.dir.name) / "file.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_a_missing_file_means_none(self):
        missing = Path(self.dir.name) / "absent.json"
        self.assertEqual(load_notes(missing), {"cards": {}, "printings": {}})
        self.assertEqual(load_gaps(missing), [])

    def test_notes_load_in_fixed_key_order(self):
        typed = {"recorded": "2026-09-22", "source": NOTE["source"], "text": NOTE["text"]}
        notes = load_notes(self.write({"_comment": "x", "printings": {"P000001": [typed]}}))
        self.assertEqual(notes, {"cards": {}, "printings": {"P000001": [NOTE]}})
        self.assertEqual(list(notes["printings"]["P000001"][0]), ["text", "source", "recorded"])

    def test_note_mistakes_are_errors_not_dropped_notes(self):
        bad = [
            {"printings": {"C000001": [NOTE]}},                        # a card id among printings
            {"cards": {"P000001": [NOTE]}},                            # and the other way
            {"cards": {"C000001": []}},                                # an empty list
            {"cards": {"C000001": [dict(NOTE, text="")]}},             # no text
            {"cards": {"C000001": [dict(NOTE, source=" padded")]}},    # stray whitespace
            {"cards": {"C000001": [dict(NOTE, recorded="22/09/2026")]}},
            {"cards": {"C000001": [dict(NOTE, recorded="2026-02-30")]}},
            {"cards": {"C000001": [dict(NOTE, by="someone")]}},        # unknown key
            {"cards": {"C000001": [{"text": "t", "source": "s"}]}},    # missing key
            {"cards": {"C000001": [NOTE, NOTE]}},                      # the same note twice
            {"cards": {}, "extra": {}},
            [NOTE],
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                load_notes(self.write(data))

    def test_gaps_load_and_mistakes_are_errors(self):
        gaps = load_gaps(self.write({"gaps": [GAP, dict(GAP, name="Card One", codex_id="C000001")]}))
        self.assertEqual([g["name"] for g in gaps], ["The Champion", "Card One"])
        bad = [
            {"gaps": [dict(GAP, codex_id="P000001")]},
            {"gaps": [dict(GAP, codex_id="")]},
            {"gaps": [dict(GAP, name="")]},
            {"gaps": [GAP, dict(GAP, text="Same gap, reworded.")]},   # one entry per missing thing
            {"gaps": [{k: v for k, v in GAP.items() if k != "source"}]},
            {"gaps": {}},
            {"gaps": [], "extra": 1},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                load_gaps(self.write(data))


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.con = populated()

    def check(self, notes=None, gaps=()):
        errors = []
        check_notes(self.con, notes or {"cards": {}, "printings": {}}, list(gaps), errors)
        return errors

    def test_notes_on_records_that_exist_pass(self):
        self.assertEqual(self.check({"cards": {"C000001": [NOTE]},
                                     "printings": {"P000001": [NOTE]}}), [])

    def test_a_note_on_nothing_is_an_error(self):
        errors = self.check({"cards": {"C000999": [NOTE]}, "printings": {"P000999": [NOTE]}})
        self.assertEqual(len(errors), 2)
        self.assertIn("C000999", errors[0])
        self.assertIn("P000999", errors[1])

    def test_a_gap_must_agree_with_the_card_it_names(self):
        self.assertEqual(self.check(gaps=[dict(GAP, name="Apprentice Wizard",
                                               codex_id="C000001")]), [])
        wrong_name = self.check(gaps=[dict(GAP, name="Polar Bears", codex_id="C000001")])
        self.assertEqual(len(wrong_name), 1)
        self.assertIn("'Apprentice Wizard'", wrong_name[0])
        no_card = self.check(gaps=[dict(GAP, codex_id="C000999")])
        self.assertIn("C000999", no_card[0])

    def test_a_gap_without_a_card_must_not_name_one_the_registry_holds(self):
        self.assertEqual(self.check(gaps=[GAP]), [])
        errors = self.check(gaps=[dict(GAP, name="apprentice wizard")])
        self.assertEqual(len(errors), 1)
        self.assertIn("C000001", errors[0])


class ExportTest(unittest.TestCase):
    def test_notes_and_gaps_reach_the_export_and_the_objects(self):
        con = populated()
        notes = {"cards": {"C000001": [NOTE]}, "printings": {"P000002": [NOTE]}}
        export = build_export(con, notes=notes, gaps=[GAP])
        by_card = {c["codex_id"]: c for c in export["cards"]}
        by_printing = {p["printing_id"]: p for p in export["printings"]}
        self.assertEqual(by_card["C000001"]["notes"], [NOTE])
        self.assertEqual(by_printing["P000002"]["notes"], [NOTE])
        self.assertEqual(by_printing["P000001"]["notes"], [])
        self.assertTrue(all("notes" in c for c in export["cards"]))
        self.assertEqual(export["gaps"], [GAP])
        self.assertEqual(export["header"]["gaps"], 1)

        objects = build_objects(export, dataset_version="v9.9.9")
        self.assertEqual(objects["gaps.json"], [GAP])
        self.assertEqual(objects["cards/C000001.json"]["notes"], [NOTE])
        self.assertEqual(objects["printings/P000002.json"]["notes"], [NOTE])
        self.assertEqual(objects["index.json"]["counts"]["gaps"], 1)
        self.assertEqual(objects["index.json"]["endpoints"]["gaps"], "gaps.json")
        # The export's notes are copies: changing one never touches the input.
        by_card["C000001"]["notes"][0]["text"] = "changed"
        self.assertEqual(NOTE["text"], "Prize support in a store kit.")


class CommittedFilesTest(unittest.TestCase):
    """The committed files load: a typo in data/ fails here, not at release."""

    def test_the_committed_notes_and_gaps_load(self):
        root = Path(__file__).resolve().parent.parent / "data"
        load_notes(root / "notes.json")
        load_gaps(root / "gaps.json")


if __name__ == "__main__":
    unittest.main()
