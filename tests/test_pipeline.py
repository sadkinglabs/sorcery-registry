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
        # New cards have never been updated: the registry-owned flag is off.
        self.assertFalse(wizard["errata"])
        broken = next(c for c in export_one["cards"] if c["name"] == "Broken Site")
        # Derived set data: per-card set membership and the set catalogue,
        # whose release date is the earliest printing date in the set.
        self.assertEqual(wizard["set_codes"], ["001"])
        self.assertEqual(broken["set_codes"], ["010"])
        self.assertEqual(export_one["header"]["sets"], 2)
        self.assertEqual(export_one["sets"], [
            {"set_code": "001", "set_name": "Alpha",
             "released_at": "2023-06-22", "cards": 1, "printings": 2,
             "api_url": "https://api.kairosarchive.net/v3/sets/001.json",
             "kairos_url": "https://kairosarchive.net/sets/001"},
            {"set_code": "010", "set_name": "Gothic",
             "released_at": "2026-05-01", "cards": 1, "printings": 1,
             "api_url": "https://api.kairosarchive.net/v3/sets/010.json",
             "kairos_url": "https://kairosarchive.net/sets/010"},
        ])

        # Every card starts with one open name_history row carrying its name
        # and one open card_history row carrying its gameplay face.
        self.assertEqual(export_one["header"]["name_history"], 2)
        by_name = {h["name"]: h for h in export_one["name_history"]}
        self.assertEqual(sorted(by_name), ["Apprentice Wizard", "Broken Site"])
        self.assertEqual(by_name["Apprentice Wizard"]["codex_id"], wizard_id)
        for row in export_one["name_history"]:
            self.assertEqual(row["valid_from"], "2026-08-19")
            self.assertIsNone(row["valid_to"])
        self.assertEqual(export_one["header"]["card_history"], 2)
        by_text = {h["rules_text"]: h for h in export_one["card_history"]}
        self.assertEqual(by_text["Spellcaster\nGenesis → Draw a spell."]["codex_id"], wizard_id)
        for row in export_one["card_history"]:
            self.assertIsNone(row["valid_to"])
            self.assertIn("cost", row)
        # Derived: the representative printing (Booster, Standard wins over
        # Foil) and whether each printing shows the current face (yes: the
        # face has never changed).
        self.assertEqual(wizard["default_printing_id"], "P000001")
        self.assertTrue(all(p["printed_as_current"] for p in export_one["printings"]))

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


