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
            "beloeb_dkk": 1800000,
            "areal_m2": 75,
            "pris_pr_m2": 24000,
            "handelstype": "Almindeligt salg",
        },
        {
            "ejendom_uuid": "u1",
            "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby",
            "dato": "2019-04-11",
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
            # A freshly filled list must arrive with a cursor, or the first key
            # aimed at it is swallowed.
            assert app.screen.query_one("#search-matches", OptionList).highlighted == 0
            assert rows[0]["tekst"] == "Prøvegade 1, 9999 Prøveby"
            assert not rows[0]["etage"] and not rows[0]["doer"]
            assert rows[1]["etage"] == "1"

    asyncio.run(walk())


def test_search_fetches_the_ticked_property_and_opens_it(tmp_path, monkeypatch):
    """Pick one flat, and the single result arrives ticked so f just works."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    database = tmp_path / "fetched.duckdb"
    unit = {"uuid": "u1", "adresse": "Prøvegade 1, 1. tv, 9999 Prøveby"}
    address, asked = _stub_search(monkeypatch, [unit])

    from textual.widgets import OptionList, SelectionList

    from yaybo.app import YayboApp
    from yaybo.screens.property import PropertyScreen

    async def walk() -> None:
        app = YayboApp(database=database)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            screen = app.screen
            await _type_address(pilot, screen)

            # Row 1 is the flat itself, floor and door intact.
            matches = screen.query_one("#search-matches", OptionList)
            matches.focus()
            matches.highlighted = 1
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert asked["address"] == address

            listing = screen.query_one("#search-units", SelectionList)
            assert listing.selected == [0], "a lone property should arrive ticked"

            # The fetch runs in a thread and the property screen reads the
            # database in another, so wait for the rows rather than for the
            # screen: arriving on it says nothing about whether it has loaded.
            await pilot.press("f")
            for _ in range(40):
                await pilot.pause(0.1)
                if isinstance(app.screen, PropertyScreen) and app.screen.tables:
                    break

            assert asked["units"] == [unit]
            assert isinstance(app.screen, PropertyScreen), "the property never opened"
            assert app.screen.tables, "the property opened but never loaded"
            assert app.screen.property_row["adresse"].startswith("Prøvegade 1")
            assert app.screen.tables["ejere"][0]["navn"] == "Ida Testesen"

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
