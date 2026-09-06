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

from yaybo import i18n

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
        return i18n.t("just now")
    minutes = seconds / 60
    if minutes < 90:
        return i18n.t("{n} min ago", n=f"{minutes:.0f}")
    hours = minutes / 60
    if hours < 36:
        return i18n.t("{n} h ago", n=f"{hours:.0f}")
    days = hours / 24
    if days < 14:
        return i18n.t("{n} d ago", n=f"{days:.0f}")
    if days < 60:
        return i18n.t("{n} w ago", n=f"{days / 7:.0f}")
    if days < 730:
        return i18n.t("{n} mo ago", n=f"{days / 30:.0f}")
    return i18n.t("{n} y ago", n=f"{days / 365:.0f}")


# Boligsiden answers in English. The stored value stays as it arrived, because
# that is what the API said; this is only how it is written on screen.
# Boligsiden's own keys, given a name a person would use. English here and
# translated where drawn, like every other label: these are a fixed set of
# categories rather than anything the register wrote.
# Boligsiden's own keys on the left, given a name a person would use on the
# right. The keys are theirs and stay as they are; the labels are English here
# and translated where drawn, like every other label. Nobody should ever be
# shown the key itself - "condo" is American, and more to the point it is an
# API token rather than a word.
BOLIGTYPER = {
    "condo": "Owner-occupied flat",
    "villa": "Detached house",
    "villa apartment": "Flat in a house",
    "terraced house": "Terraced house",
    "cooperative": "Co-op flat",
    "holiday house": "Holiday home",
    "holiday plot": "Holiday plot",
    "full year plot": "Building plot",
    "farm": "Farm",
    "hobby farm": "Smallholding",
    "houseboat": "Houseboat",
}


def rgb(colour: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    """A theme colour as plotext wants it.

    Textual keeps colours as "#5eb0ea"; plotext takes a name, a number or an
    RGB triple, and quietly draws in its own palette when handed anything else
    - so a chart that looks nothing like the rest of the application is the
    symptom of passing the string straight through.
    """
    if not colour or not colour.startswith("#") or len(colour) != 7:
        return fallback
    try:
        return (
            int(colour[1:3], 16),
            int(colour[3:5], 16),
            int(colour[5:7], 16),
        )
    except ValueError:
        return fallback


def boligtype(value) -> str:
    """Boligsiden's key becomes something readable in the current language.

    Anything unmapped is left exactly as it is: the register's own
    ejendomstype arrives through here too, and that is Danish data rather than
    a label of ours to translate.
    """
    if value in (None, ""):
        return ""
    return i18n.t(BOLIGTYPER.get(str(value).strip().lower(), str(value)))


def yes_no(value) -> str:
    if value in (None, ""):
        return NOTHING
    if isinstance(value, str):
        value = value.lower() in ("true", "ja", "yes", "1")
    return i18n.t("yes") if value else i18n.t("no")


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