class CardHistoryTest(unittest.TestCase):
    """Upstream publishes only the current values and marks nothing; the
    registry records every state of a card's gameplay face it observes."""

    def populated(self):
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")
        return con

    def rows_for(self, export, name):
        card = next(c for c in export["cards"] if c["name"] == name)
        return card, [h for h in export["card_history"] if h["codex_id"] == card["codex_id"]]

    def test_reworded_card_closes_and_opens_rows(self):
        con = self.populated()
        reworded = copy.deepcopy(RAW_API)
        reworded[0]["engine"]["rules"] = "Spellcaster\r\n\r\nGenesis → Draw two spells."
        plan = diff(load_registry_state(con), build_snapshot(reworded))
        self.assertEqual([u["name"] for u in plan["card_updates"]], ["Apprentice Wizard"])
        self.assertEqual(list(plan["card_updates"][0]["changes"]), ["rules_text"])
        apply_plan(con, plan, "2026-09-01")

        export = build_export(con)
        wizard, rows = self.rows_for(export, "Apprentice Wizard")
        self.assertEqual(wizard["rules_text"], "Spellcaster\nGenesis → Draw two spells.")
        self.assertTrue(wizard["errata"])
        self.assertEqual([(r["valid_from"], r["valid_to"], r["rules_text"]) for r in rows], [
            ("2026-08-19", "2026-09-01", "Spellcaster\nGenesis → Draw a spell."),
            ("2026-09-01", None, "Spellcaster\nGenesis → Draw two spells."),
        ])
        # Both rows carry the whole face, and the open row equals the card.
        for row in rows:
            self.assertEqual(row["cost"], 3)
            self.assertEqual(row["keywords"], ["Spellcaster", "Genesis"])
        for field in ("type", "cost", "attack", "defense", "elements", "rules_text", "back"):
            self.assertEqual(rows[-1][field], wizard[field])
        broken, broken_rows = self.rows_for(export, "Broken Site")
        self.assertEqual(len(broken_rows), 1)
        self.assertFalse(broken["errata"])
        self.assertTrue(is_noop(diff(load_registry_state(con), build_snapshot(reworded))))

    def test_stat_change_is_recorded_and_is_errata(self):
        # The Polar Bears case: a reprint changes cost and power.
        con = self.populated()
        buffed = copy.deepcopy(RAW_API)
        buffed[0]["engine"]["cost"] = 4
        buffed[0]["engine"]["attack"] = 3
        plan = diff(load_registry_state(con), build_snapshot(buffed))
        self.assertEqual(set(plan["card_updates"][0]["changes"]), {"cost", "attack"})
        apply_plan(con, plan, "2026-09-01")

        export = build_export(con)
        wizard, rows = self.rows_for(export, "Apprentice Wizard")
        self.assertTrue(wizard["errata"])
        self.assertEqual([(r["valid_to"], r["cost"], r["attack"]) for r in rows],
                         [("2026-09-01", 3, 1), (None, 4, 3)])
        # Printings released before the new face were printed with the old
        # values. With no up-to-date printing, the Booster Standard is still
        # the default.
        for printing in export["printings"]:
            if printing["codex_id"] == wizard["codex_id"]:
                self.assertFalse(printing["printed_as_current"])
        self.assertEqual(wizard["default_printing_id"], "P000001")

        # A reprint released afterwards shows the current face and becomes
        # the default - even as a promo, since it is the only printing that
        # shows what the card now is.
        later = copy.deepcopy(buffed)
        later[0]["printings"].append(upstream_printing(
            "999-apprentice_wizard-op-f", "Promo", "999", "2026-10-01",
            product="OrganizedPlay", finish="Foil"))
        apply_plan(con, diff(load_registry_state(con), build_snapshot(later)), "2026-10-01")
        export = build_export(con)
        wizard, _ = self.rows_for(export, "Apprentice Wizard")
        flags = {p["slug"]: p["printed_as_current"] for p in export["printings"]
                 if p["codex_id"] == wizard["codex_id"]}
        self.assertEqual(flags, {"001-apprentice_wizard-b-s": False,
                                 "001-apprentice_wizard-b-f": False,
                                 "999-apprentice_wizard-op-f": True})
        promo_id = next(p["printing_id"] for p in export["printings"]
                        if p["slug"] == "999-apprentice_wizard-op-f")
        self.assertEqual(wizard["default_printing_id"], promo_id)

    def test_reprint_seen_in_the_same_sync_as_the_change_is_current(self):
        # The realistic sequence: the set ships on the 1st with the new
        # values printed on it; the registry syncs on the 6th and sees the
        # stat change and the new printing together. The reprint's release
        # date is before the face's date, yet it was printed with the face.
        con = self.populated()
        later = copy.deepcopy(RAW_API)
        later[0]["engine"]["cost"] = 4
        later[0]["engine"]["attack"] = 3
        later[0]["printings"].append(upstream_printing(
            "007-apprentice_wizard-b-s", "Revised", "007", "2026-10-01"))
        apply_plan(con, diff(load_registry_state(con), build_snapshot(later)), "2026-10-06")
        export = build_export(con)
        wizard, rows = self.rows_for(export, "Apprentice Wizard")
        self.assertEqual([(r["valid_from"], r["valid_to"]) for r in rows],
                         [("2026-08-19", "2026-10-06"), ("2026-10-06", None)])
        flags = {p["slug"]: p["printed_as_current"] for p in export["printings"]
                 if p["codex_id"] == wizard["codex_id"]}
        self.assertEqual(flags, {"001-apprentice_wizard-b-s": False,
                                 "001-apprentice_wizard-b-f": False,
                                 "007-apprentice_wizard-b-s": True})
        self.assertEqual(wizard["default_printing_id"],
                         next(p["printing_id"] for p in export["printings"]
                              if p["slug"] == "007-apprentice_wizard-b-s"))
        self.assertTrue(wizard["errata"])
        self.assertIn("007", wizard["set_codes"])
        self.assertEqual([s["set_code"] for s in export["sets"]], ["001", "007", "010"])

    def test_promo_never_outranks_a_booster_showing_the_same_face(self):
        con = self.populated()
        later = copy.deepcopy(RAW_API)
        later[0]["printings"].append(upstream_printing(
            "999-apprentice_wizard-op-f", "Promo", "999", "2026-10-01",
            product="OrganizedPlay", finish="Foil"))
        apply_plan(con, diff(load_registry_state(con), build_snapshot(later)), "2026-10-01")
        export = build_export(con)
        wizard = next(c for c in export["cards"] if c["name"] == "Apprentice Wizard")
        self.assertEqual(wizard["default_printing_id"], "P000001")

    def test_retagging_is_history_but_not_errata(self):
        con = self.populated()
        retagged = copy.deepcopy(RAW_API)
        retagged[0]["engine"]["keywords"] = ["Genesis", "Spellcaster", "Ward"]
        retagged[0]["engine"]["rarity"] = "Elite"
        plan = diff(load_registry_state(con), build_snapshot(retagged))
        self.assertEqual(set(plan["card_updates"][0]["changes"]), {"keywords", "rarity"})
        apply_plan(con, plan, "2026-09-01")
        export = build_export(con)
        wizard, rows = self.rows_for(export, "Apprentice Wizard")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["rarity"], "Elite")
        self.assertFalse(wizard["errata"])
        # A re-tag is a new face state, so printings released before it are
        # reported as showing older values - the history is literal.
        self.assertFalse(any(p["printed_as_current"] for p in export["printings"]
                             if p["codex_id"] == wizard["codex_id"]))

    def test_override_can_correct_the_flag(self):
        con = self.populated()
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        apply_overrides(snapshot, [{
            "match": {"card_name": "Broken Site"}, "set_fields": {"errata": True},
            "reason": "reworded before the registry existed"}])
        plan = diff(load_registry_state(con), snapshot)
        self.assertEqual(plan["card_updates"][0]["changes"],
                         {"errata": {"old": False, "new": True}})
        apply_plan(con, plan, "2026-09-01")
        export = build_export(con)
        self.assertTrue(next(c for c in export["cards"] if c["name"] == "Broken Site")["errata"])
        # No face field changed, so no history row was touched.
        self.assertEqual(export["header"]["card_history"], 2)


