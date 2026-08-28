"""Check the block of free text the register prints against a change of owner.

Run directly - `uv run python tests/test_historik.py` - or under pytest.

Every shape here was taken from a real 731-entry pull and then rewritten with
invented people. The register carried a century of paper records into this one
field, so the grammars below are not variations on a theme - they are four
different notations that happen to share a column.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import historik


def test_price_and_a_single_owner():
    parsed = historik.parse(
        "Købesum: 3.125.000 DKK\n"
        "Adkomsthavere:\n"
        "Testperson Alfa Testesen 010195-XXXX Ejerandel: 1/1"
    )
    assert parsed["koebesum_dkk"] == "3125000"
    assert parsed["ejere"] == [
        {"navn": "Testperson Alfa Testesen", "foedselsdato": "1995-01-01",
         "cvr": "", "andel": "1/1"}
    ]


def test_two_owners_each_with_a_share():
    parsed = historik.parse(
        "Købesum: 2.300.000 DKK\n"
        "Adkomsthavere:\n"
        "Testperson Alfa Testesen 010195-XXXX Ejerandel: 1/2\n"
        "Testperson Beta Testesen 290280-XXXX Ejerandel: 1/2"
    )
    assert [e["foedselsdato"] for e in parsed["ejere"]] == ["1995-01-01", "1980-02-29"]
    assert [e["andel"] for e in parsed["ejere"]] == ["1/2", "1/2"]


def test_a_company_is_identified_by_cvr_not_a_birth_date():
    parsed = historik.parse("Adkomsthavere:\nPrøvebolig A/S 12345678 Ejerandel: 1/1")
    owner = parsed["ejere"][0]
    assert owner == {"navn": "Prøvebolig A/S", "foedselsdato": "", "cvr": "12345678",
                     "andel": "1/1"}


def test_shares_written_inline_on_one_line():
    """The older notation puts both owners on one line, with "for" before each
    share, and no identifiers at all."""
    parsed = historik.parse("Testperson Alfa for 1/2 Testperson Beta for 1/2")
    assert [(e["navn"], e["andel"]) for e in parsed["ejere"]] == [
        ("Testperson Alfa", "1/2"), ("Testperson Beta", "1/2")
    ]


def test_shares_written_as_percentages():
    parsed = historik.parse("Testperson Alfa for 10% Testperson Beta for 90%")
    assert [e["andel"] for e in parsed["ejere"]] == ["10%", "90%"]


def test_a_name_containing_for_is_not_carved_up():
    """"for" only splits a line when the shares run to the end of it - otherwise
    any name with the word in it would be cut in half."""
    parsed = historik.parse("Adkomsthavere:\nForeningen for Prøvehaver 12345678 Ejerandel: 1/1")
    assert parsed["ejere"][0]["navn"] == "Foreningen for Prøvehaver"


def test_a_bare_name_from_the_paper_register():
    parsed = historik.parse("Testperson Alfa Testesen")
    assert parsed["ejere"] == [
        {"navn": "Testperson Alfa Testesen", "foedselsdato": "", "cvr": "", "andel": ""}
    ]


def test_a_procedure_is_not_a_person():
    """"Tvangsauktion" stands where a name stands, but names nobody. It is kept
    as a note so the entry is not silently emptied, and out of the name column
    so a table of people stays a table of people."""
    parsed = historik.parse("Adkomsthavere:\nTvangsauktion Ejerandel: 1/1")
    owner = parsed["ejere"][0]
    assert owner["navn"] == ""
    assert owner["note"] == "Tvangsauktion"
    assert owner["andel"] == "1/1"


def test_an_entry_with_no_owners_at_all():
    assert historik.parse("") == {"koebesum_dkk": "", "ejere": []}
    assert historik.parse("Adkomsthavere:") == {"koebesum_dkk": "", "ejere": []}


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
