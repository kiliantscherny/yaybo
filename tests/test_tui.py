"""Walk every screen of the TUI against a database built here, offline.

Not a test of what the screens look like - of whether they can be built and
moved between at all. A Textual screen fails at runtime, on mount, in a way no
import catches, so the cheapest useful check is to open each one and see that
it renders the rows it was given.

Nothing here touches the network: XDG_CONFIG_HOME is pointed somewhere empty so
no cached session is found, and every screen that would fetch is left alone.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from yaybo import store

SAMPLE = {
    "ejendomme": [
        {
            "uuid": "u1",
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "lejlighed": "1. tv",
            "ejendomstype": "Ejerlejlighed",
            "boligtype": "condo",
            "boligareal_m2": 75,
            "areal_m2": 72,
            "bfe_nr": "1234567",
            "ejerlejlighedsnr": "3",
            "fordelingstal": "75/2000",
            "matrikel": "12ab",
            "landsejerlav": "Prøveby",
            "kommune": "Prøve",
            "ejendomsvurdering_dkk": 2000000,
            "grundvaerdi_dkk": 500000,
            "vurderingsdato": "2024-01-01",
            "boligsiden_vurdering_dkk": 2900000,
            "samlet_gaeld_dkk": 1000000,
            "frivaerdi_dkk": 1000000,
            "belaaningsgrad_pct": 50.0,
            "seneste_salg_dato": "2019-04-11",
            "seneste_salg_dkk": 2500000,
            "seneste_salg_pris_m2": 33333,
            "til_salg": "false",
            "antal_haeftelser": 1,
            "antal_servitutter": 1,
        }
    ],
    "ejere": [
        {
            "ejendom_uuid": "u1",
            "nummer": 1,
            "navn": "Ida Testesen",
            "foedselsdato": "1985-03-02",
            "andel": "1/1",
        }
    ],
    "haeftelser": [
        {
            "ejendom_uuid": "u1",
            "dokument_uuid": "d1",
            "dokument_version": "1",
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "dato_loebenummer": "01.05.2019-1001",
            "prioritet": 1,
            "dokumenttype": "Realkreditpantebrev",
            "hovedstol_dkk": 1000000,
            "rentesats_pct": 1.2,
            "rentetype": "Variabel",
            "laantype_estimat": "F3",
            "kreditorer": "Prøve Realkredit A/S",
            "tinglysningsdato": "2019-05-02",
        }
    ],
    "servitutter": [
        {
            "ejendom_uuid": "u1",
            "dokument_uuid": "d2",
            "dokument_version": "1",
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "dato_loebenummer": "14.02.1962-2002",
            "prioritet": 2,
            "dokumenttype": "Servitut",
            "tekst": "Dok om forsynings-/afløbsledninger mv.",
            "paataleberettigede": "Prøve Kommune",
            "tinglysningsdato": "1962-02-14",
        }
    ],
    "dokument_parter": [
        {
            "ejendom_uuid": "u1",
            "dokument_uuid": "d1",
            "dokumentart": "haeftelse",
            "rolle": "debitor",
            "nummer": 1,
            "navn": "Ida Testesen",
            "foedselsdato": "1985-03-02",
        }
    ],
    "underpant": [
        {
            "ejendom_uuid": "u1",
            "haeftelse_uuid": "d1",
            "dokument_uuid": "d3",
            "rettighed_uuid": "r3",
            "dato_loebenummer": "01.11.2021-3003",
            "beloeb_dkk": 400000,
            "prioritet": 1,
            "panthavere": "Prøve Bank A/S",
        }
    ],
    "handelshistorik": [
        {
            "ejendom_uuid": "u1",
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "dato": "2014-06-01",
            "registrering_id": "100001",
            "beloeb_dkk": 1800000,
            "areal_m2": 75,
            "pris_pr_m2": 24000,
            "handelstype": "Almindeligt salg",
        },
        {
            "ejendom_uuid": "u1",
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "dato": "2019-04-11",
            "registrering_id": "100002",
            "beloeb_dkk": 2500000,
            "areal_m2": 75,
            "pris_pr_m2": 33333,
            "handelstype": "Almindeligt salg",
        },
    ],
    "bygninger": [
        {
            "ejendom_uuid": "u1",
            "adresse": "Prøvegade 1, 9999 Prøveby",
            "bygning_nr": "1",
            "bygningstype": "Etageboligbebyggelse",
            "opfoerelsesaar": 1932,
            "etager": 5,
            "vaerelser": 3,
            "boligareal_m2": 75,
            "ydervaeg": "Mursten",
            "tagdaekning": "Tegl",
            "varmeinstallation": "Fjernvarme",
        }
    ],
    "adkomsthistorik": [
        {
            "ejendom_uuid": "u1",
            "post_nummer": 1,
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "dato": "2014-06-01",
            "dokumenttype": "Skøde",
            "koebesum_dkk": 1800000,
            "antal_ejere": 1,
            "historiske_ejere": "Adkomsthavere:\nOle Prøvesen",
        }
    ],
    "adkomsthistorik_ejere": [
        {
            "ejendom_uuid": "u1",
            "post_nummer": 1,
            "dato": "2014-06-01",
            "nummer": 1,
            "navn": "Ole Prøvesen",
            "foedselsdato": "1970-01-01",
        }
    ],
    "attester": [
        {
            "ejendom_uuid": "u1",
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "format": "xml",
            "dokument": "<ejendom>ingenting</ejendom>",
            "dokument_json": '{"ejendom": "ingenting"}',
        }
    ],
    # The other book. Joined to the property above, the way a co-op flat joins
    # to the building its association owns.
    "andele": [
        {
            "uuid": "a1",
            "adresse": "Prøvegade 1, ST. TH, 9999 Prøveby",
            "lejlighed": "ST. TH",
            "kommunekode": "0999",
            "vejkode": "1234",
            "ejendom_uuid": "u1",
            "bygning_adresse": "Prøvegade 1, 9999 Prøveby",
            "antal_haeftelser": 1,
            "samlet_gaeld_dkk": 1500000,
            "boligtype": "cooperative",
            "boligareal_m2": 77,
            "til_salg": "false",
        }
    ],
    "andel_meddelelser": [
        {
            "andel_uuid": "a1",
            "dato_loebenummer": "11.03.2024-1000000009",
            "adresse": "Prøvegade 1, ST. TH, 9999 Prøveby",
            "prioritet": 1,
            "dokumenttype": "Konkursdekret",
            "afgoerelsesdato": "2024-03-11",
            "debitorer": "Ida Testesen",
            "disponenter": "Kurator Prøvesen",
            "tillaegstekst": "Skifteretten har noteret konkurs.",
        }
    ],
    "andel_haeftelser": [
        {
            "andel_uuid": "a1",
            "dokument_uuid": "ad1",
            "dokument_version": "1",
            "adresse": "Prøvegade 1, ST. TH, 9999 Prøveby",
            "dato_loebenummer": "04.03.2024-1000000001",
            "prioritet": 1,
            "dokumenttype": "Ejerpantebrev",
            "hovedstol": "1.500.000 DKK",
            "hovedstol_dkk": "1.500.000 DKK",
            "rentetype": "variabel",
            "kreditorer": "Ida Testesen",
        }
    ],
}


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A database with one property in it, and no cached login anywhere."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = tmp_path / "test.duckdb"
    store.save(path, SAMPLE)
    return path


def test_store_reads_back_what_it_wrote(database):
    held = store.library(database)
    assert len(held) == 1
    assert held[0]["adresse"].startswith("Prøvegade 1")
    assert held[0]["antal_ejere"] == 1
    assert "Ida Testesen" in held[0]["ejere"]

    tables = store.property_tables(database, "u1")
    assert set(tables) >= {"ejendomme", "ejere", "haeftelser", "handelshistorik"}
    assert tables["haeftelser"][0]["laantype_estimat"] == "F3"


def test_query_is_read_only(database):
    columns, rows = store.run_query(database, "SELECT adresse FROM ejendomme")
    assert columns == ["adresse"]
    assert len(rows) == 1
    with pytest.raises(store.QueryError):
        store.run_query(database, "DELETE FROM ejendomme")


def test_exports_every_format(database, tmp_path):
    from yaybo import export

    tables = store.everything(database)
    workbook = export.export_xlsx(tables, "prøve", outdir=tmp_path)
    stored = export.export_duckdb(tables, "prøve", outdir=tmp_path)
    assert workbook is not None and workbook.exists()
    assert stored is not None and stored.exists()
    written = export.export_csv(tables, "prøve", outdir=tmp_path)
    # One file per table that has rows in it; rentestatistik has none here.
    filled = [name for name, rows in tables.items() if rows]
    assert len(written) == len(filled)
    assert all(path.exists() for path in written)
    # The second register travels with the rest rather than being a TUI-only
    # view of the database.
    assert {"andele", "andel_haeftelser"} <= set(filled)
    assert any("andel_haeftelser" in path.name for path in written)


def test_every_screen_opens(database):
    """Open the library, each peer screen, and one property, and look at them."""
    from textual.widgets import TabbedContent

    from yaybo.app import YayboApp
    from yaybo.screens.library import LibraryScreen
    from yaybo.screens.property import PropertyScreen
    from yaybo.screens.queue import QueueScreen
    from yaybo.screens.search import SearchScreen
    from yaybo.screens.sql import SqlScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, LibraryScreen)
            assert len(app.screen.shown) == 1

            # The library focuses its table, so the single-key bindings work
            # from there. On the other screens focus is in a text box, where a
            # letter has to mean the letter - hence the actions below.
            await pilot.press("slash")
            await pilot.pause()
            assert isinstance(app.screen, SearchScreen)

            app.action_queue()
            await pilot.pause()
            assert isinstance(app.screen, QueueScreen)

            app.action_sql()
            await pilot.pause()
            assert isinstance(app.screen, SqlScreen)
            await pilot.press("ctrl+r")
            await pilot.pause(0.5)
            assert app.screen.rows, "the default snippet returned nothing"

            app.action_library()
            await pilot.pause()
            assert isinstance(app.screen, LibraryScreen)

            app.push_screen(PropertyScreen("u1"))
            await pilot.pause(0.5)
            screen = app.screen
            assert isinstance(screen, PropertyScreen)
            assert screen.tables["ejere"][0]["navn"] == "Ida Testesen"
            # Two recorded sales is enough to plot, and the timeline should
            # carry every kind of event the sample has.
            assert len(screen._timeline()) >= 6
            for tab in ("tab-haeftelser", "tab-timeline", "tab-chart",
                        "tab-bygning", "tab-dokument"):
                screen.query_one("#property-tabs", TabbedContent).active = tab
                await pilot.pause()

    if os.environ.get("YAYBO_SKIP_TUI"):
        pytest.skip("TUI walk skipped by request")
    asyncio.run(walk())


def _stub_search(monkeypatch, units):
    """Stand in for DAWA and the register, and record what each was asked."""
    from yaybo.pipeline import Bundle
    from yaybo.screens import search as search_module

    address = {
        "tekst": "Prøvegade 1, 1. tv, 9999 Prøveby",
        "vejnavn": "Prøvegade",
        "husnummer": "1",
        "postnummer": "9999",
        "etage": "1",
        "doer": "tv",
    }
    asked: dict = {}

    def fake_autocomplete(query, limit=12):
        asked["query"] = query
        return [address]

    def fake_units_at(api, given):
        asked["address"] = given
        return units, ""

    def fake_fetch(api, given, given_units, **options):
        asked["units"] = given_units
        return Bundle(address=given, units=given_units, tables=SAMPLE)

    monkeypatch.setattr(search_module, "autocomplete", fake_autocomplete)
    monkeypatch.setattr(search_module.pipeline, "units_at", fake_units_at)
    monkeypatch.setattr(search_module.pipeline, "fetch", fake_fetch)
    return address, asked


async def _type_address(pilot, screen, text="Prøvegade 1"):
    from textual.widgets import Input

    screen.query_one("#search-input", Input).value = text
    await pilot.pause(0.6)


def test_search_offers_the_building_before_its_flats(tmp_path, monkeypatch):
    """One DAWA match for a flat becomes two rows: the building, then the flat.

    Asked about a house number, DAWA answers with its flats. The register is
    searched at building level whichever of them is picked, so the building has
    to be on the list in its own right - it is nearly always the row meant.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    unit = {"uuid": "u1", "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby"}
    _stub_search(monkeypatch, [unit])

    from textual.widgets import OptionList

    from yaybo.app import YayboApp
    from yaybo.screens.search import SearchScreen

    async def walk() -> None:
        app = YayboApp(database=tmp_path / "empty.duckdb")
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, SearchScreen)
            await _type_address(pilot, app.screen)

            rows = app.screen.matches
            assert len(rows) == 2, "expected a building row and a flat row"
            assert rows[0]["tekst"] == "Prøvegade 1, 9999 Prøveby"
            assert not rows[0]["etage"] and not rows[0]["doer"]
            assert rows[1]["etage"] == "1"

            # The list itself is in two labelled sections, so a heading sits
            # above each kind and the indices no longer line up with `matches`.
            from yaybo.screens.search import BUILDINGS, UNITS

            shown = app.screen.rows
            assert shown[0] == BUILDINGS and shown[2] == UNITS
            assert shown[1] is rows[0] and shown[3] is rows[1]
            # A freshly filled list must arrive with a cursor, or the first key
            # aimed at it is swallowed - and it must not arrive on a heading.
            listing = app.screen.query_one("#search-matches", OptionList)
            assert listing.highlighted == 1 == app.screen._first_choosable()

    asyncio.run(walk())


