"""Tests for the MCP server's query layer (the Registry index). The MCP
wiring itself is a thin declaration over these functions and needs the mcp
package, so it is exercised by running the server, not unit-tested here."""

import copy
import unittest

from mcp_server import Registry, card_ref, printing_ref

def _printing(printing_id, codex_id, set_name, set_code, released_at, slug,
              finish="Standard", artist="A", product="Booster"):
    return {"printing_id": printing_id, "codex_id": codex_id, "set_name": set_name,
            "set_code": set_code, "released_at": released_at, "product": product,
            "finish": finish, "slug": slug, "artist": artist,
            "artist_slug": artist.lower(), "flavour_text": None, "typeline": "",
            "back": None, "image_hash": None, "retired_at": None}


DATA = {
    "header": {"schema_version": 7, "source": "test", "cards": 2,
               "printings": 3, "slug_history": 4},
    "cards": [
        {"codex_id": "C000001", "name": "Apprentice Wizard", "type": "Minion",
         "category": "Spell", "rarity": "Ordinary", "slot": "Ordinary",
         "subtypes": ["Mortal"], "elements": ["Air"],
         "keywords": ["Spellcaster", "Genesis"], "umbrellas": [],
         "cost": 3, "attack": 1, "defense": 1, "life": None,
         "thr_air": 1, "thr_earth": 0, "thr_fire": 0, "thr_water": 0,
         "rules_text": "Spellcaster", "back": None, "errata": False,
         "set_codes": ["001", "002"], "printing_ids": ["P000001", "P000002"]},
        {"codex_id": "C000002", "name": "Witch", "type": "Minion",
         "category": "Spell", "rarity": "Elite", "slot": "Elite",
         "subtypes": ["Mortal"], "elements": ["Water", "Air"],
         "keywords": ["Spellcaster"], "umbrellas": ["Evil"],
         "cost": 2, "attack": 1, "defense": 1, "life": None,
         "thr_air": 0, "thr_earth": 0, "thr_fire": 0, "thr_water": 1,
         "rules_text": "Curse.", "back": None, "errata": True,
         "set_codes": ["001"], "printing_ids": ["P000003"]},
    ],
    "printings": [
        _printing("P000001", "C000001", "Alpha", "001", "2023-06-22",
                  "001-apprentice_wizard-b-s"),
        _printing("P000002", "C000001", "Beta", "002", "2023-10-06",
                  "002-apprentice_wizard-b-f", finish="Foil"),
        _printing("P000003", "C000002", "Alpha", "001", "2023-06-22",
                  "004-witch_x-b-s", artist="B"),
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

    def test_conflicting_slug_refuses_to_guess(self):
        # Corrupt data: one slug claimed by two printings. Last-write-wins
        # would silently pick a winner, which is the one thing the registry
        # must never do.
        data = copy.deepcopy(DATA)
        data["slug_history"].append(
            {"slug": "004-witch-b-s", "printing_id": "P000001",
             "valid_from": "2026-08-21", "valid_to": None})
        reg = Registry(data)
        self.assertIn("004-witch-b-s", reg.conflicted_slugs)
        result = reg.resolve_slug("004-witch-b-s")
        self.assertFalse(result["found"])
        self.assertIn("registry invariant violated", result["error"])
        # Everything else still resolves normally.
        self.assertTrue(reg.resolve_slug("001-apprentice_wizard-b-s")["found"])


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
        result = self.reg.search_cards(type="Minion", element="Water")
        self.assertEqual([c["codex_id"] for c in result["cards"]], ["C000002"])
        result = self.reg.search_cards(card_set="Beta")
        self.assertEqual([c["codex_id"] for c in result["cards"]], ["C000001"])

    def test_element_matches_any_of_a_multi_element_card(self):
        # Witch is Water and Air: an Air search finds both cards.
        result = self.reg.search_cards(element="air")
        self.assertEqual([c["codex_id"] for c in result["cards"]], ["C000001", "C000002"])

    def test_keyword_and_category_filters(self):
        result = self.reg.search_cards(keyword="genesis")
        self.assertEqual([c["name"] for c in result["cards"]], ["Apprentice Wizard"])
        self.assertEqual(result["cards"][0]["keywords"], ["Spellcaster", "Genesis"])
        self.assertEqual(self.reg.search_cards(category="Spell")["total_matches"], 2)
        self.assertEqual(self.reg.search_cards(category="Site")["total_matches"], 0)

    def test_errata_filter(self):
        result = self.reg.search_cards(errata=True)
        self.assertEqual([c["name"] for c in result["cards"]], ["Witch"])
        self.assertTrue(result["cards"][0]["errata"])
        self.assertEqual(self.reg.search_cards(errata=False)["total_matches"], 1)

    def test_limit_reports_total(self):
        result = self.reg.search_cards(limit=1)
        self.assertEqual(result["total_matches"], 2)
        self.assertEqual(result["returned"], 1)

    def test_pre_v7_export_with_string_lists_is_normalised(self):
        legacy = copy.deepcopy(DATA)
        legacy["cards"][1]["elements"] = "Water, Air"
        legacy["cards"][1]["subtypes"] = "Mortal"
        for printing in legacy["printings"]:
            printing["set_number"] = printing.pop("set_code")
            printing["type_text"] = printing.pop("typeline")
        reg = Registry(legacy)
        self.assertEqual(reg.search_cards(element="air")["total_matches"], 2)
        self.assertEqual(reg.resolve_slug("004-witch-b-s")["set_code"], "001")


class SearchPrintingsTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(copy.deepcopy(DATA))

    def test_product_filter_tolerates_spaces_underscores_and_case(self):
        data = copy.deepcopy(DATA)
        data["printings"][1]["product"] = "BoxTopper"
        reg = Registry(data)
        for spelling in ("Box Topper", "box_topper", "BoxTopper"):
            result = reg.search_printings(product=spelling)
            self.assertEqual([p["printing_id"] for p in result["printings"]], ["P000002"])
        self.assertEqual(result["distinct_cards"], 1)

    def test_filters_combine(self):
        result = self.reg.search_printings(card_set="Alpha", finish="Standard")
        self.assertEqual([p["printing_id"] for p in result["printings"]],
                         ["P000001", "P000003"])
        result = self.reg.search_printings(name="wizard", card_set="2")
        self.assertEqual([p["printing_id"] for p in result["printings"]], ["P000002"])
        self.assertEqual(result["printings"][0]["card_name"], "Apprentice Wizard")

    def test_counts_and_limit(self):
        result = self.reg.search_printings(limit=1)
        self.assertEqual(result["total_matches"], 3)
        self.assertEqual(result["distinct_cards"], 2)
        self.assertEqual(result["returned"], 1)


class SetContentsTest(unittest.TestCase):
    def test_distinct_cards_and_counts(self):
        # A set is addressable by name or by its official code.
        reg = Registry(copy.deepcopy(DATA))
        self.assertEqual(reg.set_contents("1"), reg.set_contents("Alpha"))
        result = reg.set_contents("Alpha")
        self.assertEqual(result["set_name"], "Alpha")
        self.assertEqual(result["distinct_cards"], 2)
        self.assertEqual(result["total_printings"], 2)
        self.assertEqual(result["set_code"], "001")
        # Ordered by name: the official data has no within-set serialisation.
        self.assertEqual([c["name"] for c in result["cards"]],
                         ["Apprentice Wizard", "Witch"])


class StatsTest(unittest.TestCase):
    def test_per_set_counts(self):
        stats = Registry(copy.deepcopy(DATA)).stats()
        self.assertEqual(stats["schema_version"], 7)
        by_code = {s["set_code"]: s for s in stats["sets"]}
        self.assertEqual(by_code["001"]["set_name"], "Alpha")
        self.assertEqual(by_code["001"]["released_at"], "2023-06-22")
        self.assertEqual(by_code["001"]["cards"], 2)
        self.assertEqual(by_code["001"]["printings"], 2)
        self.assertEqual(by_code["002"]["printings"], 1)


if __name__ == "__main__":
    unittest.main()
