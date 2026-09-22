"""Manual entries: cards and printings the registry records by hand.
data/manual.json.

The official API does not serve everything that was printed. Store-kit
prize cards, Kickstarter pledge cards and curios exist on tables and in
binders and nowhere in the API. The registry records them by hand, with
the same fields as every other card and printing and real ids from the
same counters, so they can be searched, linked and stored like anything
else. Each one is marked origin "manual" and says where the record came
from and when.

    {"cards": [CARD, ...],          # new cards, each with its printings
     "printings": [PRINTING, ...]}  # new printings of cards already held

    CARD = {"codex_id": null,       # minted by `apply` and written back
            "name": "...", <every card field: type, category, rarity, slot,
            subtypes, elements, keywords, umbrellas, cost, attack, defense,
            life, thr_air, thr_earth, thr_fire, thr_water, rules_text, back>,
            "source": "...", "recorded": "YYYY-MM-DD",
            "printings": [PRINTING, ...]}

    PRINTING = {"printing_id": null,          # minted by `apply`
                "codex_id": "C000123",        # top-level printings only
                "set_code": "999", "set_name": "Promo",
                "released_at": "2023-10-06",  # or null when unknown
                "released_with": "002",       # the set release it belongs to
                "product": "OrganizedPlay", "finish": "Foil",
                "artist": "...", "typeline": "...", "flavour_text": "...",
                "slug": "...",                # optional; predicted otherwise
                "source": "...", "recorded": "YYYY-MM-DD"}

Null means unknown, never "the same as another printing": a new printing
copies nothing from its siblings. Gameplay lives on the card, so a
printing of an existing card inherits its text by definition.

Either kind of entry may carry "withdrawn": {"on": date, "reason": "..."}
for a record found to be wrong. Ids are permanent, so a wrong record is
marked, never deleted; removing an entry from the file is an error.

`python -m registry.manual apply` mints ids for entries without one and
writes them back into the file, and updates entries that have one in
place: until upstream serves it, a manual record is our reading of the
card, so a corrected reading replaces the old. Once upstream serves a
record and a human confirms the match (see registry/diff.py), it becomes
origin "api", the sync speaks for it, and its entry stays in the file as
the record of where it started.

Slugs. Every printing carries a slug, and a manual printing gets a
predicted one in the publisher's own shape - set code, name, product code,
finish code, as in 999-sorcerer-op-f - so it reads like the rest and is
the first hint when upstream starts serving it. A prediction is
provisional: it owns nothing, is not written to slug_history, and is
replaced by upstream's slug on confirmation. Where a prediction would
repeat a slug already in use, the name part takes the release it belongs
to as a suffix (999-the_champion_002-op-f); an entry may also give its
own "slug".

Set codes are labels, never numbers: what a set is - a release, the
publisher's promo bucket, or a set of the registry's own such as CUR, the
curios - is recorded in data/sets.json (registry/sets.py), and a printing
may only name a set recorded there.
"""

import argparse
import datetime
import json
import re
import sys
import unicodedata
from pathlib import Path

from .db import (CARD_FIELDS, FACE_FIELDS, PRINTING_FACE_FIELDS, allocate_id, decode_field,
                 encode_field, face_of, open_db)
from .ids import format_card_id, format_printing_id, id_number
from .sets import is_release, load_sets

MANUAL_PATH = Path("data") / "manual.json"
RELEASED_WITH_PATH = Path("data") / "released-with.json"

CARD_ID = re.compile(r"^C\d{6}$")
PRINTING_ID = re.compile(r"^P\d{6}$")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9_]+)+$")

LIST_FIELDS = ("subtypes", "elements", "keywords", "umbrellas")
INT_FIELDS = ("cost", "attack", "defense", "life")
THRESHOLDS = ("thr_air", "thr_earth", "thr_fire", "thr_water")
TEXT_FIELDS = ("type", "category", "rarity", "slot")

PRINTING_KEYS = ("set_code", "set_name", "released_at", "released_with", "product", "finish",
                 "artist", "typeline", "flavour_text")
PROVENANCE_KEYS = ("source", "recorded")

