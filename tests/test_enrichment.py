"""Check what is worked out around the register rather than read out of it.

Run directly - `uv run python tests/test_enrichment.py` - or under pytest.

Neither test touches the network: the transfers are invented, and the DST reply
is built by hand so the awkward part of it - a dimension the query never asked
for - is present.
"""

from yaybo import store
from yaybo.enrich import bbr, laantype
from yaybo.register import rows as build

# What history_rows makes of the register's "historisk adkomst" list: one entry
# per transfer, newest first. Invented people, invented prices.
ENTRIES = [
    {"ejendom_uuid": "u1", "post_nummer": 1, "adresse": "Prøvegade 1, 3. 12",
     "dato": "2026-06-19", "dokumenttype": "Endeligt skøde",
     "koebesum_dkk": 4000000, "antal_ejere": 2, "historiske_ejere": "..."},
    {"ejendom_uuid": "u1", "post_nummer": 2, "adresse": "Prøvegade 1, 3. 12",
     "dato": "2021-10-14", "dokumenttype": "Auktionsskøde",
     "koebesum_dkk": 3000000, "antal_ejere": 1, "historiske_ejere": "..."},
    # A transfer the register recorded without a price: an inheritance, say.
    {"ejendom_uuid": "u1", "post_nummer": 3, "adresse": "Prøvegade 1, 3. 12",
     "dato": "1998-01-05", "dokumenttype": "Skifteretsattest",
     "koebesum_dkk": None, "antal_ejere": 1, "historiske_ejere": "..."},
]


def test_sales_are_the_registers_own_transfers():
    sales = build.handel_rows(ENTRIES, "u1", "Prøvegade 1, 3. 12")
    assert [s["dato"] for s in sales] == ["2026-06-19", "2021-10-14", "1998-01-05"]
    assert [s["beloeb_dkk"] for s in sales] == [4000000, 3000000, None]
    # The register's own word for the document, never a vocabulary of our own.
    assert [s["handelstype"] for s in sales] == [
        "Endeligt skøde", "Auktionsskøde", "Skifteretsattest"
    ]
    # Every row is identifiable, because the table is keyed on it.
    assert [s["registrering_id"] for s in sales] == ["1", "2", "3"]


def test_there_is_a_price_against_each_area():
    """Two measures of the same flat, so two prices and never a blend.

    The plain column divides by BBR's living area, which is what a listing
    quotes; the _tinglyst one by the register's own areal.
    """
    sales = build.handel_rows(ENTRIES, "u1", "Prøvegade 1, 3. 12", 80, 100)
    assert [s["pris_pr_m2"] for s in sales] == [40000, 30000, None]
    assert [s["pris_pr_m2_tinglyst"] for s in sales] == [50000, 37500, None]
    assert all(s["areal_m2"] == 80 and s["boligareal_m2"] == 100 for s in sales)


def test_each_price_is_empty_when_its_own_area_is():
    """One area missing must not promote the other into its column."""
    only_register = build.handel_rows(ENTRIES, "u1", "a", 80)[0]
    assert only_register["pris_pr_m2_tinglyst"] == 50000
    assert only_register["pris_pr_m2"] is None

    only_bbr = build.handel_rows(ENTRIES, "u1", "a", None, 100)[0]
    assert only_bbr["pris_pr_m2"] == 40000
    assert only_bbr["pris_pr_m2_tinglyst"] is None

    for sale in build.handel_rows(ENTRIES, "u1", "a"):
        assert sale["pris_pr_m2"] is None and sale["pris_pr_m2_tinglyst"] is None
    # The register writes an area as text often enough to matter.
    text_area = build.handel_rows(ENTRIES, "u1", "a", "80 m2")[0]
    assert text_area["pris_pr_m2_tinglyst"] == 50000


def test_the_property_row_takes_the_newest_sale():
    sales = build.handel_rows(ENTRIES, "u1", "a", 80, 100)
    row = build.latest_sale_row(sales)
    assert row["seneste_salg_dato"] == "2026-06-19"
    assert row["seneste_salg_dkk"] == 4000000
    assert row["seneste_salg_pris_m2"] == 40000
    assert row["seneste_salg_pris_m2_tinglyst"] == 50000
    assert build.latest_sale_row([]) == {}


