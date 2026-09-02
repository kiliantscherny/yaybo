"""Check the two public sources that sit outside the land register.

Run directly - `uv run python tests/test_enrichment.py` - or under pytest.

Neither test touches the network: the Boligsiden payload is a trimmed copy of
a real response with invented figures, and the DST reply is built by hand so
the awkward part of it - a dimension the query never asked for - is present.
"""

from yaybo import store
from yaybo.enrich import boligsiden, laantype
from yaybo.register import rows as build

# Trimmed from a real api.boligsiden.dk/addresses/{uuid} reply. Same keys and
# nesting; the address, the prices and the building are invented.
PAYLOAD = {
    "slug": "proevegade-1-3-12-9999-proevekoebing",
    "isOnMarket": True,
    "addressType": "condo",
    "livingArea": 71,
    "latestValuation": 1350000,
    "coordinates": {"lat": 55.5, "lon": 12.5, "type": "EPSG4326"},
    "registrations": [
        # Deliberately oldest first, to prove the sort.
        {"date": "2021-10-14", "amount": 3000000, "area": 71, "type": "family",
         "registrationID": "r-2"},
        {"date": "2026-06-19", "amount": 4000000, "area": 80, "livingArea": 80,
         "type": "normal", "perAreaPrice": 50000, "registrationID": "r-1"},
        # No area at all: the price per square metre cannot be worked out.
        {"date": "1998-01-05", "amount": 500000, "type": "auction", "registrationID": "r-3"},
    ],
    "buildings": [{
        "buildingNumber": "1", "buildingName": "Etagebolig-bygning",
        "yearBuilt": 1970, "yearRenovated": 1992, "numberOfFloors": 5,
        "numberOfRooms": 2, "numberOfBathrooms": 1, "numberOfToilets": 1,
        "housingArea": 71, "basementArea": 800, "businessArea": 1100,
        "otherArea": 93, "totalArea": 6303, "externalWallMaterial": "Letbetonsten",
        "roofingMaterial": "Tagpap med lille hældning",
        "heatingInstallation": "Fjernvarme/blokvarme",
        "supplementaryHeating": "Ingen supplerende varme",
        "kitchenCondition": "Eget køkken med afløb",
        "bathroomCondition": "Badeværelse i enheden",
        "toiletCondition": "Vandskyllende toilet i enheden",
    }],
}


def test_sales_are_newest_first_with_a_price_per_square_metre():
    found = boligsiden.parse(PAYLOAD, "uuid-a")
    sales = found["salg"]
    assert [s["dato"] for s in sales] == ["2026-06-19", "2021-10-14", "1998-01-05"]
    # Boligsiden gave this one; the second is worked out from amount and area.
    assert sales[0]["pris_pr_m2"] == 50000
    assert sales[1]["pris_pr_m2"] == round(3000000 / 71)
    # No area, so no price per square metre - rather than a division by zero.
    assert sales[2]["pris_pr_m2"] is None
    # The kind of transfer matters: a family sale is not a market price.
    assert [s["handelstype"] for s in sales] == [
        "Almindeligt salg", "Familiehandel", "Tvangsauktion"
    ]


def test_the_bbr_record_the_land_register_never_gives():
    found = boligsiden.parse(PAYLOAD, "uuid-a")
    building = found["bygninger"][0]
    assert building["opfoerelsesaar"] == 1970
    assert building["ombygningsaar"] == 1992
    assert building["vaerelser"] == 2
    assert building["varmeinstallation"] == "Fjernvarme/blokvarme"
    assert building["ydervaeg"] == "Letbetonsten"


def test_the_property_row_takes_the_latest_sale_and_the_listing():
    row = build.bolig_row(boligsiden.parse(PAYLOAD, "uuid-a"))
    assert row["til_salg"] == "true"
    assert row["boligsiden_url"].endswith("proevegade-1-3-12-9999-proevekoebing")
    assert row["seneste_salg_dato"] == "2026-06-19"
    assert row["seneste_salg_dkk"] == 4000000
    # This is BBR's living area, not the register's "tinglyste areal".
    assert row["boligareal_m2"] == 71


def test_an_address_boligsiden_has_never_heard_of():
    assert boligsiden.parse({}, "uuid-a")["salg"] == []
    assert build.bolig_row({}) == {}
    assert build.handel_rows({}, "u", "a") == []


def test_debt_and_equity_are_totalled_from_the_charges():
    properties = [
        {"uuid": "u1", "ejendomsvurdering_dkk": "2000000"},
        {"uuid": "u2", "ejendomsvurdering_dkk": ""},   # no valuation to divide by
    ]
    charges = [
        {"ejendom_uuid": "u1", "hovedstol_dkk": 1200000},
        {"ejendom_uuid": "u1", "hovedstol_dkk": 300000},
        {"ejendom_uuid": "u2", "hovedstol_dkk": 500000},
    ]
    build.add_financials(properties, charges)
    assert properties[0]["samlet_gaeld_dkk"] == 1500000
    assert properties[0]["frivaerdi_dkk"] == 500000
    assert properties[0]["belaaningsgrad_pct"] == 75.0
    # Debt is still counted, but nothing is divided by a valuation we lack.
    assert properties[1]["samlet_gaeld_dkk"] == 500000
    assert "belaaningsgrad_pct" not in properties[1]


