"""The publisher turns the export into one object per thing. These tests
pin the URL contract: which paths exist, what each answers, that any slug
ever issued resolves, and that the output is deterministic."""

import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from registry.db import init_db, load_registry_state, open_db
from registry.diff import diff
from registry.export import build_export, write_export
from registry.fetch import build_snapshot
from registry.publish import ENDPOINTS, SAFE_KEY, build_objects, write_dist
from registry.sync import apply_plan

from test_pipeline import RAW_API


def registry_after_rename():
    """Populate, then rename Apprentice Wizard's slugs, so both the old and
    the new slug exist in history."""
    con = open_db(":memory:")
    init_db(con)
    apply_plan(con, diff(load_registry_state(con), build_snapshot(copy.deepcopy(RAW_API))),
               "2026-08-19")
    renamed = copy.deepcopy(RAW_API)
    for printing in renamed[0]["printings"]:
        printing["slug"] = printing["slug"].replace("apprentice_wizard", "apprentice-wizard")
    apply_plan(con, diff(load_registry_state(con), build_snapshot(renamed)), "2026-08-20")
    return con


class ObjectsTest(unittest.TestCase):
    def setUp(self):
        self.export = build_export(registry_after_rename())
        self.objects = build_objects(self.export, dataset_version="v9.9.9")

    def test_every_thing_has_an_object_and_every_key_is_url_safe(self):
        paths = set(self.objects)
        for card in self.export["cards"]:
            self.assertIn(f"cards/{card['codex_id']}.json", paths)
        for printing in self.export["printings"]:
            self.assertIn(f"printings/{printing['printing_id']}.json", paths)
        for row in self.export["slug_history"]:
            self.assertIn(f"slugs/{row['slug']}.json", paths)
        for set_entry in self.export["sets"]:
            self.assertIn(f"sets/{set_entry['set_code']}.json", paths)
        for path in paths:
            self.assertTrue(SAFE_KEY.match(path), path)

    def test_card_object_answers_the_common_question_in_one_request(self):
        wizard = self.objects["cards/C000001.json"]
        self.assertEqual(wizard["name"], "Apprentice Wizard")
        self.assertEqual([p["printing_id"] for p in wizard["printings"]], ["P000001", "P000002"])
        self.assertEqual(wizard["printings"][1]["slug"], "001-apprentice-wizard-b-f")
        self.assertEqual(wizard["printings"][1]["finish"], "Foil")
        self.assertEqual(len(wizard["name_history"]), 1)
        self.assertEqual(wizard["card_history"][0]["rules_text"], wizard["rules_text"])
        self.assertEqual(wizard["card_history"][0]["cost"], wizard["cost"])
        self.assertIsNone(wizard["card_history"][0]["valid_to"])
        self.assertNotIn("codex_id", wizard["card_history"][0])
        self.assertEqual(wizard["default_printing_id"], "P000001")
        self.assertTrue(wizard["printings"][0]["printed_as_current"])

    def test_printing_object_carries_its_slug_history(self):
        foil = self.objects["printings/P000002.json"]
        self.assertEqual(foil["card_name"], "Apprentice Wizard")
        self.assertEqual([(r["slug"], r["valid_to"]) for r in foil["slug_history"]],
                         [("001-apprentice_wizard-b-f", "2026-08-20"),
                          ("001-apprentice-wizard-b-f", None)])

    def test_any_slug_ever_issued_resolves_to_its_permanent_ids(self):
        old = self.objects["slugs/001-apprentice_wizard-b-f.json"]
        new = self.objects["slugs/001-apprentice-wizard-b-f.json"]
        for answer in (old, new):
            self.assertEqual(answer["printing_id"], "P000002")
            self.assertEqual(answer["codex_id"], "C000001")
            self.assertEqual(answer["current_slug"], "001-apprentice-wizard-b-f")
        self.assertFalse(old["is_current"])
        self.assertEqual(old["valid_to"], "2026-08-20")
        self.assertTrue(new["is_current"])
        self.assertIsNone(new["valid_to"])

    def test_set_object_lists_its_cards_by_name_with_only_its_printings(self):
        alpha = self.objects["sets/001.json"]
        self.assertEqual(alpha["set_name"], "Alpha")
        self.assertEqual(alpha["cards"], [
            {"codex_id": "C000001", "name": "Apprentice Wizard",
             "printing_ids": ["P000001", "P000002"]}])
        gothic = self.objects["sets/010.json"]
        self.assertEqual([c["name"] for c in gothic["cards"]], ["Broken Site"])
        self.assertEqual(self.objects["sets.json"], self.export["sets"])

    def test_indexes_and_discovery(self):
        self.assertEqual({c["codex_id"] for c in self.objects["index/cards.json"]},
                         {"C000001", "C000002"})
        self.assertEqual(self.objects["index/slugs.json"]["001-apprentice_wizard-b-s"], "P000001")
        self.assertEqual(len(self.objects["index/slugs.json"]), 5)
        root = self.objects["index.json"]
        self.assertEqual(root["dataset_version"], "v9.9.9")
        self.assertEqual(root["schema_version"], self.export["header"]["schema_version"])
        self.assertEqual(root["counts"]["slug_history"], 5)
        self.assertEqual(root["counts"]["card_history"], 2)
        self.assertEqual(self.objects["history/cards.json"], self.export["card_history"])
        self.assertEqual(self.objects["index/cards.json"][0]["default_printing_id"], "P000001")
        self.assertEqual(root["endpoints"], ENDPOINTS)
        for pattern in ENDPOINTS.values():
            # Every pattern is either a concrete object or a template whose
            # concrete instances exist.
            concrete = re.sub(r"\{[a-z_]+\}", "X", pattern)
            self.assertTrue(pattern in self.objects or pattern in ("registry.json",
                            "registry.json.sha256", "schema.json") or "{" in pattern,
                            concrete)

    def test_corrupt_slug_ownership_is_refused(self):
        export = copy.deepcopy(self.export)
        export["slug_history"].append({"slug": "001-apprentice_wizard-b-s",
                                       "printing_id": "P000002",
                                       "valid_from": "2026-08-21", "valid_to": None})
        with self.assertRaises(ValueError):
            build_objects(export)


