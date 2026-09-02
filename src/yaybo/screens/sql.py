"""Query the accumulated database directly, because eventually you want to.

The other screens answer questions about one property. This one answers the
questions that only exist because the database has more than one in it - which
postcode has the highest price per square metre, who owns more than one flat in
the building, which charges are variable-rate and when they were taken out.

Read-only, and enforced by the connection rather than by inspecting the SQL: a
DuckDB handle opened read-only refuses a write, which is a far better guarantee
than looking for the word "delete".
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Select,
    Static,
    TextArea,
)

from yaybo import display, store
from yaybo.screens.base import YayboScreen

# Worth having ready: each one is a question the tables can answer but no single
# screen shows, and each is a decent starting point to edit.
SNIPPETS: dict[str, str] = {
    "Everything held, dearest first": """
SELECT adresse, boligareal_m2, ejendomsvurdering_dkk,
       samlet_gaeld_dkk, belaaningsgrad_pct
FROM ejendomme
ORDER BY ejendomsvurdering_dkk DESC NULLS LAST;
""".strip(),
    "Price per m², by postcode": """
SELECT regexp_extract(h.adresse, '(\\d{4}) [^,]*$', 1) AS postnr,
       count(*)                AS handler,
       round(median(h.pris_pr_m2)) AS median_pris_m2,
       round(avg(h.pris_pr_m2))    AS gns_pris_m2
FROM handelshistorik h
WHERE h.pris_pr_m2 IS NOT NULL
GROUP BY 1
HAVING count(*) > 1
ORDER BY median_pris_m2 DESC;
""".strip(),
    "Owners of more than one property": """
SELECT e.navn, count(DISTINCT e.ejendom_uuid) AS ejendomme,
       string_agg(DISTINCT p.adresse, ' | ') AS adresser
FROM ejere e
JOIN ejendomme p ON p.uuid = e.ejendom_uuid
WHERE e.navn <> ''
GROUP BY e.navn
HAVING count(DISTINCT e.ejendom_uuid) > 1
ORDER BY ejendomme DESC;
""".strip(),
    "Charges, with the loan type we guessed": """
SELECT h.adresse, h.hovedstol_dkk, h.rentesats_pct, h.rentetype,
       h.laantype_estimat, h.laantype_afstand, h.kreditorer,
       h.tinglysningsdato
FROM haeftelser h
ORDER BY h.hovedstol_dkk DESC NULLS LAST;
""".strip(),
    "Most heavily mortgaged": """
SELECT adresse, ejendomsvurdering_dkk, samlet_gaeld_dkk,
       belaaningsgrad_pct, frivaerdi_dkk
FROM ejendomme
WHERE belaaningsgrad_pct IS NOT NULL
ORDER BY belaaningsgrad_pct DESC
LIMIT 25;
""".strip(),
    "Buildings by age and heating": """
SELECT opfoerelsesaar, varmeinstallation, count(*) AS bygninger,
       round(avg(boligareal_m2)) AS gns_areal
FROM bygninger
WHERE opfoerelsesaar IS NOT NULL
GROUP BY 1, 2
ORDER BY 1 DESC;
""".strip(),
    "Everyone named on a document": """
SELECT p.rolle, p.navn, p.foedselsdato, p.cvr, e.adresse
FROM dokument_parter p
JOIN ejendomme e ON e.uuid = p.ejendom_uuid
ORDER BY p.navn;
""".strip(),
    "What the database holds": """
SELECT 'ejendomme' AS tabel, count(*) FROM ejendomme
UNION ALL SELECT 'ejere', count(*) FROM ejere
UNION ALL SELECT 'haeftelser', count(*) FROM haeftelser
UNION ALL SELECT 'servitutter', count(*) FROM servitutter
UNION ALL SELECT 'dokument_parter', count(*) FROM dokument_parter
UNION ALL SELECT 'handelshistorik', count(*) FROM handelshistorik
UNION ALL SELECT 'bygninger', count(*) FROM bygninger
UNION ALL SELECT 'adkomsthistorik', count(*) FROM adkomsthistorik;
""".strip(),
}

# Beyond this the table stops being something a person reads and starts being
# something they should have exported.
MAX_ROWS = 2000


class SqlScreen(YayboScreen):
    """A query box, a result table, and a way to get the result out."""

    BINDINGS = [
        Binding("ctrl+r", "run", "Run", priority=True),
        Binding("ctrl+e", "export", "Export result", priority=True),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.columns: list[str] = []
        self.rows: list[tuple] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="sql-bar"):
            yield Select(
                [(name, name) for name in SNIPPETS],
                prompt="Load a saved query…",
                id="sql-snippets",
            )
            yield Static("ctrl+R runs · ctrl+E exports the result", id="sql-hint")
        yield TextArea(next(iter(SNIPPETS.values())), id="sql-query")
        yield Static("", id="sql-status")
        yield DataTable(id="sql-results", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#sql-query", TextArea).focus()
        self._say(f"Querying {self.app.database}, read-only.")

    def _say(self, message: str) -> None:
        self.query_one("#sql-status", Static).update(message)

    @on(Select.Changed, "#sql-snippets")
    def _loaded(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        self.query_one("#sql-query", TextArea).text = SNIPPETS[str(event.value)]
        self.action_run()

    # ── running ─────────────────────────────────────────────────────────

    def action_run(self) -> None:
        sql = self.query_one("#sql-query", TextArea).text.strip()
        if not sql:
            self._say("Nothing to run.")
            return
        self._say("Running…")
        self._execute(sql)

    @work(thread=True, exclusive=True, group="sql")
    def _execute(self, sql: str) -> None:
        try:
            columns, rows = store.run_query(self.app.database, sql)
        except store.QueryError as error:
            self.app.call_from_thread(self._failed, str(error))
            return
        self.app.call_from_thread(self._show, columns, rows)

    def _failed(self, message: str) -> None:
        self.columns, self.rows = [], []
        table = self.query_one("#sql-results", DataTable)
        table.clear(columns=True)
        # DuckDB's messages point at the character that upset it, so they are
        # worth showing whole rather than summarising.
        self._say(message)

    def _show(self, columns: list[str], rows: list[tuple]) -> None:
        self.columns, self.rows = columns, rows
        table = self.query_one("#sql-results", DataTable)
        table.clear(columns=True)
        if columns:
            table.add_columns(*columns)
        for row in rows[:MAX_ROWS]:
            table.add_row(*[display.shorten(cell, 48) for cell in row])

        shown = min(len(rows), MAX_ROWS)
        more = "" if shown == len(rows) else f" (showing the first {shown})"
        self._say(f"{len(rows)} row(s){more}. ctrl+E exports the whole result.")
        if rows:
            table.focus()

    # ── getting it out ──────────────────────────────────────────────────

    @work
    async def action_export(self) -> None:
        from yaybo.widgets.export_dialog import ExportDialog

        if not self.rows:
            self.notify("Run a query first.")
            return
        # The exporters take rows as dicts, keyed by column, the same as
        # everything else they are handed.
        named = [dict(zip(self.columns, row, strict=True)) for row in self.rows]
        await self.app.push_screen_wait(
            ExportDialog({"resultat": named}, "yaybo-sql", title="Export this result")
        )

    def action_back(self) -> None:
        self.app.action_library()
