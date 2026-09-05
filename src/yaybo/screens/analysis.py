"""One analysis, opened over whatever the figures screen had in scope.

A modal rather than another screen: an analysis is a question asked of a
selection, not a place you navigate to, and escape should put you back where
you were with the selection intact.

Everything it can be asked is a dropdown. The filter box on the screen behind
is still there for anyone who wants it, but "median valuation per m², by floor"
should not require knowing that `_etage` is spelled with an underscore.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Select, Static
from textual_plotext import PlotextPlot

from yaybo import display, stats

if TYPE_CHECKING:
    from yaybo.app import YayboApp

# How far back a series may reach. Sales data goes back decades, and most
# questions are about the recent part of it.
PERIODS = (
    ("Hele perioden", 0),
    ("Sidste 5 år", 5),
    ("Sidste 10 år", 10),
    ("Sidste 20 år", 20),
)


class AnalysisScreen(ModalScreen[None]):
    """A chart and a table for one measure, over one grouping or over time."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close", show=False),
    ]

    if TYPE_CHECKING:

        @property
        def app(self) -> YayboApp: ...

    def __init__(self, analysis: stats.Analysis, scope: stats.Scope, where: str):
        super().__init__()
        self.analysis = analysis
        self.scope = scope
        self.where = where
        self.measure = analysis.measures[0] if analysis.measures else ""
        self.how = stats.BY_KEY[self.measure].default if self.measure else "median"
        self.by = stats.GROUPS[0][1]
        self.since = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="analysis-box"):
            yield Static(self.analysis.name, id="analysis-title")
            yield Static(self.where, id="analysis-where")
            # One `with` block, and no early return: compose is a generator,
            # so returning out of it here would drop the chart, the table and
            # the note along with the dropdowns it was meant to skip.
            with Horizontal(id="analysis-controls"):
                if self.analysis.kind == "summary":
                    yield Static(
                        "Every figure below is over the selection named above.",
                        id="analysis-blurb",
                    )
                else:
                    yield Select(
                        [
                            (stats.BY_KEY[key].label, key)
                            for key in self.analysis.measures
                        ],
                        value=self.measure,
                        allow_blank=False,
                        id="analysis-measure",
                    )
                    yield Select(
                        list(stats.HOWS), value=self.how, allow_blank=False,
                        id="analysis-how",
                    )
                    if self.analysis.kind == "group":
                        yield Select(
                            list(stats.GROUPS), value=self.by, allow_blank=False,
                            id="analysis-by",
                        )
                    else:
                        yield Select(
                            list(PERIODS), value=self.since, allow_blank=False,
                            id="analysis-period",
                        )
                yield Button("Luk", id="analysis-close")
            yield PlotextPlot(id="analysis-chart")
            yield DataTable(id="analysis-table", cursor_type="row",
                            zebra_stripes=True)
            yield Static("", id="analysis-note")

    def on_mount(self) -> None:
        self._redraw()

    # ── the controls ────────────────────────────────────────────────────

    @on(Select.Changed, "#analysis-measure")
    def _measure_changed(self, event: Select.Changed) -> None:
        self.measure = str(event.value)
        # Each measure has a way of being combined that makes sense for it -
        # you sum sales and you take the median of a valuation - so changing
        # the measure moves the other dropdown with it.
        self.how = stats.BY_KEY[self.measure].default
        self.query_one("#analysis-how", Select).value = self.how
        self._redraw()

    @on(Select.Changed, "#analysis-how")
    def _how_changed(self, event: Select.Changed) -> None:
        self.how = str(event.value)
        self._redraw()

    @on(Select.Changed, "#analysis-by")
    def _by_changed(self, event: Select.Changed) -> None:
        self.by = str(event.value)
        self._redraw()

    @on(Select.Changed, "#analysis-period")
    def _period_changed(self, event: Select.Changed) -> None:
        # Select hands back its own sentinel when blank, which int() cannot
        # read; the periods are ints and anything else means "all of it".
        self.since = event.value if isinstance(event.value, int) else 0
        self._redraw()

    @on(Button.Pressed, "#analysis-close")
    def _close(self) -> None:
        self.dismiss(None)

    # ── drawing it ──────────────────────────────────────────────────────

    def _redraw(self) -> None:
        if self.analysis.kind == "summary":
            self._draw_summary()
            return
        if self.analysis.kind == "time":
            self._draw_time()
        else:
            self._draw_group()

    def _spec(self) -> stats.Measure:
        return stats.BY_KEY[self.measure]

    def _write(self, value) -> str:
        return _format(value, self._spec().unit)

    def _draw_time(self) -> None:
        from datetime import date

        since = date.today().year - self.since if self.since else 0
        rows = stats.over_time(self.scope, self.measure, self.how, since)
        table = self._table(("År", 8), ("Handler", 9), (self._spec().label, 18))
        for year, count, value in rows:
            table.add_row(str(year), display.number(count), self._write(value))

        plot = self.query_one("#analysis-chart", PlotextPlot)
        points = [(year, value) for year, _, value in rows if value is not None]
        plot.plt.clear_figure()
        if len(points) < 2:
            plot.display = False
            self._note(
                "Not enough recorded sales in this selection to plot. Sale "
                "history comes from Boligsiden, which knows the sales an agent "
                "handled; a flat sold privately, or held for decades, has none."
            )
            return
        plot.display = True
        theme = self.app.current_theme
        xs = [float(year) for year, _ in points]
        ys = [value for _, value in points]
        plot.plt.plot(xs, ys, marker="braille",
                      color=display.rgb(theme.primary, (94, 176, 234)))
        plot.plt.scatter(xs, ys, marker="●",
                         color=display.rgb(theme.warning, (232, 185, 106)))
        _year_ticks(plot, [int(year) for year, _ in points])
        plot.plt.title(f"{self._spec().label} · {_how_label(self.how)}")
        plot.plt.xlabel("År")
        sales = sum(count for _, count, _ in rows)
        self._note(
            f"{sales} recorded sale(s) across {len(self.scope)} propert"
            f"{'y' if len(self.scope) == 1 else 'ies'}, in {len(rows)} year(s)."
        )

    def _draw_group(self) -> None:
        rows = stats.aggregate(self.scope, self.by, self.measure, self.how)
        label = dict((value, name) for name, value in stats.GROUPS)[self.by]
        table = self._table((label, 30), ("Ejendomme", 11),
                            (self._spec().label, 18))
        for name, count, value in rows:
            table.add_row(
                display.shorten(name, 30), display.number(count), self._write(value)
            )

        plot = self.query_one("#analysis-chart", PlotextPlot)
        bars = [(name, value) for name, _, value in rows if value is not None]
        plot.plt.clear_figure()
        if not bars:
            plot.display = False
            self._note("Nothing in this selection has that figure recorded.")
            return
        plot.display = True
        theme = self.app.current_theme
        plot.plt.bar(
            [display.shorten(name, 14) for name, _ in bars],
            [value for _, value in bars],
            color=display.rgb(theme.primary, (94, 176, 234)),
            orientation="horizontal" if len(bars) > 8 else "vertical",
        )
        plot.plt.title(f"{self._spec().label} · {_how_label(self.how)} · pr. {label}")
        self._note(
            f"{len(rows)} group(s) over {len(self.scope)} propert"
            f"{'y' if len(self.scope) == 1 else 'ies'}. "
            "Groups with nothing recorded are listed but not plotted."
        )

    def _draw_summary(self) -> None:
        self.query_one("#analysis-chart", PlotextPlot).display = False
        table = self._table(("Gruppe", 16), ("Tal", 30), ("Værdi", 20))
        for heading, figures in stats.overview(self.scope):
            first = True
            for label, unit, value in figures:
                table.add_row(
                    heading if first else "", label, _format(value, unit)
                )
                first = False
        self._note(
            f"{len(self.scope)} propert"
            f"{'y' if len(self.scope) == 1 else 'ies'} in this selection. "
            "A figure reading – is one nothing in the selection records."
        )

    def _table(self, *columns: tuple[str, int]) -> DataTable:
        """The table, rebuilt with these columns. Cleared of the last analysis.

        Columns are replaced rather than reused: the measure dropdown changes
        what the last column means, and a header left saying something else is
        worse than a moment's flicker.
        """
        table = self.query_one("#analysis-table", DataTable)
        table.clear(columns=True)
        for label, width in columns:
            table.add_column(label, width=width)
        return table

    def _note(self, message: str) -> None:
        self.query_one("#analysis-note", Static).update(message)


def _format(value, unit: str) -> str:
    if value is None:
        return display.NOTHING
    return {
        "kr": lambda v: display.kr(v),
        "pct": lambda v: display.pct(v, 1),
        "m2": lambda v: display.area(v),
        "num": lambda v: display.number(v, 1),
        "count": lambda v: display.number(v),
        "year": lambda v: str(int(v)),
        # Not every headline figure is a number - the commonest loan type is a
        # name - and a formatter that only knows numbers drops it on the floor.
        "text": lambda v: display.text(v),
    }[unit](value)


def _how_label(how: str) -> str:
    return dict((value, name) for name, value in stats.HOWS).get(how, how)


def _year_ticks(plot: PlotextPlot, years: list[int]) -> None:
    """Whole years along the bottom.

    plotext picks its own ticks from the values it was given, and given years
    as numbers it will happily label one of them 2005.9 - which is not a year
    anyone has heard of.
    """
    span = sorted(set(years))
    if not span:
        return
    step = max(1, len(span) // 8)
    ticks = span[::step]
    if span[-1] not in ticks:
        ticks.append(span[-1])
    plot.plt.xticks([float(year) for year in ticks], [str(year) for year in ticks])
