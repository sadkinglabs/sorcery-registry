"""Check an upstream payload against the publisher's published contract.

The official API page publishes a TypeScript type (CardAPIDTO) for
GET /api/cards; schema/upstream-cards.schema.json is the registry's
reading of it, and this module applies that schema to every payload
before the adapter touches it. When upstream's shape drifts under us, the
sync stops with the path of the first field that no longer fits instead
of writing nonsense into the registry.

The pipeline is stdlib-only, so this is a deliberately small validator
for the subset of JSON Schema the contract file uses: type (including
type lists), properties, required, items, anyOf, $ref into $defs, and
additionalProperties (which the contract always sets to true - unknown
new fields are tolerated). Anything else in the file is a mistake, and
is reported as one.
"""

import json
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "schema" / "upstream-cards.schema.json"

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}
_KNOWN_KEYS = {"$schema", "$id", "$defs", "title", "description", "type", "properties",
               "required", "items", "anyOf", "$ref", "additionalProperties"}


class ContractError(ValueError):
    """The payload does not fit the published contract. `path` names the
    offending element the way a consumer would read it (cards[3].engine.rules)."""

    def __init__(self, path, message):
        self.path = path
        super().__init__(f"upstream contract violated at {path}: {message}")


def load_contract(path=CONTRACT_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_contract(payload, contract=None):
    """Raise ContractError at the first violation; return None when the
    payload fits."""
    if contract is None:
        contract = load_contract()
    _check(payload, contract, contract, "cards")


def _resolve(node, root):
    if "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"unsupported $ref in contract: {ref}")
        return root["$defs"][ref[len("#/$defs/"):]]
    return node


def _check(value, node, root, path):
    node = _resolve(node, root)
    unknown = set(node) - _KNOWN_KEYS
    if unknown:
        raise ValueError(f"contract uses unsupported keywords at {path}: {sorted(unknown)}")

    if "anyOf" in node:
        errors = []
        for option in node["anyOf"]:
            try:
                _check(value, option, root, path)
                break
            except ContractError as error:
                errors.append(error)
        else:
            raise ContractError(path, "; ".join(str(e) for e in errors) or "matches no option")
        return

    if "type" in node:
        allowed = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not any(_TYPES[t](value) for t in allowed):
            raise ContractError(path, f"expected {' or '.join(allowed)}, "
                                      f"found {type(value).__name__}")

    if isinstance(value, dict):
        for key in node.get("required", []):
            if key not in value:
                raise ContractError(f"{path}.{key}", "required field is missing")
        for key, sub in node.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, root, f"{path}.{key}")
        if node.get("additionalProperties") is False:
            extra = set(value) - set(node.get("properties", {}))
            if extra:
                raise ContractError(path, f"unexpected fields {sorted(extra)}")

    if isinstance(value, list) and "items" in node:
        for index, item in enumerate(value):
            _check(item, node["items"], root, f"{path}[{index}]")
