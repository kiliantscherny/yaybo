"""The andelsboligbog: the register's second book, about a different thing.

Run directly - `uv run python tests/test_andele.py` - or under pytest.

Nothing here touches the network. The record below has the shape and the field
names of a real reply from `unsecrest/andelsoeg/hentandelsboligbog`; the
address, the amounts and the people are invented.

What is being checked is mostly the seam between the two books, because that is
where this can go wrong quietly: a share counted as a property, a building
dropped because it does not match a door, or a building's sale price recorded
as a flat's.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from yaybo import pipeline, store
from yaybo.pipeline import describe, narrow
from yaybo.register import rows as build
from yaybo.register.client import ANDELSBOG
from yaybo.screens.search import _split_books, _tally

# One share's page of the andelsboligbog. Every key the real reply has, and
# nothing it does not: no valuation, no matrikel, no area, no easements and no
# owner. That absence is the point of most of these tests.
RECORD = {
    "statuskode": 0,
    "statustekst": None,
    "uuid": "andel-1",
    "adresse": "Prøvegade 1, ST. TH, 9999 Prøveby",
    "kommuneVej": {"kommuneKode": "0999", "vejKode": "1234"},
    "haeftelser": [
        {
            "alias": "04.03.2024-1000000001",
            "version": "1",
            "prioritet": "1",
            "uuid": "doc-1",
            "haeftelsestype": "Ejerpantebrev",
            "hovedstol": "1.500.000 DKK",
            "rente": "",
            "fastvariabel": "variabel",
            "kreditorer": ["Ida Testesen", "Ole Prøvesen"],
        },
        {
            "alias": "05.03.2024-1000000002",
            "version": "1",
            "prioritet": "2",
            "uuid": "doc-2",
            "haeftelsestype": "Afgiftspantebrev",
            "hovedstol": "250.000 DKK",
            "rente": "2,5",
            "fastvariabel": "fast",
            "kreditorer": [],
        },
    ],
    "meddelelser": None,
}

# No share sampled from the live register carried a notice, so this one is
# built from the fields the register's own public view of a share reads. It is
# the only place this book names anyone but a creditor.
NOTICE = {
    "alias": "11.03.2024-1000000009",
    "version": "1",
    "uuid": "med-1",
    "prioritet": "1",
    "dokumenttype": "Konkursdekret",
    "afgoerelsesdato": "11.03.2024",
    "debitorer": ["Ida Testesen"],
    "disponenter": ["Kurator Prøvesen"],
    "tillaegstekst": "Skifteretten har noteret konkurs.",
}

BUILDING = {"uuid": "ejd-1", "adresse": "Prøvegade 1, 9999 Prøveby", "bog": "Tingbog"}
SHARE = {"uuid": "andel-1", "adresse": RECORD["adresse"], "bog": ANDELSBOG}


def test_a_share_is_read_into_a_row():
    row = build.andel_row(RECORD, "andel-1", BUILDING["uuid"], BUILDING["adresse"])
    assert row["uuid"] == "andel-1"
    assert row["lejlighed"] == "ST. TH"
    assert row["kommunekode"] == "0999"
    assert row["vejkode"] == "1234"
    assert row["antal_haeftelser"] == 2
    # The join that makes a share worth having: without it there is no
    # valuation and no association's mortgage anywhere in reach.
    assert row["ejendom_uuid"] == "ejd-1"
    assert row["bygning_adresse"] == "Prøvegade 1, 9999 Prøveby"


def test_a_share_with_no_building_found_still_makes_a_row():
    """More than one property at the address, or none, leaves the join empty
    rather than guessing which building a share belongs to."""
    row = build.andel_row(RECORD, "andel-1")
    assert row["ejendom_uuid"] == ""
    assert row["bygning_adresse"] == ""


def test_charges_read_the_same_fields_a_property_does():
    charges = build.andel_haeftelse_rows(RECORD, "andel-1")
    assert [c["dokumenttype"] for c in charges] == ["Ejerpantebrev", "Afgiftspantebrev"]
    assert charges[0]["andel_uuid"] == "andel-1"
    assert charges[0]["dato_loebenummer"] == "04.03.2024-1000000001"
    assert charges[0]["kreditorer"] == "Ida Testesen; Ole Prøvesen"
    # The amount arrives formatted, exactly as it does on the public half of
    # the tingbog, and is typed on the way into the database rather than here.
    assert charges[0]["hovedstol_dkk"] == "1.500.000 DKK"
    assert charges[1]["kreditorer"] == ""


def test_a_share_with_nothing_registered_against_it_has_no_charges():
    assert build.andel_haeftelse_rows({"adresse": "x"}, "andel-2") == []


def test_a_notice_is_where_this_book_names_somebody():
    """The andelsboligbog has no owner register. A notice is the nearest it
    comes to naming who holds a share, and it only appears when something has
    happened to them."""
    record = {**RECORD, "meddelelser": [NOTICE]}
    notices = build.andel_meddelelse_rows(record, "andel-1")
    assert len(notices) == 1
    notice = notices[0]
    assert notice["andel_uuid"] == "andel-1"
    assert notice["dokumenttype"] == "Konkursdekret"
    assert notice["debitorer"] == "Ida Testesen"
    assert notice["disponenter"] == "Kurator Prøvesen"
    # The register writes dates three ways and this one is unknown, so it is
    # normalised rather than trusted.
    assert notice["afgoerelsesdato"] == "2024-03-11"
    assert build.andel_row(record, "andel-1")["antal_meddelelser"] == 1


def test_a_notice_missing_everything_optional_still_makes_a_row():
    """Built from the register's labels rather than an observed payload, so
    every field has to tolerate being absent."""
    bare = build.andel_meddelelse_rows(
        {"adresse": "x", "meddelelser": [{"alias": "01.01.2024-1"}]}, "andel-2"
    )
    assert bare[0]["debitorer"] == ""
    assert bare[0]["disponenter"] == ""
    assert bare[0]["afgoerelsesdato"] == ""


def test_no_notices_is_the_ordinary_case():
    assert build.andel_meddelelse_rows(RECORD, "andel-1") == []
    assert build.andel_row(RECORD, "andel-1")["antal_meddelelser"] == 0


def test_debt_is_totalled_but_never_divided():
    """A share gets a total and no loan-to-value: both of the derived figures
    a property gets divide by the public valuation, and there is none."""
    andele = [build.andel_row(RECORD, "andel-1")]
    build.add_andel_debt(andele, build.andel_haeftelse_rows(RECORD, "andel-1"))
    assert andele[0]["samlet_gaeld_dkk"] == 1_750_000
    assert "belaaningsgrad_pct" not in andele[0]
    assert "frivaerdi_dkk" not in andele[0]


def test_a_share_owing_nothing_totals_zero_rather_than_nothing():
    andele = [build.andel_row({"adresse": "x"}, "andel-2")]
    build.add_andel_debt(andele, [])
    assert andele[0]["samlet_gaeld_dkk"] == 0


# Boligsiden reports the building's own sale against every door in the block -
# the same date and amount on all of them - and divides it by each flat's area
# into a price per square metre that describes nothing. A share is never sold
# as real property, so there is no flat-level sale for it to be confused with.
BOLIG = {
    "adresse_uuid": "dawa-1",
    "boligtype": "cooperative",
    "boligareal_m2": 77,
    "boligsiden_vurdering_dkk": None,
    "til_salg": "false",
    "boligsiden_url": "https://example.invalid/proevegade-1-st-th",
    "breddegrad": 55.5,
    "laengdegrad": 12.5,
    "salg": [{"dato": "1999-04-27", "beloeb_dkk": 8_800_000, "pris_pr_m2": 114_286}],
}


def test_the_buildings_sale_is_kept_off_the_shares_row():
    row = build.andel_bolig_row(BOLIG)
    assert row["boligareal_m2"] == 77
    assert row["boligtype"] == "cooperative"
    for column in ("seneste_salg_dato", "seneste_salg_dkk", "seneste_salg_pris_m2"):
        assert column not in row
    # And the table has nowhere to put one even if a caller tried.
    columns = dict(store.TABLES["andele"]["columns"])
    assert not [name for name in columns if name.startswith("seneste_salg")]


def test_a_property_keeps_its_sale():
    """The same figures are a fact about the building, and are still stored as
    one - on the ejendomme row the share joins to."""
    assert build.bolig_row(BOLIG)["seneste_salg_dkk"] == 8_800_000


def test_narrowing_to_a_flat_keeps_the_building_it_is_in():
    """The two books are not competing answers. Dropping the association's
    property because it has no door on it would throw away the valuation and
    the association's own mortgages, which is most of the point."""
    kept, warning = narrow([BUILDING, SHARE], "st", "th")
    assert kept == [BUILDING, SHARE]
    # "No separately registered unit" is true of the tingbog and misleading on
    # its own: the flat is separately registered, in the other book.
    assert warning == ""