# Finish codes in the publisher's slugs. Product codes are the initials of
# the product's words (OrganizedPlay -> op, BoxTopper -> bt), which holds
# for every product upstream serves; Rainbow is the one finish that is not
# its initial.
FINISH_CODES = {"Standard": "s", "Foil": "f", "Rainbow": "rf"}


# ---- predicted slugs ---------------------------------------------------

def slug_name(name):
    """A card name as the publisher's slugs spell it: accents and
    apostrophes dropped, lower case, every run of anything else an
    underscore."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"['\u2019]", "", ascii_name)
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")


def _initials(word):
    parts = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", word or "")
    return "".join(p[0] for p in parts).lower()


def product_code(product):
    return _initials(product)


def finish_code(finish):
    return FINISH_CODES.get(finish) or _initials(finish)


def predict_slug(set_code, card_name, product, finish, suffix=None):
    name = slug_name(card_name)
    if suffix:
        name = f"{name}_{suffix.lower()}"
    return f"{set_code.lower()}-{name}-{product_code(product)}-{finish_code(finish)}"


# ---- the file ------------------------------------------------------------

def _where(path, kind, index, entry):
    label = entry.get("name") or entry.get("printing_id") or entry.get("codex_id") or ""
    return f"{path}: {kind} {index + 1}{f' ({label})' if label else ''}"


def _check_text(where, entry, key, nullable=False, empty_ok=False):
    value = entry.get(key)
    if value is None and nullable:
        return
    if not isinstance(value, str) or (not empty_ok and not value.strip()):
        raise ValueError(f"{where}: {key} must be {'text or null' if nullable else 'text'}")
    if value != value.strip():
        raise ValueError(f"{where}: {key} has leading or trailing whitespace")


def _check_date(where, entry, key, nullable=False):
    value = entry.get(key)
    if value is None and nullable:
        return
    try:
        ok = (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
              and datetime.date.fromisoformat(value))
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"{where}: {key} must be a date, YYYY-MM-DD{' or null' if nullable else ''}")


def _check_keys(where, entry, required, optional=()):
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object")
    missing = [k for k in required if k not in entry]
    extra = sorted(set(entry) - set(required) - set(optional))
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    if extra:
        raise ValueError(f"{where}: unknown key(s) {', '.join(extra)}")


def _check_withdrawn(where, entry):
    withdrawn = entry.get("withdrawn")
    if withdrawn is None:
        return
    _check_keys(f"{where}: withdrawn", withdrawn, ("on", "reason"))
    _check_date(f"{where}: withdrawn", withdrawn, "on")
    _check_text(f"{where}: withdrawn", withdrawn, "reason")


def _check_provenance(where, entry):
    _check_text(where, entry, "source")
    _check_date(where, entry, "recorded")
    _check_withdrawn(where, entry)


def _check_card(where, card):
    required = ("codex_id", "name") + tuple(f for f in CARD_FIELDS if f != "name") \
        + PROVENANCE_KEYS + ("printings",)
    _check_keys(where, card, required, ("withdrawn",))
    if card["codex_id"] is not None and not (isinstance(card["codex_id"], str)
                                             and CARD_ID.match(card["codex_id"])):
        raise ValueError(f"{where}: codex_id must be a card id or null")
    _check_text(where, card, "name")
    for key in TEXT_FIELDS:
        _check_text(where, card, key, nullable=True)
    # rules_text is required text: "" means the card has none. Unknown text
    # is not a card anyone can record.
    _check_text(where, card, "rules_text", empty_ok=True)
    for key in LIST_FIELDS:
        value = card[key]
        if value is not None and not (isinstance(value, list)
                                      and all(isinstance(v, str) and v for v in value)):
            raise ValueError(f"{where}: {key} must be a list of names or null")
    for key in INT_FIELDS:
        value = card[key]
        if value is not None and not (isinstance(value, int) and not isinstance(value, bool)
                                      and value >= 0):
            raise ValueError(f"{where}: {key} must be a whole number or null")
    for key in THRESHOLDS:
        value = card[key]
        if not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
            raise ValueError(f"{where}: {key} must be a whole number (0 for none)")
    back = card["back"]
    if back is not None:
        _check_keys(f"{where}: back", back, FACE_FIELDS)
    _check_provenance(where, card)
    if not isinstance(card["printings"], list) or not card["printings"]:
        raise ValueError(f"{where}: a card needs at least one printing")


def _check_printing(where, printing, top_level, kinds):
    required = ("printing_id",) + (("codex_id",) if top_level else ()) \
        + PRINTING_KEYS + PROVENANCE_KEYS
    _check_keys(where, printing, required, ("slug", "back", "withdrawn"))
    pid = printing["printing_id"]
    if pid is not None and not (isinstance(pid, str) and PRINTING_ID.match(pid)):
        raise ValueError(f"{where}: printing_id must be a printing id or null")
    if top_level and not (isinstance(printing["codex_id"], str)
                          and CARD_ID.match(printing["codex_id"])):
        raise ValueError(f"{where}: codex_id must name the card this is a printing of")
    if printing["set_code"] not in kinds:
        raise ValueError(f"{where}: set {printing['set_code']!r} is not in data/sets.json; "
                         f"record what the set is there first")
    _check_text(where, printing, "set_name")
    _check_date(where, printing, "released_at", nullable=True)
    released_with = printing["released_with"]
    if released_with is not None and not is_release(released_with, kinds):
        raise ValueError(f"{where}: released_with must name a set release such as 002, or be null")
    if is_release(printing["set_code"], kinds) and released_with is not None:
        raise ValueError(f"{where}: a printing in set {printing['set_code']} is released "
                         f"with that set; leave released_with null")
    for key in ("product", "finish"):
        _check_text(where, printing, key)
    for key in ("artist", "typeline", "flavour_text"):
        _check_text(where, printing, key, nullable=True)
    if "slug" in printing and not (isinstance(printing["slug"], str) and SLUG.match(printing["slug"])):
        raise ValueError(f"{where}: slug must look like 999-the_champion-op-f")
    back = printing.get("back")
    if back is not None:
        _check_keys(f"{where}: back", back, PRINTING_FACE_FIELDS)
    _check_provenance(where, printing)


def load_manual(path=MANUAL_PATH, kinds=None):
    """The file, shape-checked: a mistake is an error here, never a record
    silently left out. A missing file means no manual entries. `kinds`
    is what each set is (data/sets.json)."""
    kinds = load_sets() if kinds is None else kinds
    path = Path(path)
    if not path.exists():
        return {"cards": [], "printings": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object with cards and printings")
    extra = sorted(set(data) - {"_comment", "cards", "printings"})
    if extra:
        raise ValueError(f"{path}: unknown key(s) {', '.join(extra)}")
    cards, printings = data.get("cards", []), data.get("printings", [])
    if not isinstance(cards, list) or not isinstance(printings, list):
        raise ValueError(f"{path}: cards and printings must be lists")
    names, ids = set(), set()
    for i, card in enumerate(cards):
        where = _where(path, "card", i, card)
        _check_card(where, card)
        if card["name"].casefold() in names:
            raise ValueError(f"{where}: {card['name']} appears twice")
        names.add(card["name"].casefold())
        for j, printing in enumerate(card["printings"]):
            _check_printing(f"{where}, printing {j + 1}", printing, top_level=False, kinds=kinds)
    for i, printing in enumerate(printings):
        _check_printing(_where(path, "printing", i, printing), printing, top_level=True, kinds=kinds)
    for entry in cards + printings + [p for c in cards for p in c["printings"]]:
        record_id = entry.get("codex_id") if "printings" in entry else entry.get("printing_id")
        if record_id is not None:
            if record_id in ids:
                raise ValueError(f"{path}: {record_id} appears twice")
            ids.add(record_id)
    return {"_comment": data.get("_comment"), "cards": cards, "printings": printings}


def write_manual(manual, path=MANUAL_PATH):
    data = {}
    if manual.get("_comment"):
        data["_comment"] = manual["_comment"]
    data["cards"] = manual["cards"]
    data["printings"] = manual["printings"]
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8", newline="\n")


def load_released_with(path=RELEASED_WITH_PATH, kinds=None):
    """{printing_id: {"set_code", "source", "recorded"}}: the release an
    official promo belongs to, which upstream's set 999 does not say."""
    kinds = load_sets() if kinds is None else kinds
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("printings"), dict):
        raise ValueError(f"{path}: expected an object with a printings map")
    extra = sorted(set(data) - {"_comment", "printings"})
    if extra:
        raise ValueError(f"{path}: unknown key(s) {', '.join(extra)}")
    out = {}
    for pid, entry in data["printings"].items():
        where = f"{path}: {pid}"
        if not PRINTING_ID.match(pid):
            raise ValueError(f"{where}: not a printing id")
        _check_keys(where, entry, ("set_code", "source", "recorded"))
        if not is_release(entry["set_code"], kinds):
            raise ValueError(f"{where}: set_code must name a set release such as 004")
        _check_text(where, entry, "source")
        _check_date(where, entry, "recorded")
        out[pid] = {"set_code": entry["set_code"], "source": entry["source"],
                    "recorded": entry["recorded"]}
    return out


