"""Fetch many addresses in a row, and be able to walk away while it happens.

One address at a time is fine for one address. A street is forty buildings and
several hundred properties, and doing that from a prompt means watching it. Here
it is a list with a progress bar: queue what you want, start it, and come back.

Two things it deliberately does not do. It does not race - a fixed pause sits
between requests, because the register is a public service and this is one
person's curiosity. And it does not lose work when the login lapses partway
through: the rows already fetched are already in the database.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    ProgressBar,
    Static,
)

from yaybo import pipeline, store
from yaybo.register.address import (
    AddressError,
    address_parts,
    street_buildings,
)
from yaybo.screens.base import YayboScreen

WAITING, RUNNING, DONE, FAILED = "·", "⟳", "✓", "✗"
# Between properties. The register is a public service, not a scraping target.
POLITE_DELAY = 1.0


class QueueScreen(YayboScreen):
    """A list of addresses to fetch, and something that works through it."""

    BINDINGS = [
        Binding("space", "run_or_pause", "Start / pause"),
        Binding("c", "clear", "Clear"),
        Binding("e", "export", "Export all"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.jobs: list[dict] = []
        self.running = False
        # Set while a run should stop. The worker checks it between properties,
        # so pausing never abandons a half-fetched one.
        self._pause = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="queue-bar"):
            yield Input(
                placeholder="An address, or a street: Prøvegade, 9999",
                id="queue-input",
            )
            yield Button("Add", id="queue-add")
            yield Button("Whole street", id="queue-street")
            yield Button("From file", id="queue-file")
        yield Static("", id="queue-status")
        yield ProgressBar(id="queue-progress", show_eta=False)
        yield DataTable(id="queue-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.add_column("", width=3)
        table.add_column("Adresse", width=52)
        table.add_column("Ejendomme", width=11)
        table.add_column("Rækker", width=9)
        table.add_column("Note", width=40)
        self.query_one("#queue-progress", ProgressBar).display = False
        self._say(
            "Type an address and press Add, or a street and a postcode and press "
            "Whole street. From file reads one address per line."
        )
        self.query_one("#queue-input", Input).focus()

    def _say(self, message: str) -> None:
        self.query_one("#queue-status", Static).update(message)

    # ── filling the queue ───────────────────────────────────────────────

    @on(Button.Pressed, "#queue-add")
    @on(Input.Submitted, "#queue-input")
    def _add(self) -> None:
        field = self.query_one("#queue-input", Input)
        query = field.value.strip()
        if not query:
            return
        self._queue([query])
        field.value = ""

    @on(Button.Pressed, "#queue-street")
    def _add_street(self) -> None:
        field = self.query_one("#queue-input", Input)
        query = field.value.strip()
        if not query:
            self._say("Type a street and a postcode first, e.g. Prøvegade, 9999.")
            return
        self._say(f"Asking DAWA for every house number on {query}…")
        self._expand_street(query)
        field.value = ""

    @work(thread=True, exclusive=True, group="street")
    def _expand_street(self, query: str) -> None:
        # address_parts is built for a full address, so it reads "Islands
        # Brygge, 2300" as a street with no number - which is exactly what is
        # wanted here.
        parts = address_parts(query)
        vejnavn = " ".join(filter(None, [parts["vejnavn"], parts["husnummer"]])).strip()
        buildings = street_buildings(vejnavn, parts["postnummer"])
        self.app.call_from_thread(self._street_found, query, buildings)

    def _street_found(self, query: str, buildings: list[dict]) -> None:
        if not buildings:
            self._say(
                f"DAWA knows no street matching {query!r}. It wants the street "
                "and the postcode, e.g. Prøvegade, 9999."
            )
            return
        self._queue([building["tekst"] for building in buildings])
        self._say(f"Queued {len(buildings)} buildings on {query}.")

    @on(Button.Pressed, "#queue-file")
    def _add_file(self) -> None:
        field = self.query_one("#queue-input", Input)
        path = Path(field.value.strip()).expanduser()
        if not path.is_file():
            self._say(f"No file at {path}. Type a path and press From file.")
            return
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self._queue(lines)
        self._say(f"Queued {len(lines)} addresses from {path}.")
        field.value = ""

    def _queue(self, addresses: list[str]) -> None:
        held = {job["query"] for job in self.jobs}
        for address in addresses:
            if address not in held:
                self.jobs.append(
                    {
                        "query": address,
                        "state": WAITING,
                        "units": 0,
                        "rows": 0,
                        "note": "",
                    }
                )
        self._redraw()

    def action_clear(self) -> None:
        if self.running:
            self._say("Pause it first - space toggles.")
            return
        self.jobs = []
        self._redraw()
        self._say("Queue cleared.")

    # ── running it ──────────────────────────────────────────────────────

    def action_run_or_pause(self) -> None:
        if self.running:
            self._pause.set()
            self.running = False
            self._say("Pausing after the property being fetched now…")
            return
        waiting = [job for job in self.jobs if job["state"] in (WAITING, FAILED)]
        if not waiting:
            self._say("Nothing waiting. Add an address first.")
            return
        self._pause.clear()
        self.running = True
        self.query_one("#queue-progress", ProgressBar).display = True
        self._run()

    @work(thread=True, exclusive=True, group="queue")
    def _run(self) -> None:
        call = self.app.call_from_thread
        todo = [job for job in self.jobs if job["state"] in (WAITING, FAILED)]
        progress = self.query_one("#queue-progress", ProgressBar)
        call(progress.update, total=len(todo), progress=0)

        for done, job in enumerate(todo):
            if self._pause.is_set():
                call(self._finished, "Paused.")
                return
            job["state"], job["note"] = RUNNING, ""
            call(self._redraw)
            call(self._say, f"[{done + 1}/{len(todo)}] {job['query']}")

            try:
                bundle = pipeline.lookup(
                    self.app.api,
                    job["query"],
                    limit=0,
                    delay=POLITE_DELAY,
                    should_stop=self._pause.is_set,
                )
                written = store.save(self.app.database, bundle.tables)
            except AddressError as error:
                job.update(state=FAILED, note=str(error))
                call(self._redraw)
                continue
            except Exception as error:  # noqa: BLE001 - shown in the row
                job.update(state=FAILED, note=f"{type(error).__name__}: {error}")
                call(self._redraw)
                continue

            job.update(
                state=DONE,
                units=len(bundle.properties),
                rows=sum(written.values()),
                note=bundle.warning,
            )
            call(self._redraw)
            call(progress.update, progress=done + 1)

        call(self._finished, "Done.")

    def _finished(self, message: str) -> None:
        self.running = False
        self.query_one("#queue-progress", ProgressBar).display = False
        fetched = sum(job["units"] for job in self.jobs)
        rows = sum(job["rows"] for job in self.jobs)
        failed = sum(1 for job in self.jobs if job["state"] == FAILED)
        self._say(
            f"{message} {fetched} properties, {rows} rows into {self.app.database}."
            + (
                f" {failed} address(es) failed - press space to retry them."
                if failed
                else ""
            )
        )
        self.notify(f"{message} {rows} rows written.")

    def _redraw(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for job in self.jobs:
            table.add_row(
                job["state"],
                job["query"][:52],
                str(job["units"] or ""),
                str(job["rows"] or ""),
                job["note"][:40],
            )

    # ── getting the results out ─────────────────────────────────────────

    @work
    async def action_export(self) -> None:
        from yaybo.widgets.export_dialog import ExportDialog

        tables = await asyncio.to_thread(store.everything, self.app.database)
        if not tables:
            self.notify("Nothing in the database to export yet.")
            return
        await self.app.push_screen_wait(
            ExportDialog(tables, "yaybo-queue", title="Export the whole database")
        )

    def action_back(self) -> None:
        if self.running:
            self._say("Still fetching - press space to pause before leaving.")
            return
        self.app.action_library()