def test_search_queues_the_ticked_property_and_fetches_it(tmp_path, monkeypatch):
    """Pick one flat, and the single result arrives ticked so f just works.

    f hands the work to the application's queue rather than doing it here. The
    screen stays where it is and stays usable, which is the point: a building
    of thirty flats used to mean thirty properties' worth of waiting on this
    screen before anything else could be looked up.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    database = tmp_path / "fetched.duckdb"
    unit = {"uuid": "u1", "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby"}
    address, asked = _stub_search(monkeypatch, [unit])

    from textual.widgets import OptionList, SelectionList

    from yaybo import fetching
    from yaybo.app import YayboApp
    from yaybo.screens.search import SearchScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            screen = app.screen
            # Narrowed the way the other walks do it, so the type checker knows
            # what the screen is and the test says what it expects.
            assert isinstance(screen, SearchScreen)
            await _type_address(pilot, screen)

            # The flat itself, floor and door intact - found by identity
            # rather than by index, because headings sit between the sections.
            matches = screen.query_one("#search-matches", OptionList)
            matches.focus()
            matches.highlighted = screen.rows.index(screen.matches[1])
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert asked["address"] == address

            listing = screen.query_one("#search-units", SelectionList)
            assert listing.selected == [0], "a lone property should arrive ticked"

            await pilot.press("f")
            await pilot.pause()
            # Handed over, not waited on.
            assert isinstance(app.screen, SearchScreen), "f should not move screen"
            assert app.fetching.jobs, "nothing reached the queue"
            assert listing.selected == [], "queued rows should not stay ticked"

            for _ in range(60):
                await pilot.pause(0.1)
                if not app.fetching.active:
                    break

            assert asked["units"] == [unit]
            job = app.fetching.jobs[0]
            assert job.state == fetching.DONE, job.note
            assert job.rows, "the job finished without writing anything"

        assert len(store.library(database)) == 1

    asyncio.run(walk())


def test_a_takes_the_whole_building_and_ticks_everything(tmp_path, monkeypatch):
    """`a` on the address list means "everything here", at either step.

    On the address list it drops the floor - so the register is asked about the
    building rather than the one flat - and arrives with every property ticked.
    `n` then clears them, and `f` refuses to fetch nothing.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    units = [
        {"uuid": f"u{n}", "adresse": f"Prøvegade 1, {n}. tv, 9999 Prøveby"}
        for n in range(1, 4)
    ]
    _stub_search(monkeypatch, units)

    from textual.widgets import OptionList, SelectionList

    from yaybo.app import YayboApp
    from yaybo.screens.search import SearchScreen

    async def walk() -> None:
        app = YayboApp(database=tmp_path / "building.duckdb")
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SearchScreen)
            await _type_address(pilot, screen)

            # ↓ out of the text box, then `a`. While the box has focus a letter
            # has to stay a letter, or "Prøvegade 30A" could not be typed.
            await pilot.press("down")
            await pilot.pause(0.2)
            assert isinstance(screen.focused, OptionList)
            await pilot.press("a")
            await pilot.pause(0.5)

            asked_about = screen.address
            assert asked_about is not None
            assert asked_about["etage"] == "" and asked_about["doer"] == ""
            assert asked_about["tekst"] == "Prøvegade 1, 9999 Prøveby"

            listing = screen.query_one("#search-units", SelectionList)
            assert sorted(listing.selected) == [0, 1, 2], "the lot should be ticked"

            await pilot.press("n")
            await pilot.pause(0.2)
            assert listing.selected == []

            # The very first space must tick a row, not just place a cursor.
            await pilot.press("space")
            await pilot.pause(0.2)
            assert listing.selected == [0]

            await pilot.press("a")
            await pilot.pause(0.2)
            assert sorted(listing.selected) == [0, 1, 2]

    asyncio.run(walk())


