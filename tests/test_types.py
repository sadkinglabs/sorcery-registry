"""types.d.ts: TypeScript declarations generated from the export's JSON
Schema. A small hand-written schema pins each construct's rendering; the
committed schema/registry.d.ts must be what the real schema generates."""

import json
import unittest
from pathlib import Path

from registry.export import SCHEMA_PATH
from registry.types import TYPES_PATH, render_sample, render_types

SCHEMA = {
    "title": "Test export",
    "description": "A tiny export.",
    "$defs": {
        "codexId": {"type": "string", "pattern": "^C[0-9]{6}$", "description": "Permanent id."},
        "date": {"type": "string"},
        "status": {"type": "string", "enum": ["missing", "ok"]},
        "face": {"type": "object", "properties": {
            "cost": {"type": ["integer", "null"], "description": "Mana cost."},
            "keywords": {"type": "array", "items": {"type": "string"}},
        }, "required": ["cost", "keywords"], "additionalProperties": False},
    },
    "type": "object",
    "properties": {
        "header": {"type": "object", "properties": {"schema_version": {"type": "integer"}},
                   "required": ["schema_version"], "additionalProperties": False},
        "cards": {"type": "array", "description": "One record per card.", "items": {
            "type": "object",
            "properties": {
                "codex_id": {"$ref": "#/$defs/codexId", "description": "The card's id."},
                "cost": {"type": ["integer", "null"], "description": "Mana cost."},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "back": {"anyOf": [{"$ref": "#/$defs/face"}, {"type": "null"}], "description": "Back face."},
                "status": {"$ref": "#/$defs/status"},
                "released_at": {"anyOf": [{"$ref": "#/$defs/date"}, {"type": "null"}]},
                "tags": {"type": "array", "items": {"type": ["string", "null"]}},
                "note": {"type": "string"},
                "weird-name": {"type": "boolean"},
            },
            "required": ["codex_id", "cost", "keywords", "back", "status", "released_at", "tags", "weird-name"],
            "additionalProperties": False,
        }},
    },
    "required": ["header", "cards"],
    "additionalProperties": False,
}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.text = render_types(SCHEMA)

    def test_defs_become_aliases_and_interfaces_with_jsdoc(self):
        self.assertIn('/** Permanent id. */\nexport type CodexId = string;', self.text)
        self.assertIn('export type IsoDate = string;', self.text)
        self.assertIn('export type Status = "missing" | "ok";', self.text)
        self.assertIn('export interface Face {\n  /** Mana cost. */\n  cost: number | null;\n  keywords: string[];\n}', self.text)

    def test_sections_become_interfaces_and_the_root_lists_them(self):
        self.assertIn('export interface Header {\n  schema_version: number;\n}', self.text)
        self.assertIn('/** One record per card. */\nexport interface Card extends Face {', self.text)
        self.assertIn('/** A tiny export. */\nexport interface Registry {\n  header: Header;\n  /** One record per card. */\n  cards: Card[];\n}', self.text)

    def test_constructs(self):
        self.assertIn('  codex_id: CodexId;', self.text)
        self.assertIn('  back: Face | null;', self.text)
        self.assertIn('  status: Status;', self.text)
        self.assertIn('  released_at: IsoDate | null;', self.text)
        self.assertIn('  tags: (string | null)[];', self.text)
        self.assertIn('  note?: string;', self.text)                  # not required
        self.assertIn('  "weird-name": boolean;', self.text)          # quoted key
        # The face fields a card repeats are inherited, not restated.
        card = self.text[self.text.index("export interface Card"):self.text.index("export interface Registry")]
        self.assertNotIn("  cost:", card)
        self.assertNotIn("  keywords:", card)

    def test_deterministic_and_generated_header(self):
        self.assertEqual(self.text, render_types(json.loads(json.dumps(SCHEMA))))
        self.assertTrue(self.text.startswith("// Test export: TypeScript declarations for registry.json."))
        self.assertIn("do not edit", self.text)

    def test_unsupported_ref_is_refused(self):
        bad = json.loads(json.dumps(SCHEMA))
        bad["properties"]["cards"]["items"]["properties"]["codex_id"] = {"$ref": "other.json#/x"}
        with self.assertRaises(ValueError):
            render_types(bad)


class CommittedFileTest(unittest.TestCase):
    def test_committed_declarations_match_the_schema(self):
        expected = render_types(json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8")))
        self.assertEqual(Path(TYPES_PATH).read_text(encoding="utf-8"), expected,
                         "schema/registry.d.ts is out of date: run `python -m registry.types`")

    def test_real_schema_declares_the_documented_names(self):
        text = Path(TYPES_PATH).read_text(encoding="utf-8")
        for name in ("Registry", "Card extends Face", "Printing", "RegistrySet", "CardHistoryRow extends Face",
                     "NameHistoryRow", "SlugHistoryRow", "Header", "Face", "PrintingFace", "ImageUrls"):
            self.assertIn(f"export interface {name}", text)
        for name in ("CodexId", "PrintingId", "IsoDate", "SetCode", "Threshold", "ImageStatus"):
            self.assertIn(f"export type {name} =", text)


class SampleTest(unittest.TestCase):
    def test_sample_is_a_literal_module_with_the_edge_cases(self):
        export = {
            "header": {"schema_version": 1}, "sets": [{"set_code": "001"}],
            "cards": [{"codex_id": "C000001", "back": None, "errata": False},
                      {"codex_id": "C000002", "back": None, "errata": False},
                      {"codex_id": "C000003", "back": {"type": "Site"}, "errata": False},
                      {"codex_id": "C000004", "back": None, "errata": True},
                      {"codex_id": "C000005", "back": None, "errata": False}],
            "printings": [{"printing_id": "P000001", "codex_id": "C000001"},
                          {"printing_id": "P000004", "codex_id": "C000004"},
                          {"printing_id": "P000005", "codex_id": "C000005",
                           "notes": [{"text": "t", "source": "s", "recorded": "2026-09-22"}]}],
            "slug_history": [{"printing_id": "P000004", "slug": "x"}],
            "name_history": [{"codex_id": "C000004", "name": "n"}],
            "card_history": [{"codex_id": "C000003"}],
        }
        text = render_sample(export, cards=1)
        self.assertTrue(text.startswith("// A slice"))
        self.assertTrue(text.rstrip().endswith("as const;"))
        sample = json.loads(text.split("export default ", 1)[1].rsplit(" as const;", 1)[0])
        self.assertEqual([c["codex_id"] for c in sample["cards"]], ["C000001", "C000003", "C000004", "C000005"])
        self.assertEqual([p["printing_id"] for p in sample["printings"]],
                         ["P000001", "P000004", "P000005"])
        self.assertEqual(sample["card_history"], [{"codex_id": "C000003"}])


if __name__ == "__main__":
    unittest.main()