# ---- applying the file ---------------------------------------------------

def artist_slug(artist):
    return None if artist is None else slug_name(artist)


def _card_values(card):
    values = {field: card[field] for field in CARD_FIELDS}
    if values["back"] is not None:
        values["back"] = {field: values["back"].get(field) for field in FACE_FIELDS}
    return values


def _printing_values(printing, slug):
    back = printing.get("back")
    return {
        "set_name": printing["set_name"], "set_code": printing["set_code"],
        "released_at": printing["released_at"], "product": printing["product"],
        "finish": printing["finish"], "slug": slug,
        "artist": printing["artist"], "artist_slug": artist_slug(printing["artist"]),
        "flavour_text": printing["flavour_text"], "typeline": printing["typeline"],
        "back": None if back is None else {f: back.get(f) for f in PRINTING_FACE_FIELDS},
        "released_with": printing["released_with"],
    }


def _provenance_values(entry):
    withdrawn = entry.get("withdrawn") or {}
    return {"origin": "manual", "manual_source": entry["source"],
            "manual_recorded": entry["recorded"],
            "withdrawn_at": withdrawn.get("on"), "withdrawn_reason": withdrawn.get("reason")}


def _first_date(card):
    """The day a manual card's face starts: the earliest release among its
    printings, or the day it was recorded when none has a date."""
    dates = [p["released_at"] for p in card["printings"] if p["released_at"]]
    return min(dates) if dates else card["recorded"]


