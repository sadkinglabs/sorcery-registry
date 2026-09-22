"""TypeScript declarations for the export, generated from its JSON Schema.

    python -m registry.types [--schema schema/registry.schema.json]
                             [--out schema/registry.d.ts] [--check]

A developer reading registry.json in TypeScript wants typed records
without an npm package to install or a schema-to-types toolchain to
run. So every release root carries types.d.ts next to schema.json: one
`import type { Registry, Card } from "./types"` and the records are
typed, field descriptions included as JSDoc. The file is a pure function
of schema.json, so a field added to the schema appears in the types on
the next release with nothing typed twice, and the committed copy at
schema/registry.d.ts is checked in CI to match (`--check`).

What the schema uses, and how it reads in TypeScript:

    type: "string" | ["string", "null"]   string | null
    $ref: "#/$defs/codexId"               CodexId (a named alias or interface)
    anyOf: [{$ref}, {type: "null"}]       Face | null
    enum: ["missing", "lowres", "ok"]     "missing" | "lowres" | "ok"
    type: "array", items: {...}           Item[]
    type: "object", properties: {...}     an interface (named) or an inline object
    required: [...]                       a property outside it is optional (`?`)
    additionalProperties: false           nothing extra; no index signature

An object type whose properties include every property of a $defs object
(a card's front is the face fields inline) is declared as extending it,
so a function that takes a Face accepts a Card.
"""

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

from .export import SCHEMA_PATH

TYPES_PATH = Path("schema/registry.d.ts")

# Names for the top-level sections' records; $defs are named from their key.
SECTION_NAMES = {
    "header": "Header",
    "sets": "RegistrySet",           # not `Set`, which would shadow the global
    "cards": "Card",
    "printings": "Printing",
    "slug_history": "SlugHistoryRow",
    "name_history": "NameHistoryRow",
    "card_history": "CardHistoryRow",
    "gaps": "Gap",
}
ROOT_NAME = "Registry"
DEF_NAMES = {"date": "IsoDate"}       # `Date` would shadow the global


def def_name(key):
    return DEF_NAMES.get(key) or key[0].upper() + key[1:]


def _ref_name(ref):
    match = re.match(r"^#/\$defs/([A-Za-z0-9_]+)$", ref)
    if not match:
        raise ValueError(f"unsupported $ref {ref!r}: only #/$defs/<name> is supported")
    return match.group(1)


PRIMITIVES = {"string": "string", "integer": "number", "number": "number",
              "boolean": "boolean", "null": "null"}


class Generator:
    def __init__(self, schema):
        self.schema = schema
        self.defs = schema.get("$defs", {})
        # Object defs, by the set of their property names, for `extends`.
        self.object_defs = {key: node for key, node in self.defs.items()
                            if node.get("type") == "object" and "properties" in node}

    # -- expressions -------------------------------------------------------

    def type_of(self, node, indent=0):
        """The TypeScript expression for a schema node."""
        if "$ref" in node:
            return def_name(_ref_name(node["$ref"]))
        if "enum" in node:
            return " | ".join(json.dumps(v) for v in node["enum"])
        if "const" in node:
            return json.dumps(node["const"])
        if "anyOf" in node or "oneOf" in node:
            parts = [self.type_of(alt, indent) for alt in node.get("anyOf") or node["oneOf"]]
            return " | ".join(dict.fromkeys(parts))
        kind = node.get("type")
        if kind is None:
            return "unknown"
        kinds = kind if isinstance(kind, list) else [kind]
        parts = []
        for k in kinds:
            if k == "array":
                item = self.type_of(node.get("items", {}), indent)
                parts.append(f"({item})[]" if " | " in item else f"{item}[]")
            elif k == "object":
                parts.append(self.inline_object(node, indent))
            elif k in PRIMITIVES:
                parts.append(PRIMITIVES[k])
            else:
                raise ValueError(f"unsupported type {k!r}")
        return " | ".join(parts)

    def inline_object(self, node, indent):
        if "properties" not in node:
            return "Record<string, unknown>"
        pad = "  " * (indent + 1)
        lines = ["{"]
        for line in self.members(node, indent + 1):
            lines.append(pad + line if line else line)
        lines.append("  " * indent + "}")
        return "\n".join(lines)

    def members(self, node, indent, skip=()):
        """Property lines (with JSDoc) for an object node."""
        required = set(node.get("required", []))
        out = []
        for name, prop in node["properties"].items():
            if name in skip:
                continue
            out.extend(self.doc(prop.get("description"), indent))
            key = name if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", name) else json.dumps(name)
            opt = "" if name in required else "?"
            out.append(f"{key}{opt}: {self.type_of(prop, indent)};")
        return out

    def doc(self, text, indent):
        if not text:
            return []
        width = max(40, 96 - 2 * indent)
        lines = textwrap.wrap(" ".join(text.split()), width=width)
        if len(lines) == 1:
            return [f"/** {lines[0]} */"]
        return ["/**"] + [f" * {line}" for line in lines] + [" */"]

    # -- declarations ------------------------------------------------------

    def extends_of(self, node):
        """Object $defs whose every property this node repeats verbatim."""
        props = node.get("properties", {})
        bases = []
        for key, base in self.object_defs.items():
            base_props = base["properties"]
            if all(name in props and props[name] == prop for name, prop in base_props.items()):
                bases.append(key)
        return bases

    def interface(self, name, node, description=None):
        bases = self.extends_of(node) if name not in map(def_name, self.object_defs) else []
        skip = set()
        for base in bases:
            skip.update(self.object_defs[base]["properties"])
        heading = f"export interface {name}"
        if bases:
            heading += " extends " + ", ".join(def_name(b) for b in bases)
        lines = self.doc(description or node.get("description"), 0)
        lines.append(heading + " {")
        lines.extend(("  " + l if l else l) for l in self.members(node, 1, skip))
        lines.append("}")
        return "\n".join(lines)

    def alias(self, name, node):
        lines = self.doc(node.get("description"), 0)
        lines.append(f"export type {name} = {self.type_of(node)};")
        return "\n".join(lines)

    def render(self):
        s = self.schema
        blocks = []
        # $defs first, aliases then objects, in the schema's own order.
        for key, node in self.defs.items():
            name = def_name(key)
            if node.get("type") == "object" and "properties" in node:
                blocks.append(self.interface(name, node))
            else:
                blocks.append(self.alias(name, node))
        # Sections.
        root_members = []
        for section, prop in s["properties"].items():
            name = SECTION_NAMES.get(section) or section[0].upper() + section[1:]
            if prop.get("type") == "array":
                blocks.append(self.interface(name, prop["items"], prop.get("description")))
                root_members.append((section, f"{name}[]", prop.get("description")))
            elif prop.get("type") == "object":
                blocks.append(self.interface(name, prop, prop.get("description")))
                root_members.append((section, name, prop.get("description")))
            else:
                root_members.append((section, self.type_of(prop), prop.get("description")))
        required = set(s.get("required", []))
        lines = self.doc(s.get("description"), 0)
        lines.append(f"export interface {ROOT_NAME} {{")
        for section, expr, description in root_members:
            lines.extend("  " + l for l in self.doc(description, 1))
            lines.append(f"  {section}{'' if section in required else '?'}: {expr};")
        lines.append("}")
        blocks.append("\n".join(lines))
        header = textwrap.dedent(f"""\
            // {s.get('title', 'Registry')}: TypeScript declarations for registry.json.
            // Generated from schema.json by `python -m registry.types`; do not edit.
            // Usage:  import type {{ {ROOT_NAME}, Card, Printing }} from "./types";
            //         const registry: {ROOT_NAME} = JSON.parse(text);
            // Every field carries the schema's description as JSDoc. A field's
            // absence from `required` reads as optional; nothing here is optional
            // today. Objects declare no index signature: the schema allows no
            // fields beyond the ones listed.
            """)
        return header + "\n" + "\n\n".join(blocks) + "\n"