# ── the library, the buildings and the figures ──────────────────────────
#
# One database with two buildings in two towns, half of it fetched while
# logged in, so the columns and the dropdowns have something to say.

BLOCK = [
    ("p1", "Islands Brygge 30B", "2300", "København S", "st. tv", 70, 3_500_000, True),
    ("p2", "Islands Brygge 30B", "2300", "København S", "3. tv", 70, 4_200_000, False),
    ("p3", "Islands Brygge 30B", "2300", "København S", "10. th", 70, 7_000_000, True),
    ("p4", "Saxogade 10", "8600", "Silkeborg", "1. tv", 90, 2_800_000, False),
]


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A database with four properties across two buildings and two towns."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = tmp_path / "library.duckdb"
    for uuid, building, postcode, town, unit, area, value, beriget in BLOCK:
        store.save(
            path,
            {
                "ejendomme": [
                    {
                        "uuid": uuid,
                        "adresse": f"{building}, {unit}, {postcode} {town}",
                        "lejlighed": unit,
                        "ejendomstype": "Ejerlejlighed",
                        "boligtype": "condo",
                        "boligareal_m2": area,
                        "ejendomsvurdering_dkk": value,
                        "samlet_gaeld_dkk": value // 2,
                        "belaaningsgrad_pct": 50.0,
                        "beriget": beriget,
                    }
                ],
                "ejere": [
                    {"ejendom_uuid": uuid, "nummer": 1, "navn": f"Ejer {uuid}",
                     "foedselsdato": "1980-01-01", "andel": "1/1"}
                ],
                "handelshistorik": [
                    {"ejendom_uuid": uuid, "registrering_id": f"{uuid}-1",
                     "dato": "2019-04-11", "beloeb_dkk": value,
                     "areal_m2": area, "pris_pr_m2": value // area}
                ],
            },
        )
    return path


def test_the_library_says_which_rows_were_fetched_with_a_login(library):
    """The one column that says whether the rest of the row is the whole story."""
    from textual.widgets import DataTable

    from yaybo.app import YayboApp
    from yaybo.screens.library import COLUMNS, MITID, LibraryScreen

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            screen = app.screen
            assert isinstance(screen, LibraryScreen)
            assert len(screen.shown) == 4
            table = screen.query_one("#library-table", DataTable)
            # Column 0 is the tick; the MitID column is deliberately next to
            # it, where a narrow terminal cannot cut it off.
            assert COLUMNS[MITID].label == "MitID" and MITID == 0
            said = {str(table.get_row_at(i)[1]) for i in range(4)}
            assert said == {"✓ ja", "✗ nej"}, "a plain yes or no, not fuld/delvis"

    asyncio.run(walk())


def test_the_library_filters_by_dropdown_and_by_text(library):
    from textual.widgets import Input, Select

    from yaybo.app import YayboApp
    from yaybo.screens.library import ALL, LibraryScreen

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            screen = app.screen
            assert isinstance(screen, LibraryScreen)

            towns = [
                value for _, value in screen.query_one("#facet-by", Select)._options
                if isinstance(value, str) and value != ALL
            ]
            assert sorted(towns) == ["København S", "Silkeborg"]

            screen.query_one("#facet-by", Select).value = "Silkeborg"
            await pilot.pause(0.3)
            assert [row["uuid"] for row in screen.shown] == ["p4"]

            screen.query_one("#facet-mitid", Select).value = "ja"
            await pilot.pause(0.3)
            assert screen.shown == [], "the two conditions are both required"

            screen.query_one("#facet-by", Select).value = ALL
            screen.query_one("#facet-mitid", Select).value = ALL
            await pilot.pause(0.3)
            screen.query_one("#library-filter", Input).value = "Saxogade"
            await pilot.pause(0.3)
            assert [row["uuid"] for row in screen.shown] == ["p4"]

    asyncio.run(walk())


def test_ticking_a_row_does_not_move_the_cursor(library):
    """Refilling the table used to walk the cursor back to the first row."""
    from textual.coordinate import Coordinate
    from textual.widgets import DataTable

    from yaybo.app import YayboApp
    from yaybo.screens.library import LibraryScreen

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            screen = app.screen
            assert isinstance(screen, LibraryScreen)
            table = screen.query_one("#library-table", DataTable)
            table.focus()
            table.cursor_coordinate = Coordinate(2, 0)
            await pilot.pause(0.2)

            await pilot.press("space")
            await pilot.pause(0.2)
            assert table.cursor_row == 2, "ticking must not send you to the top"
            assert screen.shown[2]["uuid"] in screen.ticked
            assert str(table.get_row_at(2)[0]) == "✓"

            await pilot.press("down")
            await pilot.press("space")
            await pilot.pause(0.2)
            assert len(screen.ticked) == 2 and table.cursor_row == 3

    asyncio.run(walk())


def test_the_buildings_screen_groups_the_library_by_address(library):
    from textual.widgets import DataTable

    from yaybo.app import YayboApp
    from yaybo.screens.buildings import BuildingsScreen
    from yaybo.screens.library import LibraryScreen

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            app.action_buildings()
            await pilot.pause(0.5)
            screen = app.screen
            assert isinstance(screen, BuildingsScreen)
            assert len(screen.shown) == 2, "four properties, two buildings"

            block = next(b for b in screen.shown if b.held == 3)
            assert block.complete == 2, "two of its three had a login"
            table = screen.query_one("#buildings-table", DataTable)
            row = table.get_row_at(screen.shown.index(block))
            assert str(row[0]) == "2/3", "part of a building can differ from the rest"

            # Into that building's properties, on the properties tab. The
            # cursor decides which, and the newest fetch sorts first.
            from textual.coordinate import Coordinate

            table.focus()
            table.cursor_coordinate = Coordinate(screen.shown.index(block), 0)
            await pilot.pause(0.2)
            screen.action_open()
            await pilot.pause(0.6)
            opened = app.screen
            assert isinstance(opened, LibraryScreen)
            assert len(opened.shown) == 3

    asyncio.run(walk())


def test_the_figures_open_over_whatever_is_in_scope(library):
    from textual.widgets import DataTable, OptionList, Select

    from yaybo import stats
    from yaybo.app import YayboApp
    from yaybo.screens.analysis import AnalysisScreen
    from yaybo.screens.stats import StatsScreen

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            app.action_stats()
            await pilot.pause(0.6)
            screen = app.screen
            assert isinstance(screen, StatsScreen)
            assert len(screen.scope) == 4

            screen.query_one("#scope-bygning", Select).value = (
                "Islands Brygge 30B, 2300 København S"
            )
            await pilot.pause(0.4)
            assert len(screen.scope) == 3

            listing = screen.query_one("#stats-analyses", OptionList)
            listing.highlighted = next(
                i for i in range(listing.option_count)
                if str(listing.get_option_at_index(i).id) == "sammenlign"
            )
            listing.focus()
            await pilot.press("enter")
            await pilot.pause(0.6)

            modal = app.screen
            assert isinstance(modal, AnalysisScreen)
            assert len(modal.scope) == 3, "the modal inherits the selection"
            assert modal.by == "_etage"
            assert modal.query_one("#analysis-table", DataTable).row_count == 3

            # Every analysis has to survive being opened, since a broken one is
            # only found by opening it.
            for analysis in stats.ANALYSES:
                modal.analysis = analysis
                modal.measure = analysis.measures[0] if analysis.measures else ""
                if modal.measure:
                    modal.how = stats.BY_KEY[modal.measure].default
                modal._redraw()

            await pilot.press("escape")
            await pilot.pause(0.4)
            back = app.screen
            assert isinstance(back, StatsScreen)
            assert len(back.scope) == 3, "escape keeps the selection"

    asyncio.run(walk())


def test_the_andele_screen_shows_the_other_book(database):
    """A share is not a property, and the screen for it reads a different table.

    Also the join: what makes a co-op flat worth looking up is the building
    the association owns, and the row has to be able to name it.
    """
    from textual.widgets import DataTable

    from yaybo.app import YayboApp
    from yaybo.screens.andele import AndeleScreen
    from yaybo.screens.library import LibraryScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            # The share is not in the properties list: different book.
            assert isinstance(app.screen, LibraryScreen)
            assert [row["uuid"] for row in app.screen.shown] == ["u1"]

            app.action_andele()
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)
            assert len(app.screen.shown) == 1
            share = app.screen.shown[0]
            assert share["uuid"] == "a1"
            assert share["lejlighed"] == "ST. TH"
            # Joined out to the association's property, which is where the
            # valuation and the association's own mortgages are.
            assert share["ejendom_uuid"] == "u1"
            assert share["bygning"] == "Prøvegade 1, 1. tv, 9999 Prøveby"

            table = app.screen.query_one("#andele-table", DataTable)
            assert table.row_count == 1

            # Filtering is on the address, like everywhere else.
            app.screen._apply_filter("ST. TH")
            await pilot.pause()
            assert len(app.screen.shown) == 1
            app.screen._apply_filter("nowhere at all")
            await pilot.pause()
            assert app.screen.shown == []

    asyncio.run(walk())


