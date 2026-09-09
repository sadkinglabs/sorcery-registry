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


def engine(**overrides):
    base = {
        "type": "Minion", "category": "Spell", "rarity": "Ordinary", "slot": "Ordinary",
        "rules": "Spellcaster\r\n\r\nGenesis → Draw a spell.",
        "cost": 3, "attack": 1, "defense": 1, "life": None,
        "air": 1, "earth": 0, "fire": 0, "water": 0,
        "elements": ["Air"], "subtypes": ["Mortal"], "keywords": ["Spellcaster", "Genesis"],
        "umbrellas": [], "back": None,
    }
    return {**base, **overrides}


def upstream_printing(slug, set_name, code, printed_at, **overrides):
    meta = {"finish": "Standard", "product": "Booster",
            "typeline": "An Ordinary Mortal new to power", "flavor": None,
            "artist": {"name": "Ossi Hiekkala", "slug": "ossi_hiekkala"}, "back": None}
    meta.update(overrides)
    return {"id": "cmt000000000000000000000",
            "slug": slug, "printedAt": printed_at + "T07:00:00.000Z",
            "set": {"name": set_name, "code": code,
                    "releasedAt": "2026-08-25T04:21:27.405Z"},
            "meta": meta}


RAW_API = [
    {
        "id": "cmt111111111111111111111",
        "name": "Apprentice Wizard",
        "slug": "apprentice_wizard",
        "engine": engine(),
        "printings": [
            upstream_printing("001-apprentice_wizard-b-s", "Alpha", "001", "2023-06-22"),
            upstream_printing("001-apprentice_wizard-b-f", "Alpha", "001", "2023-06-22",
                              finish="Foil"),
        ],
    },
    {
        "id": "cmt222222222222222222222",
        "name": "Broken Site",
        "slug": "broken_site",
        "engine": engine(type="Site", category="Site", rules="All sites are broken.",
                         cost=None, attack=None, defense=None, life=20,
                         air=0, elements=["None"], subtypes=[], keywords=[]),
        "printings": [
            upstream_printing("010-broken_site-b-s", "Gothic", "010", "2026-05-01",
                              typeline="", artist={"name": "", "slug": ""}),
        ],
    },
]


class CanonTest(unittest.TestCase):
    def test_line_endings_and_trailing_whitespace(self):
        self.assertEqual(canon_text("a \r\nb\r"), "a\nb")
        self.assertEqual(canon_text("\n\n a\n"), "a")
        self.assertIsNone(canon_text(None))

    def test_parse_slug(self):
        # The leading digits are the SET's code, kept as a string.
        self.assertEqual(parse_slug("004-witch-b-s"), ("004", "witch", "b", "s"))
        self.assertEqual(parse_slug("999-apprentice_wizard-wk-f"),
                         ("999", "apprentice_wizard", "wk", "f"))
        self.assertEqual(parse_slug("unparseable"), (None, None, None, None))