class _Slugs:
    """Every slug in use, and by which printing, as apply hands them out:
    current and historical slugs, and the predictions already made in this
    run, so two new entries never predict the same slug."""

    def __init__(self, con, kinds):
        self.kinds = kinds
        self.owner = {}
        for row in con.execute("SELECT slug, printing_id FROM slug_history "
                               "UNION SELECT slug, printing_id FROM printings"):
            self.owner.setdefault(row["slug"], row["printing_id"])
        self.pending = {}

    def free(self, slug, printing_id):
        owner = self.owner.get(slug, self.pending.get(slug))
        return owner is None or owner == printing_id

    def take(self, slug, key):
        self.pending[slug] = key

    def choose(self, printing, card_name, printing_id, where):
        if "slug" in printing:
            slug = printing["slug"]
            if not self.free(slug, printing_id):
                raise ValueError(f"{where}: slug {slug} is already in use")
            return slug
        plain = predict_slug(printing["set_code"], card_name, printing["product"], printing["finish"])
        if self.free(plain, printing_id):
            return plain
        suffix = printing["released_with"] or (printing["set_code"]
                                               if is_release(printing["set_code"], self.kinds) else None)
        if suffix:
            suffixed = predict_slug(printing["set_code"], card_name, printing["product"],
                                    printing["finish"], suffix)
            if self.free(suffixed, printing_id):
                return suffixed
        raise ValueError(f"{where}: the predicted slug {plain} is already in use; "
                         f"give the printing a released_with or its own slug")


def _existing(con, table, key, number):
    return con.execute(f"SELECT * FROM {table} WHERE {key} = ?", (number,)).fetchone()


