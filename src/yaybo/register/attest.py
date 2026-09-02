"""Read the logged-in record - the tingbogsattest - into columns.

The register answers `ejdsummarisk` in two ways. Asked for data it returns a
full OIO XML document, far better described than anything a browser is shown,
and taking that apart is attest_xml's job. Asked for a document it renders the
attest itself, and then the only way back to the fields is to read them off
their own printed labels - which is what the label readers below are for.

Both routes end in the same handful of columns a single property row can hold.
Everything else the document contains - every party to every mortgage, the
sub-pledges, the easement subject codes - reaches the database through its own
tables instead, built in yaybo.register.rows.
"""

from __future__ import annotations

import json
import re

from yaybo.register import attest_xml
from yaybo.register.fields import birth_from_cpr, iso_date, normalise, plain_number

# The attest prints a label, then its value on the next line, and these are the
# labels it really uses - read off a real one rather than guessed at. Note how
# little they resemble the column names they feed: the flat's own size is
# "Ejerlejlighedens areal" while a bare "Areal" is the whole plot's, and the
# flat's number is a bare "Nummer". Order inside each tuple is preference, for
# the labels the attest gives more than one of.
ATTEST_LABELS = {
    "ejerlejlighedsnr": ("nummer",),
    "bfe_nr": ("bfe-nummer", "bfe-nr", "bfenummer"),
    "areal_m2": ("ejerlejlighedens areal",),
    "opdelingsdato": ("ejerlejlighedens dato",),
    "fordelingstal": ("fordelingstal",),
    "adkomst_dokumenttype": ("dokumenttype",),
    "adkomst_dato_loebenummer": ("dato/løbenummer", "dato/loebenummer"),
    "koebesum_dkk": ("købesum i alt", "kontant købesum", "købesum"),
    "overtagelsesdato": ("dato for overtagelse", "overtagelsesdato"),
}
NAME_LABELS = ("navn", "ejer", "adkomsthaver")
BIRTH_LABELS = ("fødselsdato", "foedselsdato", "fodselsdato", "fødselsdag")
CPR_LABELS = ("cpr-nr", "cpr nr", "cprnr", "cpr", "cprnummer", "personnummer")
NUMERIC_COLUMNS = ("areal_m2", "koebesum_dkk")
DATE_COLUMNS = ("opdelingsdato", "overtagelsesdato")


def attest_details(details: dict | None) -> dict:
    """Flatten the logged-in record into the columns the CSV wants.

    Returns the property's own fields plus an `owners` map, keyed on the
    normalised name so it can be joined against the public record's owner list,
    which is the only thing the two views share.

    The register answers this one in XML - a full OIO document, far better
    described than the attest a browser is shown. The label readers below stay
    as a fallback for the day it answers with that attest instead.
    """
    if not details:
        return {}
    if "_raw" in details:
        return _from_xml(details["_raw"]) or _collect(
            _pairs_from_document(details["_raw"])
        )
    return _collect(_pairs_from_data(details))


def _from_xml(raw: str) -> dict:
    """The flat slice of the XML record that the CSV columns were built around.

    Taking the document apart is attest_xml's job; this asks it for the handful
    of fields a single row can hold. Everything else it finds - every party to
    every mortgage, the sub-pledges, the easement subject codes - reaches the
    database through its own tables rather than through here.
    """
    return attest_xml.summary(raw)


def _collect(pairs) -> dict:
    """Turn a stream of label/value pairs into one property's worth of columns.

    Two things make this a stream rather than a lookup. A person's identifying
    line is printed *under* their name, not beside it, so the last name seen
    owns whatever comes next; and the same label recurs down the document -
    "Dokumenttype" belongs to the adkomst the first time and to a mortgage
    every time after - so earlier wins.
    """
    found: dict = {"owners": {}}
    ranks: dict = {}
    owner = ""
    for label, value in pairs:
        key = _label_key(label)
        value = str(value).strip()
        if not value:
            continue

        for column, spellings in ATTEST_LABELS.items():
            if key in spellings:
                rank = spellings.index(key)
                if rank < ranks.get(column, len(spellings)):
                    ranks[column], found[column] = rank, value

        if key in NAME_LABELS:
            owner = value
            found["owners"].setdefault(normalise(value), {})
        elif owner and key in BIRTH_LABELS:
            found["owners"][normalise(owner)]["foedselsdato"] = iso_date(value)
        elif owner and key in CPR_LABELS:
            born = birth_from_cpr(value)
            if born:
                found["owners"][normalise(owner)]["foedselsdato"] = born

    for column in NUMERIC_COLUMNS:
        if column in found:
            found[column] = plain_number(found[column])
    for column in DATE_COLUMNS:
        if column in found:
            found[column] = iso_date(found[column])
    return found


def _pairs_from_document(html: str):
    """Read label/value pairs off the attest when it arrives rendered.

    Two shapes, because the attest uses both: table rows whose first cell is
    the label, and running lines where the label ends in a colon and the value
    is on the line below it.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0]:
            yield cells[0], cells[1]

    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        label, separator, value = line.partition(":")
        if separator:
            yield label, value.strip() or (
                lines[index + 1] if index + 1 < len(lines) else ""
            )


def _pairs_from_data(payload):
    """The same pairs, should the attest arrive as data instead.

    Walks rather than reaching for known paths, and yields a level's own values
    before descending, so a name and the identifying line beside it stay
    adjacent in the stream even when the object sits in a list of owners.
    """
    if isinstance(payload, list):
        for item in payload:
            yield from _pairs_from_data(item)
        return
    if not isinstance(payload, dict):
        return

    nested = []
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            nested.append(value)
        elif value is not None:
            yield key, value
    for value in nested:
        yield from _pairs_from_data(value)


def _label_key(label: str) -> str:
    """Normalise a label: "Cpr-nr.:", "Areal (m2)" and "areal" all settle down."""
    text = re.sub(r"\([^)]*\)", " ", str(label)).replace(":", " ")
    return " ".join(text.lower().split()).rstrip(".")


def attest_document(details: dict | None) -> tuple[str, str]:
    """The whole record as it arrived, and the extension to file it under.

    Kept verbatim for the file on disk: the register signs this document, and
    a re-rendered copy is no longer the thing it signed. The database gets
    attest_json() instead, which is the same content in a form that can be
    queried without parsing it again.
    """
    if not details:
        return "", ""
    if "_raw" not in details:
        return "json", json.dumps(details, ensure_ascii=False, indent=2)
    raw = details["_raw"]
    return ("xml" if raw.lstrip().startswith("<") else "txt"), raw


def attest_json(details: dict | None) -> str:
    """The record as JSON, namespaces stripped, for the database to hold.

    The columns take what is worth sorting and filtering on; this keeps
    everything else, so a field nobody has named yet is still recoverable
    without going back to the register for another copy.
    """
    if not details:
        return ""
    if "_raw" not in details:
        return json.dumps(details, ensure_ascii=False)
    outline = attest_xml.outline(details["_raw"])
    return json.dumps(outline, ensure_ascii=False) if outline else ""

