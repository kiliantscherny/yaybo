"""Check that a logged-in record still parses into the columns we promise.

Run it directly - `uv run python tests/test_attest.py` - or under pytest.

The register answers with XML, but the same endpoint can render itself as the
attest a browser is shown, so both readers are covered here. The fixtures are
real documents with invented people in them: same shape, nobody's business.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tinglysning_dl as tl

FIXTURES = Path(__file__).parent


def test_xml_record():
    """The XML the register actually answers with."""
    raw = (FIXTURES / "ejdsummarisk_sample.xml").read_text(encoding="utf-8")
    attest = tl.attest_details({"_raw": raw})

    assert attest["bfe_nr"] == "100001"
    assert attest["ejerlejlighedsnr"] == "101"
    assert attest["fordelingstal"] == "33/9231"
    # Both of these live in free text under a heading, not in a field.
    assert attest["opdelingsdato"] == "1977-11-11"
    assert attest["areal_m2"] == "33"
    assert attest["adkomst_dokumenttype"] == "Skøde"
    assert attest["adkomst_dato_loebenummer"] == "20140630-1005446835"
    # The total, not the cash part of it.
    assert attest["koebesum_dkk"] == "875000"
    # A timestamp with an offset, reduced to the day it names.
    assert attest["overtagelsesdato"] == "2014-07-01"

    owners = attest["owners"]
    assert owners["testperson alfa testesen"]["foedselsdato"] == "1957-10-02"
    assert owners["testperson beta testesen"]["foedselsdato"] == "1951-10-30"
    # A company has no date of birth; its CVR number is what identifies it.
    assert owners["prøveholding aps"] == {"cvr": "12345678"}


def test_rendered_attest():
    """The fallback: the attest as a browser is shown it."""
    text = (FIXTURES / "attest_sample.txt").read_text(encoding="utf-8")
    attest = tl.attest_details({"_raw": f"<html><body><pre>{text}</pre></body></html>"})

    assert attest["ejerlejlighedsnr"] == "101"
    assert attest["bfe_nr"] == "100001"
    assert attest["areal_m2"] == "55"
    assert attest["opdelingsdato"] == "1975-05-05"
    assert attest["fordelingstal"] == "1/300"
    # "Dokumenttype" recurs down the document - the first one is the deed's,
    # every one after belongs to a charge against the property.
    assert attest["adkomst_dokumenttype"] == "Skøde"
    assert attest["koebesum_dkk"] == "3000000"
    assert attest["overtagelsesdato"] == "2026-10-01"
    # Here a date of birth has to be read out of a masked CPR number.
    assert attest["owners"]["testperson alfa testesen"]["foedselsdato"] == "1995-01-01"


def test_birth_date_from_masked_cpr():
    assert tl._birth_from_cpr("010195-****") == "1995-01-01"
    assert tl._birth_from_cpr("290280-1234") == "1980-02-29"
    # A year that has not happened yet belongs to the last century.
    assert tl._birth_from_cpr("010160-****") == "1960-01-01"
    assert tl._birth_from_cpr("010105-****") == "2005-01-01"
    # A CVR number is not a CPR number, and 88 is not a month.
    assert tl._birth_from_cpr("55667788") == ""


def test_numbers_and_dates():
    assert tl._plain_number("1.234.567 DKK") == "1234567"
    assert tl._plain_number("55 kvm") == "55"
    assert tl._plain_number("1/300") == "1/300"  # a fraction is not a number
    assert tl._iso_date("01.03.2024") == "2026-09-15"
    assert tl._iso_date("2014-07-01+02:00") == "2014-07-01"
    assert tl._iso_date("Skøde") == "Skøde"


def test_row_joins_owners_by_name():
    """The two views share only the owners' names, so that is the join."""
    raw = (FIXTURES / "ejdsummarisk_sample.xml").read_text(encoding="utf-8")
    record = {
        "adresse": "Prøvegade 1, 3. 12, 9999 Prøvekøbing",
        "ejendomstype": "Ejerlejlighed",
        "matrikler": [],
        "vurdering": {},
        "ejere": [
            {"navn": "Testperson Alfa Testesen", "andel": "1/2"},
            {"navn": "Testperson Beta Testesen", "andel": "1/2"},
        ],
    }
    row = tl.property_row(record, "uuid-1", {}, tl.attest_details({"_raw": raw}))

    assert row["ejer_1_foedselsdato"] == "1957-10-02"
    assert row["ejer_2_foedselsdato"] == "1951-10-30"
    assert "ejer_1_foedselsdato" in tl.property_fields(2, with_attest=True)
    # Logged out there is nothing to put in it, and an always-empty column
    # reads as missing data rather than as data we never had.
    assert "ejer_1_foedselsdato" not in tl.property_fields(2)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