def _write_card(con, card_id, card, first, new):
    values = {**_card_values(card), **_provenance_values(card)}
    columns = list(values)
    if new:
        con.execute(f"INSERT INTO cards (card_id, {', '.join(columns)}) "
                    f"VALUES (?, {', '.join('?' for _ in columns)})",
                    [card_id] + [encode_field(c, values[c]) for c in columns])
        con.execute("INSERT INTO name_history (name, card_id, valid_from, valid_to) "
                    "VALUES (?, ?, ?, NULL)", (card["name"], card_id, first))
        con.execute("INSERT INTO card_history (card_id, valid_from, valid_to, face, source) "
                    "VALUES (?, ?, NULL, ?, 'manual')", (card_id, first, face_of(values)))
        return
    con.execute(f"UPDATE cards SET {', '.join(f'{c} = ?' for c in columns)} WHERE card_id = ?",
                [encode_field(c, values[c]) for c in columns] + [card_id])
    # A manual card's name and face are our reading of the printed card,
    # recorded once: a corrected reading replaces them, dates and all.
    con.execute("UPDATE name_history SET name = ?, valid_from = ? WHERE card_id = ?",
                (card["name"], first, card_id))
    con.execute("UPDATE card_history SET face = ?, valid_from = ? WHERE card_id = ?",
                (face_of(values), first, card_id))


def _write_printing(con, printing_id, card_id, printing, slug, new):
    values = {**_printing_values(printing, slug), **_provenance_values(printing)}
    columns = list(values)
    if new:
        con.execute(f"INSERT INTO printings (printing_id, card_id, {', '.join(columns)}) "
                    f"VALUES (?, ?, {', '.join('?' for _ in columns)})",
                    [printing_id, card_id] + [encode_field(c, values[c]) for c in columns])
    else:
        con.execute(f"UPDATE printings SET {', '.join(f'{c} = ?' for c in columns)} "
                    f"WHERE printing_id = ?",
                    [encode_field(c, values[c]) for c in columns] + [printing_id])


def apply_manual(con, manual, log=print, kinds=None):
    """Bring the database in line with the file: mint ids for new entries
    (writing them into `manual`, which the caller saves), update manual
    records in place, and leave confirmed ones alone. One transaction: any
    error leaves the database as it was."""
    counts = {"minted": 0, "updated": 0, "confirmed": 0}
    try:
        _apply(con, manual, counts, log, load_sets() if kinds is None else kinds)
    except Exception:
        con.rollback()
        raise
    con.commit()
    return counts


def _apply(con, manual, counts, log, kinds):
    slugs = _Slugs(con, kinds)
    seen_cards, seen_printings = set(), set()

    def printing_entry(printing, card_id, card_name, where):
        pid = printing["printing_id"]
        number = id_number(pid) if pid else None
        if number is not None:
            row = _existing(con, "printings", "printing_id", number)
            if row is None:
                raise ValueError(f"{where}: {pid} is not a printing in the registry")
            if row["card_id"] != card_id:
                raise ValueError(f"{where}: {pid} belongs to {format_card_id(row['card_id'])}")
            seen_printings.add(number)
            if row["origin"] != "manual":
                counts["confirmed"] += 1
                return
        slug = slugs.choose(printing, card_name, number, where)
        slugs.take(slug, number if number is not None else ("new", where))
        if number is None:
            number = allocate_id(con, "next_printing_id")
            _write_printing(con, number, card_id, printing, slug, new=True)
            printing["printing_id"] = format_printing_id(number)
            seen_printings.add(number)
            log(f"{printing['printing_id']}: minted, {card_name} ({slug})")
            counts["minted"] += 1
        else:
            _write_printing(con, number, card_id, printing, slug, new=False)
            counts["updated"] += 1

    for i, card in enumerate(manual["cards"]):
        where = _where("data/manual.json", "card", i, card)
        first = _first_date(card)
        if card["codex_id"] is None:
            clash = con.execute("SELECT card_id FROM cards WHERE lower(name) = lower(?)",
                                (card["name"],)).fetchone()
            if clash is not None:
                raise ValueError(f"{where}: {card['name']} is already "
                                 f"{format_card_id(clash['card_id'])}; add its printings under "
                                 f"'printings' with that codex_id")
            card_id = allocate_id(con, "next_card_id")
            _write_card(con, card_id, card, first, new=True)
            card["codex_id"] = format_card_id(card_id)
            log(f"{card['codex_id']}: minted, {card['name']}")
            counts["minted"] += 1
        else:
            card_id = id_number(card["codex_id"])
            row = _existing(con, "cards", "card_id", card_id)
            if row is None:
                raise ValueError(f"{where}: {card['codex_id']} is not a card in the registry")
            if row["origin"] == "manual":
                _write_card(con, card_id, card, first, new=False)
                counts["updated"] += 1
            else:
                counts["confirmed"] += 1
        seen_cards.add(card_id)
        for j, printing in enumerate(card["printings"]):
            printing_entry(printing, card_id, card["name"], f"{where}, printing {j + 1}")

    for i, printing in enumerate(manual["printings"]):
        where = _where("data/manual.json", "printing", i, printing)
        card_id = id_number(printing["codex_id"])
        row = _existing(con, "cards", "card_id", card_id)
        if row is None:
            raise ValueError(f"{where}: {printing['codex_id']} is not a card in the registry")
        printing_entry(printing, card_id, row["name"], where)

    # A manual record leaves the registry only by being withdrawn, never by
    # dropping out of the file.
    for table, key, seen, fmt in (("cards", "card_id", seen_cards, format_card_id),
                                  ("printings", "printing_id", seen_printings,
                                   format_printing_id)):
        for row in con.execute(f"SELECT {key} FROM {table} WHERE origin = 'manual'"):
            if row[key] not in seen:
                raise ValueError(f"{fmt(row[key])} is a manual record with no entry in "
                                 f"data/manual.json; ids are permanent, so mark it withdrawn "
                                 f"instead of removing it")


