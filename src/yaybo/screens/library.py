"""Everything already fetched, browsable without touching the network.

This is where the application opens, and it is the point of accumulating a
database rather than a folder of spreadsheets: a lookup done last month is
still here, still searchable, and still says when it was true. Re-fetching one
is a keypress, and only ever costs what that one property costs.

Three things the list has to be able to say, because a database that has grown
past a screenful stops answering them by itself: which rows came from a
logged-in session and are therefore complete, which rows match some question
you have in mind, and what order to read them in. Hence the MitID column, the
`name:value` filter and sortable headers - all of them working off the same
column table, so a column added there is sortable and filterable for free.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from yaybo import display, store
from yaybo.register.address import split_postcode
from yaybo.register.fields import normalise
from yaybo.screens.base import YayboScreen
from yaybo.widgets.queue_bar import QueueBar
from yaybo.widgets.session_bar import SessionBar


@dataclass(frozen=True)
class Column:
    """One column, and everything the three features need to know about it.

    `show` renders the cell, `key` orders it, `text` is what a filter matches.
    Keeping all three together is what stops a column being sortable but not
    filterable, or filterable on something other than what it displays.
    """

    label: str
    width: int
    name: str
    show: Callable[[dict], object]
    field: str = ""
    text: Callable[[dict], str] | None = None

    def sort_key(self, row: dict):
        return _sortable(row.get(self.field) if self.field else self.show(row))

    def matches(self, row: dict, wanted: str) -> bool:
        if self.text is not None:
            return wanted in normalise(self.text(row))
        value = row.get(self.field) if self.field else self.show(row)
        return wanted in normalise(_plain(value))


def _sortable(value):
    """A key that orders a column without tripping over its empty cells.

    The leading flag is what makes that work: everything missing sorts as one
    group at the end, and only values of the same kind are ever compared with
    each other.
    """
    if value is None or value == "":
        return (1, "")
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, (int, float)):
        return (0, value)
    if isinstance(value, datetime):
        return (0, value.timestamp())
    return (0, normalise(str(value)))


def _plain(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        # So `mitid:ja` and `mitid:nej` both find something to match against.
        return "ja fuld" if value else "nej delvis offentlig"
    return str(value)


def _kind(row: dict) -> str:
    return display.boligtype(row.get("boligtype")) or row.get("ejendomstype") or ""


def _postcode(row: dict) -> str:
    return split_postcode(row.get("adresse") or "")[1]


# MitID comes second on purpose. It is the column that says whether the rest of
# the row is the whole story, and at the far right it is the first thing a
# narrow terminal cuts off - which is exactly the wrong column to lose.
COLUMNS: tuple[Column, ...] = (
    Column("MitID", 8, "mitid", lambda r: r.get("beriget"), field="beriget"),
    Column("Adresse", 34, "adresse", lambda r: display.shorten(r.get("adresse"), 34),
           field="adresse"),
    Column("Postnr", 6, "postnr", _postcode),
    Column("Type", 12, "type", lambda r: display.shorten(_kind(r), 12),
           text=_kind),
    Column("Areal", 6, "areal",
           lambda r: display.area(r.get("boligareal_m2") or r.get("areal_m2")),
           field="boligareal_m2"),
    Column("Vurdering", 10, "vurdering",
           lambda r: display.compact_kr(r.get("ejendomsvurdering_dkk")),
           field="ejendomsvurdering_dkk"),
    Column("Gæld", 10, "gaeld",
           lambda r: display.compact_kr(r.get("samlet_gaeld_dkk")),
           field="samlet_gaeld_dkk"),
    Column("Belånt", 6, "belaant",
           lambda r: display.pct(r.get("belaaningsgrad_pct"), 0),
           field="belaaningsgrad_pct"),
    Column("Ejere", 16, "ejer", lambda r: display.shorten(r.get("ejere"), 16),
           field="ejere"),
    Column("Hentet", 9, "hentet", lambda r: display.ago(r.get("hentet")),
           field="hentet"),
)
MITID = next(index for index, column in enumerate(COLUMNS) if column.name == "mitid")
BY_NAME = {column.name: column for column in COLUMNS}


class LibraryScreen(YayboScreen):
    """The properties this database holds, in whatever order you ask for."""

    # The table, not the filter box. This is the one screen whose single-key
    # bindings have to work the moment it opens, and a focused Input eats every
    # letter that is not also a control key.
    AUTO_FOCUS = "#library-table"

    BINDINGS = [
        Binding("enter", "open", "Open"),
        Binding("space", "tick", "Tick"),
        Binding("a", "tick_all", "Tick all"),
        Binding("n", "tick_none", "Tick none"),
        Binding("f", "refetch", "Re-fetch"),
        Binding("e", "export", "Export"),
        Binding("o", "sort_next", "Sort by"),
        Binding("i", "sort_invert", "Reverse"),
        Binding("r", "refresh", "Reload"),
        Binding("ctrl+f", "focus_filter", "Filter", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.held: list[dict] = []
        self.shown: list[dict] = []
        self.ticked: set[str] = set()
        self.sort_by = len(COLUMNS) - 1  # Hentet: newest lookup first
        self.descending = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionBar()
        # The box and the button are deliberately not peers. One narrows a list
        # that is already here; the other goes out to the register and costs a
        # request. Labelling the box "Filter" and giving the other its own
        # button is what stops the box reading as a way to find new addresses.
        with Horizontal(id="library-bar"):
            yield Static("Filter", id="library-filter-label")
            yield Input(
                placeholder="text, or name:value — try  mitid:nej  postnr:2300",
                id="library-filter",
            )
            yield Static("", id="library-count")
            yield Button("＋ Find new property", id="library-new", variant="primary")
        yield Static("", id="library-scope")
        yield DataTable(id="library-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="library-empty", classes="empty-state")
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#library-table", DataTable)
        table.add_column("", width=3)
        for column in COLUMNS:
            table.add_column(column.label, width=column.width)
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

    def queue_changed(self) -> None:
        """A finished fetch has changed what the database holds."""
        if not self.app.fetching.running:
            self.action_refresh()

    # ── filtering ───────────────────────────────────────────────────────

    def _apply_filter(self, needle: str) -> None:
        self.shown = [row for row in self.held if _matches(row, needle)]
        self._sort()

    def _sort(self) -> None:
        column = COLUMNS[self.sort_by]
        self.shown.sort(key=column.sort_key, reverse=self.descending)
        self._fill()

    def action_sort_next(self) -> None:
        self.sort_by = (self.sort_by + 1) % len(COLUMNS)
        self._sort()
        self.notify(f"Sorted by {COLUMNS[self.sort_by].label}.")

    def action_sort_invert(self) -> None:
        self.descending = not self.descending
        self._sort()

    @on(DataTable.HeaderSelected, "#library-table")
    def _header_clicked(self, event: DataTable.HeaderSelected) -> None:
        """Click a header to sort by it; click the same one again to reverse."""
        index = event.column_index - 1  # the tick column has no Column of its own
        if index < 0:
            self.action_tick_all() if not self.ticked else self.action_tick_none()
            return
        if index == self.sort_by:
            self.descending = not self.descending
        else:
            self.sort_by, self.descending = index, False
        self._sort()

    # ── drawing ─────────────────────────────────────────────────────────

    def _fill(self) -> None:
        table = self.query_one("#library-table", DataTable)
        self.ticked &= {row["uuid"] for row in self.held}
        table.clear()
        for row in self.shown:
            cells = [column.show(row) for column in COLUMNS]
            cells[MITID] = self._mitid_cell(row.get("beriget"))
            table.add_row(
                "✓" if row["uuid"] in self.ticked else "", *cells, key=row["uuid"]
            )
        self._describe()
        # Only on the way in. A reload can land while the filter is being typed
        # in, or after a background re-fetch, and pulling the cursor out of the
        # box mid-word would be maddening.
        if self.shown and self.screen.focused is None:
            table.focus()

    def _mitid_cell(self, beriget) -> Text:
        """Whether this row has the half of the register that needs a login.

        The loudest thing in the table on purpose. Two rows for the same street
        can hold quite different amounts, and nothing else on the row says so.
        """
        theme = self.app.current_theme
        if beriget is None:
            return Text("–", style="dim")
        if beriget:
            return Text("✓ fuld", style=f"bold {theme.success or 'green'}")
        return Text("○ delvis", style=f"bold {theme.warning or 'yellow'}")

    def _describe(self) -> None:
        column = COLUMNS[self.sort_by]
        arrow = "↓" if self.descending else "↑"
        count = self.query_one("#library-count", Static)
        held, shown = len(self.held), len(self.shown)
        ticked = f"{len(self.ticked)} ticked · " if self.ticked else ""
        if held:
            count.update(
                f"{ticked}{shown} of {held}" if shown != held
                else f"{ticked}{held} propert{'y' if held == 1 else 'ies'}"
            )
        else:
            count.update("")
        self.query_one("#library-scope", Static).update(
            f"Already fetched — the filter searches these only. "
            f"Sorted by {column.label} {arrow} (o, i) · ＋ for a new address."
        )

        table = self.query_one("#library-table", DataTable)
        empty = self.query_one("#library-empty", Static)
        table.display = bool(self.shown)
        empty.display = not self.shown
        typed = self.query_one("#library-filter", Input).value.strip()
        if not self.held:
            empty.update(
                "Nothing fetched yet.\n\nPress / to look an address up, "
                f"or b for the queue.\n\n{self.app.database} does not exist."
            )
        elif typed:
            # The most useful thing to say to someone whose filter found
            # nothing is that the register might still have it - and that
            # enter, right here, is how to go and ask.
            empty.update(
                f"Nothing in your library matches {typed!r}.\n\n"
                "Press enter to search the register for it,\n"
                "or escape to clear the filter."
            )
        else:
            empty.update("Nothing matches that filter.")

    @on(Input.Changed, "#library-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    @on(Input.Submitted, "#library-filter")
    def _filter_done(self, event: Input.Submitted) -> None:
        # Enter in the filter means "now let me move around the results" - and
        # when there are none, it means the address being typed is not here
        # yet, which is a search, not a filter. Hand the text over rather than
        # making it be typed twice.
        if self.shown:
            self.query_one("#library-table", DataTable).focus()
        elif event.value.strip():
            self.app.search_for(event.value.strip())

    @on(Button.Pressed, "#library-new")
    def _new_property(self) -> None:
        self.app.action_search()

    def action_focus_filter(self) -> None:
        self.query_one("#library-filter", Input).focus()

    def action_clear_filter(self) -> None:
        field = self.query_one("#library-filter", Input)
        if field.value:
            field.value = ""
        else:
            self.query_one("#library-table", DataTable).focus()

    # ── choosing rows ───────────────────────────────────────────────────

    def _selected(self) -> dict | None:
        table = self.query_one("#library-table", DataTable)
        if not self.shown or table.cursor_row < 0:
            return None
        try:
            return self.shown[table.cursor_row]
        except IndexError:
            return None

    def action_tick(self) -> None:
        row = self._selected()
        if row is None:
            return
        self.ticked.symmetric_difference_update({row["uuid"]})
        self._fill()

    def action_tick_all(self) -> None:
        """Everything the filter is currently showing, not the whole database."""
        self.ticked = {row["uuid"] for row in self.shown}
        self._fill()

    def action_tick_none(self) -> None:
        self.ticked.clear()
        self._fill()

    def _chosen(self) -> list[dict]:
        """The ticked rows, or the one under the cursor when none are ticked."""
        if self.ticked:
            return [row for row in self.shown if row["uuid"] in self.ticked]
        row = self._selected()
        return [row] if row else []

    # ── acting on them ──────────────────────────────────────────────────

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
        """Queue the ticked properties to be fetched again, one job each.

        Deliberately the whole address as it was stored, floor and door
        included: that is what the register was asked for the first time, so it
        is what comes back as the same property rather than as its neighbours.
        """
        chosen = self._chosen()
        if not chosen:
            self.notify("Nothing to re-fetch.")
            return
        added = self.app.enqueue_refetch([row["adresse"] for row in chosen])
        self.ticked.clear()
        self._fill()
        held = "property" if added == 1 else "properties"
        self.notify(
            f"Queued {added} {held} to re-fetch. {self.app.queued_note()}"
            if added
            else "Those are already in the queue."
        )

    @work
    async def action_export(self) -> None:
        from yaybo.widgets.export_dialog import ExportDialog

        chosen = self._chosen() if self.ticked else []
        if chosen:
            uuids = [row["uuid"] for row in chosen]
            tables = await asyncio.to_thread(
                store.tables_for, self.app.database, uuids
            )
            title = f"Export {len(uuids)} ticked propert" + (
                "y" if len(uuids) == 1 else "ies"
            )
        else:
            # Reading the whole database can take a moment on a big one, and the
            # dialog has nothing to show until it is read.
            tables = await asyncio.to_thread(store.everything, self.app.database)
            title = "Export the whole database"
        if not tables:
            self.notify("There is nothing to export yet.")
            return
        await self.app.push_screen_wait(
            ExportDialog(tables, "yaybo-library", title=title)
        )


def _tokens(query: str) -> list[str]:
    """Split a filter into tokens, keeping quoted values in one piece.

    `bygning:"Islands Brygge 30B"` has to survive as one condition; splitting
    it on spaces would turn one exact building into four loose words that
    happen to co-occur.
    """
    try:
        return shlex.split(query)
    except ValueError:  # an unbalanced quote, i.e. still being typed
        return query.split()


def _matches(row: dict, needle: str) -> bool:
    """Whether a row survives the filter box.

    Bare words match anywhere on the row. `name:value` matches one column, so
    `mitid:nej postnr:2300` reads as both conditions at once - which is the
    whole reason for having a syntax rather than one wider search.
    """
    for token in _tokens(needle):
        name, _, value = token.partition(":")
        column = BY_NAME.get(name.lower()) if value else None
        if column is not None:
            if not column.matches(row, normalise(value)):
                return False
            continue
        wanted = normalise(token)
        if not any(column.matches(row, wanted) for column in COLUMNS):
            return False
    return True
