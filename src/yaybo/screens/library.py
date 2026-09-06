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
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.coordinate import Coordinate
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
)

from yaybo import display, i18n, stats, store
from yaybo.register.address import split_postcode
from yaybo.register.fields import normalise
from yaybo.screens.base import YayboScreen
from yaybo.widgets.nav import NavTabs
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
    Column("MitID", 7, "mitid", lambda r: r.get("beriget"), field="beriget"),
    Column("Address", 34, "adresse", lambda r: display.shorten(r.get("adresse"), 34),
           field="adresse"),
    Column("Postcode", 8, "postnr", _postcode),
    Column("Type", 12, "type", lambda r: display.shorten(_kind(r), 12),
           text=_kind),
    Column("Area", 6, "areal",
           lambda r: display.area(r.get("boligareal_m2") or r.get("areal_m2")),
           field="boligareal_m2"),
    Column("Valuation", 10, "vurdering",
           lambda r: display.compact_kr(r.get("ejendomsvurdering_dkk")),
           field="ejendomsvurdering_dkk"),
    Column("Debt", 10, "gaeld",
           lambda r: display.compact_kr(r.get("samlet_gaeld_dkk")),
           field="samlet_gaeld_dkk"),
    Column("LTV", 6, "belaant",
           lambda r: display.pct(r.get("belaaningsgrad_pct"), 0),
           field="belaaningsgrad_pct"),
    Column("Owners", 16, "ejer", lambda r: display.shorten(r.get("ejere"), 16),
           field="ejere"),
    Column("Fetched", 9, "hentet", lambda r: display.ago(r.get("hentet")),
           field="hentet"),
)
MITID = next(index for index, column in enumerate(COLUMNS) if column.name == "mitid")
BY_NAME = {column.name: column for column in COLUMNS}

