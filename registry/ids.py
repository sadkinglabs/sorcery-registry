"""Published ID format.

Internally (SQLite) identifiers are plain integers. The published form -
the JSON export and the MCP server - is a fixed-width prefixed string:
C000042 for cards, P000042 for printings. The prefix names the ID space,
so a card ID can never be mistaken for a printing ID; the digits are the
internal integer, zero-padded to six figures. The mapping is a trivial
bijection: int("C000042"[1:]) == 42.
"""

WIDTH = 6
CARD_PREFIX = "C"
PRINTING_PREFIX = "P"


def format_card_id(number):
    return f"{CARD_PREFIX}{number:0{WIDTH}d}"


def format_printing_id(number):
    return f"{PRINTING_PREFIX}{number:0{WIDTH}d}"


def id_number(value):
    """Extract the integer from any accepted ID spelling: a prefixed string
    ('C000042', 'P000042'), a bare digit string ('000042', '42'), or an
    int. Raises ValueError on anything else."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = value[1:] if value[:1].upper() in (CARD_PREFIX, PRINTING_PREFIX) else value
        if digits.isdigit():
            return int(digits)
    raise ValueError(f"not a registry id: {value!r}")