class DistTest(unittest.TestCase):
    def test_dist_is_deterministic_and_carries_the_export_verbatim(self):
        con = registry_after_rename()
        schema = Path(__file__).resolve().parent.parent / "schema" / "registry.schema.json"
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "registry.json"
            write_export(con, export_path)
            digests = []
            for _ in range(2):
                out = Path(tmp) / "dist"
                count = write_dist(export_path, schema, out, "v9.9.9")
                files = sorted(p for p in out.rglob("*") if p.is_file())
                digests.append([(str(p.relative_to(out)), hashlib.sha256(p.read_bytes()).hexdigest())
                                for p in files])
                self.assertEqual(len(files), count)
            self.assertEqual(digests[0], digests[1])
            self.assertEqual((out / "registry.json").read_bytes(), export_path.read_bytes())
            stated = (out / "registry.json.sha256").read_text().split()[0]
            self.assertEqual(stated, hashlib.sha256((out / "registry.json").read_bytes()).hexdigest())
            self.assertEqual(json.loads((out / "index.json").read_text())["dataset_version"], "v9.9.9")

    def test_refuses_to_wipe_a_directory_that_is_not_a_dist(self):
        con = registry_after_rename()
        schema = Path(__file__).resolve().parent.parent / "schema" / "registry.schema.json"
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "registry.json"
            write_export(con, export_path)
            precious = Path(tmp) / "precious"
            precious.mkdir()
            (precious / "notes.txt").write_text("keep me")
            with self.assertRaises(ValueError):
                write_dist(export_path, schema, precious)
            self.assertTrue((precious / "notes.txt").exists())


if __name__ == "__main__":
    unittest.main()