def test_a_flat_the_second_book_does_not_hold_still_warns():
    """A share only enters the book once something is registered against it,
    so a door missing from it is not a door that is not an andel."""
    kept, warning = narrow([BUILDING, SHARE], "4", "tv")
    assert BUILDING in kept
    assert "no separately registered unit" in warning


def test_a_building_query_narrows_nothing():
    assert narrow([BUILDING, SHARE], "", "") == ([BUILDING, SHARE], "")


def test_narrowing_without_the_second_book_behaves_as_before():
    kept, warning = narrow([BUILDING], "st", "th")
    assert kept == [BUILDING]
    assert "no separately registered unit" in warning


class _Api:
    """Enough of Tinglysning to drive both_books."""

    def __init__(self, andele=None, fails=False):
        self._andele, self._fails = andele or [], fails

    def find_units(self, address):
        return [BUILDING]

    def find_andele(self, address):
        if self._fails:
            raise RuntimeError("the register said nothing")
        return self._andele


def test_both_books_merges_them():
    assert pipeline.both_books(_Api([SHARE]), {}) == [BUILDING, SHARE]


def test_both_books_can_be_turned_off():
    assert pipeline.both_books(_Api([SHARE]), {}, andele_on=False) == [BUILDING]


def test_a_silent_second_book_thins_the_answer_rather_than_ending_it():
    """The tingbog has already answered. Losing the andelsboligbog is worth a
    thinner result, not a failed lookup."""
    assert pipeline.both_books(_Api(fails=True), {}) == [BUILDING]


