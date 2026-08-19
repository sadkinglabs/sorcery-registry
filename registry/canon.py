"""Text canonicalisation.

The official API is inconsistent about line endings, trailing whitespace
and unicode form, sometimes within a single card. Everything stored in or
compared against the registry passes through canon_text first, so cosmetic
churn upstream never shows up as a data change here.
"""

import re
import unicodedata


def canon_text(value):
    """Normalise a text value: NFC unicode, \\n line endings, no trailing
    whitespace, and runs of newlines collapsed to one, so rules text is one
    line per ability. The API stores the same text with and without blank
    lines in different places; collapsing is the only way both forms compare
    equal. None passes through untouched."""
    if value is None:
        return None
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n+", "\n", text)


def set_code(set_name):
    """Derive a stable machine-friendly code from a set's display name.
    'Arthurian Legends' -> 'arthurian_legends'."""
    code = canon_text(set_name).lower()
    code = re.sub(r"[^a-z0-9]+", "_", code)
    return code.strip("_")


SLUG_RE = re.compile(r"^(\d+)-(.+)-([a-z_]+)-([a-z]+)$")


def parse_slug(slug):
    """Split a slug like '004-witch-b-s' into (card_number, name_segment,
    product_code, finish_code). Returns (None, None, None, None) if the slug
    does not match the known shape; the slug is stored regardless, parsing
    only feeds the convenience columns."""
    match = SLUG_RE.match(slug)
    if not match:
        return None, None, None, None
    number, name_seg, product_code, finish_code = match.groups()
    return int(number), name_seg, product_code, finish_code


def released_date(released_at):
    """Trim an API timestamp to its date. The time component drifts with
    upstream row churn and carries no card information."""
    if released_at is None:
        return None
    return released_at[:10]
