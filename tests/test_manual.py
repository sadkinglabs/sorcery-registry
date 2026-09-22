"""Manual entries: cards and printings recorded by hand (data/manual.json).

The whole life of a manual record is pinned here: minted with real ids,
exported as origin "manual", never retired by a sync, held for review when
upstream starts serving something like it, and confirmed into an ordinary
record without its id ever changing."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from registry.changes import diff_exports, summary_line
from registry.db import DDL, init_db, load_registry_state, open_db
from registry.diff import close_names, diff, is_noop
from registry.export import build_export
from registry.fetch import build_snapshot
from registry.manual import (apply_manual, check_manual, load_manual, load_released_with,
                             predict_slug, slug_name, write_manual)
from registry.sync import apply_plan
from registry.validate import check_internal

from test_pipeline import RAW_API, engine, upstream_printing

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schema" /
                     "registry.schema.json").read_text(encoding="utf-8"))


def populated():
    con = open_db(":memory:")
    init_db(con)
    apply_plan(con, diff(load_registry_state(con), build_snapshot(copy.deepcopy(RAW_API))),
               "2026-08-19")
    return con


def printing(**over):
    base = {"printing_id": None, "set_code": "999", "set_name": "Promo",
            "released_at": "2023-10-06", "released_with": "002",
            "product": "OrganizedPlay", "finish": "Foil", "artist": "Vincent Pompetti",
            "typeline": "A prize for the victor", "flavour_text": "Glory waits.",
            "source": "Beta store kit card, photographed", "recorded": "2026-09-22"}
    base.update(over)
    return base


def card(**over):
    base = {"codex_id": None, "name": "Lantern Warden", "type": "Minion", "category": "Spell",
            "rarity": "Unique", "slot": "Unique", "subtypes": ["Mortal"], "elements": ["Fire"],
            "keywords": [], "umbrellas": [], "cost": 4, "attack": 3, "defense": 3, "life": None,
            "thr_air": 0, "thr_earth": 0, "thr_fire": 2, "thr_water": 0,
            "rules_text": "Lantern Warden can't be targeted.", "back": None,
            "source": "Store kit cards, photographed", "recorded": "2026-09-22",
            "printings": [printing(released_at="2023-06-22", released_with="001",
                                   product="Kickstarter"),
                          printing(),
                          printing(released_at="2024-10-04", released_with="004")]}
    base.update(over)
    return base


def manual(cards=(), printings=()):
    return {"cards": [copy.deepcopy(c) for c in cards],
            "printings": [copy.deepcopy(p) for p in printings]}


def quiet(_):
    pass


def validate_schema(export):
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - CI installs it
        return []
    return [e.message for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(export)]


class SlugTest(unittest.TestCase):
    def test_names_are_spelled_as_the_publisher_does(self):
        self.assertEqual(slug_name("Älvalinne Dryads"), "alvalinne_dryads")
        self.assertEqual(slug_name("Mariner's Curse"), "mariners_curse")
        self.assertEqual(slug_name("Castle's Ablaze!"), "castles_ablaze")
        self.assertEqual(slug_name("The Champion"), "the_champion")

    def test_product_and_finish_codes(self):
        self.assertEqual(predict_slug("999", "Sorcerer", "OrganizedPlay", "Foil"), "999-sorcerer-op-f")
        self.assertEqual(predict_slug("001", "Frog", "BoxTopper", "Standard"), "001-frog-bt-s")
        self.assertEqual(predict_slug("999", "Witch", "OrganizedPlay", "Rainbow"), "999-witch-op-rf")
        self.assertEqual(predict_slug("CUR", "Critical Strike", "Curio", "Standard"),
                         "cur-critical_strike-c-s")
        self.assertEqual(predict_slug("999", "The Champion", "OrganizedPlay", "Foil", "002"),
                         "999-the_champion_002-op-f")


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "manual.json"

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return load_manual(self.path)

    def test_a_missing_file_means_no_entries(self):
        self.assertEqual(load_manual(self.path), {"cards": [], "printings": []})

    def test_a_good_file_loads(self):
        loaded = self.load({"_comment": "x", "cards": [card()], "printings": []})
        self.assertEqual(loaded["cards"][0]["name"], "Lantern Warden")

    def test_mistakes_are_errors(self):
        bad_cards = [
            card(printings=[]),                                   # a card with no printing
            card(cost=-1),
            card(thr_fire=None),                                  # thresholds are never unknown
            card(rules_text=None),                                # text must be known ("" for none)
            card(subtypes="Mortal"),
            card(recorded="22/09/2026"),
            card(source=""),
            card(codex_id="P000001"),
            card(printings=[printing(set_code="99")]),
            card(printings=[printing(set_code="cur")]),           # ours are capitals
            card(printings=[printing(set_code="001", released_with="001")]),
            card(printings=[printing(released_with="999")]),      # 999 is no release
            card(printings=[printing(slug="Not A Slug")]),
            card(printings=[printing(artist=" padded")]),
            card(printings=[{k: v for k, v in printing().items() if k != "typeline"}]),
            card(withdrawn={"on": "2026-09-23"}),
        ]
        for bad in bad_cards:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.load({"cards": [bad], "printings": []})
        with self.assertRaises(ValueError):
            self.load({"cards": [card(), card()], "printings": []})    # the same card twice
        with self.assertRaises(ValueError):
            self.load({"cards": [], "printings": [printing()]})       # which card is it of?
        with self.assertRaises(ValueError):
            self.load({"cards": [], "printings": [], "extra": []})


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.con = populated()

    def apply(self, entries):
        return apply_manual(self.con, entries, log=quiet)

    def check(self, entries, released_with=None):
        errors = []
        check_internal(self.con, errors)
        check_manual(self.con, entries, released_with or {}, errors)
        return errors

    def test_minting_writes_ids_back_and_exports_manual_records(self):
        entries = manual([card()])
        counts = self.apply(entries)
        self.assertEqual(counts, {"minted": 4, "updated": 0, "confirmed": 0})
        warden = entries["cards"][0]
        self.assertEqual(warden["codex_id"], "C000003")          # the next card id
        self.assertEqual([p["printing_id"] for p in warden["printings"]],
                         ["P000004", "P000005", "P000006"])
        self.assertEqual(self.check(entries), [])

        export = build_export(self.con, released_with={})
        self.assertEqual(validate_schema(export), [])
        exported = next(c for c in export["cards"] if c["codex_id"] == "C000003")
        self.assertEqual(exported["origin"], "manual")
        self.assertEqual(exported["manual"], {"source": "Store kit cards, photographed",
                                              "recorded": "2026-09-22", "confirmed_at": None,
                                              "withdrawn": None})
        prints = {p["printing_id"]: p for p in export["printings"] if p["codex_id"] == "C000003"}
        # The Beta and Arthurian Legends printings would predict the same
        # slug: the second takes its release as a suffix.
        self.assertEqual([prints[i]["slug"] for i in sorted(prints)],
                         ["999-lantern_warden-k-f", "999-lantern_warden-op-f",
                          "999-lantern_warden_004-op-f"])
        self.assertEqual([prints[i]["released_with"] for i in sorted(prints)], ["001", "002", "004"])
        self.assertEqual(prints["P000005"]["artist_slug"], "vincent_pompetti")
        # A predicted slug owns nothing: no slug_history, and none exported.
        self.assertFalse(any(r["printing_id"] in prints for r in export["slug_history"]))
        rows = [r for r in export["card_history"] if r["codex_id"] == "C000003"]
        self.assertEqual([(r["source"], r["valid_from"], r["valid_to"]) for r in rows],
                         [("manual", "2023-06-22", None)])
        self.assertTrue(all(p["printed_as_current"] for p in prints.values()))
        self.assertEqual(exported["default_printing_id"], "P000006")   # most recent

    def test_reapplying_changes_nothing_and_a_corrected_reading_replaces_the_old(self):
        entries = manual([card()])
        self.apply(entries)
        before = build_export(self.con, released_with={})
        self.assertEqual(self.apply(entries), {"minted": 0, "updated": 4, "confirmed": 0})
        self.assertEqual(build_export(self.con, released_with={}), before)

        entries["cards"][0]["rules_text"] = "Lantern Warden can't be targeted by spells."
        entries["cards"][0]["printings"][1]["typeline"] = "A prize for the champion"
        self.apply(entries)
        self.assertEqual(self.check(entries), [])
        export = build_export(self.con, released_with={})
        rows = [r for r in export["card_history"] if r["codex_id"] == "C000003"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rules_text"], "Lantern Warden can't be targeted by spells.")

    def test_the_file_and_the_database_must_agree(self):
        entries = manual([card()])
        self.apply(entries)
        drifted = copy.deepcopy(entries)
        drifted["cards"][0]["cost"] = 5
        self.assertTrue(any("cost differs" in e for e in self.check(drifted)))
        unminted = manual([card(name="Another Card")])
        self.assertTrue(any("run python -m registry.manual apply" in e
                            for e in self.check({"cards": entries["cards"] + unminted["cards"],
                                                 "printings": []})))

    def test_a_manual_record_is_withdrawn_never_removed(self):
        entries = manual([card()])
        self.apply(entries)
        with self.assertRaises(ValueError):
            self.apply({"cards": [], "printings": []})
        entries["cards"][0]["printings"][2]["withdrawn"] = {
            "on": "2026-09-30", "reason": "The Arthurian Legends kit card was a Beta reprint."}
        self.apply(entries)
        self.assertEqual(self.check(entries), [])
        export = build_export(self.con, released_with={})
        withdrawn = next(p for p in export["printings"] if p["printing_id"] == "P000006")
        self.assertEqual(withdrawn["manual"]["withdrawn"]["on"], "2026-09-30")
        warden = next(c for c in export["cards"] if c["codex_id"] == "C000003")
        self.assertEqual(warden["default_printing_id"], "P000005")   # never a withdrawn one
        self.assertEqual(validate_schema(export), [])

    def test_a_name_already_held_is_refused(self):
        with self.assertRaises(ValueError):
            self.apply(manual([card(name="apprentice wizard")]))

    def test_a_curio_of_an_official_card_in_a_set_of_our_own(self):
        curio = printing(codex_id="C000001", set_code="CUR", set_name="Curios",
                         released_at=None, released_with="004", product="Curio",
                         finish="Standard", artist=None, flavour_text=None,
                         source="Curio card, photographed")
        entries = manual(printings=[curio])
        self.apply(entries)
        self.assertEqual(self.check(entries), [])
        export = build_export(self.con, released_with={})
        self.assertEqual(validate_schema(export), [])
        made = next(p for p in export["printings"] if p["printing_id"] == "P000004")
        self.assertEqual(made["slug"], "cur-apprentice_wizard-c-s")
        self.assertEqual((made["codex_id"], made["origin"], made["released_with"]),
                         ("C000001", "manual", "004"))
        self.assertIsNone(made["artist"])                          # unknown, not copied
        cur = next(s for s in export["sets"] if s["set_code"] == "CUR")
        self.assertEqual((cur["origin"], cur["set_name"], cur["cards"]), ("manual", "Curios", 1))
        self.assertEqual(next(s for s in export["sets"] if s["set_code"] == "001")["origin"], "api")
        wizard = next(c for c in export["cards"] if c["codex_id"] == "C000001")
        self.assertEqual(wizard["origin"], "api")
        self.assertIn("CUR", wizard["set_codes"])
        self.assertNotEqual(wizard["default_printing_id"], "P000004")   # a Booster outranks it

        from registry.publish import build_objects
        objects = build_objects(export, dataset_version="v9.9.9")
        self.assertEqual(objects["sets/CUR.json"]["cards"][0]["printing_ids"], ["P000004"])
        predicted = objects["slugs/cur-apprentice_wizard-c-s.json"]
        self.assertEqual((predicted["printing_id"], predicted["valid_from"]), ("P000004", None))
        self.assertEqual(objects["index/printings.json"][-1]["origin"], "manual")

    def test_released_with_for_official_promos_is_checked(self):
        errors = self.check({"cards": [], "printings": []},
                            {"P000001": {"set_code": "002", "source": "s", "recorded": "2026-09-22"}})
        self.assertTrue(any("in set 001" in e for e in errors))
        errors = self.check({"cards": [], "printings": []},
                            {"P000999": {"set_code": "002", "source": "s", "recorded": "2026-09-22"}})
        self.assertTrue(any("not a printing" in e for e in errors))

    def test_the_committed_files_load(self):
        root = Path(__file__).resolve().parent.parent / "data"
        load_manual(root / "manual.json")
        load_released_with(root / "released-with.json")

    def test_write_manual_round_trips(self):
        entries = manual([card()])
        self.apply(entries)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual.json"
            write_manual(entries, path)
            self.assertEqual(load_manual(path)["cards"][0]["codex_id"], "C000003")


def with_upstream_card(name="Lantern Warden", slugs=("999-lantern_warden-op-f",), **engine_over):
    """RAW_API plus a card upstream now serves."""
    raw = copy.deepcopy(RAW_API)
    raw.append({
        "id": "cmt333333333333333333333", "name": name, "slug": slug_name(name),
        "engine": engine(type="Minion", rarity="Unique", slot="Unique",
                         rules="Lantern Warden can't be targeted.", cost=4, attack=3, defense=3,
                         air=0, fire=2, elements=["Fire"], keywords=[], **engine_over),
        "printings": [upstream_printing(slug, "Promo", "999", "2023-10-06", finish="Foil",
                                        product="OrganizedPlay", typeline="A prize for the victor",
                                        artist={"name": "Vincent Pompetti",
                                                "slug": "vincent_pompetti"})
                      for slug in slugs],
    })
    return build_snapshot(raw)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.con = populated()
        self.entries = manual([card()])
        apply_manual(self.con, self.entries, log=quiet)

    def plan(self, snapshot, decisions=None):
        return diff(load_registry_state(self.con), snapshot, decisions)

    def test_a_sync_never_retires_a_manual_record(self):
        plan = self.plan(build_snapshot(copy.deepcopy(RAW_API)))
        self.assertTrue(is_noop(plan), plan)

    def test_upstream_serving_the_card_waits_for_a_person(self):
        plan = self.plan(with_upstream_card())
        self.assertEqual(plan["new_cards"], [])
        self.assertEqual(plan["new_printings"], [])
        self.assertEqual([c["kind"] for c in plan["ambiguous"]], ["manual-card"])
        self.assertEqual(plan["ambiguous"][0]["missing"], ["Lantern Warden"])

    def test_confirming_keeps_every_id_and_hands_the_record_to_upstream(self):
        before = build_export(self.con, released_with={})
        snapshot = with_upstream_card(slugs=("999-lantern_warden-op-f",
                                             "999-lantern_warden_arthur-op-f"))
        # With the card confirmed, its printings are matched next - and
        # both upstream OP foils resemble both manual OP foils.
        plan = self.plan(snapshot, {"confirm_cards": [{"card_id": 3, "name": "Lantern Warden"}]})
        self.assertEqual(plan["confirm_cards"],
                         [{"card_id": 3, "old_name": "Lantern Warden", "new_name": "Lantern Warden"}])
        self.assertEqual(sorted(c["kind"] for c in plan["ambiguous"]),
                         ["manual-printing", "manual-printing"])
        decisions = {"confirm_cards": [{"card_id": 3, "name": "Lantern Warden"}],
                     "confirm_printings": [
                         {"printing_id": 5, "slug": "999-lantern_warden-op-f"},
                         {"printing_id": 6, "slug": "999-lantern_warden_arthur-op-f"}]}
        plan = self.plan(snapshot, decisions)
        self.assertEqual(plan["ambiguous"], [])
        self.assertEqual(plan["new_cards"], [])
        self.assertEqual(plan["new_printings"], [])
        self.assertEqual([c["new_slug"] for c in plan["confirm_printings"]],
                         ["999-lantern_warden-op-f", "999-lantern_warden_arthur-op-f"])
        # Upstream's values win where they differ from our reading.
        self.assertIn({"printing_id": 6, "slug": "999-lantern_warden_arthur-op-f",
                       "changes": {"released_at": {"old": "2024-10-04", "new": "2023-10-06"}}},
                      plan["printing_updates"])
        apply_plan(self.con, plan, "2026-10-01")

        after = build_export(self.con, released_with={})
        self.assertEqual(validate_schema(after), [])
        warden = next(c for c in after["cards"] if c["codex_id"] == "C000003")
        self.assertEqual(warden["origin"], "api")
        self.assertEqual(warden["manual"]["confirmed_at"], "2026-10-01")
        confirmed = next(p for p in after["printings"] if p["printing_id"] == "P000006")
        self.assertEqual((confirmed["origin"], confirmed["slug"]),
                         ("api", "999-lantern_warden_arthur-op-f"))
        self.assertIn({"slug": "999-lantern_warden_arthur-op-f", "printing_id": "P000006",
                       "valid_from": "2026-10-01", "valid_to": None}, after["slug_history"])
        kickstarter = next(p for p in after["printings"] if p["printing_id"] == "P000004")
        self.assertEqual(kickstarter["origin"], "manual")       # not served: still ours
        self.assertEqual({c["codex_id"] for c in after["cards"]},
                         {c["codex_id"] for c in before["cards"]})
        self.assertEqual({p["printing_id"] for p in after["printings"]},
                         {p["printing_id"] for p in before["printings"]})

        errors = []
        check_internal(self.con, errors)
        check_manual(self.con, self.entries, {}, errors)
        self.assertEqual(errors, [])
        # The manual file still applies: confirmed records are left alone.
        self.assertEqual(apply_manual(self.con, self.entries, log=quiet)["confirmed"], 3)

        changes = diff_exports(before, after, "v3.4.0", "v3.4.1")
        self.assertEqual(changes["manual"]["confirmed"], ["C000003", "P000005", "P000006"])
        self.assertIn("3 manual records confirmed upstream", summary_line(changes))
        # And a later sync with the same upstream is a no-op: nothing retires.
        self.assertTrue(is_noop(self.plan(snapshot)))

    def test_a_close_name_waits_too_and_can_be_declared_different(self):
        snapshot = with_upstream_card(name="The Lantern Warden", slugs=("999-the_lantern_warden-op-f",))
        plan = self.plan(snapshot)
        self.assertEqual([c["kind"] for c in plan["ambiguous"]], ["manual-card"])
        plan = self.plan(snapshot, {"new_cards": ["The Lantern Warden"]})
        self.assertEqual(plan["ambiguous"], [])
        self.assertEqual([c["name"] for c in plan["new_cards"]], ["The Lantern Warden"])

    def test_a_new_upstream_printing_like_a_manual_one_waits(self):
        # A manual OP foil of an official card; upstream then serves one.
        extra = printing(codex_id="C000001", released_with=None,
                         source="Store kit card, photographed")
        self.entries["printings"].append(copy.deepcopy(extra))
        apply_manual(self.con, self.entries, log=quiet)
        raw = copy.deepcopy(RAW_API)
        raw[0]["printings"].append(upstream_printing("999-apprentice_wizard-op-f", "Promo", "999",
                                                     "2023-10-06", finish="Foil",
                                                     product="OrganizedPlay"))
        snapshot = build_snapshot(raw)
        plan = self.plan(snapshot)
        self.assertEqual([c["kind"] for c in plan["ambiguous"]], ["manual-printing"])
        self.assertEqual(plan["new_printings"], [])
        # Declaring it new is refused while the manual printing holds that
        # very slug as its prediction.
        with self.assertRaises(ValueError):
            self.plan(snapshot, {"new_printings": ["999-apprentice_wizard-op-f"]})
        plan = self.plan(snapshot, {"confirm_printings": [
            {"printing_id": 7, "slug": "999-apprentice_wizard-op-f"}]})
        self.assertEqual(plan["ambiguous"], [])
        self.assertEqual(len(plan["confirm_printings"]), 1)

    def test_a_decision_that_does_not_match_is_an_error(self):
        with self.assertRaises(ValueError):
            self.plan(with_upstream_card(), {"confirm_cards": [{"card_id": 1, "name": "Lantern Warden"}]})
        with self.assertRaises(ValueError):
            self.plan(with_upstream_card(), {"confirm_cards": [{"card_id": 3, "name": "Nope"}]})


class CloseNamesTest(unittest.TestCase):
    def test_spellings_of_one_card(self):
        self.assertTrue(close_names("The Champion", "Champion"))
        self.assertTrue(close_names("The Champion", "the champion"))
        self.assertTrue(close_names("Critical Strike", "Critical Strikes"))
        self.assertFalse(close_names("The Champion", "Polar Bears"))
        self.assertFalse(close_names("Frog", "Fog"))


class MigrationTest(unittest.TestCase):
    def v11(self):
        con = open_db(":memory:")
        ddl = DDL
        for column in ("origin           TEXT NOT NULL DEFAULT 'api',", "manual_source    TEXT,",
                       "manual_recorded  TEXT,", "confirmed_at     TEXT,", "withdrawn_at     TEXT,",
                       "withdrawn_reason TEXT"):
            ddl = ddl.replace(f"    {column}\n", "")
        ddl = ddl.replace("    released_with TEXT,\n", "")
        ddl = ddl.replace("default_printing_id INTEGER REFERENCES printings(printing_id),",
                          "default_printing_id INTEGER REFERENCES printings(printing_id)")
        ddl = ddl.replace("retired_at   TEXT,", "retired_at   TEXT")
        con.executescript(ddl)
        return con

    def test_v12_adds_the_manual_columns_from_v11_or_an_early_v12(self):
        from registry.db import get_meta
        from registry.migrate_v12 import migrate
        for found in ("11", "12"):
            con = self.v11()
            self.assertNotIn("origin", {r["name"] for r in con.execute("PRAGMA table_info(cards)")})
            con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (found,))
            migrate(con)
            self.assertEqual(get_meta(con, "schema_version"), "12")
            fresh = sqlite3.connect(":memory:")
            fresh.executescript(DDL)
            for table in ("cards", "printings"):
                self.assertEqual([r[1] for r in con.execute(f"PRAGMA table_info({table})")],
                                 [r[1] for r in fresh.execute(f"PRAGMA table_info({table})")])
            with self.assertRaises(ValueError):
                migrate(con)


if __name__ == "__main__":
    unittest.main()
