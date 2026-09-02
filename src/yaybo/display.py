"""How a value is written on screen, as opposed to how it is stored.

Danish conventions throughout, because that is what the register uses and what
anyone checking a figure against tinglysning.dk or Boligsiden will be reading:
full stops for thousands, a comma for the decimal mark, ISO dates only where
sorting matters more than reading.

Everything here takes whatever the database happened to hand back - a string, an
int, a Decimal, a None - and returns a string. A value that cannot be read is
shown as an em dash rather than as a zero: nothing recorded and nothing owed are
very different facts about a property.
"""

from __future__ import annotations

from datetime import date, datetime

NOTHING = "–"


def kr(value, *, unit: str = "") -> str:
    """1234567 becomes "1.234.567". Nothing becomes an em dash."""
    number = _number(value)
    if number is None:
        return NOTHING
    written = f"{int(round(number)):,}".replace(",", ".")
    return f"{written} {unit}".strip()


def compact_kr(value) -> str:
    """1234567 becomes "1,2 mio." - for a column that has to stay narrow."""
    number = _number(value)
    if number is None:
        return NOTHING
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f} mio.".replace(".", ",", 1)
    if abs(number) >= 1_000:
        return f"{number / 1_000:.0f}k"
    return f"{number:.0f}"


def pct(value, places: int = 1) -> str:
    number = _number(value)
    if number is None:
        return NOTHING
    return f"{number:.{places}f}".replace(".", ",") + " %"


def area(value) -> str:
    number = _number(value)
    if number is None:
        return NOTHING
    return f"{number:.0f} m²"


def number(value, places: int = 0) -> str:
    found = _number(value)
    if found is None:
        return NOTHING
    return f"{found:.{places}f}".replace(".", ",") if places else f"{found:.0f}"


def when(value) -> str:
    """A date as the register writes it: 01.03.2024."""
    day = parse_date(value)
    return f"{day.day:02d}.{day.month:02d}.{day.year}" if day else NOTHING


def iso(value) -> str:
    """A date as it sorts: 2024-03-01."""
    day = parse_date(value)
    return day.isoformat() if day else NOTHING


def ago(value) -> str:
    """How long ago something was fetched, in the roughest useful unit."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return NOTHING
    else:
        return NOTHING

    seconds = (datetime.now() - moment).total_seconds()
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    hours = minutes / 60
    if hours < 36:
        return f"{hours:.0f} h ago"
    days = hours / 24
    if days < 14:
        return f"{days:.0f} d ago"
    if days < 60:
        return f"{days / 7:.0f} w ago"
    if days < 730:
        return f"{days / 30:.0f} mo ago"
    return f"{days / 365:.0f} y ago"


# Boligsiden answers in English. The stored value stays as it arrived, because
# that is what the API said; this is only how it is written on screen.
BOLIGTYPER = {
    "condo": "Ejerlejlighed",
    "villa": "Villa",
    "villa apartment": "Villalejlighed",
    "terraced house": "Rækkehus",
    "cooperative": "Andelsbolig",
    "holiday house": "Sommerhus",
    "holiday plot": "Sommerhusgrund",
    "full year plot": "Helårsgrund",
    "farm": "Landejendom",
    "hobby farm": "Hobbylandbrug",
    "houseboat": "Husbåd",
}


def boligtype(value) -> str:
    """"condo" becomes "Ejerlejlighed"; anything unmapped is left as it is."""
    if value in (None, ""):
        return ""
    return BOLIGTYPER.get(str(value).strip().lower(), str(value))


def yes_no(value) -> str:
    if value in (None, ""):
        return NOTHING
    if isinstance(value, str):
        value = value.lower() in ("true", "ja", "yes", "1")
    return "ja" if value else "nej"


def text(value, empty: str = NOTHING) -> str:
    """Anything at all, as one line, with the whitespace tidied."""
    if value in (None, ""):
        return empty
    return " ".join(str(value).split())


def shorten(value, width: int) -> str:
    """One line, cut to fit, with an ellipsis where it was cut."""
    written = text(value)
    return written if len(written) <= width else written[: width - 1] + "…"


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
