"""The upstream contract check: the adapter refuses a payload whose shape
no longer matches the publisher's published CardAPIDTO, tolerates the
harmless (new fields, new vocabulary), and names what broke."""

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from registry import fetch
from registry.contract import CONTRACT_PATH, ContractError, check_contract, load_contract
from registry.fetch import USER_AGENT, build_snapshot, fetch_api

from test_pipeline import RAW_API


class ContractTest(unittest.TestCase):
    def test_the_fixture_fits_and_the_real_snapshot_too_when_present(self):
        check_contract(copy.deepcopy(RAW_API))
        snapshot = Path(__file__).resolve().parent.parent / "review" / "upstream-snapshot.json"
        if snapshot.exists():  # a local working file, never in CI
            check_contract(json.loads(snapshot.read_text(encoding="utf-8")))

    def test_new_fields_and_new_vocabulary_pass(self):
        raw = copy.deepcopy(RAW_API)
        raw[0]["legality"] = {"standard": True}
        raw[0]["engine"]["power"] = 1
        raw[0]["engine"]["type"] = "Relic"            # a new card type: data, not drift
        raw[0]["printings"][0]["meta"]["product"] = "CollectorsEdition"
        raw[0]["printings"][0]["meta"]["finish"] = "Etched"
        check_contract(raw)
        build_snapshot(raw)

    def test_a_missing_field_names_its_path(self):
        raw = copy.deepcopy(RAW_API)
        del raw[1]["engine"]["rules"]
        with self.assertRaises(ContractError) as caught:
            build_snapshot(raw)
        self.assertEqual(caught.exception.path, "cards[1].engine.rules")

    def test_a_retyped_field_names_its_path(self):
        raw = copy.deepcopy(RAW_API)
        raw[0]["printings"][1]["set"] = "Alpha"
        with self.assertRaises(ContractError) as caught:
            check_contract(raw)
        self.assertEqual(caught.exception.path, "cards[0].printings[1].set")
        self.assertIn("expected object", str(caught.exception))
        raw = copy.deepcopy(RAW_API)
        raw[0]["engine"]["cost"] = "3"
        with self.assertRaises(ContractError) as caught:
            check_contract(raw)
        self.assertEqual(caught.exception.path, "cards[0].engine.cost")

    def test_back_faces_are_checked_like_fronts_and_null_is_fine(self):
        raw = copy.deepcopy(RAW_API)
        raw[0]["engine"]["back"] = {"type": "Avatar"}  # a back face missing everything else
        with self.assertRaises(ContractError) as caught:
            check_contract(raw)
        self.assertTrue(caught.exception.path.startswith("cards[0].engine.back"))
        raw[0]["engine"]["back"] = None
        check_contract(raw)

    def test_the_whole_payload_must_be_a_list(self):
        with self.assertRaises(ContractError) as caught:
            check_contract({"cards": []})
        self.assertEqual(caught.exception.path, "cards")

    def test_contract_file_uses_only_what_the_checker_understands(self):
        # The checker refuses keywords it does not implement, so a contract
        # edit cannot silently pass because a rule was ignored.
        check_contract([], load_contract(CONTRACT_PATH))
        bad = load_contract(CONTRACT_PATH)
        bad["items"]["properties"]["name"]["pattern"] = "^.+$"
        with self.assertRaises(ValueError):
            check_contract(copy.deepcopy(RAW_API), bad)

    def test_agrees_with_a_full_json_schema_validator(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        contract = load_contract()
        validator = jsonschema.Draft202012Validator(contract)
        cases = [copy.deepcopy(RAW_API)]
        broken = copy.deepcopy(RAW_API)
        del broken[0]["printings"][0]["meta"]["artist"]
        cases.append(broken)
        retyped = copy.deepcopy(RAW_API)
        retyped[1]["engine"]["elements"] = "None"
        cases.append(retyped)
        for case in cases:
            full = validator.is_valid(case)
            try:
                check_contract(case)
                ours = True
            except ContractError:
                ours = False
            self.assertEqual(ours, full)


class FetchConductTest(unittest.TestCase):
    def test_every_request_identifies_the_registry(self):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(url=url, **kwargs)
            response = mock.Mock()
            response.json.return_value = []
            return response

        with mock.patch.object(fetch, "_get", fake_get):
            self.assertEqual(fetch_api(), [])
        self.assertEqual(seen["url"], fetch.API_URL)
        self.assertEqual(seen["headers"]["User-Agent"], USER_AGENT)
        self.assertIn("kairosarchive.net", USER_AGENT)
        self.assertIn("sorcery-registry", USER_AGENT)


if __name__ == "__main__":
    unittest.main()
