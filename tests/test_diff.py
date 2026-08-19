"""Diff classification tests. The rename cases are the ones that matter:
a wrong guess forks one card into two ids, so every ambiguous shape here
must land in quarantine, never in an automatic decision."""

import unittest

from registry.diff import diff, is_noop

CARD_DEFAULTS = {
    "type": "Minion", "rarity": "Ordinary", "subtypes": "Mortal",
    "elements": "Air", "cost": 3, "attack": 1, "defence": 1, "life": None,
    "thr_air": 1, "thr_earth": 0, "thr_fire": 0, "thr_water": 0,
    "rules_text": "Spellcaster",
}

PRINTING_DEFAULTS = {
    "set_name": "Alpha", "released_at": "2023-04-19",
    "set_number": "001", "product": "Booster", "finish": "Standard",
    "artist": "A. Artist", "flavour_text": "", "type_text": "",
    "rarity": "Ordinary", "type": "Minion", "rules_text": "Spellcaster",
    "cost": 3, "attack": 1, "defence": 1, "life": None,
    "thr_air": 1, "thr_earth": 0, "thr_fire": 0, "thr_water": 0,
    "image_hash": None,
}


def card(name, **kw):
    return {"name": name, **CARD_DEFAULTS, **kw}


def printing(slug, card_name, **kw):
    return {"slug": slug, "card_name": card_name, **PRINTING_DEFAULTS, **kw}


def registry_of(cards, printings):
    reg_cards = {}
    for index, record in enumerate(cards, 1):
        reg_cards[record["name"]] = {**record, "card_id": index}
    reg_printings = {}
    for index, record in enumerate(printings, 1):
        reg_printings[record["slug"]] = {
            **record, "printing_id": index, "retired_at": record.get("retired_at")}
    return {"cards": reg_cards, "printings": reg_printings}


def api_of(cards, printings):
    return {"cards": {c["name"]: c for c in cards},
            "printings": {p["slug"]: p for p in printings}}


class NoopTest(unittest.TestCase):
    def test_identical_snapshots_produce_no_work(self):
        reg = registry_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        api = api_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        plan = diff(reg, api)
        self.assertTrue(is_noop(plan), plan)


class NewThingsTest(unittest.TestCase):
    def test_new_card_with_printings(self):
        reg = registry_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        api = api_of(
            [card("Witch"), card("Wizard", rules_text="Genesis: draw.")],
            [printing("004-witch-b-s", "Witch"),
             printing("005-wizard-b-s", "Wizard", set_number="005")])
        plan = diff(reg, api)
        self.assertEqual([c["name"] for c in plan["new_cards"]], ["Wizard"])
        self.assertEqual([p["slug"] for p in plan["new_printings"]], ["005-wizard-b-s"])
        self.assertFalse(plan["ambiguous"])

    def test_new_printing_of_existing_card_is_not_a_rename(self):
        reg = registry_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        api = api_of(
            [card("Witch")],
            [printing("004-witch-b-s", "Witch"),
             printing("102-witch-b-s", "Witch", set_name="Beta",
                      set_number="102")])
        plan = diff(reg, api)
        self.assertEqual([p["slug"] for p in plan["new_printings"]], ["102-witch-b-s"])
        self.assertFalse(plan["printing_renames"])
        self.assertFalse(plan["ambiguous"])


class AttributeUpdateTest(unittest.TestCase):
    def test_changed_fields_update_without_touching_ids(self):
        reg = registry_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        api = api_of([card("Witch", cost=4)],
                     [printing("004-witch-b-s", "Witch", cost=4, artist="B. Artist")])
        plan = diff(reg, api)
        self.assertEqual(plan["card_updates"][0]["changes"]["cost"], {"old": 3, "new": 4})
        changed = plan["printing_updates"][0]["changes"]
        self.assertEqual(set(changed), {"cost", "artist"})
        self.assertFalse(plan["new_cards"] or plan["new_printings"] or plan["ambiguous"])


