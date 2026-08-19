"""Slug ownership: once a slug has referred to a printing it may never
refer to a different one. The rule is enforced three times over - by the
database engine, by the diff planner, and by the validator - because each
layer can be reached without passing through the others."""

import sqlite3
import unittest

from registry.db import init_db, load_registry_state, open_db
from registry.diff import diff
from registry.sync import apply_plan
from registry.validate import check_internal

from test_diff import api_of, card, printing, registry_of

OWNERSHIP_TRIGGERS = ("slug_history_no_reassign", "printings_slug_owned_insert",
                      "printings_slug_owned_update")


def fresh_db():
    con = open_db(":memory:")
    init_db(con)
    return con


def seed(con, slug="004-witch-b-s"):
    """One card, one printing holding `slug`, one open history row."""
    con.execute("INSERT INTO cards (card_id, name) VALUES (1, 'Witch')")
    con.execute("INSERT INTO printings (printing_id, card_id, set_name, slug) "
                "VALUES (1, 1, 'Alpha', ?)", (slug,))
    con.execute("INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
                "VALUES (?, 1, '2026-08-19', NULL)", (slug,))
    con.execute("INSERT INTO cards (card_id, name) VALUES (2, 'Wizard')")
    con.execute("INSERT INTO printings (printing_id, card_id, set_name, slug) "
                "VALUES (2, 2, 'Alpha', '005-wizard-b-s')")
    con.execute("INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
                "VALUES ('005-wizard-b-s', 2, '2026-08-19', NULL)")
    con.commit()


class EngineTest(unittest.TestCase):
    def test_history_row_cannot_claim_another_printings_slug(self):
        con = fresh_db()
        seed(con)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
                        "VALUES ('004-witch-b-s', 2, '2026-08-20', NULL)")

    def test_printing_cannot_be_updated_onto_an_owned_slug(self):
        con = fresh_db()
        seed(con)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE printings SET slug = '004-witch-b-s' WHERE printing_id = 2")

    def test_printing_cannot_be_inserted_with_an_owned_slug(self):
        con = fresh_db()
        seed(con)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO printings (printing_id, card_id, set_name, slug) "
                        "VALUES (3, 2, 'Beta', '004-witch-b-s')")

    def test_a_printing_may_reclaim_its_own_former_slug(self):
        con = fresh_db()
        seed(con)
        con.execute("UPDATE printings SET slug = '004-witch_x-b-s' WHERE printing_id = 1")
        con.execute("INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
                    "VALUES ('004-witch_x-b-s', 1, '2026-08-20', NULL)")
        con.execute("UPDATE printings SET slug = '004-witch-b-s' WHERE printing_id = 1")
        con.execute("INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
                    "VALUES ('004-witch-b-s', 1, '2026-08-21', NULL)")


class RoundTripTest(unittest.TestCase):
    """A -> B -> A on the same printing is legitimate and must survive the
    whole pipeline, ending with both slugs owned by that one printing."""

    def test_rename_out_and_back_is_allowed(self):
        con = fresh_db()
        # Populate through the pipeline so ids come from the counters.
        apply_plan(con, diff(load_registry_state(con),
                             api_of([card("Witch")],
                                    [printing("004-witch-b-s", "Witch")])),
                   "2026-08-19")
        printing_id = load_registry_state(con)["printings"]["004-witch-b-s"]["printing_id"]

        renamed = api_of([card("Witch")], [printing("004-witch_x-b-s", "Witch")])
        plan = diff(load_registry_state(con), renamed)
        self.assertEqual(len(plan["printing_renames"]), 1)
        self.assertFalse(plan["ambiguous"])
        apply_plan(con, plan, "2026-08-20")

        back = api_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        plan = diff(load_registry_state(con), back)
        self.assertEqual(len(plan["printing_renames"]), 1)
        self.assertEqual(plan["printing_renames"][0]["new_slug"], "004-witch-b-s")
        self.assertFalse(plan["ambiguous"])
        apply_plan(con, plan, "2026-08-21")

        owners = load_registry_state(con)["slug_owners"]
        self.assertEqual(owners["004-witch-b-s"], printing_id)
        self.assertEqual(owners["004-witch_x-b-s"], printing_id)


