"""Canonicalisation, overrides, export determinism, and an end-to-end
round trip through apply_plan proving ids survive a slug rename."""

import copy
import unittest

from registry.canon import canon_text, parse_slug, set_code
from registry.db import init_db, load_registry_state, open_db
from registry.diff import diff, is_noop
from registry.export import build_export, render
from registry.fetch import apply_overrides, build_snapshot
from registry.sync import apply_plan

RAW_API = [
    {
        "name": "Apprentice Wizard",
        "guardian": {
            "rarity": "Ordinary", "type": "Minion",
            "rulesText": "Spellcaster\r\nGenesis → Draw a spell.",
            "cost": 3, "attack": 1, "defence": 1, "life": None,
            "thresholds": {"air": 1, "earth": 0, "fire": 0, "water": 0},
        },
        "elements": "Air",
        "subTypes": "Mortal",
        "sets": [{
            "name": "Alpha",
            "releasedAt": "2023-04-19T00:00:00.000Z",
            "metadata": {
                "rarity": "Ordinary", "type": "Minion",
                "rulesText": "Spellcaster\n\nGenesis → Draw a spell.",
                "cost": 3, "attack": 1, "defence": 1, "life": None,
                "thresholds": {"air": 1, "earth": 0, "fire": 0, "water": 0},
            },
            "variants": [
                {"slug": "001-apprentice_wizard-b-s", "finish": "Standard",
                 "product": "Booster", "artist": "Ossi Hiekkala",
                 "flavorText": "", "typeText": "An Ordinary Mortal new to power"},
                {"slug": "001-apprentice_wizard-b-f", "finish": "Foil",
                 "product": "Booster", "artist": "Ossi Hiekkala",
                 "flavorText": "", "typeText": "An Ordinary Mortal new to power"},
            ],
        }],
    },
    {
        "name": "Broken Site",
        "guardian": {
            "rarity": "Ordinary", "type": "Site",
            "rulesText": "UPDATED: All sites are broken.",
            "cost": None, "attack": None, "defence": None, "life": 20,
            "thresholds": {"air": 0, "earth": 0, "fire": 0, "water": 0},
        },
        "elements": "None",
        "subTypes": "",
        "sets": [{
            "name": "Gothic",
            "releasedAt": "2026-05-01T00:00:00.000Z",
            "metadata": {
                "rarity": "Ordinary", "type": "Site",
                "rulesText": "UPDATED: All sites are broken.",
                "cost": None, "attack": None, "defence": None, "life": 20,
                "thresholds": {"air": 0, "earth": 0, "fire": 0, "water": 0},
            },
            "variants": [
                {"slug": "010-broken_site-b-s", "finish": "Standard",
                 "product": "Booster", "artist": "", "flavorText": "", "typeText": ""},
            ],
        }],
    },
]


class CanonTest(unittest.TestCase):
    def test_line_endings_and_trailing_whitespace(self):
        self.assertEqual(canon_text("a \r\nb\r"), "a\nb")
        self.assertEqual(canon_text("\n\n a\n"), "a")
        self.assertIsNone(canon_text(None))

    def test_set_code(self):
        self.assertEqual(set_code("Arthurian Legends"), "arthurian_legends")
        self.assertEqual(set_code("Alpha"), "alpha")

    def test_parse_slug(self):
        # The leading digits are the SET's number, kept as a string.
        self.assertEqual(parse_slug("004-witch-b-s"), ("004", "witch", "b", "s"))
        self.assertEqual(parse_slug("999-apprentice_wizard-wk-f"),
                         ("999", "apprentice_wizard", "wk", "f"))
        self.assertEqual(parse_slug("unparseable"), (None, None, None, None))