def test_the_two_are_counted_separately_not_added_up():
    """A co-op block is one property and a dozen shares. Thirteen of something
    is not a thing that exists."""
    assert describe([BUILDING, SHARE]) == "1 property and 1 co-op share"
    assert describe([BUILDING, SHARE, SHARE]) == "1 property and 2 co-op shares"
    assert describe([BUILDING]) == "1 property"
    assert describe([SHARE]) == "1 co-op share"
    assert describe([]) == "0 properties"


def test_the_search_screen_says_the_same_thing_in_danish():
    assert _split_books([BUILDING, SHARE, SHARE]) == (1, 2)
    assert _tally(1, 2) == "1 ejendom + 2 andele"
    assert _tally(1, 1) == "1 ejendom + 1 andel"
    assert _tally(3, 0) == "3 ejendomme"
    assert _tally(0, 4) == "4 andele"


def _one(db, sql):
    row = db.sql(sql).fetchone()
    assert row is not None, sql
    return row[0]


def test_the_two_tables_survive_a_round_trip():
    andele = [build.andel_row(RECORD, "andel-1", "ejd-1", BUILDING["adresse"])]
    charges = build.andel_haeftelse_rows(RECORD, "andel-1")
    build.add_andel_debt(andele, charges)

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "andele.duckdb"
        written = store.save(path, {"andele": andele, "andel_haeftelser": charges})
        assert written["andele"] == 1
        assert written["andel_haeftelser"] == 2

        with duckdb.connect(str(path), read_only=True) as db:
            # Typed on the way in, so the formatted amount can be summed.
            assert _one(db, "SELECT sum(hovedstol_dkk) FROM andel_haeftelser") == 1750000
            assert _one(db, "SELECT samlet_gaeld_dkk FROM andele") == 1750000
            assert _one(db, "SELECT rentesats_pct FROM andel_haeftelser "
                            "WHERE dokument_uuid = 'doc-2'") == 2.5
            # The join the whole table exists for.
            assert _one(
                db,
                "SELECT a.lejlighed FROM andele a "
                "JOIN andel_haeftelser h ON h.andel_uuid = a.uuid "
                "WHERE h.dokument_uuid = 'doc-1'",
            ) == "ST. TH"


def test_notices_round_trip_and_are_keyed_by_their_registration():
    """No document uuid to key on the way a charge has - the date and serial
    is what identifies one registration."""
    notices = build.andel_meddelelse_rows({**RECORD, "meddelelser": [NOTICE]}, "andel-1")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "notices.duckdb"
        written = store.save(path, {"andel_meddelelser": notices})
        assert written["andel_meddelelser"] == 1
        with duckdb.connect(str(path), read_only=True) as db:
            assert _one(db, "SELECT debitorer FROM andel_meddelelser") == "Ida Testesen"
            assert str(_one(db, "SELECT afgoerelsesdato FROM andel_meddelelser")) == (
                "2024-03-11"
            )
        assert store.TABLES["andel_meddelelser"]["pk"] == [
            "andel_uuid", "dato_loebenummer"
        ]


def test_re_fetching_a_share_replaces_its_charges():
    """Same rule as a property: the database holds the latest reading, not a
    history of readings."""
    charges = build.andel_haeftelse_rows(RECORD, "andel-1")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "andele.duckdb"
        store.save(path, {"andel_haeftelser": charges})
        store.save(path, {"andel_haeftelser": charges[:1]})
        with duckdb.connect(str(path), read_only=True) as db:
            assert _one(db, "SELECT count(*) FROM andel_haeftelser") == 1


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