class PlannerTest(unittest.TestCase):
    def registry_with_history(self):
        """P1 (Witch) was renamed 004-witch-b-s -> 004-witch_x-b-s, so the
        old slug is historical but no longer current."""
        reg = registry_of(
            [card("Witch"), card("Wizard", rules_text="other")],
            [printing("004-witch_x-b-s", "Witch")])
        reg["slug_owners"] = {"004-witch-b-s": 1, "004-witch_x-b-s": 1}
        return reg

    def test_new_printing_claiming_a_historical_slug_is_quarantined(self):
        reg = self.registry_with_history()
        api = api_of(
            [card("Witch"), card("Wizard", rules_text="other")],
            [printing("004-witch_x-b-s", "Witch"),
             printing("004-witch-b-s", "Wizard", rules_text="other",
                      set_name="Beta")])
        plan = diff(reg, api)
        self.assertFalse(plan["new_printings"])
        self.assertFalse(plan["printing_renames"])
        cases = [c for c in plan["ambiguous"]
                 if c["problem"] == "historical slug would be reassigned to a different printing"]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0], {
            "kind": "printing",
            "problem": "historical slug would be reassigned to a different printing",
            "slug": "004-witch-b-s",
            "owned_by_printing_id": 1,
            "attempted_printing_id": None,
        })

    def test_rename_onto_a_historical_slug_is_quarantined(self):
        # P2 (Wizard) vanishes and reappears under a slug P1 already owns:
        # a clean set+product+finish pairing that ownership must veto.
        reg = registry_of(
            [card("Witch"), card("Wizard", rules_text="other")],
            [printing("004-witch_x-b-s", "Witch"),
             printing("005-wizard-b-s", "Wizard", rules_text="other")])
        reg["slug_owners"] = {"004-witch-b-s": 1, "004-witch_x-b-s": 1,
                              "005-wizard-b-s": 2}
        api = api_of(
            [card("Witch"), card("Wizard", rules_text="other")],
            [printing("004-witch_x-b-s", "Witch"),
             printing("004-witch-b-s", "Wizard", rules_text="other")])
        plan = diff(reg, api)
        self.assertFalse(plan["printing_renames"])
        self.assertFalse(plan["printing_updates"])
        cases = [c for c in plan["ambiguous"]
                 if c["problem"] == "historical slug would be reassigned to a different printing"]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["owned_by_printing_id"], 1)
        self.assertEqual(cases[0]["attempted_printing_id"], 2)

    def test_retired_printing_still_holding_the_slug_uses_the_common_path(self):
        # P1 is retired but still carries slug A; upstream hands A to a
        # different card. That is the pre-existing quarantine shape.
        reg = registry_of(
            [card("Witch"), card("Wizard", rules_text="other")],
            [printing("004-witch-b-s", "Witch", retired_at="2026-01-01")])
        reg["slug_owners"] = {"004-witch-b-s": 1}
        api = api_of([card("Witch"), card("Wizard", rules_text="other")],
                     [printing("004-witch-b-s", "Wizard", rules_text="other")])
        plan = diff(reg, api)
        self.assertEqual([c["problem"] for c in plan["ambiguous"]],
                         ["slug kept but now belongs to a different card"])
        self.assertFalse(plan["new_printings"] or plan["printing_renames"]
                         or plan["printing_updates"] or plan["unretire_printings"])

    def test_contradictory_human_decision_raises(self):
        reg = registry_of(
            [card("Witch"), card("Wizard", rules_text="other")],
            [printing("004-witch_x-b-s", "Witch"),
             printing("005-wizard-b-s", "Wizard", rules_text="other")])
        reg["slug_owners"] = {"004-witch-b-s": 1, "004-witch_x-b-s": 1,
                              "005-wizard-b-s": 2}
        api = api_of(
            [card("Witch"), card("Wizard", rules_text="other")],
            [printing("004-witch_x-b-s", "Witch"),
             printing("004-witch-b-s", "Wizard", rules_text="other",
                      set_name="Beta")])
        decisions = {"printing_renames": [
            {"printing_id": 2, "new_slug": "004-witch-b-s"}]}
        with self.assertRaises(ValueError):
            diff(reg, api, decisions)


class RollbackTest(unittest.TestCase):
    def test_a_violating_plan_leaves_the_database_untouched(self):
        con = fresh_db()
        seed(con)
        before_slugs = con.execute(
            "SELECT printing_id, slug FROM printings ORDER BY printing_id").fetchall()
        before_history = con.execute("SELECT count(*) AS n FROM slug_history").fetchone()["n"]

        plan = {"card_renames": [], "new_cards": [], "card_updates": [],
                "new_printings": [],
                "printing_renames": [{"printing_id": 2,
                                      "old_slug": "005-wizard-b-s",
                                      "new_slug": "004-witch-b-s",
                                      "decided_by": "human"}],
                "printing_updates": [], "retire_printings": [],
                "unretire_printings": [], "ambiguous": [], "notes": []}
        with self.assertRaises(sqlite3.IntegrityError):
            apply_plan(con, plan, "2026-08-20")

        after_slugs = con.execute(
            "SELECT printing_id, slug FROM printings ORDER BY printing_id").fetchall()
        after_history = con.execute("SELECT count(*) AS n FROM slug_history").fetchone()["n"]
        self.assertEqual([tuple(r) for r in after_slugs],
                         [tuple(r) for r in before_slugs])
        self.assertEqual(after_history, before_history)


class ValidatorTest(unittest.TestCase):
    def test_conflicting_history_rows_are_reported(self):
        con = fresh_db()
        seed(con)
        for name in OWNERSHIP_TRIGGERS:
            con.execute(f"DROP TRIGGER {name}")
        con.execute("INSERT INTO slug_history (slug, printing_id, valid_from, valid_to) "
                    "VALUES ('004-witch-b-s', 2, '2026-08-20', '2026-08-21')")
        con.commit()

        errors = []
        check_internal(con, errors)
        conflicts = [e for e in errors if "refers to more than one printing" in e]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("004-witch-b-s", conflicts[0])
        self.assertIn("[1, 2]", conflicts[0])


if __name__ == "__main__":
    unittest.main()