# One month, one loan type each side of the answer, built the way DST builds it.
def _dst(values):
    return {
        "dimension": {
            "id": ["DATA", "INDSEK", "RENTFIX", "ContentsCode", "Tid"],
            "size": [2, 1, 5, 1, 1],
            "DATA": {"category": {"index": {"AL51EFFR": 0, "AL51BIDS": 1}}},
            "INDSEK": {"category": {"index": {"1430": 0}}},
            "RENTFIX": {"category": {"index": dict(zip(laantype.RENTFIX, range(5)))}},
            "ContentsCode": {"category": {"index": {"DNRNURI": 0}}},
            "Tid": {"category": {"index": {"2025M01": 0}}},
        },
        "value": values,
    }


def test_jsonstat_is_read_by_name_not_by_position():
    """DST returns a ContentsCode dimension the query never asked for. Working
    out an offset from the order the variables were sent in only works because
    that dimension happens to have one member."""
    # Effective rates for the five types, then the five bidrag. coupon = eff - bidrag.
    table = laantype._read(_dst([4.0, 3.0, 3.5, 4.5, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0]))
    assert {code: figures["kupon_pct"] for code, figures in table["2025M01"].items()} == {
        "1M3M": 3.0, "1A": 2.0, "3A": 2.5, "5A": 3.5, "S10A": 4.0
    }
    # The effective rate and the bidrag are kept as well: the coupon is what
    # the register writes down, but those two are what a borrower pays.
    assert table["2025M01"]["1M3M"] == {
        "effektiv_rente_pct": 4.0, "bidrag_pct": 1.0, "kupon_pct": 3.0
    }


def test_the_series_is_stored_as_rows_so_an_estimate_can_be_checked():
    """A column saying "F3" with nothing behind it has to be taken on trust."""
    table = laantype._read(_dst([4.0, 3.0, 3.5, 4.5, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0]))
    rows = laantype.rate_rows(table)
    assert len(rows) == 5
    assert rows[0] == {
        "maaned": "2025M01", "rentfix_kode": "1M3M", "laantype": "F-kort",
        "effektiv_rente_pct": 4.0, "bidrag_pct": 1.0, "kupon_pct": 3.0,
    }
    # Every row must fill the columns the table declares.
    columns = {name for name, _ in store.TABLES["rentestatistik"]["columns"]}
    assert all(set(row) == columns for row in rows)


def test_a_rate_is_matched_to_the_nearest_loan_type():
    table = laantype._read(_dst([4.0, 3.0, 3.5, 4.5, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0]))
    found = laantype.classify(2.51, ["2025M01"], table)
    assert found["laantype_estimat"] == "F3"       # coupon 2.5
    assert found["laantype_afstand"] == 0.01
    assert found["laantype_kilde"] == "DST"


def test_a_rate_near_nothing_is_left_unnamed():
    table = laantype._read(_dst([4.0, 3.0, 3.5, 4.5, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0]))
    found = laantype.classify(19.0, ["2025M01"], table)
    assert found["laantype_estimat"] == ""
    assert found["laantype_afstand"] > laantype.UNCERTAIN


def test_the_registers_own_flag_settles_a_close_call():
    """F3 at 2.5 and F5 at 3.5 - a rate of 3.2 is nearer F5, but well inside
    CLOSE of nothing else. Use a rate that sits between two candidates."""
    table = laantype._read(_dst([4.0, 3.0, 3.5, 3.7, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0]))
    # coupons: F-kort 3.0, F1 2.0, F3 2.5, F5 2.7, Fastforrentet 4.0
    # 2.6 is 0.1 from F3 and 0.1 from F5 - a genuine tie.
    assert laantype.classify(2.62, ["2025M01"], table)["laantype_estimat"] == "F5"
    # The register saying the rate is fixed cannot make an F-loan fixed, and
    # Fastforrentet is not within CLOSE here, so the answer must not change.
    assert laantype.classify(2.62, ["2025M01"], table, "fast")["laantype_estimat"] == "F5"


def test_months_stop_where_the_published_series_does():
    assert laantype._months_before("2025-01-03", 3) == ["2025M01", "2024M12", "2024M11"]
    # DNRNURI does not reach back this far, so there is nothing to match against.
    assert laantype._months_before("1990-01-01", 6) == []
    assert laantype._months_before("", 6) == []


def test_no_table_declares_the_same_column_twice():
    """The register's own laantype ("obligationslaan") and the estimated
    product ("F3") are different facts and once shared a name."""
    for name, spec in store.TABLES.items():
        columns = [column for column, _ in spec["columns"]]
        assert len(columns) == len(set(columns)), f"{name} repeats a column"
        assert spec["key"] in columns, f"{name} is keyed on a column it lacks"


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
