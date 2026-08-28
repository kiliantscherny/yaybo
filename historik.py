"""Read the block of free text the register prints against a change of owner.

`rest/ejdhistoriskadkomst` answers with one entry per transfer, and the entry's
substance is a block of text meant for a <pre> tag rather than for a column:

    Købesum: 3.125.000 DKK
    Adkomsthavere:
    Testperson Alfa Testesen 010195-XXXX Ejerandel: 1/1

Four grammars turn up in it, because the register carried a century of paper
records into the same field. Newest first:

    Name 010195-XXXX Ejerandel: 1/1   a person, identified by a masked CPR
    Prøvekredit A/S 12345678 Ejerandel: 1/1   a company, identified by CVR
    Name A for 1/2 Name B for 1/2    both owners on one line, shares inline
    Name                             a name and nothing else, from paper

Only the CPR's first six digits survive into the result, as a date of birth.
The serial is the half that identifies a living person rather than describing
one, and it is masked in the source anyway; see fields.birth_from_cpr.
"""

from __future__ import annotations

import re

import fields

# The register writes these three and nothing else - checked against every
# entry in a 731-row pull. A fourth would land in `ejere` as a name, which is
# wrong but visible, rather than being silently dropped.
KOEBESUM = re.compile(r"^K(?:ø|oe)besum:\s*(.+)$", re.I)
HEADER = re.compile(r"^Adkomsthavere:\s*$", re.I)

# "Name 010195-XXXX Ejerandel: 1/1", where the identifier is optional: an
# entry reading "Tvangsauktion Ejerandel: 1/1" names a procedure, not a person.
ANDEL_LINE = re.compile(
    r"^(?P<navn>.+?)"
    r"(?:\s+(?P<cpr>\d{6}-[\dX*]{4})|\s+(?P<cvr>\d{8}))?"
    r"\s+Ejerandel:\s*(?P<andel>\S+)\s*$",
    re.I,
)

# "Testperson Alfa for 1/2 Testperson Beta for 1/2" - and once, in percent.
INLINE_ANDEL = re.compile(r"(?P<navn>.+?)\s+for\s+(?P<andel>\d+/\d+|\d+(?:[.,]\d+)?\s*%)")

# Words that occupy the name slot without naming anybody.
NOT_A_NAME = {"tvangsauktion", "auktion", "ukendt"}


def parse(text: str) -> dict:
    """Split one history entry's text into a price and a list of owners.

    Returns {"koebesum_dkk": str, "ejere": [...]}, where each owner carries
    whichever of navn / foedselsdato / cvr / andel the entry actually gave.
    An entry that says only a name yields an owner with only a name.
    """
    result: dict = {"koebesum_dkk": "", "ejere": []}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or HEADER.match(line):
            continue

        price = KOEBESUM.match(line)
        if price:
            result["koebesum_dkk"] = fields.plain_number(price.group(1).strip())
            continue

        result["ejere"].extend(_owners(line))
    return result


def _owners(line: str) -> list[dict]:
    """Every owner named on one line. Usually one; the inline form gives two."""
    inline = list(INLINE_ANDEL.finditer(line))
    if inline and inline[-1].end() == len(line.rstrip()):
        # Only trust this shape when the shares run to the end of the line,
        # so a name that merely contains the word "for" is not carved up.
        return [_owner(m.group("navn"), andel=m.group("andel")) for m in inline]

    match = ANDEL_LINE.match(line)
    if match:
        return [
            _owner(
                match.group("navn"),
                cpr=match.group("cpr") or "",
                cvr=match.group("cvr") or "",
                andel=match.group("andel"),
            )
        ]

    # A bare name, carried over from the paper register with no share and no
    # identifier. Some of these are two people run together with no separator
    # at all - four words that are either one person or two - and there is
    # nothing in the line to say which. Splitting on a guess would invent an
    # owner, so the line stays whole and wrong-but-visible rather than
    # confidently wrong.
    return [_owner(line)]


def _owner(navn: str, *, cpr: str = "", cvr: str = "", andel: str = "") -> dict:
    navn = navn.strip(" ,")
    owner = {"navn": navn, "foedselsdato": "", "cvr": cvr, "andel": _andel(andel)}
    if cpr:
        owner["foedselsdato"] = fields.birth_from_cpr(cpr)
    if navn.lower() in NOT_A_NAME:
        # Keep the word - it is what the register says - but do not let it
        # stand as a person in a table of people.
        owner["navn"], owner["note"] = "", navn
    return owner


def _andel(andel: str) -> str:
    """"1/2" and "10 %" both settle down; anything else is left alone."""
    andel = andel.strip()
    return andel.replace(" ", "") if andel else ""