class SlugRenameTest(unittest.TestCase):
    def test_clean_slug_rename_matches_and_keeps_id(self):
        reg = registry_of([card("Apprentice Wizard")],
                          [printing("004-apprentice-wizard-b-s", "Apprentice Wizard", set_number="004")])
        api = api_of([card("Apprentice Wizard")],
                     [printing("004-apprentice_wizard-b-s", "Apprentice Wizard", set_number="004")])
        plan = diff(reg, api)
        self.assertEqual(plan["printing_renames"], [{
            "printing_id": 1, "old_slug": "004-apprentice-wizard-b-s",
            "new_slug": "004-apprentice_wizard-b-s",
            "decided_by": "set+product+finish"}])
        self.assertFalse(plan["new_printings"] or plan["retire_printings"] or plan["ambiguous"])

    def test_whole_set_convention_flip_renames_every_slug(self):
        names = [f"Card {i}" for i in range(20)]
        reg = registry_of(
            [card(n, rules_text=f"unique text {n}") for n in names],
            [printing(f"{i:03d}-card-{i}-b-s", f"Card {i}", set_name=f"Set {i}",
                      set_number=f"{i:03d}") for i in range(20)])
        api = api_of(
            [card(n, rules_text=f"unique text {n}") for n in names],
            [printing(f"{i:03d}-card_{i}-b-s", f"Card {i}", set_name=f"Set {i}",
                      set_number=f"{i:03d}") for i in range(20)])
        plan = diff(reg, api)
        self.assertEqual(len(plan["printing_renames"]), 20)
        self.assertFalse(plan["new_printings"] or plan["retire_printings"] or plan["ambiguous"])

    def test_rename_with_attribute_change_still_pairs_on_key(self):
        reg = registry_of([card("Witch")], [printing("004-witch-b-s", "Witch", set_number="004")])
        api = api_of([card("Witch")],
                     [printing("004-witch_x-b-s", "Witch", set_number="004", artist="New Artist")])
        plan = diff(reg, api)
        self.assertEqual(len(plan["printing_renames"]), 1)
        self.assertEqual(plan["printing_updates"][0]["changes"]["artist"]["new"], "New Artist")

    def test_set_renumbering_pairs_and_updates_set_number(self):
        # The actual incident shape: EC renumbers the sets, so the slug's
        # leading digits change while the set itself is the same. The pair
        # key deliberately excludes set_number, so this is a clean rename
        # plus a set_number attribute update.
        reg = registry_of([card("Witch")], [printing("004-witch-b-s", "Witch", set_number="004")])
        api = api_of([card("Witch")], [printing("017-witch-b-s", "Witch", set_number="017")])
        plan = diff(reg, api)
        self.assertEqual(plan["printing_renames"][0]["decided_by"], "set+product+finish")
        self.assertEqual(plan["printing_updates"][0]["changes"]["set_number"],
                         {"old": "004", "new": "017"})
        self.assertFalse(plan["ambiguous"])

    def test_two_vanished_printings_sharing_a_key_quarantine(self):
        # Same card, same set/product/finish, two olds and two news land in
        # one pairing group. Guessing the pairing 50/50 is exactly the
        # forbidden move.
        reg = registry_of(
            [card("Witch")],
            [printing("004-witch-b-s", "Witch", set_number="004"),
             printing("005-witch-b-s", "Witch", set_number="005")])
        api = api_of(
            [card("Witch")],
            [printing("021-witch-b-s", "Witch", set_number="021"),
             printing("022-witch-b-s", "Witch", set_number="022")])
        plan = diff(reg, api)
        self.assertFalse(plan["printing_renames"])
        self.assertEqual(len(plan["ambiguous"]), 1)
        self.assertEqual(plan["ambiguous"][0]["kind"], "printing")
        self.assertFalse(plan["retire_printings"] or plan["new_printings"])

    def test_removal_plus_new_printing_of_same_card_quarantines(self):
        # A printing vanished while a different-looking one appeared for the
        # same card in another set. Retire + add, or a rename with a set
        # change? Not decidable from the data: quarantine.
        reg = registry_of([card("Witch")], [printing("004-witch-b-s", "Witch", set_number="004")])
        api = api_of([card("Witch")],
                     [printing("099-witch-p-s", "Witch", set_name="Promotional", product="Organized_Play",
                               set_number="099")])
        plan = diff(reg, api)
        self.assertFalse(plan["printing_renames"])
        self.assertEqual(len(plan["ambiguous"]), 1)

    def test_slug_taken_over_by_a_different_card_quarantines(self):
        reg = registry_of([card("Witch"), card("Wizard", rules_text="other")],
                          [printing("004-witch-b-s", "Witch")])
        api = api_of([card("Witch"), card("Wizard", rules_text="other")],
                     [printing("004-witch-b-s", "Wizard", rules_text="other")])
        plan = diff(reg, api)
        self.assertEqual(plan["ambiguous"][0]["problem"],
                         "slug kept but now belongs to a different card")