def test_enter_on_an_andel_opens_it_and_names_who_is_on_its_charges(database):
    """The list counts a share's charges without showing them, so everyone
    named on one was unreachable until enter led somewhere.

    Driven with a keypress rather than by calling the action, because the bug
    was that the binding existed and did nothing: a focused DataTable takes
    enter for itself and answers with RowSelected.
    """
    from textual.widgets import DataTable, TabbedContent

    from yaybo.app import YayboApp
    from yaybo.screens.andel import AndelScreen
    from yaybo.screens.andele import AndeleScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            app.action_andele()
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)

            await pilot.press("enter")
            await pilot.pause(0.8)
            assert isinstance(app.screen, AndelScreen), "enter did nothing"

            screen = app.screen
            charges = screen.query_one("#table-andel-haeftelser", DataTable)
            notices = screen.query_one("#table-andel-meddelelser", DataTable)
            assert charges.row_count == 1
            assert notices.row_count == 1
            # The names are the point of the screen.
            assert screen.tables["andel_haeftelser"][0]["kreditorer"]
            assert screen.tables["andel_meddelelser"][0]["debitorer"]

            # An empty tab has to be empty on purpose rather than broken.
            tabs = screen.query_one("#andel-tabs", TabbedContent)
            assert "1" in str(tabs.get_tab("tab-andel-haeftelser").label)

            await pilot.press("escape")
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)

    asyncio.run(walk())


