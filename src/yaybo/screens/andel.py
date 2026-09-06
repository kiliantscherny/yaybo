"""One co-op share in full, read back out of the database.

The register's own public view of a share is a single page: the address and
its municipality codes, then the notices, then the charges with everyone named
on each. All of that is fetched and stored, and until this screen existed none
of it could be looked at - the Andele list counts the charges but never shows
them, which made the names on them unreachable from inside the application.

Three tabs rather than one page, to match how a property is shown, and the
counts on the tab labels are what say a share has no notices rather than
leaving an empty tab looking broken.

The Oversigt tab says what the book does not hold as plainly as what it does.
A share has no valuation, no matrikel and no owner of record, and a screen that
simply left those out would read as a fetch that had gone wrong.
"""

from __future__ import annotations

from rich.table import Table as RichTable
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)

from yaybo import display, pipeline, store
from yaybo.screens.base import YayboScreen
from yaybo.widgets.nav import NavTabs
from yaybo.widgets.queue_bar import QueueBar
from yaybo.widgets.session_bar import SessionBar

# label, column, how to write it, how wide
HAEFTELSER = (
    ("Pri.", "prioritet", display.number, 5),
    ("Dato/løbenr.", "dato_loebenummer", display.text, 22),
    ("Type", "dokumenttype", display.text, 20),
    ("Hovedstol", "hovedstol_dkk", display.kr, 15),
    ("Rentetype", "rentetype", display.text, 11),
    ("Rente", "rentesats_pct", display.pct, 8),
    ("Kreditorer", "kreditorer", display.text, 40),
)
MEDDELELSER = (
    ("Dato/løbenr.", "dato_loebenummer", display.text, 22),
    ("Type", "dokumenttype", display.text, 22),
    ("Afgjort", "afgoerelsesdato", display.when, 12),
    ("Debitorer", "debitorer", display.text, 30),
    ("Disponenter", "disponenter", display.text, 30),
    ("Tillægstekst", "tillaegstekst", display.text, 40),
)

COUNTED_TABS = {
    "tab-andel-haeftelser": ("Hæftelser", "andel_haeftelser"),
    "tab-andel-meddelelser": ("Meddelelser", "andel_meddelelser"),
}


