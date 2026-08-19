"""Canonicalisation, overrides, export determinism, and an end-to-end
round trip through apply_plan proving ids survive a slug rename."""

import copy
import unittest

import registry.db
from registry.canon import canon_text, parse_slug
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
        # Derived set data: per-card set membership and the set catalogue.
        self.assertEqual(wizard["set_numbers"], ["001"])
        self.assertEqual(broken["set_numbers"], ["010"])
        self.assertEqual(export_one["header"]["sets"], 2)
        self.assertEqual(export_one["sets"], [
            {"set_number": "001", "set_name": "Alpha",
             "released_at": "2023-04-19", "cards": 1, "printings": 2},
            {"set_number": "010", "set_name": "Gothic",
             "released_at": "2026-05-01", "cards": 1, "printings": 1},
        ])

        # Every card starts with one open name_history row carrying its name.
        self.assertEqual(export_one["header"]["name_history"], 2)
        by_name = {h["name"]: h for h in export_one["name_history"]}
        self.assertEqual(sorted(by_name), ["Apprentice Wizard", "Broken Site"])
        self.assertEqual(by_name["Apprentice Wizard"]["codex_id"], wizard_id)
        for row in export_one["name_history"]:
            self.assertEqual(row["valid_from"], "2026-08-19")
            self.assertIsNone(row["valid_to"])

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

    def test_rename_with_changed_rules_text_quarantines_then_resolves(self):
        con = self.fresh_db()
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        apply_plan(con, diff(load_registry_state(con), snapshot), "2026-08-19")
        wizard_id = load_registry_state(con)["cards"]["Apprentice Wizard"]["card_id"]

        # Upstream renames the card AND rewords it in the same sync, so the
        # gameplay fingerprint cannot pair the two names.
        modified = copy.deepcopy(RAW_API)
        modified[0]["name"] = "Apprentice Sorcerer"
        modified[0]["guardian"]["rulesText"] = "Spellcaster\r\nGenesis → Draw two spells."
        modified[0]["sets"][0]["metadata"]["rulesText"] = "Spellcaster\n\nGenesis → Draw two spells."
        for variant in modified[0]["sets"][0]["variants"]:
            variant["slug"] = variant["slug"].replace("apprentice_wizard", "apprentice_sorcerer")
        modified_snapshot = build_snapshot(modified)

        plan = diff(load_registry_state(con), modified_snapshot)
        self.assertEqual(len(plan["ambiguous"]), 1)
        self.assertEqual(plan["ambiguous"][0]["kind"], "card")
        self.assertFalse(plan["new_cards"])

        decisions = {"card_renames": [
            {"card_id": wizard_id, "new_name": "Apprentice Sorcerer"}]}
        plan = diff(load_registry_state(con), modified_snapshot, decisions)
        self.assertFalse(plan["ambiguous"])
        self.assertFalse(plan["new_cards"])
        self.assertEqual(plan["card_renames"][0]["new_name"], "Apprentice Sorcerer")
        self.assertEqual(len(plan["printing_renames"]), 2)
        apply_plan(con, plan, "2026-08-20")

        # The card kept its identity: same row, same id, new name.
        state = load_registry_state(con)
        self.assertNotIn("Apprentice Wizard", state["cards"])
        self.assertEqual(state["cards"]["Apprentice Sorcerer"]["card_id"], wizard_id)
        self.assertEqual(len(state["cards"]), 2)

        # The old name stays resolvable: its row is closed, a new one opens.
        export = build_export(con)
        codex_id = next(c["codex_id"] for c in export["cards"]
                        if c["name"] == "Apprentice Sorcerer")
        old_rows = [h for h in export["name_history"]
                    if h["name"] == "Apprentice Wizard"]
        self.assertEqual(len(old_rows), 1)
        self.assertEqual(old_rows[0]["codex_id"], codex_id)
        self.assertEqual(old_rows[0]["valid_to"], "2026-08-20")
        new_rows = [h for h in export["name_history"]
                    if h["name"] == "Apprentice Sorcerer"]
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0]["codex_id"], codex_id)
        self.assertEqual(new_rows[0]["valid_from"], "2026-08-20")
        self.assertIsNone(new_rows[0]["valid_to"])

        # And the same snapshot now diffs to nothing at all.
        self.assertTrue(is_noop(diff(state, modified_snapshot)))

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


