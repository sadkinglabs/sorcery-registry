"""What each set is: data/sets.json.

A set code is a label, not a number. 001 is Alpha because the publisher
says so, 003 does not exist, and 999 is where the publisher files every
promo. Nothing about a set can be read from its code's characters. What
a set is must be recorded, like every other registry-owned fact:

    {"sets": {"001": {"kind": "release"}, "999": {"kind": "promo"},
              "CUR": {"kind": "registry"}}}

    release   a set release: its printings came out with it (Alpha, Gothic)
    promo     the publisher's bucket for promos, which came out with many
              releases; which one is recorded per printing (released_with)
    registry  a set of the registry's own, for printings the official API
              will never serve (CUR, the curios)

The export publishes the kind on every set, and everything that asks
"which release did this come out with" reads it. The validator refuses a
set the file does not classify, so a new set from upstream stops the
release until a person says what it is.

The one rule about a code's shape is a collision rule, not a meaning:
the registry's own codes are three capital letters, which the publisher's
three-digit codes can never be, so a code of ours can never collide with
one of theirs.
"""

import json
import re
from pathlib import Path

SETS_PATH = Path("data") / "sets.json"
KINDS = ("release", "promo", "registry")
REGISTRY_CODE = re.compile(r"^[A-Z]{3}$")


def load_sets(path=SETS_PATH):
    """{set_code: kind}, shape-checked. A missing file means no set is
    classified, which the validator reports for every set held."""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sets"), dict):
        raise ValueError(f"{path}: expected an object with a sets map")
    extra = sorted(set(data) - {"_comment", "sets"})
    if extra:
        raise ValueError(f"{path}: unknown key(s) {', '.join(extra)}")
    kinds = {}
    for code, entry in data["sets"].items():
        where = f"{path}: set {code}"
        if not isinstance(code, str) or not code or code != code.strip():
            raise ValueError(f"{where}: a set code is a non-empty string")
        if not isinstance(entry, dict) or set(entry) != {"kind"}:
            raise ValueError(f"{where}: expected {{\"kind\": ...}}")
        kind = entry["kind"]
        if kind not in KINDS:
            raise ValueError(f"{where}: kind must be one of {', '.join(KINDS)}")
        if kind == "registry" and not REGISTRY_CODE.match(code):
            raise ValueError(f"{where}: a set of the registry's own has a code of three capital "
                             f"letters, so it can never collide with a publisher's code")
        kinds[code] = kind
    return kinds


def is_release(set_code, kinds):
    """Whether the set is itself a release, as recorded - never guessed
    from the code."""
    return set_code is not None and kinds.get(set_code) == "release"


def check_sets(con, kinds, errors):
    """The validator's view: every set the registry holds is classified,
    and a set of the registry's own holds only records kept by hand."""
    for row in con.execute("SELECT set_code, count(*) AS n, "
                           "sum(origin != 'manual') AS official FROM printings GROUP BY set_code"):
        code = row["set_code"]
        if code is None:
            continue
        if code not in kinds:
            errors.append(f"data/sets.json: set {code} is not classified; add it with its kind "
                          f"(release, promo or registry)")
        elif kinds[code] == "registry" and row["official"]:
            errors.append(f"data/sets.json: set {code} is the registry's own, but "
                          f"{row['official']} of its printings are official records")
