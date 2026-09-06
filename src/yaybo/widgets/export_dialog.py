"""Pick a format and write a set of tables out, without leaving the screen."""

from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RadioButton, RadioSet, Static

from yaybo import export, i18n


class ExportDialog(ModalScreen):
    """Modal: choose CSV, Excel or DuckDB, and say what came of it."""

    BINDINGS = [("escape", "dismiss_dialog", "Cancel")]

    def __init__(
        self,
        tables: dict[str, list[dict]],
        stem: str,
        *,
        title: str = "Export",
        outdir: Path | None = None,
    ) -> None:
        super().__init__()
        # Empty tables are dropped here rather than in the exporter, so the
        # dialog can say what it is about to write before writing it.
        self.tables = {name: rows for name, rows in tables.items() if rows}
        self.stem = stem
        self.dialog_title = title
        self.outdir = outdir
        self.chosen = "DuckDB"

    def compose(self) -> ComposeResult:
        with Vertical(id="export-box"):
            yield Label(self.dialog_title, id="export-title")
            yield Static(self._summary(), id="export-summary")
            with RadioSet(id="export-format"):
                yield RadioButton("DuckDB", value=True)
                yield RadioButton("Excel (XLSX)")
                yield RadioButton("CSV")
            yield Static("", id="export-note")
            with Horizontal(id="export-buttons"):
                yield Button(i18n.t("Cancel"), id="export-cancel")
                yield Button(i18n.t("Export"), variant="primary", id="export-go")

    def _summary(self) -> str:
        if not self.tables:
            return i18n.t("Nothing to export.")
        total = sum(len(rows) for rows in self.tables.values())
        parts = ", ".join(
            f"{len(rows)} {name}" for name, rows in list(self.tables.items())[:6]
        )
        more = (
            "" if len(self.tables) <= 6
            else i18n.t(", and {n} more", n=len(self.tables) - 6)
        )
        return i18n.t(
            "{rows} rows across {tables} tables", rows=total, tables=len(self.tables)
        ) + f"\n{parts}{more}"

    @on(RadioSet.Changed, "#export-format")
    def _chose(self, event: RadioSet.Changed) -> None:
        self.chosen = str(event.pressed.label)

    @on(Button.Pressed, "#export-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_dismiss_dialog(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#export-go")
    def _go(self) -> None:
        if not self.tables:
            self.dismiss(None)
            return
        self.query_one("#export-go", Button).disabled = True
        self.query_one("#export-note", Static).update(i18n.t("Writing…"))
        self._write()

    @work(thread=True)
    def _write(self) -> None:
        try:
            written = export.FORMATS[self.chosen](
                self.tables, self.stem, outdir=self.outdir
            )
        except Exception as error:  # noqa: BLE001 - shown to the user verbatim
            self.app.call_from_thread(
                self.query_one("#export-note", Static).update,
                i18n.t("Failed: {error}", error=error),
            )
            self.app.call_from_thread(
                setattr, self.query_one("#export-go", Button), "disabled", False
            )
            return

        paths = written if isinstance(written, list) else [written] if written else []
        message = (
            i18n.t("Wrote {path}", path=paths[0]) if len(paths) == 1
            else i18n.t("Wrote {n} files to {where}",
                        n=len(paths), where=paths[0].parent) if paths
            else i18n.t("Nothing to write.")
        )
        self.app.call_from_thread(self.app.notify, message)
        self.app.call_from_thread(self.dismiss, paths[0] if paths else None)