# ---- the validator's view ------------------------------------------------

def check_manual(con, manual, released_with, errors, kinds=None):
    """The file and the database agree, manual records stay out of slug
    ownership, and the registry's own set codes are only on its own
    records."""
    kinds = load_sets() if kinds is None else kinds

    def err(message):
        errors.append(f"manual: {message}")

    entries = [(c, None) for c in manual["cards"]]
    printings = [(p, c) for c in manual["cards"] for p in c["printings"]] + \
        [(p, None) for p in manual["printings"]]
    listed_cards, listed_printings = set(), set()

    for card, _ in entries:
        if card["codex_id"] is None:
            err(f"{card['name']} has no codex_id yet (run python -m registry.manual apply)")
            continue
        number = id_number(card["codex_id"])
        listed_cards.add(number)
        row = _existing(con, "cards", "card_id", number)
        if row is None:
            err(f"{card['codex_id']} is not a card in the registry")
            continue
        if row["origin"] != "manual":
            if row["confirmed_at"] is None:
                err(f"{card['codex_id']} is listed in data/manual.json but was never a manual record")
            continue
        expected = {**_card_values(card), **_provenance_values(card)}
        actual = {c: decode_field(c, row[c]) for c in expected}
        for field in expected:
            if expected[field] != actual[field]:
                err(f"{card['codex_id']}: {field} differs from data/manual.json "
                    f"(run python -m registry.manual apply)")
        history = con.execute("SELECT source, valid_to FROM card_history WHERE card_id = ?",
                              (number,)).fetchall()
        if [(h["source"], h["valid_to"]) for h in history] != [("manual", None)]:
            err(f"{card['codex_id']}: a manual card has exactly one history row, source manual")

    for printing, card in printings:
        pid = printing["printing_id"]
        if pid is None:
            err(f"a printing of {card['name'] if card else printing['codex_id']} has no "
                f"printing_id yet (run python -m registry.manual apply)")
            continue
        number = id_number(pid)
        listed_printings.add(number)
        row = _existing(con, "printings", "printing_id", number)
        if row is None:
            err(f"{pid} is not a printing in the registry")
            continue
        owner = card["codex_id"] if card else printing["codex_id"]
        if owner and row["card_id"] != id_number(owner):
            err(f"{pid} belongs to {format_card_id(row['card_id'])}, not {owner}")
        if row["origin"] != "manual":
            if row["confirmed_at"] is None:
                err(f"{pid} is listed in data/manual.json but was never a manual record")
            continue
        expected = {**_printing_values(printing, row["slug"]), **_provenance_values(printing)}
        actual = {c: decode_field(c, row[c]) for c in expected}
        for field in expected:
            if expected[field] != actual[field]:
                err(f"{pid}: {field} differs from data/manual.json "
                    f"(run python -m registry.manual apply)")
        if "slug" in printing and row["slug"] != printing["slug"]:
            err(f"{pid}: slug {row['slug']} is not the one data/manual.json gives")

    for table, key, listed, fmt in (("cards", "card_id", listed_cards, format_card_id),
                                    ("printings", "printing_id", listed_printings,
                                     format_printing_id)):
        for row in con.execute(f"SELECT * FROM {table}"):
            label = fmt(row[key])
            if row["origin"] not in ("api", "manual"):
                err(f"{label}: origin {row['origin']!r} is neither api nor manual")
            if row["origin"] == "manual" and row[key] not in listed:
                err(f"{label} is a manual record with no entry in data/manual.json")
            if row["origin"] == "api" and row["manual_source"] is None and (
                    row["confirmed_at"] or row["withdrawn_at"] or row["manual_recorded"]):
                err(f"{label}: an api record carries manual provenance")
            if row["confirmed_at"] is not None and row["origin"] != "api":
                err(f"{label}: confirmed but still manual")
            if row["confirmed_at"] is not None and row["manual_source"] is None:
                err(f"{label}: confirmed, but says nothing of the manual record it started as")
            if row["withdrawn_at"] is not None and row["origin"] != "manual":
                err(f"{label}: only a manual record can be withdrawn")

    for row in con.execute("SELECT p.printing_id, p.slug, p.origin, p.set_code, p.released_with, "
                           "p.manual_source, c.withdrawn_at AS card_withdrawn, p.withdrawn_at "
                           "FROM printings p JOIN cards c ON c.card_id = p.card_id"):
        label = format_printing_id(row["printing_id"])
        if row["origin"] == "manual":
            if con.execute("SELECT 1 FROM slug_history WHERE printing_id = ?",
                           (row["printing_id"],)).fetchone():
                err(f"{label}: a manual printing's slug is a prediction and has no slug_history")
            owner = con.execute("SELECT printing_id FROM slug_history WHERE slug = ?",
                                (row["slug"],)).fetchone()
            if owner is not None:
                err(f"{label}: predicted slug {row['slug']} belongs to "
                    f"{format_printing_id(owner['printing_id'])}")
            if row["card_withdrawn"] and not row["withdrawn_at"]:
                err(f"{label}: its card is withdrawn, so it must be too")
        elif row["released_with"] is not None and row["manual_source"] is None:
            # A confirmed manual printing keeps the release recorded for
            # it; every other official printing takes it from the file.
            err(f"{label}: released_with for an official printing belongs in "
                f"data/released-with.json")

    names = {}
    for row in con.execute("SELECT DISTINCT set_code, set_name FROM printings"):
        names.setdefault(row["set_code"], set()).add(row["set_name"])
    for code, spelled in names.items():
        if len(spelled) > 1:
            err(f"set {code} is named {sorted(spelled)}; one set, one name")

    for pid, entry in released_with.items():
        row = _existing(con, "printings", "printing_id", id_number(pid))
        if row is None:
            err(f"data/released-with.json names {pid}, which is not a printing")
        elif row["manual_source"] is not None:
            err(f"data/released-with.json names {pid}, a manual record; give its "
                f"released_with in data/manual.json")
        elif is_release(row["set_code"], kinds):
            err(f"data/released-with.json names {pid}, which is in set {row['set_code']} "
                f"and so released with it")
        elif entry["set_code"] not in names:
            err(f"data/released-with.json gives {pid} set {entry['set_code']}, which the "
                f"registry does not hold")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record cards and printings from data/manual.json.")
    parser.add_argument("command", choices=["apply", "check"])
    parser.add_argument("--db", default="registry.sqlite")
    parser.add_argument("--manual", default=str(MANUAL_PATH))
    args = parser.parse_args(argv)
    manual = load_manual(args.manual)
    con = open_db(args.db)
    if args.command == "apply":
        counts = apply_manual(con, manual)
        if counts["minted"]:
            write_manual(manual, args.manual)
        print(f"{counts['minted']} minted, {counts['updated']} updated, "
              f"{counts['confirmed']} already confirmed upstream")
        return 0
    errors = []
    check_manual(con, manual, load_released_with(), errors)
    for error in errors:
        print(f"  - {error}")
    print("OK" if not errors else f"FAIL: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
