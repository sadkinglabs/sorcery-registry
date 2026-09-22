"""changes.json: what one release changed against the previous one. The
document is a pure function of two exports, so these tests build small
exports by hand and pin the counts, the lists and the identifier rule."""

import copy
import unittest

from registry.changes import check, diff_exports, summary_line


def export(cards, printings, sets=("001",), history=(), schema_version=11, gaps=None):
    doc = {
        "header": {"schema_version": schema_version},
        "sets": [{"set_code": code, "set_name": f"Set {code}"} for code in sets],
        "cards": cards,
        "printings": printings,
        "card_history": list(history),
    }
    if gaps is not None:
        doc["gaps"] = gaps
    return doc


def note(text, source="Community report, Sorcery Discord", recorded="2026-09-22"):
    return {"text": text, "source": source, "recorded": recorded}


def gap(name, codex_id=None, text="Known to exist, not recorded."):
    return {"name": name, "codex_id": codex_id, "text": text,
            "source": "Community report, Sorcery Discord", "recorded": "2026-09-22"}


def card(codex_id, **fields):
    base = {"codex_id": codex_id, "name": f"Card {codex_id}", "rules_text": "Text.", "errata": False}
    base.update(fields)
    return base


def printing(printing_id, codex_id, image_hash=None, back=None, **fields):
    base = {"printing_id": printing_id, "codex_id": codex_id, "slug": f"001-{printing_id.lower()}",
            "image_hash": image_hash, "back": back}
    base.update(fields)
    return base


BEFORE = export(
    [card("C000001"), card("C000002")],
    [printing("P000001", "C000001", image_hash="aaaaaaaaaaaa"),
     printing("P000002", "C000002")],
    history=[{"codex_id": "C000001", "valid_from": "2026-01-01", "valid_to": None, "source": "api"}],
)