class AndelScreen(YayboScreen):
    """Everything the database holds about one co-op share."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("g", "building", "Its building"),
        Binding("e", "export", "Export"),
        Binding("f", "refetch", "Re-fetch"),
    ]

    def __init__(self, uuid: str) -> None:
        super().__init__()
        self.uuid = uuid
        self.tables: dict[str, list[dict]] = {}

    @property
    def andel_row(self) -> dict:
        rows = self.tables.get("andele") or [{}]
        return rows[0]

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionBar()
        yield NavTabs("andele")
        yield Static("Loading…", id="andel-title")
        with TabbedContent(id="andel-tabs"):
            with TabPane("Oversigt", id="tab-andel-overview"):
                yield VerticalScroll(id="andel-overview")
            with TabPane("Hæftelser", id="tab-andel-haeftelser"):
                yield DataTable(
                    id="table-andel-haeftelser", cursor_type="row", zebra_stripes=True
                )
            with TabPane("Meddelelser", id="tab-andel-meddelelser"):
                yield DataTable(
                    id="table-andel-meddelelser", cursor_type="row", zebra_stripes=True
                )
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        # After the refresh, for the same reason PropertyScreen waits: a
        # screen's on_mount can arrive before the widgets inside its tab panes
        # have been mounted, and which race wins varies by machine.
        self.call_after_refresh(self._prepare_tables)

    def _prepare_tables(self) -> None:
        for identifier, spec in (
            ("#table-andel-haeftelser", HAEFTELSER),
            ("#table-andel-meddelelser", MEDDELELSER),
        ):
            table = self.query_one(identifier, DataTable)
            for label, _, _, width in spec:
                table.add_column(label, width=width)
        self.action_refresh()

    # ── loading ─────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        tables = store.andel_tables(self.app.database, self.uuid)
        self.app.call_from_thread(self._fill, tables)

    def _fill(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        row = self.andel_row
        if not row:
            self.query_one("#andel-title", Static).update(
                "That andel is no longer in the database."
            )
            return

        title = Text()
        title.append(display.text(row.get("adresse")), style="bold")
        title.append("   andel", style="dim")
        title.append(f"   fetched {display.ago(row.get('hentet'))}", style="dim")
        self.query_one("#andel-title", Static).update(title)

        self._fill_overview(row)
        self._fill_table(
            "#table-andel-haeftelser", HAEFTELSER, tables.get("andel_haeftelser")
        )
        self._fill_table(
            "#table-andel-meddelelser", MEDDELELSER, tables.get("andel_meddelelser")
        )
        self._label_tabs()

    def _fill_table(self, identifier: str, spec, rows: list[dict] | None) -> None:
        table = self.query_one(identifier, DataTable)
        table.clear()
        for row in rows or []:
            table.add_row(
                *[
                    display.shorten(write(row.get(column)), width)
                    for _, column, write, width in spec
                ]
            )

    def _label_tabs(self) -> None:
        tabs = self.query_one("#andel-tabs", TabbedContent)
        for identifier, (label, table) in COUNTED_TABS.items():
            count = len(self.tables.get(table) or [])
            try:
                tab = tabs.get_tab(identifier)
            except Exception:  # noqa: BLE001 - a missing tab is not worth raising
                continue
            tab.label = f"{label} {count}" if count else label

    def _fill_overview(self, row: dict) -> None:
        panel = self.query_one("#andel-overview", VerticalScroll)
        panel.remove_children()

        charges = self.tables.get("andel_haeftelser") or []
        notices = self.tables.get("andel_meddelelser") or []
        # The nearest this book comes to naming who holds the share. An
        # ejerpantebrev is a deed the owner issues to themselves, so its
        # creditor is in practice the andelshaver - said as an inference,
        # because the register never states it.
        issued = "; ".join(
            display.text(charge.get("kreditorer"))
            for charge in charges
            if (charge.get("dokumenttype") or "").lower() == "ejerpantebrev"
            and charge.get("kreditorer")
        )

        sections = [
            (
                "Andelsboligen",
                [
                    ("Adresse", display.text(row.get("adresse"))),
                    ("Etage/dør", display.text(row.get("lejlighed"))),
                    ("Kommunekode", display.text(row.get("kommunekode"))),
                    ("Vejkode", display.text(row.get("vejkode"))),
                    ("Boligareal", display.area(row.get("boligareal_m2"))),
                    ("Boligtype", display.text(row.get("boligtype"))),
                    ("Til salg", display.text(row.get("til_salg"))),
                ],
            ),
            (
                "Hæftelser på andelen",
                [
                    ("Antal", display.number(len(charges))),
                    ("Samlet gæld", display.kr(row.get("samlet_gaeld_dkk"), unit="kr.")),
                    ("Meddelelser", display.number(len(notices))),
                ],
            ),
            (
                "Foreningens ejendom",
                [
                    ("Adresse", display.text(row.get("bygning_adresse"))),
                    ("Ejendom", display.text(row.get("ejendom_uuid"))),
                ],
            ),
        ]
        if issued:
            sections.append(("Udstedt ejerpantebrev til", [("Navn", issued)]))

        for heading, pairs in sections:
            panel.mount(Static(heading, classes="section-heading"))
            panel.mount(Static(_facts(pairs), classes="facts"))

        panel.mount(Static("Hvad bogen ikke har", classes="section-heading"))
        panel.mount(
            Static(
                "An andel is not real property, so the andelsboligbog records no "
                "valuation, no matrikel, no area and no easements for it, and no "
                "owner: it registers rights over a share, not title to one. Who "
                "holds it is the association's record.\n\n"
                "Samlet gæld above is what is charged against this share alone. "
                "It is not what living here owes - an andelshaver also owes a "
                "portion of the association's own mortgage, which is registered "
                "against the building. Press g for that.",
                classes="facts",
            )
        )

    # ── acting on it ────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_building(self) -> None:
        building = display.text(self.andel_row.get("bygning_adresse"), "")
        if not building:
            self.notify("No building recorded for this andel.", severity="warning")
            return
        self.app.library_for(building)

    @work
    async def action_export(self) -> None:
        from yaybo.widgets.export_dialog import ExportDialog

        if not self.tables:
            self.notify("Nothing stored for this andel yet.")
            return
        await self.app.push_screen_wait(
            ExportDialog(
                self.tables,
                display.text(self.andel_row.get("adresse"), "andel"),
                title="Export this andel",
            )
        )

    def action_refetch(self) -> None:
        address = self.andel_row.get("adresse")
        if not address:
            return
        self.notify(f"Re-fetching {address}…")
        self._refetch(address)

    @work(thread=True, exclusive=True, group="refetch")
    def _refetch(self, address: str) -> None:
        try:
            # Two: the share and the association's building, which is what its
            # address resolves to. See app.enqueue_refetch.
            bundle = pipeline.lookup(self.app.api, address, limit=2, delay=0)
            store.save(self.app.database, bundle.tables)
        except Exception as error:  # noqa: BLE001 - shown to the user verbatim
            self.app.call_from_thread(
                self.notify, f"Could not re-fetch: {error}", severity="error"
            )
            return
        self.app.call_from_thread(self.notify, "Re-fetched.")
        self.app.call_from_thread(self.action_refresh)


def _facts(pairs) -> RichTable:
    """A label/value block, aligned on the labels."""
    grid = RichTable.grid(padding=(0, 2))
    grid.add_column(justify="right", style="dim", no_wrap=True, min_width=20)
    grid.add_column(overflow="fold")
    for label, value in pairs:
        grid.add_row(label, str(value))
    return grid