# The historisk adkomst lists previous owners only. The transfer that put the
# current owner there is on the property's own row, and leaving it out made
# every property miss its most recent sale - the one anybody actually wants.
# Dated after everything in ENTRIES, which is what the register guarantees:
# the history is who owned it *before* the owner this document put there.
CURRENT = {
    "koebesum_dkk": 4449000,
    "adkomst_dato_loebenummer": "20260715-1017732059",
    "adkomst_dokumenttype": "Skøde",
    "overtagelsesdato": "2026-09-15",
}


def test_the_adkomst_in_force_is_the_newest_sale():
    sales = build.handel_rows(ENTRIES, "u1", "a", 80, 100, current=CURRENT)
    assert len(sales) == len(ENTRIES) + 1
    newest = sales[0]
    # Dated by registration, as the history is - not by the handover date.
    assert newest["dato"] == "2026-07-15"
    assert newest["beloeb_dkk"] == 4449000
    assert newest["handelstype"] == "Skøde"
    assert newest["registrering_id"] == "20260715-1017732059"
    assert build.latest_sale_row(sales)["seneste_salg_dkk"] == 4449000


def test_an_adkomst_that_was_not_a_purchase_is_not_a_sale():
    """An inheritance or a division transfers without a price. It belongs in
    adkomsthistorik, and putting it here would invent a sale of nothing."""
    for missing in ({"koebesum_dkk": None}, {"koebesum_dkk": 0},
                    {"koebesum_dkk": 4449000}):
        assert build.handel_rows(ENTRIES, "u1", "a", current=missing) == \
            build.handel_rows(ENTRIES, "u1", "a")


def test_the_registers_document_codes_are_read_the_same_way_on_both_sides():
    """The attest says `endeligtskoede`, the history says `ENDELIGTSKOEDE`,
    and an auction comes back with an ø where the map spells oe."""
    sales = build.handel_rows(
        [{"dato": "2020-01-01", "dokumenttype": "ENDELIGTSKOEDE",
          "koebesum_dkk": 1, "post_nummer": 1},
         {"dato": "2019-01-01", "dokumenttype": "AUKTIONSSKØDE",
          "koebesum_dkk": 1, "post_nummer": 2},
         {"dato": "2018-01-01", "dokumenttype": "SKIFTERETSATTEST",
          "koebesum_dkk": 1, "post_nummer": 3}],
        "u1", "a",
    )
    assert [s["handelstype"] for s in sales] == [
        "Endeligt skøde", "Auktionsskøde", "Skifteretsattest"
    ]
    # A code with no expansion is left exactly as it came, rather than guessed
    # at: word boundaries are not recoverable from ENDELIGTSKOEDE.
    unknown = build.handel_rows(
        [{"dato": "2020-01-01", "dokumenttype": "NOGETNYT",
          "koebesum_dkk": 1, "post_nummer": 1}], "u1", "a",
    )
    assert unknown[0]["handelstype"] == "NOGETNYT"


def test_bbr_fills_the_flats_own_area_and_type():
    flat = {"boligareal_m2": 91, "boligtype": "Egentlig beboelseslejlighed"}
    assert build.bbr_row(flat) == flat
    # No key, no answer, and nothing written over what is already there.
    assert build.bbr_row({}) == {}
    assert build.bbr_row({"boligareal_m2": None}) == {}


def test_a_property_the_register_has_no_history_for():
    assert build.handel_rows([], "u1", "a") == []
    assert build.latest_sale_row([]) == {}


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
            "RENTFIX": {
                "category": {
                    "index": dict(zip(laantype.RENTFIX, range(5), strict=True))
                }
            },
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


# ── the BBR credential ──────────────────────────────────────────────────


def test_the_environment_wins_over_a_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DATAFORDELER_API_KEY=from-the-file\n", encoding="utf-8")
    monkeypatch.setenv(bbr.ENV_VAR, "from-the-environment")
    assert bbr.api_key(env) == "from-the-environment"


def test_a_dotenv_is_read_when_the_environment_is_silent(tmp_path, monkeypatch):
    monkeypatch.delenv(bbr.ENV_VAR, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\nOTHER=x\nDATAFORDELER_API_KEY=\"quoted-key\"\n", encoding="utf-8"
    )
    assert bbr.api_key(env) == "quoted-key"
    assert bbr.configured(env)


def test_no_key_anywhere_is_not_an_error(tmp_path, monkeypatch):
    """Without a key every other table still fills; only BBR stays empty."""
    monkeypatch.delenv(bbr.ENV_VAR, raising=False)
    assert bbr.api_key(tmp_path / "nothing-here") == ""
    assert not bbr.configured(tmp_path / "nothing-here")