class DiffTest(unittest.TestCase):
    def test_nothing_changed_is_all_zeros(self):
        changes = diff_exports(BEFORE, copy.deepcopy(BEFORE), "v3.0.0", "v3.0.1")
        self.assertEqual(changes["from"], "v3.0.0")
        self.assertEqual(changes["to"], "v3.0.1")
        self.assertEqual(changes["schema_version"], {"from": 11, "to": 11})
        self.assertEqual(set(changes["summary"].values()), {0})
        self.assertEqual(summary_line(changes), "0 identifiers removed")
        self.assertEqual(check(changes), [])

    def test_counts_and_lists_name_what_changed(self):
        after = copy.deepcopy(BEFORE)
        after["cards"][0]["rules_text"] = "New text."
        after["cards"][0]["errata"] = True
        after["cards"].append(card("C000003"))
        after["printings"][1]["image_hash"] = "bbbbbbbbbbbb"            # image added
        after["printings"][0]["image_hash"] = "cccccccccccc"            # image replaced
        after["printings"][0]["slug"] = "001-renamed"
        after["printings"].append(printing("P000003", "C000003", image_hash="dddddddddddd"))
        after["sets"].append({"set_code": "002", "set_name": "Set 002"})
        after["card_history"][0]["valid_to"] = "2026-06-01"
        after["card_history"].append({"codex_id": "C000001", "valid_from": "2026-06-01",
                                      "valid_to": None, "source": "card"})
        changes = diff_exports(BEFORE, after, "v3.0.0", "v3.1.0")
        self.assertEqual(changes["summary"], {
            "cards_added": 1, "cards_changed": 1, "cards_removed": 0,
            "printings_added": 1, "printings_changed": 2, "printings_removed": 0,
            "sets_added": 1, "images_added": 2, "images_replaced": 1,
            "history_rows_added": 1, "notes_added": 0, "notes_removed": 0,
            "gaps_added": 0, "gaps_closed": 0, "identifiers_removed": 0})
        self.assertEqual(changes["cards"]["changed"],
                         [{"codex_id": "C000001", "name": "Card C000001", "fields": ["rules_text", "errata"]}])
        self.assertEqual(changes["cards"]["added"], ["C000003"])
        self.assertEqual([p["printing_id"] for p in changes["printings"]["changed"]], ["P000001", "P000002"])
        self.assertEqual(changes["printings"]["changed"][0]["fields"], ["slug", "image_hash"])
        self.assertEqual(changes["images"], {"added": ["P000002", "P000003"], "replaced": ["P000001"]})
        self.assertEqual(changes["sets"]["added"], ["002"])
        self.assertEqual(changes["history"]["added"],
                         [{"codex_id": "C000001", "valid_from": "2026-06-01", "source": "card"}])
        self.assertEqual(summary_line(changes),
                         "1 card added · 1 card changed · 1 printing added · 2 printings changed · "
                         "1 set added · 2 images added · 1 image replaced · 1 history row added · "
                         "0 identifiers removed")

    def test_a_back_face_image_counts_too(self):
        after = copy.deepcopy(BEFORE)
        after["printings"][0]["back"] = {"image_urls": {"original": "https://x/P000001.k1.back.original.png"}}
        changes = diff_exports(BEFORE, after, "v3.0.0", "v3.0.1")
        self.assertEqual(changes["images"], {"added": ["P000001"], "replaced": []})
        later = copy.deepcopy(after)
        later["printings"][0]["back"]["image_urls"]["original"] = "https://x/P000001.k2.back.original.png"
        self.assertEqual(diff_exports(after, later, "v3.0.1", "v3.0.2")["images"],
                         {"added": [], "replaced": ["P000001"]})

    def test_removed_identifiers_are_counted_and_refused_within_a_major(self):
        after = copy.deepcopy(BEFORE)
        del after["cards"][1]
        del after["printings"][1]
        changes = diff_exports(BEFORE, after, "v3.0.0", "v3.0.1")
        self.assertEqual(changes["summary"]["identifiers_removed"], 2)
        self.assertEqual(changes["cards"]["removed"], ["C000002"])
        self.assertEqual(changes["printings"]["removed"], ["P000002"])
        self.assertEqual(len(check(changes)), 1)
        self.assertIn("C000002, P000002", check(changes)[0])
        # A new major may break: the check passes, the counts still tell.
        self.assertEqual(check(diff_exports(BEFORE, after, "v3.0.0", "v4.0.0")), [])

    def test_first_release_comes_from_nothing(self):
        changes = diff_exports(None, BEFORE, None, "v1.0.0")
        self.assertIsNone(changes["from"])
        self.assertEqual(changes["schema_version"], {"from": None, "to": 11})
        self.assertEqual(changes["summary"]["cards_added"], 2)
        self.assertEqual(changes["summary"]["printings_added"], 2)
        self.assertEqual(changes["summary"]["images_added"], 1)
        self.assertEqual(changes["summary"]["history_rows_added"], 1)
        self.assertEqual(changes["summary"]["identifiers_removed"], 0)
        self.assertEqual(check(changes), [])

    def test_a_field_a_release_adds_changes_only_the_records_that_carry_something(self):
        # schema 12 adds notes to every record: [] on most, one note on one.
        after = copy.deepcopy(BEFORE)
        for record in after["cards"] + after["printings"]:
            record["notes"] = []
            record["new_field"] = None
        after["cards"][1]["other_field"] = "set"
        changes = diff_exports(BEFORE, after, "v3.3.3", "v3.4.0")
        self.assertEqual(changes["cards"]["changed"],
                         [{"codex_id": "C000002", "name": "Card C000002", "fields": ["other_field"]}])
        self.assertEqual(changes["printings"]["changed"], [])
        # And the other way: a field dropped while empty is no change either.
        self.assertEqual(diff_exports(after, BEFORE, "v3.4.0", "v4.0.0")["cards"]["changed"],
                         [{"codex_id": "C000002", "name": "Card C000002", "fields": ["other_field"]}])

    def test_notes_have_their_own_section_and_never_change_a_record(self):
        before = copy.deepcopy(BEFORE)
        for record in before["cards"] + before["printings"]:
            record["notes"] = []
        before["cards"][0]["notes"] = [note("Old wording.")]
        after = copy.deepcopy(before)
        after["printings"][1]["notes"] = [note("Store kit prize support.")]
        after["cards"][0]["notes"] = [note("New wording.")]
        changes = diff_exports(before, after, "v3.4.0", "v3.4.1")
        self.assertEqual(changes["cards"]["changed"], [])
        self.assertEqual(changes["printings"]["changed"], [])
        self.assertEqual(changes["notes"], {
            "added": [{"id": "C000001", **note("New wording.")},
                      {"id": "P000002", **note("Store kit prize support.")}],
            "removed": [{"id": "C000001", **note("Old wording.")}]})
        self.assertEqual(changes["summary"]["notes_added"], 2)
        self.assertEqual(changes["summary"]["notes_removed"], 1)
        self.assertEqual(summary_line(changes), "2 notes added · 1 note removed · 0 identifiers removed")

    def test_a_notes_only_release_says_so(self):
        # Against an export from before notes existed, as v3.3.3 -> v3.4.0.
        after = copy.deepcopy(BEFORE)
        for record in after["cards"] + after["printings"]:
            record["notes"] = []
        after["printings"][0]["notes"] = [note("Prize support.")]
        changes = diff_exports(BEFORE, after, "v3.3.3", "v3.4.0")
        self.assertEqual(summary_line(changes), "1 note added · 0 identifiers removed")
        self.assertEqual(changes["printings"]["changed"], [])

    def test_gaps_recorded_and_closed_by_what_they_name(self):
        before = export(BEFORE["cards"], BEFORE["printings"],
                        gaps=[gap("The Champion"), gap("Card One", "C000001")])
        after = export(BEFORE["cards"], BEFORE["printings"],
                       gaps=[gap("The Champion", text="Reworded, same gap."),
                             gap("Card Two", "C000002")])
        changes = diff_exports(before, after, "v3.4.0", "v3.4.1")
        self.assertEqual(changes["gaps"], {
            "added": [{"name": "Card Two", "codex_id": "C000002"}],
            "closed": [{"name": "Card One", "codex_id": "C000001"}]})
        self.assertEqual(summary_line(changes),
                         "1 gap recorded · 1 gap closed · 0 identifiers removed")
        # An export from before the register existed has no gaps.
        first = diff_exports(BEFORE, after, "v3.3.3", "v3.4.0")
        self.assertEqual(first["summary"]["gaps_added"], 2)
        self.assertEqual(first["summary"]["gaps_closed"], 0)

    def test_summary_line_reads_documents_from_before_notes(self):
        changes = diff_exports(BEFORE, copy.deepcopy(BEFORE), "v3.3.2", "v3.3.3")
        for key in ("notes_added", "notes_removed", "gaps_added", "gaps_closed"):
            del changes["summary"][key]
        self.assertEqual(summary_line(changes), "0 identifiers removed")

    def test_is_deterministic(self):
        a = diff_exports(BEFORE, copy.deepcopy(BEFORE), "v3.0.0", "v3.0.1")
        b = diff_exports(copy.deepcopy(BEFORE), copy.deepcopy(BEFORE), "v3.0.0", "v3.0.1")
        self.assertEqual(list(a), list(b))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
