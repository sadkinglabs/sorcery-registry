"""Tests for the MCP server's query layer (the Registry index). The MCP
wiring itself is a thin declaration over these functions and needs the mcp
package, so it is exercised by running the server, not unit-tested here."""

import copy
import unittest

from mcp_server import Registry, card_ref, printing_ref

DATA = {
    "header": {"schema_version": 1, "source": "test", "cards": 2,
               "printings": 3, "slug_history": 4},
    "cards": [
        {"codex_id": "C000001", "name": "Apprentice Wizard", "type": "Minion",
         "rarity": "Ordinary", "subtypes": "Mortal", "elements": "Air",
         "cost": 3, "attack": 1, "defence": 1, "life": None,
         "thr_air": 1, "thr_earth": 0, "thr_fire": 0, "thr_water": 0,
         "rules_text": "Spellcaster", "printing_ids": ["P000001", "P000002"]},
        {"codex_id": "C000002", "name": "Witch", "type": "Minion",
         "rarity": "Elite", "subtypes": "Mortal", "elements": "Water",
         "cost": 2, "attack": 1, "defence": 1, "life": None,
         "thr_air": 0, "thr_earth": 0, "thr_fire": 0, "thr_water": 1,
         "rules_text": "Curse.", "printing_ids": ["P000003"]},
    ],
    "printings": [
        {"printing_id": "P000001", "codex_id": "C000001", "set_name": "Alpha", "released_at": "2023-04-19", "set_number": "001",
         "product": "Booster", "finish": "Standard",
         "slug": "001-apprentice_wizard-b-s", "artist": "A", "flavour_text": "",
         "type_text": "", "rarity": "Ordinary", "type": "Minion",
         "rules_text": "Spellcaster", "cost": 3, "attack": 1, "defence": 1,
         "life": None, "thr_air": 1, "thr_earth": 0, "thr_fire": 0,
         "thr_water": 0, "image_hash": None, "retired_at": None},
        {"printing_id": "P000002", "codex_id": "C000001", "set_name": "Beta", "released_at": "2023-11-10", "set_number": "002",
         "product": "Booster", "finish": "Foil",
         "slug": "002-apprentice_wizard-b-f", "artist": "A", "flavour_text": "",
         "type_text": "", "rarity": "Ordinary", "type": "Minion",
         "rules_text": "Spellcaster", "cost": 3, "attack": 1, "defence": 1,
         "life": None, "thr_air": 1, "thr_earth": 0, "thr_fire": 0,
         "thr_water": 0, "image_hash": None, "retired_at": None},
        {"printing_id": "P000003", "codex_id": "C000002", "set_name": "Alpha", "released_at": "2023-04-19", "set_number": "001",
         "product": "Booster", "finish": "Standard", "slug": "004-witch_x-b-s",
         "artist": "B", "flavour_text": "", "type_text": "", "rarity": "Elite",
         "type": "Minion", "rules_text": "Curse.", "cost": 2, "attack": 1,
         "defence": 1, "life": None, "thr_air": 0, "thr_earth": 0,
         "thr_fire": 0, "thr_water": 1, "image_hash": None, "retired_at": None},
    ],
    "slug_history": [
        {"slug": "001-apprentice_wizard-b-s", "printing_id": "P000001",
         "valid_from": "2026-08-19", "valid_to": None},
        {"slug": "002-apprentice_wizard-b-f", "printing_id": "P000002",
         "valid_from": "2026-08-19", "valid_to": None},
        {"slug": "004-witch-b-s", "printing_id": "P000003",
         "valid_from": "2026-08-19", "valid_to": "2026-08-20"},
        {"slug": "004-witch_x-b-s", "printing_id": "P000003",
         "valid_from": "2026-08-20", "valid_to": None},
    ],
}


class IdFormatTest(unittest.TestCase):
    def test_every_spelling_normalises(self):
        for value in ("C000042", "c000042", "000042", "42", 42):
            self.assertEqual(card_ref(value), "C000042")
        for value in ("P000042", "000042", 42):
            self.assertEqual(printing_ref(value), "P000042")

    def test_legacy_integer_export_is_normalised_on_load(self):
        legacy = copy.deepcopy(DATA)
        for card in legacy["cards"]:
            card["codex_id"] = int(card["codex_id"][1:])
            del card["printing_ids"]
        for printing in legacy["printings"]:
            printing["printing_id"] = int(printing["printing_id"][1:])
            printing["codex_id"] = int(printing["codex_id"][1:])
        for row in legacy["slug_history"]:
            row["printing_id"] = int(row["printing_id"][1:])
        reg = Registry(legacy)
        result = reg.resolve_slug("004-witch-b-s")
        self.assertEqual(result["printing_id"], "P000003")
        self.assertEqual(result["codex_id"], "C000002")

    def test_former_card_id_key_is_renamed_on_load(self):
        legacy = copy.deepcopy(DATA)
        for card in legacy["cards"]:
            card["card_id"] = card.pop("codex_id")
        for printing in legacy["printings"]:
            printing["card_id"] = printing.pop("codex_id")
        reg = Registry(legacy)
        self.assertEqual(reg.resolve_slug("004-witch-b-s")["codex_id"], "C000002")
        self.assertEqual(reg.get_card("C000002")["name"], "Witch")


class ResolveSlugTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(copy.deepcopy(DATA))

    def test_current_slug_resolves(self):
        result = self.reg.resolve_slug("001-apprentice_wizard-b-s")
        self.assertTrue(result["found"])
        self.assertEqual(result["printing_id"], "P000001")
        self.assertEqual(result["codex_id"], "C000001")
        self.assertTrue(result["queried_slug_is_current"])

    def test_historical_slug_resolves_to_same_printing(self):
        # The killer feature: a pre-rename slug still finds its printing.
        result = self.reg.resolve_slug("004-witch-b-s")
        self.assertTrue(result["found"])
        self.assertEqual(result["printing_id"], "P000003")
        self.assertEqual(result["current_slug"], "004-witch_x-b-s")
        self.assertFalse(result["queried_slug_is_current"])

    def test_unknown_slug_reports_not_found(self):
        self.assertFalse(self.reg.resolve_slug("999-nothing-b-s")["found"])


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(copy.deepcopy(DATA))

    def test_get_card_bundles_all_printings(self):
        card = self.reg.get_card("C000001")
        self.assertTrue(card["found"])
        self.assertEqual(card["name"], "Apprentice Wizard")
        self.assertEqual([p["printing_id"] for p in card["printings"]],
                         ["P000001", "P000002"])
        self.assertEqual(card["printings"][1]["finish"], "Foil")

    def test_flexible_id_input(self):
        # Bare numbers and unpadded strings are accepted as lookup input.
        self.assertEqual(self.reg.get_card(1)["name"], "Apprentice Wizard")
        self.assertEqual(self.reg.get_card("2")["name"], "Witch")
        self.assertEqual(self.reg.get_printing(3)["slug"], "004-witch_x-b-s")

    def test_get_printing_carries_card_name(self):
        printing = self.reg.get_printing("P000003")
        self.assertTrue(printing["found"])
        self.assertEqual(printing["card_name"], "Witch")
        self.assertEqual(printing["slug"], "004-witch_x-b-s")

    def test_missing_ids_report_not_found(self):
        self.assertFalse(self.reg.get_card(99)["found"])
        self.assertFalse(self.reg.get_printing("P000099")["found"])


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(copy.deepcopy(DATA))

    def test_name_substring_case_insensitive(self):
        result = self.reg.search_cards(name="wiTCH")
        self.assertEqual([c["name"] for c in result["cards"]], ["Witch"])

    def test_filters_combine(self):
        result = self.reg.search_cards(type="Minion", element="Air")
        self.assertEqual([c["codex_id"] for c in result["cards"]], ["C000001"])
        result = self.reg.search_cards(card_set="Beta")
        self.assertEqual([c["codex_id"] for c in result["cards"]], ["C000001"])

    def test_limit_reports_total(self):
        result = self.reg.search_cards(limit=1)
        self.assertEqual(result["total_matches"], 2)
        self.assertEqual(result["returned"], 1)

    def test_errata_filter_and_derivation(self):
        # The flag is derived on load when an export predates it.
        data = copy.deepcopy(DATA)
        data["cards"][1]["rules_text"] = "UPDATED: Curse."
        reg = Registry(data)
        result = reg.search_cards(errata=True)
        self.assertEqual([c["name"] for c in result["cards"]], ["Witch"])
        self.assertTrue(result["cards"][0]["errata"])
        self.assertEqual(reg.search_cards(errata=False)["total_matches"], 1)


class SetContentsTest(unittest.TestCase):
    def test_distinct_cards_and_counts(self):
        # A set is addressable by name or by its official number.
        reg = Registry(copy.deepcopy(DATA))
        self.assertEqual(reg.set_contents("1"), reg.set_contents("Alpha"))
        result = reg.set_contents("Alpha")
        self.assertEqual(result["set_name"], "Alpha")
        self.assertEqual(result["distinct_cards"], 2)
        self.assertEqual(result["total_printings"], 2)
        self.assertEqual(result["set_number"], "001")
        # Ordered by name: the official data has no within-set serialisation.
        self.assertEqual([c["name"] for c in result["cards"]],
                         ["Apprentice Wizard", "Witch"])


class StatsTest(unittest.TestCase):
    def test_per_set_counts(self):
        stats = Registry(copy.deepcopy(DATA)).stats()
        self.assertEqual(stats["schema_version"], 1)
        by_number = {s["set_number"]: s for s in stats["sets"]}
        self.assertEqual(by_number["001"]["set_name"], "Alpha")
        self.assertEqual(by_number["001"]["cards"], 2)
        self.assertEqual(by_number["001"]["printings"], 2)
        self.assertEqual(by_number["002"]["printings"], 1)


if __name__ == "__main__":
    unittest.main()
