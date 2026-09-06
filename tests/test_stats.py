"""The figures a set of properties produces, without a database or a terminal.

Two derived fields carry most of this. The register records neither a building
nor a floor, so both are read off the address - and if they were read six
different ways in six different places, the same two flats would land in
different buildings. Everything below goes through `annotate`, once, which is
what makes "the third floor against the tenth" a question with one answer.
"""

from __future__ import annotations

from datetime import date

import pytest

from yaybo import stats

TODAY = date.today()


def flat(uuid, building, postcode, town, unit, **extra):
    row = {
        "uuid": uuid,
        "adresse": f"{building}, {unit}, {postcode} {town}",
        "lejlighed": unit,
        "boligtype": "condo",
        "boligareal_m2": 70,
        "ejendomsvurdering_dkk": 3_500_000,
        "samlet_gaeld_dkk": 1_000_000,
        "belaaningsgrad_pct": 28.6,
        "beriget": True,
    }
    row.update(extra)
    return row


@pytest.fixture
def tables():
    """One block over several floors, one neighbour, and one other town."""
    properties = [
        flat("a", "Islands Brygge 30B", "2300", "København S", "st. tv"),
        flat("b", "Islands Brygge 30B", "2300", "København S", "3. tv",
             ejendomsvurdering_dkk=4_200_000),
        flat("c", "Islands Brygge 30B", "2300", "København S", "10. th",
             ejendomsvurdering_dkk=7_000_000, beriget=False),
        flat("d", "Islands Brygge 4", "2300", "København S", "2. th"),
        flat("e", "Saxogade 10", "8600", "Silkeborg", "1. tv"),
    ]
    return {
        "ejendomme": properties,
        "handelshistorik": [
            {"ejendom_uuid": "a", "dato": "2015-04-11", "pris_pr_m2": 30_000,
             "beloeb_dkk": 2_100_000},
            {"ejendom_uuid": "b", "dato": "2015-09-02", "pris_pr_m2": 34_000,
             "beloeb_dkk": 2_380_000},
            {"ejendom_uuid": "c", "dato": "2022-06-01", "pris_pr_m2": 80_000,
             "beloeb_dkk": 5_600_000},
            {"ejendom_uuid": "e", "dato": "2022-02-01", "pris_pr_m2": 20_000,
             "beloeb_dkk": 1_400_000},
        ],
        "ejere": [
            {"ejendom_uuid": "a", "navn": "Ida", "foedselsdato": "1980-01-01"},
            {"ejendom_uuid": "a", "navn": "Bo", "foedselsdato": "1990-01-01"},
            {"ejendom_uuid": "b", "navn": "Prøvebolig A/S", "cvr": "12345678"},
        ],
        "haeftelser": [
            {"ejendom_uuid": "a", "hovedstol_dkk": 900_000, "rentesats_pct": 1.5,
             "laantype_estimat": "F3"},
            {"ejendom_uuid": "b", "hovedstol_dkk": 1_100_000, "rentesats_pct": 3.5,
             "laantype_estimat": "F3"},
        ],
        "bygninger": [
            {"ejendom_uuid": "a", "opfoerelsesaar": 1932, "vaerelser": 3},
        ],
    }


# ── the derived fields ──────────────────────────────────────────────────


def test_a_building_is_the_address_with_the_flat_taken_off():
    rows = stats.annotate([flat("a", "Islands Brygge 30B", "2300", "København S",
                                "3. tv")])
    row = rows[0]
    assert row["_bygning"] == "Islands Brygge 30B, 2300 København S"
    assert row["_postnr"] == "2300"
    assert row["_by"] == "København S"
    assert row["_vej"] == "Islands Brygge"
    assert row["_etage"] == "3"


def test_the_floor_is_read_from_the_address_when_the_register_gave_none():
    row = flat("a", "Islands Brygge 30B", "2300", "København S", "5. th")
    row["lejlighed"] = ""
    assert stats.annotate([row])[0]["_etage"] == "5"


def test_floors_are_ordered_the_way_a_building_is(tables):
    scope = stats.scope_of(tables, 'bygning:"Islands Brygge 30B, 2300 København S"')
    names = [name for name, _, _ in stats.aggregate(scope, "_etage", "vurdering")]
    assert names == ["st", "3", "10"], "alphabetically the tenth sorts before the third"


# ── choosing a set ──────────────────────────────────────────────────────


def test_a_filter_narrows_the_child_tables_too(tables):
    scope = stats.scope_of(tables, 'bygning:"Islands Brygge 30B, 2300 København S"')
    assert len(scope) == 3
    assert {row["ejendom_uuid"] for row in scope.trades} == {"a", "b", "c"}
    assert {row["ejendom_uuid"] for row in scope.owners} == {"a", "b"}


def test_two_conditions_are_both_required(tables):
    assert len(stats.scope_of(tables, "postnr:2300")) == 4
    assert len(stats.scope_of(tables, "postnr:2300 etage:3")) == 1
    assert len(stats.scope_of(tables, "postnr:2300 by:Silkeborg")) == 0


