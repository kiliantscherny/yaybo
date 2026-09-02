"""Check the whole OIO document, not just the columns one row can hold.

Run directly - `uv run python tests/test_attest_xml.py` - or under pytest.

This is what logging in actually buys: every party to every mortgage with
their own date of birth, the sub-pledges, the interest terms, and what each
easement is about. The fixture is a real document's shape with invented people
in it.
"""

import json
from pathlib import Path

from yaybo.register import attest_xml
from yaybo.register import rows as build

RAW = (Path(__file__).parent / "ejdsummarisk_sample.xml").read_text(encoding="utf-8")
RECORD = attest_xml.parse(RAW)


def test_property_details():
    ejendom = RECORD["ejendom"]
    assert ejendom["bfe_nr"] == "100001"
    assert ejendom["matrikelnr"] == "42"
    # The property's own address names its street; a party's gives only codes.
    assert (ejendom["vejnavn"], ejendom["husnr"], ejendom["etage"], ejendom["doer"]) == (
        "Prøvegade", "1", "3", "12"
    )
    assert ejendom["grund_areal_m2"] == 4200
    assert ejendom["udstykningsdato"] == "1975-05-05"
    assert ejendom["ejendomsvurdering_dkk"] == 2100000
    assert ejendom["vurderingsdato"] == "2020-01-01"
    # This one is free text under a heading, so it stays a string.
    assert ejendom["areal_m2"] == "33"


def test_a_fixed_rate_is_not_read_as_danish_thousands():
    """The regression this fixture exists for.

    The XML is xs:decimal, where "3.500" is three and a half. The rendered
    attest is Danish, where the same string is three thousand five hundred.
    Reading the first with the second's rules gives a 3500% mortgage.
    """
    charge = RECORD["haeftelser"][0]
    assert charge["rentetype"] == "fast"
    assert charge["rentesats_pct"] == 3.5
    assert charge["rente_foreloebig"] == "false"


def test_a_variable_rate_keeps_its_reference_and_margin():
    """"CITA6 + 0.45" is a different loan from a flat 2.74 that stands at the
    same number today, so all three parts are kept."""
    charge = RECORD["haeftelser"][1]
    assert charge["rentetype"] == "variabel"
    assert charge["rentesats_pct"] == 2.74
    assert charge["reference_rente"] == "CITA6"
    assert charge["rente_margin_pct"] == 0.45
    assert charge["saerlige_vilkaar"] == ["refinansiering", "inkonvertibel"]
    assert charge["laantype"] == "obligationslaan"


def test_a_charge_names_both_ends_and_its_sub_pledge():
    charge = RECORD["haeftelser"][0]
    assert charge["hovedstol_dkk"] == 26000
    assert charge["prioritet"] == "18"
    assert charge["senest_paategnet"] == "2021-02-02"
    assert charge["konverteret_pantebrev"] == "true"
    assert charge["kreditorer"][0]["cvr"] == "11223344"
    # A debtor is a person, and this is the date of birth the login is for.
    assert charge["debitorer"][0]["foedselsdato"] == "1957-10-02"
    assert charge["meddelelseshavere"][0]["navn"] == "E/F PRØVEGÅRDEN"
    assert len(charge["respekterer"]) == 2
    pledge = charge["underpant"][0]
    assert pledge["beloeb_dkk"] == 26000
    assert pledge["panthavere"][0]["cvr"] == "55667788"
    assert charge["tekst"] == "Prøvetekst om fællesareal i stuen"


def test_an_easement_says_what_it_is_about():
    easement = RECORD["servitutter"][0]
    # Collected from any element whose name ends in "Kode", so a subject the
    # register adds later is picked up without naming it here.
    assert easement["indhold"] == ["andet", "vej"]
    assert easement["betydning_for_vaerdi"] == "true"
    assert easement["ogsaa_lyst_paa"] == "150"
    assert easement["tekst"].startswith("Dok om vej mv")

    vedtaegt = RECORD["servitutter"][1]
    assert vedtaegt["paataleberettigede"][0]["cvr"] == "55667788"
    assert vedtaegt["tekst"].startswith("Vedtægter")
    assert vedtaegt["afgift_dkk"] == 1850


def test_every_party_gets_a_row_with_a_role():
    rows = build.party_rows(RECORD, "uuid-1")
    roles = {}
    for row in rows:
        roles.setdefault(row["rolle"], []).append(row)
    assert set(roles) == {
        "adkomsthaver", "kreditor", "debitor", "meddelelseshaver", "underpanthaver",
        "paataleberettiget",
    }
    assert len(roles["adkomsthaver"]) == 3
    assert len(roles["debitor"]) == 2
    # A person carries a date of birth, a company a CVR number, never both.
    for row in rows:
        assert not (row["foedselsdato"] and row["cvr"])
    assert all(row["ejendom_uuid"] == "uuid-1" for row in rows)


def test_sub_pledges_get_their_own_rows():
    rows = build.underpant_rows(RECORD, "uuid-1")
    assert len(rows) == 1
    assert rows[0]["beloeb_dkk"] == 26000
    assert rows[0]["haeftelse_uuid"] == RECORD["haeftelser"][0]["dokument_uuid"]
    assert rows[0]["panthavere"] == "E/F PRØVEGÅRDEN"


def test_charge_rows_prefer_the_xml_and_fall_back_to_the_public_record():
    rich = build.haeftelse_rows({"adresse": "Prøvegade 1"}, "uuid-1", RECORD)
    assert rich[0]["hovedstol_dkk"] == 26000
    assert rich[0]["hovedstol"] == "26.000 DKK"  # written back the register's way
    assert rich[0]["antal_underpant"] == 1

    # Logged out there is no XML, only the public lookup's own shape.
    public = {
        "adresse": "Prøvegade 1",
        "haeftelser": [{"alias": "20250101-1000000002", "haeftelsestype": "realkreditpantebrev",
                        "hovedstol": "2.000.000 DKK", "rente": "2,74", "uuid": "x"}],
    }
    plain = build.haeftelse_rows(public, "uuid-1")
    assert plain[0]["hovedstol"] == "2.000.000 DKK"
    assert plain[0]["dokumenttype"] == "realkreditpantebrev"


def test_outline_keeps_what_parse_never_named():
    """The columns take what is worth filtering on; the outline keeps the rest,
    so a field nobody has named yet is still there to be found."""
    whole = attest_xml.outline(RAW)
    charge = whole["EjendomSummarisk"]["HaeftelseSummariskSamling"]["HaeftelseSummarisk"][1]
    margin = charge["HaeftelseRente"]["HaeftelseRenteVariabel"]["HaeftelseReferenceRente"][
        "ReferenceRenteTillaegFradrag"
    ]
    # parse() reads the margin as a number and drops the two indicators.
    assert margin["FastVariabelIndikator"] == "variabel"
    assert margin["TillaegFradragIndikator"] == "tillaeg"
    # Repeated siblings become a list; a single one stays scalar.
    assert isinstance(
        whole["EjendomSummarisk"]["ServitutSummariskSamling"]["ServitutSummarisk"], list
    )
    assert json.dumps(whole)  # it has to survive the trip into a JSON column


def test_a_payload_that_is_not_the_xml_we_expect():
    assert attest_xml.parse("<html><body>nope</body></html>") == {}
    assert attest_xml.parse("not xml at all") == {}
    assert attest_xml.outline("not xml at all") is None


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
