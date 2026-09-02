"""Everything already fetched, browsable without touching the network.

This is where the application opens, and it is the point of accumulating a
database rather than a folder of spreadsheets: a lookup done last month is
still here, still searchable, and still says when it was true. Re-fetching one
is a keypress, and only ever costs what that one property costs.
"""

from __future__ import annotations

import asyncio

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from yaybo import display, pipeline, store
from yaybo.register.fields import normalise
from yaybo.screens.base import YayboScreen

COLUMNS = (
    ("Adresse", 42),
    ("Type", 14),
    ("Areal", 8),
    ("Vurdering", 11),
    ("Gæld", 11),
    ("Belånt", 8),
    ("Ejere", 26),
    ("Hentet", 10),
)


class LibraryScreen(YayboScreen):
    """The properties this database holds, newest lookup first."""

    # The table, not the filter box. This is the one screen whose single-key
    # bindings have to work the moment it opens, and a focused Input eats every
    # letter that is not also a control key.
    AUTO_FOCUS = "#library-table"

    BINDINGS = [
        Binding("enter", "open", "Open"),
        Binding("f", "refetch", "Re-fetch"),
        Binding("e", "export", "Export all"),
        Binding("r", "refresh", "Reload"),
        Binding("ctrl+f", "focus_filter", "Filter", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.held: list[dict] = []
        self.shown: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="library-bar"):
            yield Input(placeholder="Filter by address or owner…", id="library-filter")
            yield Static("", id="library-count")
        yield DataTable(id="library-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="library-empty", classes="empty-state")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#library-table", DataTable)
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
        self.held = held
        self._apply_filter(self.query_one("#library-filter", Input).value)
        # An empty library on the first run is a screen explaining where the
        # search box is. Go there instead - escape comes straight back.
        if not held and self.app.consume_first_run():
            self.app.action_search()

    def _apply_filter(self, needle: str) -> None:
        wanted = normalise(needle)
        self.shown = [
            row
            for row in self.held
            if not wanted
            or wanted in normalise(f"{row.get('adresse') or ''} {row.get('ejere') or ''}")
        ]
        self._fill()

    def _fill(self) -> None:
        table = self.query_one("#library-table", DataTable)
        table.clear()
        for row in self.shown:
            table.add_row(
                display.shorten(row.get("adresse"), 42),
                display.shorten(
                    display.boligtype(row.get("boligtype"))
                    or row.get("ejendomstype"),
                    14,
                ),
                display.area(row.get("boligareal_m2") or row.get("areal_m2")),
                display.compact_kr(row.get("ejendomsvurdering_dkk")),
                display.compact_kr(row.get("samlet_gaeld_dkk")),
                display.pct(row.get("belaaningsgrad_pct"), 0),
                display.shorten(row.get("ejere"), 26),
                display.ago(row.get("hentet")),
                key=row["uuid"],
            )

        count = self.query_one("#library-count", Static)
        empty = self.query_one("#library-empty", Static)
        if self.held:
            held = len(self.held)
            count.update(
                f"{len(self.shown)} of {held}" if len(self.shown) != held
                else f"{held} propert{'y' if held == 1 else 'ies'}"
            )
        else:
            count.update("")
        table.display = bool(self.shown)
        empty.display = not self.shown
        empty.update(
            "Nothing matches that filter."
            if self.held
            else f"Nothing fetched yet.\n\nPress / to look an address up, "
            f"or b to queue a whole street.\n\n{self.app.database} does not exist."
        )
        # Only on the way in. A reload can land while the filter is being typed
        # in, or after a background re-fetch, and pulling the cursor out of the
        # box mid-word would be maddening.
        if self.shown and self.screen.focused is None:
            table.focus()

    @on(Input.Changed, "#library-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    @on(Input.Submitted, "#library-filter")
    def _filter_done(self) -> None:
        # Enter in the filter means "now let me move around the results".
        if self.shown:
            self.query_one("#library-table", DataTable).focus()

    def action_focus_filter(self) -> None:
        self.query_one("#library-filter", Input).focus()

    def action_clear_filter(self) -> None:
        field = self.query_one("#library-filter", Input)
        if field.value:
            field.value = ""
        else:
            self.query_one("#library-table", DataTable).focus()

    # ── acting on a row ─────────────────────────────────────────────────

    def _selected(self) -> dict | None:
        table = self.query_one("#library-table", DataTable)
        if not self.shown or table.cursor_row < 0:
            return None
        try:
            return self.shown[table.cursor_row]
        except IndexError:
            return None

    @on(DataTable.RowSelected, "#library-table")
    def _opened(self, event: DataTable.RowSelected) -> None:
        self._open(str(event.row_key.value))

    def action_open(self) -> None:
        row = self._selected()
        if row:
            self._open(row["uuid"])

    def _open(self, uuid: str) -> None:
        from yaybo.screens.property import PropertyScreen

        self.app.push_screen(PropertyScreen(uuid))

    def action_refetch(self) -> None:
        row = self._selected()
        if not row:
            return
        self.notify(f"Re-fetching {row['adresse']}…")
        self._refetch(row["adresse"])

    @work(thread=True)
    def _refetch(self, address: str) -> None:
        """Ask the register for one property again, and replace what we hold.

        Deliberately the whole address as it was stored, floor and door
        included: that is what the register was asked for the first time, so it
        is what will come back as the same property rather than as its
        neighbours.
        """
        try:
            bundle = pipeline.lookup(self.app.api, address, limit=1, delay=0)
            written = store.save(self.app.database, bundle.tables)
        except Exception as error:  # noqa: BLE001 - shown to the user verbatim
            self.app.call_from_thread(
                self.notify, f"Could not re-fetch: {error}", severity="error"
            )
            return
        rows = sum(written.values())
        self.app.call_from_thread(self.notify, f"Re-fetched {address} - {rows} rows")
        self.app.call_from_thread(self.action_refresh)

    @work
    async def action_export(self) -> None:
        from yaybo.widgets.export_dialog import ExportDialog

        # Reading the whole database can take a moment on a big one, and the
        # dialog has nothing to show until it is read.
        tables = await asyncio.to_thread(store.everything, self.app.database)
        if not tables:
            self.notify("There is nothing in the database to export yet.")
            return
        await self.app.push_screen_wait(
            ExportDialog(tables, "yaybo-library", title="Export the whole database")
        )