def test_g_from_an_andel_goes_to_its_building(database):
    """b is the queue everywhere else, so the building is on g - which is
    Bygninger globally, narrowed here to this share's own."""
    from yaybo.app import YayboApp
    from yaybo.screens.andele import AndeleScreen
    from yaybo.screens.library import LibraryScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            app.action_andele()
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)
            await pilot.press("g")
            await pilot.pause(0.8)
            # The association's property, on the properties tab, narrowed to it.
            assert isinstance(app.screen, LibraryScreen)
            assert len(app.screen.shown) == 1
            assert app.screen.shown[0]["uuid"] == "u1"

    asyncio.run(walk())


def test_re_fetching_an_andel_asks_for_two_properties_not_one(database):
    """An andel's address resolves to the share and to the association's
    building, so a cap of one would drop the property row it joins to.

    Auto-fetch is turned off first, so the queue parks the job instead of
    running it - the assertion is about what was queued, not about fetching.
    """
    from yaybo.app import YayboApp
    from yaybo.screens.andele import AndeleScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            app.fetching.auto = False
            app.action_andele()
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)

            await pilot.press("f")
            await pilot.pause(0.3)
            assert len(app.fetching.jobs) == 1
            job = app.fetching.jobs[0]
            assert job.limit == 2, "a share and its building, not just the first"
            assert "ST. TH" in job.query

    asyncio.run(walk())


