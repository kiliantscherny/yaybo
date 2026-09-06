"""Key figures across a set of properties, rather than about one of them.

Two halves. The top narrows what the figures are about - a city, a postcode, a
street, a building - and the bottom is a list of questions that can be asked of
whatever is left. Picking one opens it over that selection.

Everything is a dropdown, filled from the database rather than typed. The
earlier version of this screen took a filter string, which worked and was
almost impossible to guess at: knowing that `bygning:"Islands Brygge 30B"` is
the way to ask about one block is not knowledge anyone should need. The string
is still there, underneath, and still what the property screen hands over.

Nothing is fetched here. It reads what has already been gathered, so it is
instant and works offline - and only ever describes the properties you have
chosen to hold, which is worth remembering before reading a median off eleven
flats that all happen to be in the same stairwell.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, OptionList, Select, Static
from textual.widgets.option_list import Option

from yaybo import i18n, stats
from yaybo.screens.base import YayboScreen
from yaybo.widgets.nav import NavTabs
from yaybo.widgets.queue_bar import QueueBar
from yaybo.widgets.session_bar import SessionBar

# The scope dropdowns, outermost first. Each one is filled from what survives
# the ones above it, so choosing a city leaves only that city's postcodes.
SCOPE = (
    ("scope-by", "Town", "_by", "by"),
    ("scope-postnr", "Postcode", "_postnr", "postnr"),
    ("scope-vej", "Street", "_vej", "vej"),
    ("scope-bygning", "Building", "_bygning", "bygning"),
)
ALL = "\x00alle"


class StatsScreen(YayboScreen):
    """Narrow the properties with dropdowns, then ask something of them."""

    AUTO_FOCUS = "#stats-analyses"

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Reload"),
        Binding("c", "clear_scope", "All properties"),
    ]

    def __init__(self, query: str = "") -> None:
        super().__init__()
        # Set when another screen hands over a selection - the property screen
        # asking for its own building, say. Dropdowns take over from there.
        self.typed = query
        self.tables: dict[str, list[dict]] = {}
        self.scope = stats.Scope()
        self.chosen: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionBar()
        yield NavTabs("noegletal")
        with Horizontal(id="stats-scope-bar"):
            for widget_id, label, _, _ in SCOPE:
                yield Static(i18n.t(label), classes="scope-label")
                yield Select([], prompt=i18n.t("all"), id=widget_id,
                             classes="scope-select")
        yield Static("", id="stats-scope")
        yield OptionList(id="stats-analyses")
        yield Static("", id="stats-hint", classes="hint-text")
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        listing = self.query_one("#stats-analyses", OptionList)
        listing.add_options(
            [
                Option(_analysis_label(analysis), id=analysis.key)
                for analysis in stats.ANALYSES
            ]
        )
        listing.highlighted = 0
        self.query_one("#stats-hint", Static).update(
            i18n.t(
                "enter opens one over the selection above · c resets it to "
                "everything · r reloads from the database"
            )
        )
        self.action_refresh()

    # ── reading ─────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        tables = self.app.stats_tables()
        self.app.call_from_thread(self._loaded, tables)

    def _loaded(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        # Annotate once, here, so every dropdown and every figure downstream
        # reads the same derived building and floor.
        stats.annotate(self.tables.get("ejendomme") or [])
        if self.typed:
            self._adopt(self.typed)
            self.typed = ""
        self._fill_scope()
        self._recompute()

    def _adopt(self, query: str) -> None:
        """Turn a handed-over filter string into dropdown choices.

        The property screen asks for `bygning:"..."`; this is what makes that
        arrive as a chosen building rather than as text nobody can see.
        """
        for token in stats.tokens(query):
            name, _, value = token.partition(":")
            for _, _, field, filter_name in SCOPE:
                if value and name.lower() == filter_name:
                    self.chosen[field] = value

    def queue_changed(self) -> None:
        """A finished fetch means there is more to aggregate over."""
        if not self.app.fetching.running:
            self.action_refresh()

    # ── the scope dropdowns ─────────────────────────────────────────────

    @on(Select.Changed, ".scope-select")
    def _scope_changed(self, event: Select.Changed) -> None:
        field = next(
            (f for wid, _, f, _ in SCOPE if wid == event.select.id), ""
        )
        if not field:
            return
        chosen = None if event.value in (Select.BLANK, ALL, None) else str(event.value)
        # Idempotent on purpose. Filling a dropdown sets its value, and Textual
        # delivers the resulting Changed as a message rather than a call, so it
        # arrives after any "I am busy" flag would already have been cleared.
        # Comparing against what is already chosen is what makes re-entry safe.
        if self.chosen.get(field) == chosen:
            return
        if chosen is None:
            self.chosen.pop(field, None)
        else:
            self.chosen[field] = chosen
        self._recompute()

    def _fill_scope(self) -> None:
        """Fill every dropdown from the whole database, once per load.

        Deliberately not cascading. Narrowing each list to what the ones above
        it leave standing means rebuilding options from inside the handler for
        a change, which is a message that arrives while the styles for the last
        one are still being applied - and the two chase each other. The lists
        are self-describing instead: a building carries its own postcode and
        town, so an impossible combination reads as "0 of 336" rather than
        being impossible to express.
        """
        rows = self.tables.get("ejendomme") or []
        for widget_id, _, field, _ in SCOPE:
            select = self.query_one(f"#{widget_id}", Select)
            select.set_options(
                [("alle", ALL)]
                + [
                    (f"{name}  ({count})", name)
                    for name, count in stats.choices(rows, field)
                ]
            )
            select.value = self.chosen.get(field) or ALL

    def action_clear_scope(self) -> None:
        self.chosen.clear()
        for widget_id, _, _, _ in SCOPE:
            self.query_one(f"#{widget_id}", Select).value = ALL
        self._recompute()

    # ── what is in scope, and asking something of it ─────────────────────

    def _query(self) -> str:
        parts = []
        for _, _, field, filter_name in SCOPE:
            value = self.chosen.get(field)
            if value:
                parts.append(f'{filter_name}:"{value}"')
        return " ".join(parts)

    def _recompute(self) -> None:
        self.scope = stats.scope_of(self.tables, self._query())
        held = len(self.tables.get("ejendomme") or [])
        shown = len(self.scope)
        buildings = len({row["_bygning"] for row in self.scope.properties})
        where = self._describe_scope()
        thin = (
            i18n.t("  ·  nothing matches this combination - press c to reset")
            if held and not shown
            else i18n.t("  ·  too few to read much into") if 0 < shown < 5 else ""
        )
        self.query_one("#stats-scope", Static).update(
            i18n.t(
                "{where}   ·   {shown} of {properties} in {buildings}{thin}",
                where=where, shown=shown,
                properties=(i18n.t("{n} property", n=held) if held == 1
                            else i18n.t("{n} properties", n=held)),
                buildings=(i18n.t("{n} building", n=buildings) if buildings == 1
                           else i18n.t("{n} buildings", n=buildings)),
                thin=thin,
            )
        )

    def _describe_scope(self) -> str:
        parts = [
            self.chosen[field] for _, _, field, _ in SCOPE if self.chosen.get(field)
        ]
        return " · ".join(reversed(parts)) if parts else i18n.t("Everything held")

    @on(OptionList.OptionSelected, "#stats-analyses")
    def _chose(self, event: OptionList.OptionSelected) -> None:
        analysis = next(
            a for a in stats.ANALYSES if a.key == str(event.option.id)
        )
        if not self.scope.properties:
            self.notify(i18n.t("Nothing in that selection to compute anything over."))
            return
        from yaybo.screens.analysis import AnalysisScreen

        self.app.push_screen(
            AnalysisScreen(analysis, self.scope, self._describe_scope())
        )

    def action_back(self) -> None:
        self.app.action_library()


def _analysis_label(analysis: stats.Analysis):
    from rich.text import Text

    label = Text()
    label.append(i18n.t(analysis.name), style="bold")
    label.append(f"\n   {i18n.t(analysis.blurb)}", style="dim")
    return label