# The questions with a fixed set of answers. A dropdown filled from the data
# beats a filter grammar for these: nobody should have to know that the field
# is spelled `_postnr`, or guess which spellings of a boligtype are in there.
FACETS = (
    ("facet-by", "Town", "_by"),
    ("facet-postnr", "Postcode", "_postnr"),
    ("facet-type", "Type", "_type"),
    ("facet-mitid", "MitID", "_mitid"),
)
ALL = "\x00alle"


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
        self.chosen: dict[str, str] = {}
        # Set when a click landed on the tick column, so the row-selected
        # message that follows it opens nothing.
        self._ticked_by_click = False
        self.sort_by = len(COLUMNS) - 1  # Hentet: newest lookup first
        self.descending = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionBar()
        yield NavTabs("ejendomme")
        # The box and the button are deliberately not peers. One narrows a list
        # that is already here; the other goes out to the register and costs a
        # request. Labelling the box "Filter" and giving the other its own
        # button is what stops the box reading as a way to find new addresses.
        with Horizontal(id="library-bar"):
            yield Static(i18n.t("Filter"), id="library-filter-label")
            yield Input(placeholder=i18n.t("address or owner"), id="library-filter")
            yield Static("", id="library-count")
            yield Button(i18n.t("＋ Find new property"), id="library-new",
                         variant="primary")
        with Horizontal(id="library-facets"):
            for widget_id, label, _ in FACETS:
                yield Static(i18n.t(label), classes="facet-label")
                yield Select([], prompt=i18n.t("all"), id=widget_id,
                             classes="facet-select")
        yield Static("", id="library-scope")
        yield DataTable(id="library-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="library-empty", classes="empty-state")
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#library-table", DataTable)
        table.add_column("", width=3)
        for column in COLUMNS:
            table.add_column(i18n.t(column.label), width=column.width)
        self.action_refresh()

    # ── loading ─────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        held = store.library(self.app.database)
        self.app.call_from_thread(self._loaded, held)

    def _loaded(self, held: list[dict]) -> None:
        # The same derived fields the figures screen groups by - building,
        # floor, postcode, town - so a town means the same thing on both.
        self.held = stats.annotate(held)
        self._fill_facets()
        self._apply_filter(self.query_one("#library-filter", Input).value)
        # An empty library on the first run is a screen explaining where the
        # search box is. Go there instead - escape comes straight back.
        if not held and self.app.consume_first_run():
            self.app.action_search()

    def _fill_facets(self) -> None:
        """Fill each dropdown from the whole library, once per load.

        Not cascading, for the reason the figures screen is not: rebuilding a
        Select's options from inside its own change handler means reacting to a
        message that arrives while the last one is still settling, and the two
        chase each other.
        """
        for widget_id, _, field in FACETS:
            select = self.query_one(f"#{widget_id}", Select)
            select.set_options(
                [("alle", ALL)]
                + [(f"{name}  ({n})", name)
                   for name, n in stats.choices(self.held, field)]
            )
            select.value = self.chosen.get(field) or ALL

    @on(Select.Changed, ".facet-select")
    def _facet_changed(self, event: Select.Changed) -> None:
        field = next((f for wid, _, f in FACETS if wid == event.select.id), "")
        if not field:
            return
        chosen = None if event.value in (Select.BLANK, ALL, None) else str(event.value)
        # Idempotent: filling the dropdowns sets their values, and that change
        # arrives as a message once this handler has already returned.
        if self.chosen.get(field) == chosen:
            return
        if chosen is None:
            self.chosen.pop(field, None)
        else:
            self.chosen[field] = chosen
        self._apply_filter(self.query_one("#library-filter", Input).value)

    def action_clear_filter(self) -> None:
        """Escape clears the text, then the dropdowns, then leaves the box."""
        field = self.query_one("#library-filter", Input)
        if field.value:
            field.value = ""
        elif self.chosen:
            self.chosen.clear()
            self._fill_facets()
            self._apply_filter("")
        else:
            self.query_one("#library-table", DataTable).focus()

    def queue_changed(self) -> None:
        """A finished fetch has changed what the database holds."""
        if not self.app.fetching.running:
            self.action_refresh()

    # ── filtering ───────────────────────────────────────────────────────

    def _apply_filter(self, needle: str) -> None:
        self.shown = [
            row
            for row in self.held
            if all(row.get(f) == v for f, v in self.chosen.items())
            and _matches(row, needle)
        ]
        self._sort()

    def _sort(self) -> None:
        column = COLUMNS[self.sort_by]
        self.shown.sort(key=column.sort_key, reverse=self.descending)
        self._fill()

    def action_sort_next(self) -> None:
        self.sort_by = (self.sort_by + 1) % len(COLUMNS)
        self._sort()
        self.notify(i18n.t("Sorted by {name}.",
                            name=i18n.t(COLUMNS[self.sort_by].label)))

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
            table.add_row(self._tick_cell(row["uuid"]), *cells, key=row["uuid"])
        self._describe()
        # Only on the way in. A reload can land while the filter is being typed
        # in, or after a background re-fetch, and pulling the cursor out of the
        # box mid-word would be maddening.
        if self.shown and self.screen.focused is None:
            table.focus()

    def _mitid_cell(self, beriget) -> Text:
        """Whether this row was fetched while logged in with MitID.

        A plain yes or no. It used to read "fuld" and "delvis", which described
        the consequence rather than the fact and left people working out which
        was which - the question is only ever whether the login was on.
        """
        theme = self.app.current_theme
        if beriget is None:
            return Text("–", style="dim")
        if beriget:
            return Text(i18n.t("✓ yes"), style=f"bold {theme.success or 'green'}")
        return Text(i18n.t("✗ no"), style=f"bold {theme.warning or 'yellow'}")

    def _describe(self) -> None:
        column = COLUMNS[self.sort_by]
        arrow = "↓" if self.descending else "↑"
        count = self.query_one("#library-count", Static)
        held, shown = len(self.held), len(self.shown)
        ticked = (
            i18n.t("{n} ticked · ", n=len(self.ticked)) if self.ticked else ""
        )
        if held:
            whole = (
                i18n.t("{n} property", n=held) if held == 1
                else i18n.t("{n} properties", n=held)
            )
            count.update(
                f"{ticked}" + (i18n.t("{shown} of {held}", shown=shown, held=held)
                               if shown != held else whole)
            )
        else:
            count.update("")
        chosen = "  ·  ".join(
            f"{i18n.t(label)}: {self.chosen[field]}"
            for _, label, field in FACETS
            if self.chosen.get(field)
        )
        self.query_one("#library-scope", Static).update(
            i18n.t(
                "Already fetched — searching here never leaves the database. "
                "Sorted by {name} {arrow} (o, i) · ＋ for a new address.",
                name=i18n.t(column.label), arrow=arrow,
            )
            + (f"\n{chosen}" if chosen else "")
        )

        table = self.query_one("#library-table", DataTable)
        empty = self.query_one("#library-empty", Static)
        table.display = bool(self.shown)
        empty.display = not self.shown
        typed = self.query_one("#library-filter", Input).value.strip()
        if not self.held:
            empty.update(
                i18n.t(
                    "Nothing fetched yet.\n\nPress / to look an address up, "
                    "or b for the queue.\n\n{path} does not exist.",
                    path=self.app.database,
                )
            )
        elif typed:
            # The most useful thing to say to someone whose filter found
            # nothing is that the register might still have it - and that
            # enter, right here, is how to go and ask.
            empty.update(
                i18n.t(
                    "Nothing in your library matches {typed!r}.\n\n"
                    "Press enter to search the register for it,\n"
                    "or escape to clear the filter.",
                    typed=typed,
                )
            )
        else:
            empty.update(i18n.t("Nothing matches that filter."))

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

    def show_only(self, text: str) -> None:
        """Narrow to one thing by name, as though it had been typed."""
        self.chosen.clear()
        self._fill_facets()
        field = self.query_one("#library-filter", Input)
        field.value = text
        self._apply_filter(text)

    def action_focus_filter(self) -> None:
        self.query_one("#library-filter", Input).focus()

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
        if row is not None:
            self._toggle(row["uuid"])

    def _toggle(self, uuid: str) -> None:
        """Tick or untick one row, in place.

        Repainting the one cell rather than refilling the table, because
        clearing a DataTable puts the cursor back on the first row - so ticking
        the fortieth property walked you back to the first, every time.
        """
        self.ticked.symmetric_difference_update({uuid})
        table = self.query_one("#library-table", DataTable)
        for index, row in enumerate(self.shown):
            if row["uuid"] == uuid:
                table.update_cell_at(Coordinate(index, 0), self._tick_cell(uuid))
                break
        self._describe()

    def action_tick_all(self) -> None:
        """Everything the filter is currently showing, not the whole database."""
        self.ticked = {row["uuid"] for row in self.shown}
        self._repaint_ticks()

    def action_tick_none(self) -> None:
        self.ticked.clear()
        self._repaint_ticks()

    def _repaint_ticks(self) -> None:
        table = self.query_one("#library-table", DataTable)
        for index, row in enumerate(self.shown):
            table.update_cell_at(Coordinate(index, 0), self._tick_cell(row["uuid"]))
        self._describe()

    def _tick_cell(self, uuid: str) -> str:
        return "✓" if uuid in self.ticked else ""

    @on(events.Click)
    def _clicked(self, event: events.Click) -> None:
        """A click in the tick column ticks, rather than opening the property.

        The DataTable has already moved its cursor and queued a RowSelected by
        the time this runs, so the flag is what stops that turning into an
        open. Everywhere else on the row still opens it.
        """
        meta = event.style.meta
        if meta.get("column") != 0 or "row" not in meta:
            return
        index = meta["row"]
        if 0 <= index < len(self.shown):
            self._toggle(self.shown[index]["uuid"])
            self._ticked_by_click = True

    def _chosen(self) -> list[dict]:
        """The ticked rows, or the one under the cursor when none are ticked."""
        if self.ticked:
            return [row for row in self.shown if row["uuid"] in self.ticked]
        row = self._selected()
        return [row] if row else []

    # ── acting on them ──────────────────────────────────────────────────

    @on(DataTable.RowSelected, "#library-table")
    def _opened(self, event: DataTable.RowSelected) -> None:
        if self._ticked_by_click:
            self._ticked_by_click = False
            return
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
            self.notify(i18n.t("Nothing to re-fetch."))
            return
        added = self.app.enqueue_refetch([row["adresse"] for row in chosen])
        self.ticked.clear()
        self._repaint_ticks()
        self.notify(
            i18n.t("Queued {n} to re-fetch. {note}",
                   n=(i18n.t("{n} property", n=added) if added == 1
                      else i18n.t("{n} properties", n=added)),
                   note=self.app.queued_note())
            if added
            else i18n.t("Those are already in the queue.")
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
            title = i18n.t(
                "Export {n} ticked",
                n=(i18n.t("{n} property", n=len(uuids)) if len(uuids) == 1
                   else i18n.t("{n} properties", n=len(uuids))),
            )
        else:
            # Reading the whole database can take a moment on a big one, and the
            # dialog has nothing to show until it is read.
            tables = await asyncio.to_thread(store.everything, self.app.database)
            title = i18n.t("Export the whole database")
        if not tables:
            self.notify(i18n.t("There is nothing to export yet."))
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