def test_the_andele_filter_box_takes_and_gives_back_focus(database):
    from textual.widgets import DataTable, Input

    from yaybo.app import YayboApp
    from yaybo.screens.andele import AndeleScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            app.action_andele()
            await pilot.pause(0.5)
            screen = app.screen
            assert isinstance(screen, AndeleScreen)

            await pilot.press("ctrl+f")
            await pilot.pause(0.2)
            box = screen.query_one("#andele-filter", Input)
            assert box.has_focus

            box.value = "ST. TH"
            await pilot.pause(0.2)
            assert len(screen.shown) == 1

            # Escape clears first, and only then hands focus back.
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert box.value == ""
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert screen.query_one("#andele-table", DataTable).has_focus

    asyncio.run(walk())


def test_a_database_without_the_second_book_still_opens_the_tab(library):
    """Every database written before this existed, and any fetched with
    --no-andele, has no andele table at all."""
    from yaybo.app import YayboApp
    from yaybo.screens.andele import AndeleScreen

    assert store.andele(library) == []

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            # Let the app finish mounting its first screen before navigating.
            # Switching screens mid-mount races the widgets the Library is
            # still composing, and the failure surfaces somewhere else
            # entirely - in Header, or in Tabs - which is a hard bug to read.
            await pilot.pause()
            app.action_andele()
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)
            assert app.screen.shown == []

    asyncio.run(walk())


