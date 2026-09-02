"""One property in full, read back out of the database a tab at a time.

Everything here comes from the stored rows rather than from the register, so
opening a property costs nothing and works on a train. It is also why the
screen and the exports can never disagree: they are reading the same tables.

The two tabs worth explaining are Forløb and Kurve. Forløb is the one view the
register itself does not offer - sales, transfers, mortgages and easements are
four separate lists there, and putting them on one timeline is what makes a
property's story legible. Kurve plots what the place has sold for per square
metre, which is the only figure that compares one flat to another.
"""

from __future__ import annotations

from rich.table import Table as RichTable
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual_plotext import PlotextPlot

from yaybo import display, pipeline, store

# label, column, how to write it, how wide
EJERE = (
    ("#", "nummer", display.number, 4),
    ("Navn", "navn", display.text, 34),
    ("Født", "foedselsdato", display.when, 12),
    ("CVR", "cvr", display.text, 10),
    ("Andel", "andel", display.text, 10),
)
HAEFTELSER = (
    ("Pri.", "prioritet", display.number, 5),
    ("Dato/løbenr.", "dato_loebenummer", display.text, 18),
    ("Type", "dokumenttype", display.text, 22),
    ("Hovedstol", "hovedstol_dkk", display.kr, 14),
    ("Rente", "rentesats_pct", display.pct, 9),
    ("Låntype", "laantype_estimat", display.text, 14),
    ("Kreditor", "kreditorer", display.text, 28),
    ("Tinglyst", "tinglysningsdato", display.when, 12),
)
SERVITUTTER = (
    ("Pri.", "prioritet", display.number, 5),
    ("Dato/løbenr.", "dato_loebenummer", display.text, 18),
    ("Type", "dokumenttype", display.text, 26),
    ("Om", "tekst", display.text, 46),
    ("Påtaleberettiget", "paataleberettigede", display.text, 26),
    ("Tinglyst", "tinglysningsdato", display.when, 12),
)
PARTER = (
    ("Dokument", "dokumentart", display.text, 12),
    ("Rolle", "rolle", display.text, 16),
    ("Navn", "navn", display.text, 34),
    ("Født", "foedselsdato", display.when, 12),
    ("CVR", "cvr", display.text, 10),
    ("Andel", "andel", display.text, 10),
)
UNDERPANT = (
    ("Pri.", "prioritet", display.number, 5),
    ("Dato/løbenr.", "dato_loebenummer", display.text, 18),
    ("Beløb", "beloeb_dkk", display.kr, 14),
    ("Panthaver", "panthavere", display.text, 40),
)
HANDLER = (
    ("Dato", "dato", display.when, 12),
    ("Beløb", "beloeb_dkk", display.kr, 14),
    ("Areal", "areal_m2", display.area, 9),
    ("Pr. m²", "pris_pr_m2", display.kr, 11),
    ("Handelstype", "handelstype", display.text, 20),
)

# Which tabs carry a row count, and which table they are counting.
COUNTED_TABS = {
    "tab-ejere": ("Ejere", "ejere"),
    "tab-haeftelser": ("Hæftelser", "haeftelser"),
    "tab-servitutter": ("Servitutter", "servitutter"),
    "tab-parter": ("Parter", "dokument_parter"),
    "tab-handler": ("Handler", "handelshistorik"),
    "tab-bygning": ("Bygning", "bygninger"),
}

# The theme's amber and verdigris, since plotext does not know about it.
LINE = (224, 164, 88)
POINT = (127, 179, 163)

TIMELINE = (
    ("Dato", 12),
    ("Hvad", 26),
    ("Beløb", 14),
    ("Detalje", 60),
)