class DefaultPrintingTest(unittest.TestCase):
    def test_override_pins_a_printing(self):
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con),
                             build_snapshot(copy.deepcopy(RAW_API))), "2026-08-19")
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        apply_overrides(snapshot, [{
            "match": {"card_name": "Apprentice Wizard"},
            "set_fields": {"default_printing_id": "P000002"},
            "reason": "the foil is the art everyone knows"}])
        plan = diff(load_registry_state(con), snapshot)
        self.assertEqual(plan["card_updates"][0]["changes"],
                         {"default_printing_id": {"old": None, "new": "P000002"}})
        apply_plan(con, plan, "2026-09-01")
        export = build_export(con)
        wizard = next(c for c in export["cards"] if c["name"] == "Apprentice Wizard")
        self.assertEqual(wizard["default_printing_id"], "P000002")
        self.assertTrue(is_noop(diff(load_registry_state(con), snapshot)))
        # A pin naming another card's printing is a validation error, and
        # the export falls back to the rule rather than publish it.
        from registry.validate import check_internal
        con.execute("UPDATE cards SET default_printing_id = 3 WHERE card_id = 1")
        errors = []
        check_internal(con, errors)
        self.assertTrue(any("not one of its printings" in e for e in errors), errors)
        wizard = next(c for c in build_export(con)["cards"] if c["name"] == "Apprentice Wizard")
        self.assertEqual(wizard["default_printing_id"], "P000001")

    def test_rule_order(self):
        from registry.export import default_printing
        def p(pid, product="Booster", finish="Standard", released="2024-01-01",
              retired=None, current=True):
            return {"printing_id": pid, "product": product, "finish": finish,
                    "released_at": released, "retired_at": retired,
                    "printed_as_current": current}
        self.assertEqual(default_printing([p(1, finish="Foil"), p(2)]), 2)
        self.assertEqual(default_printing([p(1), p(2, product="BoxTopper", released="2025-01-01")]), 1)
        self.assertEqual(default_printing([p(1), p(2, released="2025-01-01")]), 2)
        self.assertEqual(default_printing([p(2), p(1)]), 1)
        self.assertEqual(default_printing([p(1, retired="2026-01-01"), p(2, finish="Foil")]), 2)
        self.assertEqual(default_printing([p(1, retired="2026-01-01")]), 1)
        # Showing the current face outranks everything but retirement.
        self.assertEqual(default_printing([p(1, current=False),
                                           p(2, product="Dust", finish="Foil")]), 2)
        self.assertIsNone(default_printing([]))


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
        front_keys = [k for k in wizard if k not in ("codex_id", "name", "back", "errata",
                                                     "set_codes", "printing_ids",
                                                     "default_printing_id", "api_url",
                                                     "kairos_url", "image_urls",
                                                     "image_status")]
        self.assertEqual(list(wizard["back"]), front_keys)
        self.assertEqual(wizard["back"]["life"], 20)
        printing = next(p for p in export["printings"]
                        if p["slug"] == "001-apprentice_wizard-b-s")
        self.assertEqual(list(printing["back"]),
                         ["artist", "artist_slug", "flavour_text", "typeline", "image_urls"])
        self.assertEqual(printing["back"]["artist_slug"], "bryon_wackwitz")