def test_the_andele_tab_opens_on_a_database_from_before_a_column_existed(tmp_path,
                                                                         monkeypatch):
    """The reported failure went through the screen, so the walk does too.

    A table only gains a column when something is next saved into it, so an
    andel fetched before the column existed leaves the screen reading a table
    that is a version behind.
    """
    import duckdb
    from textual.widgets import DataTable

    from yaybo.app import YayboApp
    from yaybo.screens.andele import AndeleScreen

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = tmp_path / "older.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute(
            'CREATE TABLE "andele" ("uuid" VARCHAR, "adresse" VARCHAR, '
            '"lejlighed" VARCHAR, "antal_haeftelser" BIGINT, '
            '"samlet_gaeld_dkk" BIGINT, "hentet" TIMESTAMP)'
        )
        db.execute(
            "INSERT INTO andele VALUES ('a1', 'Prøvegade 1, ST. TH, 9999 Prøveby',"
            " 'ST. TH', 1, 1500000, now())"
        )

    async def walk() -> None:
        app = YayboApp(database=path)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()          # mount first, then navigate
            app.action_andele()
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)
            # The row draws, with the columns it has and dashes for the rest.
            assert len(app.screen.shown) == 1
            table = app.screen.query_one("#andele-table", DataTable)
            assert table.row_count == 1

    asyncio.run(walk())