def test_a_quoted_value_survives_its_spaces(tables):
    scope = stats.scope_of(tables, 'bygning:"Islands Brygge 4, 2300 København S"')
    assert [row["uuid"] for row in scope.properties] == ["d"]


def test_choices_offer_what_is_actually_there(tables):
    towns = stats.choices(stats.annotate(tables["ejendomme"]), "_by")
    assert towns == [("København S", 4), ("Silkeborg", 1)], "commonest first"


# ── the figures ─────────────────────────────────────────────────────────


def test_a_measure_reads_from_whichever_table_holds_it(tables):
    scope = stats.scope_of(tables, "")
    assert stats.BY_KEY["vurdering"].source == "property"
    assert stats.BY_KEY["salg_m2"].source == "trade"
    assert stats.BY_KEY["alder"].source == "owner"
    assert stats.BY_KEY["hovedstol"].source == "charge"
    assert len(scope.trades) == 4


def test_rows_that_are_not_properties_are_grouped_by_their_property(tables):
    """"Average owner age by floor" only means something if this holds."""
    scope = stats.scope_of(tables, 'bygning:"Islands Brygge 30B, 2300 København S"')
    ages = dict(
        (name, value) for name, _, value in stats.aggregate(scope, "_etage", "alder")
    )
    # Two owners on the ground floor, born 1980 and 1990; the company on the
    # third has no age to average and must not become a zero.
    assert ages["st"] == pytest.approx((TODAY - date(1980, 1, 1)).days / 365.25 / 2
                                       + (TODAY - date(1990, 1, 1)).days / 365.25 / 2,
                                       rel=0.01)
    assert ages["3"] is None


def test_a_company_is_not_given_an_age(tables):
    scope = stats.scope_of(tables, "")
    figures = dict((label, value) for label, _, value in stats.owner_figures(scope))
    assert figures["Of those, companies"] == 1
    assert figures["Owners in total"] == 3


def test_the_tenth_floor_is_dearer_per_square_metre_than_the_third(tables):
    scope = stats.scope_of(tables, 'bygning:"Islands Brygge 30B, 2300 København S"')
    per_m2 = dict(
        (name, value)
        for name, _, value in stats.aggregate(scope, "_etage", "vurdering_m2")
    )
    ground, third, tenth = per_m2["st"], per_m2["3"], per_m2["10"]
    assert ground is not None and third is not None and tenth is not None
    assert tenth > third > ground


def test_how_a_measure_is_combined_can_be_chosen(tables):
    scope = stats.scope_of(tables, "postnr:2300")
    median = stats.aggregate(scope, "_postnr", "vurdering", "median")[0][2]
    total = stats.aggregate(scope, "_postnr", "vurdering", "sum")[0][2]
    count = stats.aggregate(scope, "_postnr", "vurdering", "count")[0][2]
    assert count == 4
    assert total == 3_500_000 + 4_200_000 + 7_000_000 + 3_500_000
    assert median == (3_500_000 + 4_200_000) / 2


def test_a_group_with_nothing_recorded_reports_nothing_rather_than_zero(tables):
    scope = stats.scope_of(tables, "")
    rooms = dict(
        (name, value)
        for name, _, value in stats.aggregate(scope, "_by", "vaerelser")
    )
    assert rooms["Silkeborg"] is None, "no building record is not zero rooms"


# ── over time ───────────────────────────────────────────────────────────


def test_a_series_is_whole_years(tables):
    scope = stats.scope_of(tables, "")
    series = stats.over_time(scope, "salg_m2", "median")
    assert [year for year, _, _ in series] == [2015, 2022]
    assert all(isinstance(year, int) for year, _, _ in series)


def test_a_year_holding_two_sales_reports_their_median(tables):
    scope = stats.scope_of(tables, "")
    by_year = {year: value for year, _, value in stats.over_time(scope, "salg_m2")}
    assert by_year[2015] == 32_000


def test_a_period_cuts_the_series_short(tables):
    scope = stats.scope_of(tables, "")
    recent = stats.over_time(scope, "salg_m2", "median", since=2020)
    assert [year for year, _, _ in recent] == [2022]


def test_counting_sales_is_a_measure_of_its_own(tables):
    scope = stats.scope_of(tables, "")
    counted = {year: value for year, _, value in stats.over_time(scope, "handler")}
    assert counted == {2015: 2, 2022: 2}


def test_the_overview_names_every_section(tables):
    scope = stats.scope_of(tables, "")
    sections = [name for name, _ in stats.overview(scope)]
    assert sections == ["Properties", "Owners", "Charges", "Buildings"]
    figures = dict((label, value) for label, _, value in stats.summary(scope))
    assert figures["Properties"] == 5
    assert figures["Buildings"] == 3
    assert figures["Without MitID data"] == 1


def test_every_analysis_names_measures_that_exist():
    """The screen builds its dropdowns from these, so a typo is a broken tab."""
    for analysis in stats.ANALYSES:
        for key in analysis.measures:
            assert key in stats.BY_KEY, f"{analysis.key} names an unknown measure"
        assert analysis.kind in ("summary", "time", "group")
    assert {a.kind for a in stats.ANALYSES} == {"summary", "time", "group"}