class SnapshotTest(unittest.TestCase):
    def test_flatten_shapes_and_canonicalisation(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        wizard = snapshot["cards"]["Apprentice Wizard"]
        # \r\n and \n\n both collapse to the same canonical text.
        self.assertEqual(wizard["rules_text"], "Spellcaster\nGenesis → Draw a spell.")
        printing = snapshot["printings"]["001-apprentice_wizard-b-s"]
        self.assertEqual(printing["rules_text"], wizard["rules_text"])
        self.assertEqual(printing["set_number"], "001")
        self.assertEqual(printing["set_code"], "alpha")
        self.assertEqual(printing["released_at"], "2023-04-19")

    def test_duplicate_slug_fails_loudly(self):
        raw = copy.deepcopy(RAW_API)
        raw[1]["sets"][0]["variants"][0]["slug"] = "001-apprentice_wizard-b-s"
        with self.assertRaises(ValueError):
            build_snapshot(raw)


class OverridesTest(unittest.TestCase):
    def test_override_corrects_card_and_printings(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        unmatched = apply_overrides(snapshot, [{
            "match": {"card_name": "Broken Site"},
            "set_fields": {"life": None},
            "reason": "API data error: only Avatars have life."}])
        self.assertEqual(unmatched, [])
        self.assertIsNone(snapshot["cards"]["Broken Site"]["life"])
        self.assertIsNone(snapshot["printings"]["010-broken_site-b-s"]["life"])

    def test_unmatched_override_is_reported_not_fatal(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        unmatched = apply_overrides(snapshot, [{
            "match": {"card_name": "No Such Card"},
            "set_fields": {"life": None},
            "reason": "upstream fixed it"}])
        self.assertEqual(len(unmatched), 1)

    def test_override_without_reason_is_rejected(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        with self.assertRaises(ValueError):
            apply_overrides(snapshot, [{"match": {"card_name": "Broken Site"},
                                        "set_fields": {"life": None}}])


class EndToEndTest(unittest.TestCase):
    def fresh_db(self):
        con = open_db(":memory:")
        init_db(con)
        return con

    def test_populate_rename_and_export_keep_ids_stable(self):
        con = self.fresh_db()
        snapshot = build_snapshot(copy.deepcopy(RAW_API))

        # First run: everything is new, ids assigned sequentially.
        plan = diff(load_registry_state(con), snapshot)
        self.assertEqual(len(plan["new_cards"]), 2)
        self.assertEqual(len(plan["new_printings"]), 3)
        apply_plan(con, plan, "2026-08-19")
        export_one = build_export(con)
        wizard = next(c for c in export_one["cards"]
                      if c["name"] == "Apprentice Wizard")
        wizard_id = wizard["codex_id"]
        foil_id = next(p["printing_id"] for p in export_one["printings"]
                       if p["slug"] == "001-apprentice_wizard-b-f")
        # Each card lists its printings, derived from the printings table.
        self.assertEqual(
            wizard["printing_ids"],
            sorted(p["printing_id"] for p in export_one["printings"]
                   if p["codex_id"] == wizard_id))
        self.assertIn(foil_id, wizard["printing_ids"])
        # The errata flag reads the upstream "UPDATED" rules-text convention.
        self.assertFalse(wizard["errata"])
        broken = next(c for c in export_one["cards"] if c["name"] == "Broken Site")
        self.assertTrue(broken["errata"])

        # Second run, same data: a no-op, and the export is byte-identical.
        plan = diff(load_registry_state(con), snapshot)
        self.assertTrue(is_noop(plan))
        self.assertEqual(render(export_one), render(build_export(con)))

        # Third run: the naming convention flips back to hyphens.
        renamed = copy.deepcopy(RAW_API)
        for variant in renamed[0]["sets"][0]["variants"]:
            variant["slug"] = variant["slug"].replace("apprentice_wizard", "apprentice-wizard")
        plan = diff(load_registry_state(con), build_snapshot(renamed))
        self.assertEqual(len(plan["printing_renames"]), 2)
        self.assertFalse(plan["ambiguous"])
        apply_plan(con, plan, "2026-08-20")

        export_two = build_export(con)
        self.assertEqual(
            next(p["printing_id"] for p in export_two["printings"]
                 if p["slug"] == "001-apprentice-wizard-b-f"),
            foil_id)
        self.assertEqual(
            next(c["codex_id"] for c in export_two["cards"]
                 if c["name"] == "Apprentice Wizard"),
            wizard_id)
        # The old slug is recoverable from history.
        old_rows = [h for h in export_two["slug_history"]
                    if h["slug"] == "001-apprentice_wizard-b-f"]
        self.assertEqual(len(old_rows), 1)
        self.assertEqual(old_rows[0]["printing_id"], foil_id)
        self.assertEqual(old_rows[0]["valid_to"], "2026-08-20")

    def test_engine_refuses_id_mutation_and_deletion(self):
        import sqlite3
        con = self.fresh_db()
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        apply_plan(con, diff(load_registry_state(con), snapshot), "2026-08-19")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE cards SET card_id = 99 WHERE card_id = 1")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("DELETE FROM printings WHERE printing_id = 1")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE printings SET card_id = 2 WHERE printing_id = 1")


if __name__ == "__main__":
    unittest.main()