def test_every_tab_names_a_place_that_exists(library):
    """The nav bar is only useful if each tab actually goes somewhere."""
    from yaybo.app import YayboApp
    from yaybo.widgets.nav import PLACES, NavTabs

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            for key, _, action in PLACES:
                getattr(app, f"action_{action}")()
                await pilot.pause(0.5)
                tabs = app.screen.query_one(NavTabs)
                assert tabs.active == key, f"{action} should sit on the {key} tab"

    asyncio.run(walk())


def test_activating_another_tab_navigates(library):
    """The other half of the tab bar: it marks where you are, and going
    somewhere else takes you there.

    test_every_tab_names_a_place_that_exists covers the marking. This covers
    the handler, which is guarded against the activation a Tabs raises for its
    own tab on mount and could swallow a real one by mistake.
    """
    from yaybo.app import YayboApp
    from yaybo.screens.andele import AndeleScreen
    from yaybo.screens.queue import QueueScreen
    from yaybo.widgets.nav import NavTabs

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            app.action_andele()
            await pilot.pause(0.5)
            assert isinstance(app.screen, AndeleScreen)

            tabs = app.screen.query_one(NavTabs)
            assert tabs.active == "andele"

            tabs.active = "koe"
            await pilot.pause(0.5)
            assert isinstance(app.screen, QueueScreen)

    asyncio.run(walk())


def test_the_queue_screen_acts_on_what_is_ticked(library, monkeypatch):
    """Every action there works on a selection, or on all of it when none is."""
    from textual.coordinate import Coordinate
    from textual.widgets import Button, DataTable

    from yaybo import fetching
    from yaybo.app import YayboApp
    from yaybo.screens.queue import QueueScreen

    async def walk() -> None:
        app = YayboApp(database=library)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            # Nothing starts on its own, so the list can be looked at.
            app.fetching.auto = False
            app.enqueue_refetch([row[1] + ", " + row[4] for row in
                                 [(b[0], b[1], b[2], b[3], b[4]) for b in BLOCK]])
            app.action_queue()
            await pilot.pause(0.5)

            screen = app.screen
            assert isinstance(screen, QueueScreen)
            assert not screen.query("#queue-input"), "nothing is added here any more"
            assert len(screen.shown) == 4
            assert app.fetching.held == 4, "auto-fetch off parks them"
            assert "off" in str(screen.query_one("#queue-auto", Button).label)

            table = screen.query_one("#queue-table", DataTable)
            table.focus()
            table.cursor_coordinate = Coordinate(1, 0)
            await pilot.pause(0.2)
            await pilot.press("space")
            await pilot.pause(0.2)
            assert screen.ticked == {screen.shown[1].key}
            assert str(table.get_row_at(1)[0]) == "✓"

            # Removing takes the ticked one and leaves the rest alone.
            await pilot.press("d")
            await pilot.pause(0.3)
            assert len(app.fetching.jobs) == 3
            assert screen.ticked == set(), "the selection goes with what it removed"

            await pilot.press("a")
            await pilot.pause(0.2)
            assert len(screen.ticked) == 3

            # And the toggle really does flip the application's setting.
            await pilot.press("t")
            await pilot.pause(0.3)
            assert app.fetching.auto is True
            assert app.fetching.held == 0, "turning it on releases what was parked"

    monkeypatch.setattr(fetching, "POLITE_DELAY", 0)
    asyncio.run(walk())


def test_the_export_dialog_offers_exactly_what_it_can_write(database, tmp_path):
    """The formats it lists and the formats the exporter knows are one list.

    Writing is covered by test_exports_every_format, which calls the exporter
    directly. What can only go wrong here is the two drifting apart - a button
    for a format nothing knows how to produce.
    """
    from textual.widgets import RadioButton, RadioSet

    from yaybo import export
    from yaybo.app import YayboApp
    from yaybo.widgets.export_dialog import ExportDialog

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(
                ExportDialog(store.everything(database), "prøve", outdir=tmp_path)
            )
            await pilot.pause(0.4)

            dialog = app.screen
            assert isinstance(dialog, ExportDialog)
            # Empty tables are dropped before the dialog says what it will write.
            assert all(rows for rows in dialog.tables.values())
            assert "ejendomme" in dialog.tables

            offered = [
                str(button.label)
                for button in dialog.query_one("#export-format", RadioSet).query(
                    RadioButton
                )
            ]
            assert set(offered) == set(export.FORMATS)
            assert dialog.chosen in offered, "something has to be picked to start with"

    asyncio.run(walk())