class RemovalTest(unittest.TestCase):
    def test_vanished_printing_with_no_candidate_retires(self):
        reg = registry_of(
            [card("Witch")],
            [printing("004-witch-b-s", "Witch"),
             printing("004-witch-b-f", "Witch", finish="Foil")])
        api = api_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        plan = diff(reg, api)
        self.assertEqual(plan["retire_printings"],
                         [{"printing_id": 2, "slug": "004-witch-b-f",
                           "decided_by": "no candidate"}])
        self.assertFalse(plan["ambiguous"])

    def test_retired_printing_reappearing_unretires(self):
        reg = registry_of([card("Witch")],
                          [printing("004-witch-b-s", "Witch", retired_at="2026-01-01")])
        api = api_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        plan = diff(reg, api)
        self.assertEqual(plan["unretire_printings"],
                         [{"printing_id": 1, "slug": "004-witch-b-s"}])

    def test_retired_printing_stays_retired_when_still_absent(self):
        reg = registry_of([card("Witch")],
                          [printing("004-witch-b-s", "Witch"),
                           printing("old-witch-b-s", "Witch", retired_at="2026-01-01",
                                    set_number=None)])
        api = api_of([card("Witch")], [printing("004-witch-b-s", "Witch")])
        plan = diff(reg, api)
        self.assertTrue(is_noop(plan), plan)


class CardRenameTest(unittest.TestCase):
    def test_card_rename_matches_on_fingerprint(self):
        reg = registry_of([card("Wich", rules_text="A very specific ability.")],
                          [printing("004-wich-b-s", "Wich",
                                    rules_text="A very specific ability.")])
        api = api_of([card("Witch", rules_text="A very specific ability.")],
                     [printing("004-witch-b-s", "Witch",
                               rules_text="A very specific ability.")])
        plan = diff(reg, api)
        self.assertEqual(plan["card_renames"], [{
            "card_id": 1, "old_name": "Wich", "new_name": "Witch",
            "decided_by": "fingerprint"}])
        # The printing follows its card through the rename.
        self.assertEqual(len(plan["printing_renames"]), 1)
        self.assertFalse(plan["new_cards"] or plan["ambiguous"])

    def test_two_cards_with_identical_fingerprints_quarantine(self):
        reg = registry_of(
            [card("Old One", rules_text="Same text."),
             card("Old Two", rules_text="Same text.")],
            [])
        api = api_of(
            [card("New One", rules_text="Same text."),
             card("New Two", rules_text="Same text.")],
            [])
        plan = diff(reg, api)
        self.assertFalse(plan["card_renames"])
        self.assertFalse(plan["new_cards"])
        self.assertEqual(plan["ambiguous"][0]["kind"], "card")

    def test_disappearance_plus_newcomer_quarantines(self):
        # A card vanished and an unrelated-looking card appeared. It may be
        # a rename whose attributes also changed, so nothing is decided:
        # auto-issuing "Fresh" an id would fork "Gone" if they are one card.
        reg = registry_of([card("Gone", rules_text="Old ability.")],
                          [printing("004-gone-b-s", "Gone", rules_text="Old ability.")])
        api = api_of([card("Fresh", rules_text="New ability.", cost=9)],
                     [printing("009-fresh-b-s", "Fresh", rules_text="New ability.",
                               cost=9, set_number="009")])
        plan = diff(reg, api)
        self.assertFalse(plan["card_renames"])
        self.assertFalse(plan["new_cards"])
        self.assertFalse(plan["retire_printings"])
        self.assertFalse(plan["new_printings"])
        self.assertEqual(len(plan["ambiguous"]), 1)
        case = plan["ambiguous"][0]
        self.assertEqual(case["kind"], "card")
        self.assertEqual([c["name"] for c in case["missing"]], ["Gone"])
        self.assertEqual([c["name"] for c in case["candidates"]], ["Fresh"])


