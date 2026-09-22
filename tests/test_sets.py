"""What each set is: data/sets.json. A set code is a label, never a number,
so what a set is must be recorded; these tests pin that nothing is read
from a code's characters."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from registry.db import init_db, load_registry_state, open_db
from registry.diff import diff
from registry.export import build_export
from registry.fetch import build_snapshot
from registry.sets import check_sets, is_release, load_sets
from registry.sync import apply_plan

from test_pipeline import RAW_API


def populated():
    con = open_db(":memory:")
    init_db(con)
    apply_plan(con, diff(load_registry_state(con), build_snapshot(copy.deepcopy(RAW_API))),
               "2026-08-19")
    return con


class LoadTest(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sets.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_sets(path)

    def test_the_committed_file_classifies_every_real_set(self):
        kinds = load_sets(Path(__file__).resolve().parent.parent / "data" / "sets.json")
        self.assertEqual(kinds["001"], "release")
        self.assertEqual(kinds["999"], "promo")
        self.assertEqual(kinds["CUR"], "registry")

    def test_mistakes_are_errors(self):
        for bad in ({"sets": {"001": {"kind": "booster"}}},
                    {"sets": {"001": {"kind": "release", "extra": 1}}},
                    {"sets": {"001": "release"}},
                    {"sets": {"cur": {"kind": "registry"}}},    # ours are capitals
                    {"sets": {"998": {"kind": "registry"}}},    # never shaped like theirs
                    {"sets": []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.load(bad)

    def test_a_missing_file_classifies_nothing(self):
        self.assertEqual(load_sets(Path(tempfile.gettempdir()) / "no-such-sets.json"), {})


class MeaningTest(unittest.TestCase):
    def test_a_release_is_what_is_recorded_not_what_a_code_looks_like(self):
        kinds = {"001": "release", "999": "promo", "998": "promo", "CUR": "registry", "ABC": "release"}
        self.assertTrue(is_release("001", kinds))
        self.assertFalse(is_release("998", kinds))    # three digits, still not a release
        self.assertFalse(is_release("002", kinds))    # three digits, but not recorded
        self.assertFalse(is_release("CUR", kinds))
        self.assertTrue(is_release("ABC", kinds))
        self.assertFalse(is_release(None, kinds))

    def test_the_export_carries_each_sets_kind_and_derives_released_with_from_it(self):
        con = populated()
        # The fixture's sets are 001 and 010. Call 010 a promo bucket: its
        # printings then belong to no release until one is recorded.
        export = build_export(con, set_kinds={"001": "release", "010": "promo"}, released_with={})
        self.assertEqual({s["set_code"]: s["kind"] for s in export["sets"]},
                         {"001": "release", "010": "promo"})
        by_set = {p["set_code"]: p["released_with"] for p in export["printings"]}
        self.assertEqual(by_set, {"001": "001", "010": None})

    def test_the_validator_refuses_a_set_nobody_classified(self):
        con = populated()
        errors = []
        check_sets(con, {"001": "release"}, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("set 010 is not classified", errors[0])
        errors = []
        check_sets(con, {"001": "release", "010": "registry"}, errors)
        self.assertIn("the registry's own", errors[0])
        errors = []
        check_sets(con, {"001": "release", "010": "release"}, errors)
        self.assertEqual(errors, [])


class CurioTest(unittest.TestCase):
    """Every curio is a card of its own, named as one."""

    def setUp(self):
        self.con = populated()
        self.kinds = {"001": "release", "010": "release", "CUR": "registry"}
        self.card_id, self.name = self.con.execute(
            "SELECT card_id, name FROM cards ORDER BY card_id LIMIT 1").fetchone()

    def errors(self):
        errors = []
        check_sets(self.con, self.kinds, errors)
        return errors

    def make_curio(self, name):
        self.con.execute("UPDATE cards SET name = ? WHERE card_id = ?", (name, self.card_id))
        self.con.execute("UPDATE printings SET set_code = 'CUR', origin = 'manual' "
                         "WHERE card_id = ?", (self.card_id,))

    def test_a_curio_card_named_as_one_passes(self):
        self.make_curio(self.name + " (Curio)")
        self.assertEqual(self.errors(), [])

    def test_a_curio_needs_the_suffix(self):
        self.make_curio(self.name)
        self.assertIn("its name ends with '(Curio)'", self.errors()[0])

    def test_a_curio_is_never_a_printing_of_a_played_card(self):
        # Another card's printing moved into CUR: that card has official
        # printings too.
        other = self.con.execute("SELECT card_id FROM printings GROUP BY card_id "
                                 "HAVING count(*) > 1 LIMIT 1").fetchone()[0]
        pid = self.con.execute("SELECT printing_id FROM printings WHERE card_id = ? LIMIT 1",
                               (other,)).fetchone()[0]
        self.con.execute("UPDATE printings SET set_code = 'CUR', origin = 'manual' "
                         "WHERE printing_id = ?", (pid,))
        self.assertIn("never a printing of a card that is played", self.errors()[0])

    def test_the_suffix_is_only_for_curios(self):
        self.con.execute("UPDATE cards SET name = 'Troll (Curio)' WHERE card_id = ?",
                         (self.card_id,))
        self.assertIn("named as a curio, but none of its printings is in CUR", self.errors()[0])


if __name__ == "__main__":
    unittest.main()