class MigrationTest(unittest.TestCase):
    def test_v10_records_the_version_and_refuses_anything_but_v9(self):
        from registry.db import get_meta, set_meta
        from registry.migrate_v10 import migrate
        con = open_db(":memory:")
        init_db(con)
        set_meta(con, "schema_version", "9")
        migrate(con)
        self.assertEqual(get_meta(con, "schema_version"), "10")
        with self.assertRaises(ValueError):
            migrate(con)



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


class RecordAddressesTest(unittest.TestCase):
    """Every record says where it lives (schema 9). The addresses are
    derived from the ids at export time, so they can never disagree with
    the record they sit on."""

    HELD = {"recipe": 1, "printings": {
        "P000001": {"front": {"key": "ab12cd34ef56", "lowres": False, "original_ext": "png"}}}}

    def _export(self, overrides=None, images=None):
        con = open_db(":memory:")
        init_db(con)
        snapshot = build_snapshot(copy.deepcopy(RAW_API))
        if overrides:
            apply_overrides(snapshot, overrides)
        apply_plan(con, diff(load_registry_state(con), snapshot), "2026-08-19")
        return build_export(con, images=images if images is not None else {"printings": {}})

    def test_cards_printings_and_sets_carry_their_addresses(self):
        export = self._export()
        card = export["cards"][0]
        self.assertEqual(card["api_url"], "https://api.kairosarchive.net/v3/cards/C000001.json")
        self.assertEqual(card["kairos_url"], "https://kairosarchive.net/cards/C000001")
        printing = export["printings"][1]
        self.assertEqual(printing["api_url"],
                         "https://api.kairosarchive.net/v3/printings/P000002.json")
        self.assertEqual(printing["kairos_url"], "https://kairosarchive.net/printings/P000002")
        alpha = export["sets"][0]
        self.assertEqual(alpha["api_url"], "https://api.kairosarchive.net/v3/sets/001.json")
        self.assertEqual(alpha["kairos_url"], "https://kairosarchive.net/sets/001")
        # The api_url points at the moving major alias, never a release root.
        self.assertIn("/v3/", card["api_url"])
        self.assertNotRegex(card["api_url"], r"/v3\.\d")

    def test_without_an_image_the_fields_exist_and_say_so(self):
        export = self._export()
        for record in export["cards"] + export["printings"]:
            self.assertIsNone(record["image_urls"])
            self.assertEqual(record["image_status"], "missing")

    def test_image_urls_are_self_describing_and_derived_from_the_key(self):
        from registry.export import image_urls
        urls = image_urls("P000937", "ab12cd34ef56")
        self.assertEqual(urls, {
            "small": "https://api.kairosarchive.net/images/P000937.ab12cd34ef56.small.webp",
            "normal": "https://api.kairosarchive.net/images/P000937.ab12cd34ef56.normal.webp",
            "large": "https://api.kairosarchive.net/images/P000937.ab12cd34ef56.large.webp",
            "original": "https://api.kairosarchive.net/images/P000937.ab12cd34ef56.original.png"})
        back = image_urls("P000937", "9f8e7d6c5b4a", back=True)
        self.assertEqual(back["normal"],
                         "https://api.kairosarchive.net/images/P000937.9f8e7d6c5b4a.back.normal.webp")
        self.assertIsNone(image_urls("P000937", None))

    def test_a_card_carries_its_default_printings_image(self):
        # What the registry holds comes from data/images.json, never from
        # the database: the export is handed that document.
        export = self._export(images=self.HELD)
        printing = export["printings"][0]
        self.assertEqual(printing["image_status"], "ok")
        self.assertEqual(printing["image_hash"], "ab12cd34ef56")
        self.assertEqual(printing["image_urls"]["original"],
                         "https://api.kairosarchive.net/images/P000001.ab12cd34ef56.original.png")
        self.assertEqual(printing["image_urls"]["small"],
                         "https://api.kairosarchive.net/images/P000001.ab12cd34ef56.small.webp")
        card = export["cards"][0]
        self.assertEqual(card["default_printing_id"], "P000001")
        self.assertEqual(card["image_urls"], printing["image_urls"])
        self.assertEqual(card["image_status"], "ok")
        # The other card has no image, and says so on both levels.
        self.assertEqual(export["cards"][1]["image_status"], "missing")

    def test_back_face_of_a_printing_has_the_field(self):
        raw = copy.deepcopy(RAW_API)
        raw[0]["engine"]["back"] = engine(type="Avatar", category="Avatar", rarity=None)
        raw[0]["printings"][0]["meta"]["back"] = {
            "finish": "Standard", "product": "Booster", "flavor": None,
            "typeline": "Back", "artist": {"name": "B", "slug": "b"}}
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con), build_snapshot(raw)), "2026-08-19")
        printing = build_export(con)["printings"][0]
        self.assertIn("image_urls", printing["back"])
        self.assertIsNone(printing["back"]["image_urls"])
        self.assertIsNone(build_export(con)["printings"][1]["back"])

    def test_export_with_an_image_conforms_to_the_schema(self):
        import json
        from pathlib import Path
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema_file = Path(__file__).resolve().parent.parent / "schema" / "registry.schema.json"
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        held = copy.deepcopy(self.HELD)
        held["printings"]["P000002"] = {"front": {"key": "0123456789ab", "lowres": True,
                                                  "original_ext": "jpg"}}
        export = json.loads(render(self._export(images=held)))
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(export))
        self.assertEqual(errors, [], [e.message for e in errors[:3]])
        self.assertEqual(export["printings"][1]["image_status"], "lowres")
        self.assertTrue(export["printings"][1]["image_urls"]["original"].endswith(".original.jpg"))

    def test_validator_ties_the_held_images_to_the_export(self):
        from registry.validate import check_images
        export = self._export(images=self.HELD)
        errors = []
        check_images(export, self.HELD, errors)
        self.assertEqual(errors, [])
        wrong = {"printings": {"P000001": {"front": {"key": "ffffffffffff"},
                                           "back": {"key": "0123456789ab"}},
                               "P000099": {"front": {"key": "0123456789ab"}}}}
        errors = []
        check_images(export, wrong, errors)
        self.assertEqual(len(errors), 3, errors)
        self.assertIn("does not carry the held key", errors[0])
        self.assertIn("has no back face", errors[1])
        self.assertIn("unknown printing P000099", errors[2])

    def test_validator_catches_addresses_that_name_another_record(self):
        from registry.validate import check_addresses
        export = self._export()
        errors = []
        check_addresses(export, errors)
        self.assertEqual(errors, [])
        tampered = copy.deepcopy(export)
        tampered["cards"][0]["api_url"] = tampered["cards"][1]["api_url"]
        tampered["printings"][0]["image_status"] = "ok"
        tampered["sets"][0]["kairos_url"] = "https://kairosarchive.net/sets/002"
        errors = []
        check_addresses(tampered, errors)
        self.assertEqual(len(errors), 3, errors)
        self.assertIn("card C000001: api_url", errors[0])
        self.assertIn("image_status 'ok' disagrees", errors[1])
        self.assertIn("set 001: kairos_url", errors[2])