class CardForkGuardTest(unittest.TestCase):
    """A rename plus an attribute change in the same sync defeats the
    fingerprint. The classifier must fail closed rather than issue the new
    name a fresh id, which would fork one card into two identities."""

    def assert_no_printing_work(self, plan):
        self.assertFalse(plan["retire_printings"])
        self.assertFalse(plan["new_printings"])
        self.assertFalse(plan["printing_renames"])
        self.assertFalse([a for a in plan["ambiguous"] if a["kind"] == "printing"])

    def test_rename_with_rules_text_change_quarantines(self):
        reg = registry_of([card("Old Name", rules_text="Old ability.")],
                          [printing("004-old_name-b-s", "Old Name",
                                    rules_text="Old ability.")])
        api = api_of([card("New Name", rules_text="New ability.")],
                     [printing("004-new_name-b-s", "New Name",
                               rules_text="New ability.")])
        plan = diff(reg, api)
        self.assertFalse(plan["card_renames"])
        self.assertFalse(plan["new_cards"])
        self.assertEqual(len(plan["ambiguous"]), 1)
        case = plan["ambiguous"][0]
        self.assertEqual(case["kind"], "card")
        self.assertEqual([c["name"] for c in case["missing"]], ["Old Name"])
        self.assertEqual([c["name"] for c in case["candidates"]], ["New Name"])
        self.assert_no_printing_work(plan)

    def test_rename_with_stat_change_quarantines(self):
        reg = registry_of([card("Old Name")], [printing("004-old_name-b-s", "Old Name")])
        api = api_of([card("New Name", cost=4)],
                     [printing("004-new_name-b-s", "New Name", cost=4)])
        plan = diff(reg, api)
        self.assertFalse(plan["card_renames"] or plan["new_cards"])
        self.assertEqual(len(plan["ambiguous"]), 1)
        self.assertEqual(plan["ambiguous"][0]["kind"], "card")
        self.assertEqual(plan["ambiguous"][0]["candidates"][0]["cost"], 4)
        self.assert_no_printing_work(plan)

    def test_clean_rename_plus_genuinely_new_card_needs_no_review(self):
        # The gate only fires on disappearances the fingerprint could not
        # explain: a clean rename is explained, so the newcomer is new.
        reg = registry_of([card("Wich", rules_text="A very specific ability.")],
                          [printing("004-wich-b-s", "Wich",
                                    rules_text="A very specific ability.")])
        api = api_of(
            [card("Witch", rules_text="A very specific ability."),
             card("Brand New", rules_text="Something else entirely.")],
            [printing("004-witch-b-s", "Witch",
                      rules_text="A very specific ability."),
             printing("009-brand_new-b-s", "Brand New", set_number="009",
                      rules_text="Something else entirely.")])
        plan = diff(reg, api)
        self.assertEqual([r["new_name"] for r in plan["card_renames"]], ["Witch"])
        self.assertEqual([c["name"] for c in plan["new_cards"]], ["Brand New"])
        self.assertEqual([p["slug"] for p in plan["new_printings"]], ["009-brand_new-b-s"])
        self.assertEqual(len(plan["printing_renames"]), 1)
        self.assertFalse(plan["ambiguous"])

    def test_unexplained_disappearance_pulls_every_newcomer_into_review(self):
        # The classifier cannot know which newcomer is the rename, so both
        # are candidates and neither is auto-added.
        reg = registry_of([card("Gone", rules_text="Old ability.")],
                          [printing("004-gone-b-s", "Gone", rules_text="Old ability.")])
        api = api_of(
            [card("Renamed Gone", rules_text="Reworded ability."),
             card("Brand New", rules_text="Something else entirely.")],
            [printing("004-renamed_gone-b-s", "Renamed Gone",
                      rules_text="Reworded ability."),
             printing("009-brand_new-b-s", "Brand New", set_number="009",
                      rules_text="Something else entirely.")])
        plan = diff(reg, api)
        self.assertFalse(plan["new_cards"] or plan["card_renames"])
        self.assertEqual(len(plan["ambiguous"]), 1)
        case = plan["ambiguous"][0]
        self.assertEqual([c["name"] for c in case["missing"]], ["Gone"])
        self.assertEqual([c["name"] for c in case["candidates"]],
                         ["Brand New", "Renamed Gone"])
        self.assert_no_printing_work(plan)

    def test_several_disappeared_and_several_added_land_in_one_case(self):
        reg = registry_of(
            [card("Gone One", rules_text="One."), card("Gone Two", rules_text="Two.")],
            [])
        api = api_of(
            [card("New One", rules_text="Three."), card("New Two", rules_text="Four.")],
            [])
        plan = diff(reg, api)
        self.assertFalse(plan["new_cards"] or plan["card_renames"])
        self.assertEqual(len(plan["ambiguous"]), 1)
        case = plan["ambiguous"][0]
        self.assertEqual([c["name"] for c in case["missing"]], ["Gone One", "Gone Two"])
        self.assertEqual([c["name"] for c in case["candidates"]], ["New One", "New Two"])

    def test_decisions_resolve_the_quarantine(self):
        reg = registry_of([card("Old Name", rules_text="Old ability.")],
                          [printing("004-old_name-b-s", "Old Name",
                                    rules_text="Old ability.")])
        api = api_of(
            [card("New Name", rules_text="New ability."),
             card("Brand New", rules_text="Something else entirely.")],
            [printing("004-new_name-b-s", "New Name", rules_text="New ability."),
             printing("009-brand_new-b-s", "Brand New", set_number="009",
                      rules_text="Something else entirely.")])
        decisions = {"card_renames": [{"card_id": 1, "new_name": "New Name"}],
                     "new_cards": ["Brand New"]}
        plan = diff(reg, api, decisions)
        self.assertFalse(plan["ambiguous"])
        self.assertEqual(plan["card_renames"], [{
            "card_id": 1, "old_name": "Old Name", "new_name": "New Name",
            "decided_by": "human"}])
        self.assertEqual([c["name"] for c in plan["new_cards"]], ["Brand New"])
        # The renamed card's printing follows it instead of retiring.
        self.assertEqual([r["new_slug"] for r in plan["printing_renames"]],
                         ["004-new_name-b-s"])
        self.assertFalse(plan["retire_printings"])
        self.assertEqual([p["slug"] for p in plan["new_printings"]],
                         ["009-brand_new-b-s"])

    def test_forced_new_card_leaves_a_lone_disappearance_to_retire(self):
        reg = registry_of([card("Gone", rules_text="Old ability.")],
                          [printing("004-gone-b-s", "Gone", rules_text="Old ability.")])
        api = api_of([card("Brand New", rules_text="Something else entirely.")],
                     [printing("009-brand_new-b-s", "Brand New", set_number="009",
                               rules_text="Something else entirely.")])
        plan = diff(reg, api, {"new_cards": ["Brand New"]})
        self.assertFalse(plan["ambiguous"])
        self.assertEqual([c["name"] for c in plan["new_cards"]], ["Brand New"])
        self.assertEqual(len(plan["notes"]), 1)
        self.assertIn("Gone", plan["notes"][0])
        self.assertEqual([r["printing_id"] for r in plan["retire_printings"]], [1])


