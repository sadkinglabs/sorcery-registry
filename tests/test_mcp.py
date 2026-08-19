"""Tests for the MCP server's query layer (the Registry index). The MCP
wiring itself is a thin declaration over these functions and needs the mcp
package, so it is exercised by running the server, not unit-tested here."""

import unittest

from mcp_server import Registry

DATA = {
    "header": {"schema_version": 1, "source": "test", "cards": 2,
               "printings": 3, "slug_history": 4},
    "cards": [
        {"card_id": 1, "name": "Apprentice Wizard", "type": "Minion",
         "rarity": "Ordinary", "subtypes": "Mortal", "elements": "Air",
         "cost": 3, "attack": 1, "defence": 1, "life": None,
         "thr_air": 1, "thr_earth": 0, "thr_fire": 0, "thr_water": 0,
         "rules_text": "Spellcaster"},
        {"card_id": 2, "name": "Witch", "type": "Minion",
         "rarity": "Elite", "subtypes": "Mortal", "elements": "Water",
         "cost": 2, "attack": 1, "defence": 1, "life": None,
         "thr_air": 0, "thr_earth": 0, "thr_fire": 0, "thr_water": 1,
         "rules_text": "Curse."},
    ],
    "printings": [
        {"printing_id": 1, "card_id": 1, "set_code": "alpha", "set_name": "Alpha",
         "released_at": "2023-04-19", "card_number": 1, "product": "Booster",
         "finish": "Standard", "slug": "001-apprentice_wizard-b-s",
         "artist": "A", "flavour_text": "", "type_text": "", "rarity": "Ordinary",
         "type": "Minion", "rules_text": "Spellcaster", "cost": 3, "attack": 1,
         "defence": 1, "life": None, "thr_air": 1, "thr_earth": 0, "thr_fire": 0,
         "thr_water": 0, "image_hash": None, "retired_at": None},
        {"printing_id": 2, "card_id": 1, "set_code": "beta", "set_name": "Beta",
         "released_at": "2023-11-10", "card_number": 2, "product": "Booster",
         "finish": "Foil", "slug": "002-apprentice_wizard-b-f",
         "artist": "A", "flavour_text": "", "type_text": "", "rarity": "Ordinary",
         "type": "Minion", "rules_text": "Spellcaster", "cost": 3, "attack": 1,
         "defence": 1, "life": None, "thr_air": 1, "thr_earth": 0, "thr_fire": 0,
         "thr_water": 0, "image_hash": None, "retired_at": None},
        {"printing_id": 3, "card_id": 2, "set_code": "alpha", "set_name": "Alpha",
         "released_at": "2023-04-19", "card_number": 4, "product": "Booster",
         "finish": "Standard", "slug": "004-witch_x-b-s",
         "artist": "B", "flavour_text": "", "type_text": "", "rarity": "Elite",
         "type": "Minion", "rules_text": "Curse.", "cost": 2, "attack": 1,
         "defence": 1, "life": None, "thr_air": 0, "thr_earth": 0, "thr_fire": 0,
         "thr_water": 1, "image_hash": None, "retired_at": None},
    ],
    "slug_history": [
        {"slug": "001-apprentice_wizard-b-s", "printing_id": 1,
         "valid_from": "2026-08-19", "valid_to": None},
        {"slug": "002-apprentice_wizard-b-f", "printing_id": 2,
         "valid_from": "2026-08-19", "valid_to": None},
        {"slug": "004-witch-b-s", "printing_id": 3,
         "valid_from": "2026-08-19", "valid_to": "2026-08-20"},
        {"slug": "004-witch_x-b-s", "printing_id": 3,
         "valid_from": "2026-08-20", "valid_to": None},
    ],
}


class ResolveSlugTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(DATA)

    def test_current_slug_resolves(self):
        result = self.reg.resolve_slug("001-apprentice_wizard-b-s")
        self.assertTrue(result["found"])
        self.assertEqual(result["printing_id"], 1)
        self.assertEqual(result["card_id"], 1)
        self.assertTrue(result["queried_slug_is_current"])

    def test_historical_slug_resolves_to_same_printing(self):
        # The killer feature: a pre-rename slug still finds its printing.
        result = self.reg.resolve_slug("004-witch-b-s")
        self.assertTrue(result["found"])
        self.assertEqual(result["printing_id"], 3)
        self.assertEqual(result["current_slug"], "004-witch_x-b-s")
        self.assertFalse(result["queried_slug_is_current"])

    def test_unknown_slug_reports_not_found(self):
        self.assertFalse(self.reg.resolve_slug("999-nothing-b-s")["found"])


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(DATA)

    def test_get_card_bundles_all_printings(self):
        card = self.reg.get_card(1)
        self.assertTrue(card["found"])
        self.assertEqual(card["name"], "Apprentice Wizard")
        self.assertEqual([p["printing_id"] for p in card["printings"]], [1, 2])
        self.assertEqual(card["printings"][1]["finish"], "Foil")

    def test_get_printing_carries_card_name(self):
        printing = self.reg.get_printing(3)
        self.assertTrue(printing["found"])
        self.assertEqual(printing["card_name"], "Witch")
        self.assertEqual(printing["slug"], "004-witch_x-b-s")

    def test_missing_ids_report_not_found(self):
        self.assertFalse(self.reg.get_card(99)["found"])
        self.assertFalse(self.reg.get_printing(99)["found"])


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(DATA)

    def test_name_substring_case_insensitive(self):
        result = self.reg.search_cards(name="wiTCH")
        self.assertEqual([c["name"] for c in result["cards"]], ["Witch"])

    def test_filters_combine(self):
        result = self.reg.search_cards(type="Minion", element="Air")
        self.assertEqual([c["card_id"] for c in result["cards"]], [1])
        result = self.reg.search_cards(set_code="beta")
        self.assertEqual([c["card_id"] for c in result["cards"]], [1])

    def test_limit_reports_total(self):
        result = self.reg.search_cards(limit=1)
        self.assertEqual(result["total_matches"], 2)
        self.assertEqual(result["returned"], 1)


class SetContentsTest(unittest.TestCase):
    def test_distinct_cards_and_counts(self):
        result = Registry(DATA).set_contents("Alpha")
        self.assertEqual(result["distinct_cards"], 2)
        self.assertEqual(result["total_printings"], 2)
        self.assertEqual([c["card_number"] for c in result["cards"]], [1, 4])


class StatsTest(unittest.TestCase):
    def test_per_set_counts(self):
        stats = Registry(DATA).stats()
        self.assertEqual(stats["schema_version"], 1)
        by_code = {s["set_code"]: s for s in stats["sets"]}
        self.assertEqual(by_code["alpha"]["cards"], 2)
        self.assertEqual(by_code["alpha"]["printings"], 2)
        self.assertEqual(by_code["beta"]["printings"], 1)


if __name__ == "__main__":
    unittest.main()
