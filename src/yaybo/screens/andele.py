"""What the database holds out of the andelsboligbog, one share to a row.

A share is not a property and this is not the Ejendomme tab with a filter on
it. The register keeps them in a different book, and that book records far
less: no valuation, no matrikel, no area, no easements, nobody's name. What it
does record is what is charged against the share, which is the one figure here
that comes from the register at all - the area beside it is Boligsiden's, and
the valuation is the association's building rather than the flat.

Hence the two columns on the right. `Gæld` is what this share owes; `Bygning`
is the property the association owns, which is where a co-op flat's other
liability lives - a share of the association's own mortgage, which is nowhere
in this book and is not added in here.

There is no owner column because the book has no owner. It registers rights
over a share, not title to one; who holds an andel is the association's
record, not the register's. `Medd.` is as close as it comes: a notice names
the andelshaver, and is noted when something has happened to them rather than
to the flat.
"""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from yaybo import display, store
from yaybo.register.fields import normalise
from yaybo.screens.base import YayboScreen
from yaybo.widgets.nav import NavTabs
from yaybo.widgets.queue_bar import QueueBar
from yaybo.widgets.session_bar import SessionBar

COLUMNS = (
    ("Andel", 38),
    ("Etage", 7),
    ("Areal", 6),
    ("Hæft.", 6),
    ("Medd.", 6),
    ("Gæld", 10),
    ("Gæld/m²", 9),
    ("Til salg", 8),
    ("Bygning", 28),
    ("Hentet", 9),
)