class DecisionsTest(unittest.TestCase):
    def ambiguous_fixture(self):
        reg = registry_of(
            [card("Witch")],
            [printing("004-witch-b-s", "Witch", set_number="004"),
             printing("005-witch-b-s", "Witch", set_number="005")])
        api = api_of(
            [card("Witch")],
            [printing("021-witch-b-s", "Witch", set_number="021"),
             printing("022-witch-b-s", "Witch", set_number="022")])
        return reg, api

    def test_decisions_resolve_a_quarantined_case(self):
        reg, api = self.ambiguous_fixture()
        decisions = {"printing_renames": [
            {"printing_id": 1, "new_slug": "021-witch-b-s"},
            {"printing_id": 2, "new_slug": "022-witch-b-s"}]}
        plan = diff(reg, api, decisions)
        self.assertFalse(plan["ambiguous"])
        self.assertEqual({(r["printing_id"], r["new_slug"]) for r in plan["printing_renames"]},
                         {(1, "021-witch-b-s"), (2, "022-witch-b-s")})
        self.assertEqual({r["decided_by"] for r in plan["printing_renames"]}, {"human"})

    def test_partial_decision_lets_the_rest_resolve_automatically(self):
        reg, api = self.ambiguous_fixture()
        decisions = {"printing_renames": [{"printing_id": 1, "new_slug": "021-witch-b-s"}]}
        plan = diff(reg, api, decisions)
        # With one pair decided, the leftover 1:1 pairs automatically.
        self.assertFalse(plan["ambiguous"])
        self.assertEqual(len(plan["printing_renames"]), 2)

    def test_retire_decision(self):
        reg, api = self.ambiguous_fixture()
        decisions = {"retire_printings": [1],
                     "printing_renames": [{"printing_id": 2, "new_slug": "021-witch-b-s"}],
                     "new_printings": ["022-witch-b-s"]}
        plan = diff(reg, api, decisions)
        self.assertFalse(plan["ambiguous"])
        self.assertEqual(plan["retire_printings"][0]["printing_id"], 1)
        self.assertEqual([p["slug"] for p in plan["new_printings"]], ["022-witch-b-s"])

    def test_stale_decision_raises_instead_of_guessing(self):
        reg, api = self.ambiguous_fixture()
        decisions = {"printing_renames": [{"printing_id": 99, "new_slug": "021-witch-b-s"}]}
        with self.assertRaises(ValueError):
            diff(reg, api, decisions)


if __name__ == "__main__":
    unittest.main()
