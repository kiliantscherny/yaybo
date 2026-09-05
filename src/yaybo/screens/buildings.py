"""What the library holds, one building to a row rather than one flat.

A block of flats arrives as sixty rows that differ only by their floor, which
is the wrong shape for two of the questions people actually have: how much of
this building do I hold, and how stale is it. Grouping them answers both at a
glance, and going into one takes you back to the properties with that building
already picked.

The grouping is the same derived field the figures screen uses - the address
with the flat taken off it - so a building means the same thing everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from yaybo import display, stats, store
from yaybo.register.fields import normalise
from yaybo.screens.base import YayboScreen
from yaybo.widgets.nav import NavTabs
from yaybo.widgets.queue_bar import QueueBar
from yaybo.widgets.session_bar import SessionBar

COLUMNS = (
    ("MitID", 9),
    ("Bygning", 40),
    ("Postnr", 6),
    ("Ejendomme", 10),
    ("Areal", 6),
    ("Vurd./m²", 9),
    ("Gæld", 10),
    ("Belånt", 6),
    ("Hentet", 9),
)


@dataclass
class Building:
    """One address, and everything the database holds under it."""

    name: str
    postcode: str
    rows: list[dict]

    @property
    def held(self) -> int:
        return len(self.rows)

    @property
    def complete(self) -> int:
        return sum(1 for row in self.rows if row.get("beriget"))

    @property
    def fetched(self):
        stamps = [row.get("hentet") for row in self.rows if row.get("hentet")]
        return max(stamps) if stamps else None


class BuildingsScreen(YayboScreen):
    """The library grouped by address, newest first."""

    AUTO_FOCUS = "#buildings-table"

    BINDINGS = [
        Binding("enter", "open", "Its properties"),
        Binding("k", "figures", "Nøgletal"),
        Binding("f", "refetch", "Re-fetch all"),
        Binding("r", "refresh", "Reload"),
        Binding("ctrl+f", "focus_filter", "Filter", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.buildings: list[Building] = []
        self.shown: list[Building] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionBar()
        yield NavTabs("bygninger")
        with Horizontal(id="buildings-bar"):
            yield Static("Søg", id="buildings-filter-label")
            yield Input(placeholder="address", id="buildings-filter")
            yield Static("", id="buildings-count")
        yield Static("", id="buildings-scope")
        yield DataTable(id="buildings-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="buildings-empty", classes="empty-state")
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#buildings-table", DataTable)
        for label, width in COLUMNS:
            table.add_column(label, width=width)
        self.action_refresh()

    # ── loading ─────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        held = store.library(self.app.database)
        self.app.call_from_thread(self._loaded, held)

    def _loaded(self, held: list[dict]) -> None:
        grouped: dict[str, Building] = {}
        for row in stats.annotate(held):
            name = row.get("_bygning") or row.get("adresse") or "—"
            found = grouped.get(name)
            if found is None:
                found = grouped[name] = Building(name, row.get("_postnr") or "", [])
            found.rows.append(row)
        self.buildings = sorted(
            grouped.values(),
            key=lambda b: (b.fetched is None, b.fetched or 0),
            reverse=True,
        )
        self._apply_filter(self.query_one("#buildings-filter", Input).value)

    def queue_changed(self) -> None:
        if not self.app.fetching.running:
            self.action_refresh()

    # ── filtering and drawing ───────────────────────────────────────────

    def _apply_filter(self, needle: str) -> None:
        wanted = normalise(needle)
        self.shown = [
            building for building in self.buildings
            if not wanted or wanted in normalise(building.name)
        ]
        self._fill()

    def _fill(self) -> None:
        table = self.query_one("#buildings-table", DataTable)
        table.clear()
        theme = self.app.current_theme
        for building in self.shown:
            areas = [
                row.get("boligareal_m2") or row.get("areal_m2")
                for row in building.rows
            ]
            table.add_row(
                self._mitid_cell(building, theme),
                display.shorten(building.name, 40),
                building.postcode,
                str(building.held),
                display.area(_median(areas)),
                display.compact_kr(_median([
                    (row.get("ejendomsvurdering_dkk") or 0)
                    / (row.get("boligareal_m2") or row.get("areal_m2") or 0)
                    for row in building.rows
                    if row.get("ejendomsvurdering_dkk")
                    and (row.get("boligareal_m2") or row.get("areal_m2"))
                ])),
                display.compact_kr(_median(
                    [row.get("samlet_gaeld_dkk") for row in building.rows]
                )),
                display.pct(_median(
                    [row.get("belaaningsgrad_pct") for row in building.rows]
                ), 0),
                display.ago(building.fetched),
                key=building.name,
            )
        self._describe()

    def _mitid_cell(self, building: Building, theme) -> Text:
        """How much of this building was fetched while logged in.

        A building can be half one thing and half the other - fetched over two
        sessions, one of which had lapsed - so a bare yes or no would be a lie
        here in a way it is not on a single property.
        """
        whole, part = building.held, building.complete
        if part == whole:
            return Text("✓ ja", style=f"bold {theme.success or 'green'}")
        if part == 0:
            return Text("✗ nej", style=f"bold {theme.warning or 'yellow'}")
        return Text(f"{part}/{whole}", style=f"bold {theme.warning or 'yellow'}")

    def _describe(self) -> None:
        held, shown = len(self.buildings), len(self.shown)
        properties = sum(b.held for b in self.shown)
        self.query_one("#buildings-count", Static).update(
            f"{shown} of {held}" if shown != held else f"{held} building(s)"
        )
        self.query_one("#buildings-scope", Static).update(
            f"{properties} propert{'y' if properties == 1 else 'ies'} in "
            f"{shown} building(s) · enter opens a building's properties, "
            "k its figures, f re-fetches all of it."
        )
        table = self.query_one("#buildings-table", DataTable)
        empty = self.query_one("#buildings-empty", Static)
        table.display = bool(self.shown)
        empty.display = not self.shown
        empty.update(
            "Nothing fetched yet.\n\nPress / to look an address up."
            if not self.buildings
            else "No building matches that."
        )

    @on(Input.Changed, "#buildings-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    def action_focus_filter(self) -> None:
        self.query_one("#buildings-filter", Input).focus()

    def action_clear_filter(self) -> None:
        field = self.query_one("#buildings-filter", Input)
        if field.value:
            field.value = ""
        else:
            self.query_one("#buildings-table", DataTable).focus()

    # ── acting on one ───────────────────────────────────────────────────

    def _selected(self) -> Building | None:
        table = self.query_one("#buildings-table", DataTable)
        if not self.shown or table.cursor_row < 0:
            return None
        try:
            return self.shown[table.cursor_row]
        except IndexError:
            return None

    @on(DataTable.RowSelected, "#buildings-table")
    def _opened(self, event: DataTable.RowSelected) -> None:
        self.app.library_for(str(event.row_key.value))

    def action_open(self) -> None:
        building = self._selected()
        if building:
            self.app.library_for(building.name)

    def action_figures(self) -> None:
        building = self._selected()
        if building:
            self.app.stats_for(f'bygning:"{building.name}"')

    def action_refetch(self) -> None:
        """Queue every property in this building to be fetched again."""
        building = self._selected()
        if building is None:
            return
        added = self.app.enqueue_refetch(
            [row["adresse"] for row in building.rows if row.get("adresse")]
        )
        held = "property" if added == 1 else "properties"
        self.notify(
            f"Queued {added} {held} from {building.name}. {self.app.queued_note()}"
            if added
            else "Those are already in the queue."
        )

    def action_back(self) -> None:
        self.app.action_library()


def _median(values) -> float | None:
    found = sorted(
        float(v) for v in values
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    )
    if not found:
        return None
    middle = len(found) // 2
    if len(found) % 2:
        return found[middle]
    return (found[middle - 1] + found[middle]) / 2
