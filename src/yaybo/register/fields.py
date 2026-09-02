"""Turn the register's way of writing a value into a plain one.

These four are shared by everything that reads the register, which speaks in
three dialects at once: an OIO XML document, the attest a browser is shown, and
short blocks of free text inside both. The same date arrives as "01.03.2024",
as "2014-07-01+02:00", and hidden in the first six digits of a CPR number, so
the normalising has to live somewhere all three readers can reach.
"""

from __future__ import annotations

import re
from datetime import date


def birth_from_cpr(cpr: str) -> str:
    """Read a date of birth out of a CPR number, masked or not.

    The attest gives no date of birth as such - it prints "Cpr-nr.:
    010195-****", and a CPR opens with the birth date as DDMMYY. Only that half
    is legible, which is also the only half worth keeping, so the serial is
    never carried into the CSV even on the day the register stops masking it.

    The century lives in the masked digits and has to be inferred: a two-digit
    year later than this one belongs to the last century, since nobody buying
    property today was born in the 2090s.
    """
    digits = re.sub(r"\D", "", cpr.split("-")[0])
    if len(digits) != 6:
        return ""
    day, month, year = int(digits[:2]), int(digits[2:4]), int(digits[4:])
    century = 1900 if year > date.today().year % 100 else 2000
    try:
        return date(century + year, month, day).isoformat()
    except ValueError:
        return ""


def plain_number(value: str) -> str:
    """"1.234.567 DKK" becomes "1234567"; "55 kvm" becomes "55".

    Danish thousands separators are full stops, so a spreadsheet reads the
    attest's own figures as text. Anything that is not purely a number and a
    unit is left exactly as it was.
    """
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d{3})*(?:,\d+)?)\s*(?:kvm|m2|m²|dkk|kr\.?|%)?\s*", value, re.I
    )
    if not match:
        return value
    return match.group(1).replace(".", "").replace(",", ".")


def iso_date(value: str) -> str:
    """Reduce a date to just the day it names.

    The attest writes "01.03.2024" and the XML writes "2014-07-01+02:00" - a
    timestamp with an offset, whose time of day says when a record was touched
    rather than anything about the date itself.
    """
    value = value.strip()
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", value)
    if match:
        return f"{match[3]}-{match[2]}-{match[1]}"
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match[1] if match else value


def normalise(text: str) -> str:
    """Flatten a name or address to something two spellings of it both reach."""
    return " ".join(text.lower().replace(",", " ").split())