class PropertyScreen(Screen):
    """Everything the database holds about one property."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("e", "export", "Export"),
        Binding("f", "refetch", "Re-fetch"),
    ]

    def __init__(self, uuid: str) -> None:
        super().__init__()
        self.uuid = uuid
        self.tables: dict[str, list[dict]] = {}

    @property
    def property_row(self) -> dict:
        rows = self.tables.get("ejendomme") or [{}]
        return rows[0]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Loading…", id="property-title")
        with TabbedContent(id="property-tabs"):
            with TabPane("Oversigt", id="tab-overview"):
                yield VerticalScroll(id="overview")
            with TabPane("Ejere", id="tab-ejere"):
                yield DataTable(id="table-ejere", cursor_type="row", zebra_stripes=True)
            with TabPane("Hæftelser", id="tab-haeftelser"):
                with VerticalScroll():
                    yield DataTable(
                        id="table-haeftelser", cursor_type="row", zebra_stripes=True
                    )
                    # A pledge of the mortgage deed itself. Rare, and meaningless
                    # away from the charge it sits on, so it goes here rather
                    # than in a tab of its own.
                    yield Static("Underpant", classes="section-heading")
                    yield DataTable(
                        id="table-underpant", cursor_type="row", zebra_stripes=True
                    )
            with TabPane("Servitutter", id="tab-servitutter"):
                yield DataTable(id="table-servitutter", cursor_type="row", zebra_stripes=True)
            with TabPane("Parter", id="tab-parter"):
                yield DataTable(id="table-parter", cursor_type="row", zebra_stripes=True)
            with TabPane("Handler", id="tab-handler"):
                yield DataTable(id="table-handler", cursor_type="row", zebra_stripes=True)
            with TabPane("Forløb", id="tab-timeline"):
                yield DataTable(id="table-timeline", cursor_type="row", zebra_stripes=True)
            with TabPane("Kurve", id="tab-chart"):
                yield PlotextPlot(id="chart")
                yield Static("", id="chart-note", classes="hint-text")
            with TabPane("Bygning", id="tab-bygning"):
                yield VerticalScroll(id="bygning")
            with TabPane("Dokument", id="tab-dokument"):
                yield TextArea("", read_only=True, id="dokument")
        yield Footer()

    def on_mount(self) -> None:
        for identifier, spec in (
            ("#table-ejere", EJERE),
            ("#table-haeftelser", HAEFTELSER),
            ("#table-underpant", UNDERPANT),
            ("#table-servitutter", SERVITUTTER),
            ("#table-parter", PARTER),
            ("#table-handler", HANDLER),
        ):
            table = self.query_one(identifier, DataTable)
            for label, _, _, width in spec:
                table.add_column(label, width=width)
        timeline = self.query_one("#table-timeline", DataTable)
        for label, width in TIMELINE:
            timeline.add_column(label, width=width)
        self.action_refresh()

    # ── loading ─────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        tables = store.property_tables(self.app.database, self.uuid)
        self.app.call_from_thread(self._fill, tables)

    def _fill(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        row = self.property_row
        if not row:
            self.query_one("#property-title", Static).update(
                "That property is no longer in the database."
            )
            return

        title = Text()
        title.append(display.text(row.get("adresse")), style="bold")
        kind = display.boligtype(row.get("boligtype")) or display.text(row.get("ejendomstype"), "")
        if kind:
            title.append(f"   {kind}", style="dim")
        title.append(f"   fetched {display.ago(row.get('hentet'))}", style="dim")
        self.query_one("#property-title", Static).update(title)

        self._fill_overview(row)
        self._fill_table("#table-ejere", EJERE, tables.get("ejere"))
        self._fill_table("#table-haeftelser", HAEFTELSER, tables.get("haeftelser"))
        self._fill_table("#table-underpant", UNDERPANT, tables.get("underpant"))
        self._fill_table("#table-servitutter", SERVITUTTER, tables.get("servitutter"))
        self._fill_table("#table-parter", PARTER, tables.get("dokument_parter"))
        self._fill_table("#table-handler", HANDLER, tables.get("handelshistorik"))
        self._fill_timeline()
        self._fill_chart()
        self._fill_bygning()
        self._fill_document()
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
        """Put the row count on each tab, so an empty one is empty on purpose."""
        tabs = self.query_one("#property-tabs", TabbedContent)
        for identifier, (label, table) in COUNTED_TABS.items():
            count = len(self.tables.get(table) or [])
            try:
                tab = tabs.get_tab(identifier)
            except Exception:
                continue
            tab.label = f"{label} {count}" if count else label

    # ── the tabs that are not just a table ──────────────────────────────

    def _fill_overview(self, row: dict) -> None:
        panel = self.query_one("#overview", VerticalScroll)
        panel.remove_children()

        owners = self.tables.get("ejere") or []
        owned = [
            f"{display.text(o.get('navn'))}"
            + (f"  ({display.text(o.get('andel'))})" if o.get("andel") else "")
            + (f"  f. {display.when(o.get('foedselsdato'))}" if o.get("foedselsdato") else "")
            for o in owners
        ]

        # Both of these run off the public valuation, which sits well below what
        # a place would fetch, so one is a floor and the other a ceiling. Said
        # here rather than only in the docs, because a number on a screen gets
        # believed.
        equity = display.kr(row.get("frivaerdi_dkk"), unit="kr.")
        loaded = display.pct(row.get("belaaningsgrad_pct"))

        sections = [
            (
                "Ejendommen",
                [
                    ("Adresse", display.text(row.get("adresse"))),
                    ("Type", display.boligtype(row.get("boligtype"))
                     or display.text(row.get("ejendomstype"))),
                    ("Boligareal", display.area(row.get("boligareal_m2"))),
                    ("Tinglyst areal", display.area(row.get("areal_m2"))),
                    ("BFE-nummer", display.text(row.get("bfe_nr"))),
                    ("Ejerlejlighedsnr.", display.text(row.get("ejerlejlighedsnr"))),
                    ("Fordelingstal", display.text(row.get("fordelingstal"))),
                    ("Matrikel", display.text(row.get("matrikel"))),
                    ("Landsejerlav", display.text(row.get("landsejerlav"))),
                    ("Kommune", display.text(row.get("kommune"))),
                ],
            ),
            (
                "Ejere",
                [(f"{n}.", owner) for n, owner in enumerate(owned, start=1)]
                or [("", "Ingen ejere registreret")],
            ),
            (
                "Værdi og gæld",
                [
                    ("Ejendomsvurdering", display.kr(row.get("ejendomsvurdering_dkk"), unit="kr.")),
                    ("Grundværdi", display.kr(row.get("grundvaerdi_dkk"), unit="kr.")),
                    ("Vurderingsdato", display.when(row.get("vurderingsdato"))),
                    ("Boligsiden vurderer", display.kr(row.get("boligsiden_vurdering_dkk"), unit="kr.")),
                    ("Samlet gæld", display.kr(row.get("samlet_gaeld_dkk"), unit="kr.")),
                    ("Friværdi (mindst)", equity),
                    ("Belåningsgrad (højst)", loaded),
                    ("Hæftelser", display.number(row.get("antal_haeftelser"))),
                    ("Servitutter", display.number(row.get("antal_servitutter"))),
                ],
            ),
            (
                "Seneste handel",
                [
                    ("Dato", display.when(row.get("seneste_salg_dato"))),
                    ("Beløb", display.kr(row.get("seneste_salg_dkk"), unit="kr.")),
                    ("Pris pr. m²", display.kr(row.get("seneste_salg_pris_m2"), unit="kr.")),
                    ("Købesum (skøde)", display.kr(row.get("koebesum_dkk"), unit="kr.")),
                    ("Overtagelse", display.when(row.get("overtagelsesdato"))),
                    ("Til salg nu", display.yes_no(row.get("til_salg"))),
                    ("Boligsiden", display.text(row.get("boligsiden_url"))),
                ],
            ),
        ]
        for heading, pairs in sections:
            panel.mount(Static(heading, classes="section-heading"))
            panel.mount(Static(_facts(pairs), classes="facts"))
        panel.mount(
            Static(
                "Friværdi and belåningsgrad are worked out against the public "
                "valuation, which runs below market. Treat the first as a floor "
                "and the second as a ceiling.",
                classes="hint-text",
            )
        )

    def _fill_bygning(self) -> None:
        panel = self.query_one("#bygning", VerticalScroll)
        panel.remove_children()
        buildings = self.tables.get("bygninger") or []
        if not buildings:
            panel.mount(
                Static(
                    "No BBR record stored. It comes from Boligsiden, which does "
                    "not answer for every address.",
                    classes="empty-state",
                )
            )
            return
        for building in buildings:
            panel.mount(
                Static(
                    display.text(building.get("bygningstype"), "Bygning")
                    + f"  (nr. {display.text(building.get('bygning_nr'))})",
                    classes="section-heading",
                )
            )
            panel.mount(
                Static(
                    _facts(
                        [
                            ("Opført", display.number(building.get("opfoerelsesaar"))),
                            ("Ombygget", display.number(building.get("ombygningsaar"))),
                            ("Etager", display.number(building.get("etager"))),
                            ("Værelser", display.number(building.get("vaerelser"))),
                            ("Badeværelser", display.number(building.get("badevaerelser"))),
                            ("Toiletter", display.number(building.get("toiletter"))),
                            ("Boligareal", display.area(building.get("boligareal_m2"))),
                            ("Kælder", display.area(building.get("kaelderareal_m2"))),
                            ("Erhverv", display.area(building.get("erhvervsareal_m2"))),
                            ("Samlet areal", display.area(building.get("samlet_areal_m2"))),
                            ("Ydervæg", display.text(building.get("ydervaeg"))),
                            ("Tag", display.text(building.get("tagdaekning"))),
                            ("Varme", display.text(building.get("varmeinstallation"))),
                            ("Supplerende varme", display.text(building.get("supplerende_varme"))),
                            ("Køkken", display.text(building.get("koekken"))),
                            ("Bad", display.text(building.get("badeforhold"))),
                            ("Toilet", display.text(building.get("toiletforhold"))),
                        ]
                    ),
                    classes="facts",
                )
            )

    def _fill_timeline(self) -> None:
        table = self.query_one("#table-timeline", DataTable)
        table.clear()
        for when, what, amount, detail in self._timeline():
            table.add_row(
                display.when(when),
                display.shorten(what, 26),
                amount,
                display.shorten(detail, 60),
            )

    def _timeline(self) -> list[tuple]:
        """Sales, transfers, charges and easements on one chronology.

        Four lists in the register, one story in real life. Sorted newest
        first; anything with no date sinks to the bottom rather than being
        dropped, because "we do not know when" is still a fact about it.
        """
        events: list[tuple] = []
        for sale in self.tables.get("handelshistorik") or []:
            per = display.kr(sale.get("pris_pr_m2"))
            events.append(
                (
                    sale.get("dato"),
                    f"Solgt · {display.text(sale.get('handelstype'), '')}".strip(" ·"),
                    display.kr(sale.get("beloeb_dkk")),
                    f"{per} kr./m² af {display.area(sale.get('areal_m2'))}"
                    if sale.get("pris_pr_m2")
                    else "",
                )
            )
        for entry in self.tables.get("adkomsthistorik") or []:
            events.append(
                (
                    entry.get("dato"),
                    f"Adkomst · {display.text(entry.get('dokumenttype'), '')}".strip(" ·"),
                    display.kr(entry.get("koebesum_dkk")),
                    _owners_of(self.tables.get("adkomsthistorik_ejere"), entry.get("post_nummer")),
                )
            )
        for charge in self.tables.get("haeftelser") or []:
            rate = display.pct(charge.get("rentesats_pct"))
            kind = display.text(charge.get("laantype_estimat"), "")
            events.append(
                (
                    charge.get("tinglysningsdato"),
                    f"Hæftelse · {display.text(charge.get('dokumenttype'), '')}".strip(" ·"),
                    display.kr(charge.get("hovedstol_dkk")),
                    " · ".join(
                        part
                        for part in (
                            kind,
                            rate if charge.get("rentesats_pct") else "",
                            display.text(charge.get("kreditorer"), ""),
                        )
                        if part
                    ),
                )
            )
        for easement in self.tables.get("servitutter") or []:
            events.append(
                (
                    easement.get("tinglysningsdato"),
                    f"Servitut · {display.text(easement.get('dokumenttype'), '')}".strip(" ·"),
                    "",
                    display.text(easement.get("tekst"), ""),
                )
            )
        row = self.property_row
        if row.get("vurderingsdato"):
            events.append(
                (
                    row.get("vurderingsdato"),
                    "Offentlig vurdering",
                    display.kr(row.get("ejendomsvurdering_dkk")),
                    f"grundværdi {display.kr(row.get('grundvaerdi_dkk'))}",
                )
            )
        # Newest first, and anything undated last rather than first: an en dash
        # sorts after every digit, so sorting on the date alone would float the
        # unknowns to the top of the list.
        events.sort(
            key=lambda event: (bool(display.parse_date(event[0])), display.iso(event[0])),
            reverse=True,
        )
        return events

    def _fill_chart(self) -> None:
        plot = self.query_one("#chart", PlotextPlot)
        note = self.query_one("#chart-note", Static)
        points = sorted(
            (
                (display.iso(sale.get("dato")), sale.get("pris_pr_m2"), sale)
                for sale in self.tables.get("handelshistorik") or []
                if sale.get("pris_pr_m2") and sale.get("dato")
            ),
            key=lambda point: point[0],
        )
        plot.plt.clear_figure()
        if len(points) < 2:
            plot.plt.clear_data()
            plot.display = False
            note.set_classes("empty-state")
            note.update(
                "Not enough recorded sales to plot.\n\nBoligsiden knows the sales "
                "an agent handled; a flat sold privately, or held for decades, has "
                "none."
            )
            return
        plot.display = True
        note.set_classes("hint-text")

        # Plotted against a plain number rather than a date axis, so the same
        # code works whatever plotext decides its date handling looks like.
        xs = [_as_year(when) for when, _, _ in points]
        ys = [float(price) for _, price, _ in points]
        plot.plt.plot(xs, ys, marker="braille", color=LINE)
        plot.plt.scatter(xs, ys, marker="●", color=POINT)
        ticks = sorted({int(x) for x in xs})
        if len(ticks) > 8:
            ticks = ticks[:: max(1, len(ticks) // 8)]
        plot.plt.xticks(ticks, [str(year) for year in ticks])
        plot.plt.title(f"Pris pr. m² · {display.text(self.property_row.get('adresse'))}")
        plot.refresh()

        first, last = ys[0], ys[-1]
        change = ((last / first) - 1) * 100 if first else 0
        note.update(
            f"{len(points)} recorded sales, {points[0][0][:4]}–{points[-1][0][:4]}. "
            f"{display.kr(first)} → {display.kr(last)} kr./m², "
            f"{'up' if change >= 0 else 'down'} {abs(change):.0f}%."
        )

    def _fill_document(self) -> None:
        area = self.query_one("#dokument", TextArea)
        documents = self.tables.get("attester") or []
        if not documents:
            area.text = (
                "No register document stored for this property.\n\n"
                "The tingbogsattest is only shown to a logged-in session - press "
                "ctrl+L to log in, then f to fetch this property again."
            )
            return
        area.text = documents[0].get("dokument") or ""

    # ── acting on it ────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    @work
    async def action_export(self) -> None:
        from yaybo.widgets.export_dialog import ExportDialog

        if not self.tables:
            self.notify("Nothing stored for this property yet.")
            return
        await self.app.push_screen_wait(
            ExportDialog(
                self.tables,
                display.text(self.property_row.get("adresse"), "ejendom"),
                title="Export this property",
            )
        )

    def action_refetch(self) -> None:
        address = self.property_row.get("adresse")
        if not address:
            return
        self.notify(f"Re-fetching {address}…")
        self._refetch(address)

    @work(thread=True, exclusive=True, group="refetch")
    def _refetch(self, address: str) -> None:
        try:
            bundle = pipeline.lookup(self.app.api, address, limit=1, delay=0)
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


def _owners_of(rows: list[dict] | None, post: int | None) -> str:
    """The people named in one entry of the ownership history."""
    named = [
        display.text(row.get("navn"))
        for row in rows or []
        if row.get("post_nummer") == post and row.get("navn")
    ]
    return "; ".join(named)


def _as_year(iso_date: str) -> float:
    """2019-04-11 becomes 2019.28 - a number an axis can be drawn against."""
    year, month, day = int(iso_date[:4]), int(iso_date[5:7]), int(iso_date[8:10])
    return year + ((month - 1) + (day - 1) / 31) / 12