def render_types(schema):
    """The declaration file for a parsed schema. Pure and deterministic."""
    return Generator(schema).render()


def render_sample(export, cards=40):
    """A slice of the export as a TypeScript module, `export default {...}
    as const`, so tsc sees every value as a literal ("ok", not string)
    and can check the real data against the declarations structurally.
    The first `cards` cards, plus a double-faced card, an errata card and
    a card with notes on it or on a printing, so the nullable and nested
    shapes are exercised, with their printings and history rows, every
    set and the whole gap register."""
    chosen = list(export["cards"][:cards])
    ids = {c["codex_id"] for c in chosen}
    noted = {p["codex_id"] for p in export["printings"] if p.get("notes")}
    for pick in (lambda c: c.get("back"), lambda c: c.get("errata"),
                 lambda c: c.get("notes") or c["codex_id"] in noted):
        extra = next((c for c in export["cards"] if pick(c) and c["codex_id"] not in ids), None)
        if extra:
            chosen.append(extra)
            ids.add(extra["codex_id"])
    printings = [p for p in export["printings"] if p["codex_id"] in ids]
    printing_ids = {p["printing_id"] for p in printings}
    sample = {
        "header": export["header"],
        "sets": export["sets"],
        "cards": chosen,
        "printings": printings,
        "slug_history": [r for r in export["slug_history"] if r["printing_id"] in printing_ids],
        "name_history": [r for r in export["name_history"] if r["codex_id"] in ids],
        "card_history": [r for r in export["card_history"] if r["codex_id"] in ids],
        "gaps": export["gaps"],
    }
    return ("// A slice of registry.json as a literal-typed module, for tests/ts.\n"
            "export default " + json.dumps(sample, ensure_ascii=False) + " as const;\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schema", default=str(SCHEMA_PATH))
    parser.add_argument("--out", default=str(TYPES_PATH))
    parser.add_argument("--check", action="store_true",
                        help="fail if --out does not already hold exactly what the schema generates")
    parser.add_argument("--sample", default=None,
                        help="instead: write a literal-typed slice of --export to this .ts path")
    parser.add_argument("--export", default="export/registry.json")
    args = parser.parse_args()
    if args.sample:
        export = json.loads(Path(args.export).read_text(encoding="utf-8"))
        Path(args.sample).write_text(render_sample(export), encoding="utf-8", newline="\n")
        print(f"wrote {args.sample}")
        return 0
    text = render_types(json.loads(Path(args.schema).read_text(encoding="utf-8")))
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else None
        if current != text:
            print(f"::error::{out} is out of date; run `python -m registry.types` and commit the result")
            return 1
        print(f"{out} matches {args.schema}")
        return 0
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