class AndeleScreen(YayboScreen):
    """Every co-op share held, newest first."""

    AUTO_FOCUS = "#andele-table"

    BINDINGS = [
        Binding("enter", "open", "Open"),
        # g rather than b: b is the queue everywhere else, and shadowing a
        # global with something unrelated is worse than not binding it. g is
        # Bygninger globally, and this is that narrowed to one andel - the
        # same shadowing PropertyScreen does with k.
        Binding("g", "open_building", "Its building"),
        Binding("f", "refetch", "Re-fetch"),
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
        yield SessionBar()
        yield NavTabs("andele")
        with Horizontal(id="andele-bar"):
            yield Static("Søg", id="andele-filter-label")
            yield Input(placeholder="address", id="andele-filter")
            yield Static("", id="andele-count")
        yield Static("", id="andele-scope")
        yield DataTable(id="andele-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="andele-empty", classes="empty-state")
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#andele-table", DataTable)
        for label, width in COLUMNS:
            table.add_column(label, width=width)
        self.action_refresh()

    # ── loading ─────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        held = store.andele(self.app.database)
        self.app.call_from_thread(self._loaded, held)

    def _loaded(self, held: list[dict]) -> None:
        self.held = held
        self._apply_filter(self.query_one("#andele-filter", Input).value)

    def queue_changed(self) -> None:
        if not self.app.fetching.running:
            self.action_refresh()

    # ── filtering and drawing ───────────────────────────────────────────

    @on(Input.Changed, "#andele-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    def _apply_filter(self, needle: str) -> None:
        wanted = normalise(needle)
        self.shown = [
            row for row in self.held
            if not wanted or wanted in normalise(str(row.get("adresse") or ""))
        ]
        self._fill()

    def _fill(self) -> None:
        table = self.query_one("#andele-table", DataTable)
        if not table.columns:
            return
        table.clear()
        for row in self.shown:
            debt = row.get("samlet_gaeld_dkk")
            area = row.get("boligareal_m2")
            table.add_row(
                display.shorten(str(row.get("adresse") or ""), 38),
                str(row.get("lejlighed") or "—"),
                display.area(area),
                str(row.get("antal_haeftelser") or 0),
                self._notices(row),
                display.compact_kr(debt),
                display.compact_kr(debt / area if debt and area else None),
                self._for_sale(row),
                display.shorten(str(row.get("bygning") or "—"), 28),
                display.ago(row.get("hentet")),
                key=str(row.get("uuid")),
            )
        self._describe()

    def _notices(self, row: dict) -> Text:
        """How many notices are noted on the share.

        Worth colouring rather than counting quietly. A notice is the register
        recording that something has happened to the andelshaver - a death, a
        bankruptcy, a court taking away their power to dispose of it - and it
        is the only thing in this book that names anybody but a creditor.
        """
        count = row.get("antal_meddelelser") or 0
        if not count:
            return Text("—", style="dim")
        theme = self.app.current_theme
        return Text(str(count), style=f"bold {theme.warning or 'yellow'}")

    def _for_sale(self, row: dict) -> Text:
        listed = row.get("til_salg")
        if listed is None:
            return Text("—", style="dim")
        theme = self.app.current_theme
        if listed:
            return Text("ja", style=f"bold {theme.success or 'green'}")
        return Text("nej", style="dim")

    def _describe(self) -> None:
        held, shown = len(self.held), len(self.shown)
        self.query_one("#andele-count", Static).update(
            f"{shown} of {held}" if shown != held else f"{held} andel(e)"
        )
        owed = sum(row.get("samlet_gaeld_dkk") or 0 for row in self.shown)
        self.query_one("#andele-scope", Static).update(
            f"{shown} andel(e) · {display.compact_kr(owed)} charged against them. "
            "Not what they owe: a share of the association's own mortgage sits "
            "against the building in the tingbog. enter opens the andel and "
            "everyone named on its charges, g opens the building."
        )
        empty = self.query_one("#andele-empty", Static)
        empty.display = not self.shown
        if not self.shown:
            empty.update(
                "Nothing from the andelsboligbog yet.\n\n"
                "Look an address up on Søg - a co-op building answers with its "
                "shares as well as the association's property."
                if not self.held
                else "No andel matches that."
            )

    # ── acting on one ───────────────────────────────────────────────────

    def _current(self) -> dict | None:
        table = self.query_one("#andele-table", DataTable)
        row = table.cursor_row
        if not self.shown or row < 0 or row >= len(self.shown):
            return None
        return self.shown[row]

    @on(DataTable.RowSelected, "#andele-table")
    def _opened(self, event: DataTable.RowSelected) -> None:
        """Enter, and a click on a row.

        The binding above is what puts `enter` in the footer; this is what
        makes it do anything. A focused DataTable takes the key for itself and
        answers with RowSelected, so a screen that only declares the binding
        has a key in its footer that does nothing at all - which is what this
        tab shipped with.
        """
        self._open(str(event.row_key.value))

    def _open(self, uuid: str) -> None:
        from yaybo.screens.andel import AndelScreen

        if uuid:
            self.app.push_screen(AndelScreen(uuid))

    def action_open(self) -> None:
        """Open the share itself.

        The list counts a share's charges without showing them, so everyone
        named on one - which is as close as this book comes to saying who holds
        the flat - was unreachable until enter led here.
        """
        row = self._current()
        if row is not None:
            self._open(str(row.get("uuid")))

    def action_open_building(self) -> None:
        """Go to the association's property, for what a share does not have:
        the valuation, the easements and the association's own mortgages."""
        row = self._current()
        if row is None:
            return
        building = str(row.get("bygning") or row.get("bygning_adresse") or "")
        if not building:
            self.notify("No building found for this andel.", severity="warning")
            return
        self.app.library_for(building)

    def action_refetch(self) -> None:
        row = self._current()
        if row is None:
            return
        # Two, not one: an andel's address resolves to the share itself and to
        # the association's building, and re-fetching the share alone would
        # drop the only property row it joins to.
        added = self.app.enqueue_refetch([str(row.get("adresse") or "")], limit=2)
        if added:
            self.notify(f"Queued 1 andel. {self.app.queued_note()}")

    def action_focus_filter(self) -> None:
        self.query_one("#andele-filter", Input).focus()

    def action_clear_filter(self) -> None:
        box = self.query_one("#andele-filter", Input)
        if box.value:
            box.value = ""
        else:
            self.query_one("#andele-table", DataTable).focus()