class DerivedPowerTest(unittest.TestCase):
    def test_the_rule(self):
        from registry.export import power
        self.assertEqual(power(3, 3), 3)        # equal: the shared value
        self.assertEqual(power(4, 2), 3)        # mean
        self.assertEqual(power(3, 2), 2)        # floor of the mean
        self.assertEqual(power(0, 5), 2)
        self.assertIsNone(power(None, 2))
        self.assertIsNone(power(2, None))

    def test_every_face_carries_power_after_defense(self):
        raw = copy.deepcopy(RAW_API)
        raw[0]["engine"]["attack"], raw[0]["engine"]["defense"] = 5, 2
        raw[0]["engine"]["back"] = engine(type="Avatar", category="Avatar", rarity=None,
                                          attack=3, defense=3)
        raw[0]["printings"][0]["meta"]["back"] = {
            "finish": "Standard", "product": "Booster", "flavor": None,
            "typeline": "Back", "artist": {"name": "B", "slug": "b"}}
        con = open_db(":memory:")
        init_db(con)
        apply_plan(con, diff(load_registry_state(con), build_snapshot(raw)), "2026-08-19")
        export = build_export(con, images={"printings": {}})
        wizard = export["cards"][0]
        keys = list(wizard)
        self.assertEqual(keys[keys.index("defense") + 1], "power")
        self.assertEqual(wizard["power"], 3)
        self.assertEqual(wizard["back"]["power"], 3)
        self.assertEqual(list(wizard["back"])[list(wizard["back"]).index("defense") + 1], "power")
        site = export["cards"][1]
        self.assertIsNone(site["power"])   # a site has no attack or defense
        row = next(r for r in export["card_history"] if r["codex_id"] == "C000001")
        self.assertEqual(row["power"], 3)
        self.assertEqual(row["back"]["power"], 3)


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

    def test_card_history_must_agree_with_the_card(self):
        from registry.validate import check_internal
        con = self.populated()
        con.execute("UPDATE cards SET cost = 9 WHERE card_id = 1")
        errors = []
        check_internal(con, errors)
        self.assertTrue(any("open card_history row" in e for e in errors), errors)


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
                    "card_history"):
            self.assertEqual(manifest["counts"][key], header[key])
        self.assertEqual([a["name"] for a in manifest["artifacts"]],
                         ["registry.json", "registry.sqlite", "registry.schema.json"])
        self.assertEqual(manifest["artifacts"][0]["sha256"], stated)


if __name__ == "__main__":
    unittest.main()