class SnapshotTest(unittest.TestCase):
    def test_flatten_shapes_and_canonicalisation(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        wizard = snapshot["cards"]["Apprentice Wizard"]
        # \r\n\r\n collapses to one newline per ability.
        self.assertEqual(wizard["rules_text"], "Spellcaster\nGenesis → Draw a spell.")
        self.assertEqual(wizard["defense"], 1)
        self.assertEqual(wizard["thr_air"], 1)
        self.assertEqual(wizard["elements"], ["Air"])
        self.assertEqual(wizard["keywords"], ["Spellcaster", "Genesis"])
        self.assertIsNone(wizard["back"])
        # Gameplay data is card-level only: printings carry physical facts.
        printing = snapshot["printings"]["001-apprentice_wizard-b-s"]
        self.assertNotIn("rules_text", printing)
        self.assertEqual(printing["set_code"], "001")
        self.assertEqual(printing["artist"], "Ossi Hiekkala")
        self.assertEqual(printing["artist_slug"], "ossi_hiekkala")
        self.assertEqual(printing["typeline"], "An Ordinary Mortal new to power")
        # printedAt is the release date; set.releasedAt is a database
        # timestamp upstream and is never read.
        self.assertEqual(printing["released_at"], "2023-06-22")
        self.assertIsNone(printing["back"])

    def test_upstream_ids_are_not_carried(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        self.assertNotIn("id", snapshot["cards"]["Apprentice Wizard"])
        self.assertNotIn("id", snapshot["printings"]["001-apprentice_wizard-b-s"])

    def test_back_faces(self):
        raw = copy.deepcopy(RAW_API)
        raw[0]["engine"]["back"] = engine(type="Avatar", category="Avatar", rarity=None,
                                          rules="Tap → Play or draw a site.", life=20,
                                          cost=None, keywords=[])
        raw[0]["printings"][0]["meta"]["back"] = {
            "finish": "Standard", "product": "Booster", "flavor": None,
            "typeline": "Your Avatar is a force of nature!",
            "artist": {"name": "Bryon Wackwitz", "slug": "bryon_wackwitz"}}
        snapshot = build_snapshot(raw)
        back = snapshot["cards"]["Apprentice Wizard"]["back"]
        self.assertEqual(back["type"], "Avatar")
        self.assertEqual(back["life"], 20)
        self.assertEqual(back["rules_text"], "Tap → Play or draw a site.")
        self.assertNotIn("back", back)
        self.assertEqual(snapshot["printings"]["001-apprentice_wizard-b-s"]["back"], {
            "artist": "Bryon Wackwitz", "artist_slug": "bryon_wackwitz",
            "flavour_text": None, "typeline": "Your Avatar is a force of nature!"})

    def test_duplicate_slug_fails_loudly(self):
        raw = copy.deepcopy(RAW_API)
        raw[1]["printings"][0]["slug"] = "001-apprentice_wizard-b-s"
        with self.assertRaises(ValueError):
            build_snapshot(raw)


class OverridesTest(unittest.TestCase):
    def test_override_corrects_the_card(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        unmatched = apply_overrides(snapshot, [{
            "match": {"card_name": "Broken Site"},
            "set_fields": {"life": None},
            "reason": "API data error: only Avatars have life."}])
        self.assertEqual(unmatched, [])
        self.assertIsNone(snapshot["cards"]["Broken Site"]["life"])
        # life is a card fact; the printing record has no such column.
        self.assertNotIn("life", snapshot["printings"]["010-broken_site-b-s"])

    def test_override_restricted_to_a_set_touches_printings_only(self):
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        unmatched = apply_overrides(snapshot, [{
            "match": {"card_name": "Apprentice Wizard", "set_name": "Alpha"},
            "set_fields": {"artist": "Corrected Artist"},
            "reason": "misattributed upstream"}])
        self.assertEqual(unmatched, [])
        for slug in ("001-apprentice_wizard-b-s", "001-apprentice_wizard-b-f"):
            self.assertEqual(snapshot["printings"][slug]["artist"], "Corrected Artist")
        self.assertNotIn("artist", snapshot["cards"]["Apprentice Wizard"])

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
        # Lists round-trip through SQLite as lists, in upstream's order.
        self.assertEqual(wizard["keywords"], ["Spellcaster", "Genesis"])
        self.assertEqual(wizard["elements"], ["Air"])
        self.assertIsNone(wizard["back"])
        broken = next(c for c in export_one["cards"] if c["name"] == "Broken Site")
        # Derived set data: per-card set membership and the set catalogue,
        # whose release date is the earliest printing date in the set.
        self.assertEqual(wizard["set_codes"], ["001"])
        self.assertEqual(broken["set_codes"], ["010"])
        self.assertEqual(export_one["header"]["sets"], 2)
        self.assertEqual(export_one["sets"], [
            {"set_code": "001", "set_name": "Alpha",
             "released_at": "2023-06-22", "cards": 1, "printings": 2},
            {"set_code": "010", "set_name": "Gothic",
             "released_at": "2026-05-01", "cards": 1, "printings": 1},
        ])

        # Every card starts with one open name_history row carrying its name
        # and one open rules_history row carrying its text.
        self.assertEqual(export_one["header"]["name_history"], 2)
        by_name = {h["name"]: h for h in export_one["name_history"]}
        self.assertEqual(sorted(by_name), ["Apprentice Wizard", "Broken Site"])
        self.assertEqual(by_name["Apprentice Wizard"]["codex_id"], wizard_id)
        for row in export_one["name_history"]:
            self.assertEqual(row["valid_from"], "2026-08-19")
            self.assertIsNone(row["valid_to"])
        self.assertEqual(export_one["header"]["rules_history"], 2)
        by_text = {h["rules_text"]: h for h in export_one["rules_history"]}
        self.assertEqual(by_text["Spellcaster\nGenesis → Draw a spell."]["codex_id"], wizard_id)
        for row in export_one["rules_history"]:
            self.assertIsNone(row["valid_to"])

        # Second run, same data: a no-op, and the export is byte-identical.
        plan = diff(load_registry_state(con), snapshot)
        self.assertTrue(is_noop(plan))
        self.assertEqual(render(export_one), render(build_export(con)))

        # Third run: the naming convention flips back to hyphens.
        renamed = copy.deepcopy(RAW_API)
        for printing in renamed[0]["printings"]:
            printing["slug"] = printing["slug"].replace("apprentice_wizard", "apprentice-wizard")
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
        modified[0]["engine"]["rules"] = "Spellcaster\r\nGenesis → Draw two spells."
        for printing in modified[0]["printings"]:
            printing["slug"] = printing["slug"].replace("apprentice_wizard", "apprentice_sorcerer")
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


class RulesHistoryTest(unittest.TestCase):
    """Upstream publishes only the current text and no longer marks errata;
    the registry records every change it observes instead."""

    def test_reworded_card_closes_and_opens_rows(self):
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")

        reworded = copy.deepcopy(RAW_API)
        reworded[0]["engine"]["rules"] = "Spellcaster\r\n\r\nGenesis → Draw two spells."
        plan = diff(load_registry_state(con), build_snapshot(reworded))
        self.assertEqual([u["name"] for u in plan["card_updates"]], ["Apprentice Wizard"])
        self.assertEqual(list(plan["card_updates"][0]["changes"]), ["rules_text"])
        self.assertFalse(plan["card_renames"] or plan["ambiguous"])
        apply_plan(con, plan, "2026-09-01")

        export = build_export(con)
        wizard = next(c for c in export["cards"] if c["name"] == "Apprentice Wizard")
        self.assertEqual(wizard["rules_text"], "Spellcaster\nGenesis → Draw two spells.")
        rows = [h for h in export["rules_history"] if h["codex_id"] == wizard["codex_id"]]
        self.assertEqual(rows, [
            {"rules_text": "Spellcaster\nGenesis → Draw a spell.",
             "codex_id": wizard["codex_id"], "valid_from": "2026-08-19",
             "valid_to": "2026-09-01"},
            {"rules_text": "Spellcaster\nGenesis → Draw two spells.",
             "codex_id": wizard["codex_id"], "valid_from": "2026-09-01",
             "valid_to": None},
        ])
        # The untouched card still has exactly its one open row.
        broken = next(c for c in export["cards"] if c["name"] == "Broken Site")
        self.assertEqual(len([h for h in export["rules_history"]
                              if h["codex_id"] == broken["codex_id"]]), 1)


class FrozenFlavourTextTest(unittest.TestCase):
    """Upstream serves flavour text as null for every printing since the
    rebuild. A null must not erase the text the registry already holds."""

    def test_null_upstream_leaves_registry_value_alone(self):
        con = open_db(":memory:")
        init_db(con)
        raw = copy.deepcopy(RAW_API)
        raw[0]["printings"][0]["meta"]["flavor"] = "The first of many."
        apply_plan(con, diff(load_registry_state(con), build_snapshot(raw)), "2026-08-19")

        plan = diff(load_registry_state(con), build_snapshot(copy.deepcopy(RAW_API)))
        self.assertTrue(is_noop(plan), plan)
        state = load_registry_state(con)
        self.assertEqual(state["printings"]["001-apprentice_wizard-b-s"]["flavour_text"],
                         "The first of many.")

    def test_real_upstream_value_still_updates(self):
        con = open_db(":memory:")
        init_db(con)
        raw = copy.deepcopy(RAW_API)
        raw[0]["printings"][0]["meta"]["flavor"] = "The first of many."
        apply_plan(con, diff(load_registry_state(con), build_snapshot(raw)), "2026-08-19")
        raw[0]["printings"][0]["meta"]["flavor"] = "The first of many, revised."
        plan = diff(load_registry_state(con), build_snapshot(raw))
        self.assertEqual(plan["printing_updates"][0]["changes"]["flavour_text"]["new"],
                         "The first of many, revised.")


class BackFaceRoundTripTest(unittest.TestCase):
    def test_faces_survive_storage_and_export_in_fixed_order(self):
        con = open_db(":memory:")
        init_db(con)
        raw = copy.deepcopy(RAW_API)
        raw[0]["engine"]["back"] = engine(type="Avatar", category="Avatar", rarity=None,
                                          rules="Tap → Play or draw a site.", life=20,
                                          cost=None, keywords=[])
        raw[0]["printings"][0]["meta"]["back"] = {
            "finish": "Standard", "product": "Booster", "flavor": None,
            "typeline": "Your Avatar is a force of nature!",
            "artist": {"name": "Bryon Wackwitz", "slug": "bryon_wackwitz"}}
        snapshot = build_snapshot(raw)
        apply_plan(con, diff(load_registry_state(con), snapshot), "2026-08-19")
        self.assertTrue(is_noop(diff(load_registry_state(con), snapshot)))

        export = build_export(con)
        wizard = next(c for c in export["cards"] if c["name"] == "Apprentice Wizard")
        front_keys = [k for k in wizard if k not in ("codex_id", "name", "back",
                                                     "set_codes", "printing_ids")]
        self.assertEqual(list(wizard["back"]), front_keys)
        self.assertEqual(wizard["back"]["life"], 20)
        printing = next(p for p in export["printings"]
                        if p["slug"] == "001-apprentice_wizard-b-s")
        self.assertEqual(list(printing["back"]),
                         ["artist", "artist_slug", "flavour_text", "typeline"])
        self.assertEqual(printing["back"]["artist_slug"], "bryon_wackwitz")


class MigrationTest(unittest.TestCase):
    """The v6 -> v7 rebuild keeps every id and history row and re-encodes
    only what the old API's shape forced on the data."""

    V6_DDL = """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE cards (card_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        type TEXT, rarity TEXT, subtypes TEXT, elements TEXT, cost INTEGER,
        attack INTEGER, defence INTEGER, life INTEGER,
        thr_air INTEGER NOT NULL DEFAULT 0, thr_earth INTEGER NOT NULL DEFAULT 0,
        thr_fire INTEGER NOT NULL DEFAULT 0, thr_water INTEGER NOT NULL DEFAULT 0,
        rules_text TEXT NOT NULL DEFAULT '');
    CREATE TABLE printings (printing_id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL,
        set_name TEXT NOT NULL, released_at TEXT, set_number TEXT, product TEXT,
        finish TEXT, slug TEXT NOT NULL UNIQUE, artist TEXT, flavour_text TEXT,
        type_text TEXT, rarity TEXT, type TEXT, rules_text TEXT, cost INTEGER,
        attack INTEGER, defence INTEGER, life INTEGER,
        thr_air INTEGER NOT NULL DEFAULT 0, thr_earth INTEGER NOT NULL DEFAULT 0,
        thr_fire INTEGER NOT NULL DEFAULT 0, thr_water INTEGER NOT NULL DEFAULT 0,
        image_hash TEXT, retired_at TEXT);
    CREATE TABLE slug_history (slug TEXT NOT NULL, printing_id INTEGER NOT NULL,
        valid_from TEXT NOT NULL, valid_to TEXT);
    CREATE TABLE name_history (name TEXT NOT NULL, card_id INTEGER NOT NULL,
        valid_from TEXT NOT NULL, valid_to TEXT);
    INSERT INTO meta VALUES ('schema_version', '6'), ('next_card_id', '8'),
                            ('next_printing_id', '12');
    INSERT INTO cards VALUES (7, 'Daperyll Vampire', 'Minion', 'Exceptional',
        'Undead, Beast', 'Earth, Water', 5, 4, 4, NULL, 0, 2, 0, 1,
        'UPDATED: Airborne' || char(10) || 'Strike damage heals you.');
    INSERT INTO printings VALUES (11, 7, 'Alpha', '2023-04-19', '001', 'Booster',
        'Standard', '001-daperyll_vampire-b-s', 'An Artist', 'Flavour.', 'A typeline',
        'Exceptional', 'Minion', 'old copy', 5, 4, 4, NULL, 0, 2, 0, 1, NULL, NULL);
    INSERT INTO slug_history VALUES ('001-daperyll-vampire-b-s', 11, '2026-08-19', '2026-08-20');
    INSERT INTO slug_history VALUES ('001-daperyll_vampire-b-s', 11, '2026-08-20', NULL);
    INSERT INTO name_history VALUES ('Daperyll Vampire', 7, '2026-08-19', NULL);
    """

    def test_rebuild_keeps_ids_and_reencodes_fields(self):
        from registry.migrate_v7 import migrate
        from registry.validate import check_internal
        old = open_db(":memory:")
        old.executescript(self.V6_DDL)
        new = open_db(":memory:")
        migrate(old, new, "2026-09-09")

        state = load_registry_state(new)
        card = state["cards"]["Daperyll Vampire"]
        self.assertEqual(card["card_id"], 7)
        self.assertEqual(card["defense"], 4)
        self.assertEqual(card["subtypes"], ["Undead", "Beast"])
        self.assertEqual(card["elements"], ["Earth", "Water"])
        self.assertEqual(card["rules_text"], "Airborne\nStrike damage heals you.")
        self.assertIsNone(card["category"])
        printing = state["printings"]["001-daperyll_vampire-b-s"]
        self.assertEqual(printing["printing_id"], 11)
        self.assertEqual(printing["set_code"], "001")
        self.assertEqual(printing["typeline"], "A typeline")
        self.assertEqual(printing["flavour_text"], "Flavour.")
        self.assertNotIn("rules_text", printing)
        self.assertEqual(state["slug_owners"],
                         {"001-daperyll-vampire-b-s": 11, "001-daperyll_vampire-b-s": 11})
        self.assertEqual(registry.db.get_meta(new, "next_card_id"), "8")
        self.assertEqual(registry.db.get_meta(new, "next_printing_id"), "12")
        rows = new.execute("SELECT rules_text, valid_from, valid_to FROM rules_history").fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [("Airborne\nStrike damage heals you.", "2026-09-09", None)])
        errors = []
        check_internal(new, errors)
        self.assertEqual(errors, [])

    def test_refuses_a_database_that_is_not_v6(self):
        from registry.migrate_v7 import migrate
        old = open_db(":memory:")
        init_db(old)
        with self.assertRaises(ValueError):
            migrate(old, open_db(":memory:"), "2026-09-09")


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
        raw = copy.deepcopy(RAW_API)
        raw[0]["engine"]["back"] = engine(type="Avatar", category="Avatar", rarity=None)
        raw[0]["printings"][0]["meta"]["back"] = {
            "finish": "Standard", "product": "Booster", "flavor": None,
            "typeline": "Back", "artist": {"name": "B", "slug": "b"}}
        apply_plan(con, diff(load_registry_state(con), build_snapshot(raw)), "2026-08-19")
        export = json.loads(render(build_export(con)))
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(export))
        self.assertEqual(errors, [], [e.message for e in errors[:3]])


class HistoryValidationTest(unittest.TestCase):
    def populated(self):
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")
        return con

    def test_missing_open_name_row_is_reported(self):
        from registry.validate import check_internal
        con = self.populated()
        errors = []
        check_internal(con, errors)
        self.assertEqual(errors, [])

        con.execute("UPDATE name_history SET valid_to = '2026-01-01' "
                    "WHERE card_id = 1 AND valid_to IS NULL")
        errors = []
        check_internal(con, errors)
        self.assertTrue(any("open name_history rows" in e for e in errors), errors)

    def test_rules_history_must_agree_with_the_card(self):
        from registry.validate import check_internal
        con = self.populated()
        con.execute("UPDATE cards SET rules_text = 'edited by hand' WHERE card_id = 1")
        errors = []
        check_internal(con, errors)
        self.assertTrue(any("open rules_history row" in e for e in errors), errors)


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
        for key in ("sets", "cards", "printings", "slug_history", "name_history",
                    "rules_history"):
            self.assertEqual(manifest["counts"][key], header[key])
        self.assertEqual([a["name"] for a in manifest["artifacts"]],
                         ["registry.json", "registry.sqlite", "registry.schema.json"])
        self.assertEqual(manifest["artifacts"][0]["sha256"], stated)


if __name__ == "__main__":
    unittest.main()