class ExportArtifactsTest(unittest.TestCase):
    def test_write_export_emits_matching_checksum(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from registry.export import checksum_path, write_export
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "registry.json"
            write_export(con, out)
            sha = checksum_path(out)
            self.assertTrue(sha.exists())
            stated = sha.read_text(encoding="utf-8").split()
            self.assertEqual(stated[1], "registry.json")
            actual = hashlib.sha256(out.read_bytes()).hexdigest()
            self.assertEqual(stated[0], actual)

    def test_export_conforms_to_published_schema(self):
        import json
        from pathlib import Path
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema_file = Path(__file__).resolve().parent.parent / "schema" / "registry.schema.json"
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")
        export = json.loads(render(build_export(con)))
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(export))
        self.assertEqual(errors, [], [e.message for e in errors[:3]])


class NameHistoryValidationTest(unittest.TestCase):
    def test_missing_open_name_row_is_reported(self):
        from registry.validate import check_internal
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")

        errors = []
        check_internal(con, errors)
        self.assertEqual(errors, [])

        con.execute("UPDATE name_history SET valid_to = '2026-01-01' "
                    "WHERE card_id = 1 AND valid_to IS NULL")
        errors = []
        check_internal(con, errors)
        self.assertTrue(any("open name_history rows" in e for e in errors), errors)


class SnapshotArtifactTest(unittest.TestCase):
    """A network fetch must leave its raw payload on disk; a --from-file run
    must leave that file untouched."""

    def run_sync(self, argv, fetch_return=None):
        import contextlib
        import io
        import sys
        from unittest import mock
        import registry.sync

        # main() keeps its connection; hold on to it so the test can close it.
        opened = []

        def open_db(*args, **kwargs):
            con = registry.db.open_db(*args, **kwargs)
            opened.append(con)
            return con

        with mock.patch.object(sys, "argv", ["registry.sync"] + argv), \
                mock.patch.object(registry.sync, "open_db", open_db), \
                mock.patch.object(registry.sync, "fetch_api",
                                  return_value=copy.deepcopy(fetch_return)) as fetch:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = registry.sync.main()
            finally:
                for con in opened:
                    con.close()
        return code, fetch

    def test_network_fetch_snapshots_and_from_file_does_not(self):
        import json
        import os
        import tempfile
        from pathlib import Path

        snapshot = Path("review") / "upstream-snapshot.json"
        original_cwd = os.getcwd()
        # main() keeps its sqlite connection open, and Windows refuses to
        # delete a file another handle still holds; the temp dir goes on the
        # OS's cleanup list either way.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            try:
                os.chdir(tmp)

                # A run that goes to the network saves exactly what it got.
                code, fetch = self.run_sync(
                    ["--db", "one.sqlite", "--init", "--dry-run"], RAW_API)
                self.assertEqual(code, 0)
                self.assertEqual(fetch.call_count, 1)
                self.assertTrue(snapshot.exists())
                self.assertEqual(
                    json.loads(snapshot.read_text(encoding="utf-8")), RAW_API)

                # A --from-file run reads its own file and never touches the
                # snapshot: deleted here, it must stay deleted.
                other = Path("other-api.json")
                other.write_text(json.dumps(RAW_API[:1]), encoding="utf-8")
                snapshot.unlink()
                code, fetch = self.run_sync(
                    ["--db", "two.sqlite", "--init", "--dry-run",
                     "--from-file", str(other)], RAW_API)
                self.assertEqual(code, 0)
                self.assertEqual(fetch.call_count, 0)
                self.assertFalse(snapshot.exists())
            finally:
                os.chdir(original_cwd)


class ManifestTest(unittest.TestCase):
    def test_manifest_reports_counts_and_digests(self):
        import tempfile
        from pathlib import Path
        from registry.export import checksum_path, write_export
        from registry.manifest import build_manifest

        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")
        header = build_export(con)["header"]

        schema_path = Path(__file__).resolve().parent.parent / "schema" / "registry.schema.json"
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "registry.json"
            write_export(con, export_path)
            db_path = Path(tmp) / "registry.sqlite"
            db_path.write_bytes(b"only the bytes of this file matter to the manifest")

            manifest = build_manifest("v0.0.0-test", export_path, db_path, schema_path)
            stated = checksum_path(export_path).read_text(encoding="utf-8").split()[0]

        self.assertEqual(manifest["dataset_version"], "v0.0.0-test")
        self.assertEqual(manifest["schema_version"], header["schema_version"])
        for key in ("sets", "cards", "printings", "slug_history", "name_history"):
            self.assertEqual(manifest["counts"][key], header[key])
        self.assertEqual([a["name"] for a in manifest["artifacts"]],
                         ["registry.json", "registry.sqlite", "registry.schema.json"])
        self.assertEqual(manifest["artifacts"][0]["sha256"], stated)


if __name__ == "__main__":
    unittest.main()
