"""Everything queued, and what to do with any of it.

Nothing is added here. Properties come from the Search screen, which is the one
place that knows what the register actually holds at an address - this screen
used to take a typed address as well, and guessing a street out of free text was
a worse version of the search that already exists.

What it is for is the list itself: which of the queued buildings to start, which
to retry, which to drop, which to export. Every action works on whatever is
ticked, or on all of them when nothing is - a list with no selection reads as
"this list", and making someone tick forty rows in order to act on forty rows is
not a safety feature.

Two things it deliberately does not do. It does not race - a fixed pause sits
between properties, because the register is a public service and this is one
person's curiosity. And it does not lose work when the login lapses partway
through: the rows already fetched are already in the database.
"""

from __future__ import annotations

import asyncio

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from yaybo import i18n
from yaybo.fetching import DONE
from yaybo.screens.base import YayboScreen
from yaybo.widgets.nav import NavTabs
from yaybo.widgets.queue_bar import QueueBar
from yaybo.widgets.session_bar import SessionBar

COLUMNS = (
    ("", 3),
    ("", 3),
    ("Address", 46),
    ("Properties", 11),
    ("Rows", 9),
    ("Note", 40),
)


class QueueScreen(YayboScreen):
    """The queue as a list, with every action working on a selection of it."""

    AUTO_FOCUS = "#queue-table"

    BINDINGS = [
        Binding("space", "tick", "Tick"),
        Binding("a", "tick_all", "Tick all"),
        Binding("n", "tick_none", "Tick none"),
        Binding("f", "start", "Start"),
        Binding("r", "retry", "Retry"),
        Binding("d", "remove", "Remove"),
        Binding("e", "export", "Export"),
        Binding("t", "toggle_auto", "Auto-fetch"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Job keys, not row numbers: the list is redrawn on every move the
        # queue makes, and rows come and go underneath the selection.
        self.ticked: set[str] = set()
        self.shown: list = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionBar()
        yield NavTabs("koe")
        yield Static("", id="queue-status")
        with Horizontal(id="queue-actions"):
            yield Button(i18n.t("▶  Start"), id="queue-run", variant="primary")
            yield Button(i18n.t("Retry"), id="queue-retry")
            yield Button(i18n.t("Remove"), id="queue-remove")
            yield Button(i18n.t("Export"), id="queue-export")
            yield Button("", id="queue-auto")
        yield DataTable(id="queue-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="queue-empty", classes="empty-state")
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        for label, width in COLUMNS:
            table.add_column(i18n.t(label), width=width)
        self.queue_changed()

    def _say(self, message: str) -> None:
        self.query_one("#queue-status", Static).update(message)

    # ── choosing rows ───────────────────────────────────────────────────

    def _under_cursor(self):
        table = self.query_one("#queue-table", DataTable)
        if not self.shown or table.cursor_row < 0:
            return None
        try:
            return self.shown[table.cursor_row]
        except IndexError:
            return None

    def action_tick(self) -> None:
        job = self._under_cursor()
        if job is None:
            return
        self.ticked.symmetric_difference_update({job.key})
        self.queue_changed()

    def action_tick_all(self) -> None:
        self.ticked = {job.key for job in self.app.fetching.jobs}
        self.queue_changed()

    def action_tick_none(self) -> None:
        self.ticked.clear()
        self.queue_changed()

    @property
    def _chosen(self) -> set[str] | None:
        """What the actions act on. None means "everything in the list"."""
        return self.ticked or None

    # ── acting on them ──────────────────────────────────────────────────

    @on(Button.Pressed, "#queue-run")
    def action_start(self) -> None:
        queue = self.app.fetching
        if queue.running:
            self.app.action_stop_fetching()
            return
        started = queue.start(self._chosen)
        if not started and not queue.waiting:
            self._say("Nothing to start. Queue something from the search screen.")
            return
        self.app.start_fetching()

    @on(Button.Pressed, "#queue-retry")
    def action_retry(self) -> None:
        retried = self.app.fetching.retry(self._chosen)
        if not retried:
            self._say("Nothing among those has failed.")
            return
        self.app.start_fetching()
        self._say(f"Retrying {retried}.")

    @on(Button.Pressed, "#queue-remove")
    def action_remove(self) -> None:
        """Drop the ticked jobs, or the whole list when none are ticked."""
        gone = self.app.fetching.remove(self._chosen)
        self.ticked.clear()
        self.queue_changed()
        self._say(
            f"Removed {gone} from the queue."
            if gone
            else "Nothing to remove - what is left is being fetched now."
        )

    @on(Button.Pressed, "#queue-auto")
    def action_toggle_auto(self) -> None:
        self.app.action_toggle_auto_fetch()
        self.queue_changed()

    @on(Button.Pressed, "#queue-export")
    @work
    async def action_export(self) -> None:
        from yaybo import store
        from yaybo.widgets.export_dialog import ExportDialog

        uuids = self.app.fetching.uuids_for(self._chosen)
        if not uuids:
            self.notify(i18n.t("Those have not fetched anything yet."))
            return
        tables = await asyncio.to_thread(store.tables_for, self.app.database, uuids)
        if not tables:
            self.notify(i18n.t("Nothing in the database for those yet."))
            return
        await self.app.push_screen_wait(
            ExportDialog(
                tables,
                "yaybo-queue",
                title=i18n.t(
                    "Export {n} from the queue",
                    n=(i18n.t("{n} property", n=len(uuids)) if len(uuids) == 1
                       else i18n.t("{n} properties", n=len(uuids))),
                ),
            )
        )

    # ── showing it ──────────────────────────────────────────────────────

    def queue_changed(self) -> None:
        """Called by the application whenever the queue moves, and on mount."""
        queue = self.app.fetching
        found = self.query("#queue-table")
        # The worker reaches this from its own thread, and the screen may be
        # anywhere between not yet composed and fully mounted. Neither a
        # missing table nor one without its columns can take a row, and
        # on_mount calls this again once both are true.
        if not found:
            return
        table = found.first(DataTable)
        if not table.columns:
            return

        # Ticks on jobs that have since been removed would otherwise linger and
        # silently widen the next action.
        self.ticked &= {job.key for job in queue.jobs}
        self.shown = list(queue.jobs)
        table.clear()
        for job in self.shown:
            table.add_row(
                "✓" if job.key in self.ticked else "",
                job.state,
                job.label[:46],
                f"{job.fetched}/{job.expected}"
                if job.expected > 1
                else str(job.fetched or ""),
                str(job.rows or ""),
                job.note[:40],
                key=job.key,
            )

        self.query_one("#queue-run", Button).label = (
            i18n.t("■  Stop") if queue.running else i18n.t("▶  Start")
        )
        self.query_one("#queue-auto", Button).label = (
            i18n.t("Auto-fetch: on") if queue.auto else i18n.t("Auto-fetch: off")
        )
        table.display = bool(self.shown)
        empty = self.query_one("#queue-empty", Static)
        empty.display = not self.shown
        empty.update(
            i18n.t(
                "Nothing queued.\n\nPress / to find a property, tick what you "
                "want\nand press f to send it here."
            )
        )
        self._describe(queue)

    def _describe(self, queue) -> None:
        if not queue.jobs:
            self._say(
                i18n.t("Auto-fetch is on - queued properties start straight away.")
                if queue.auto
                else i18n.t(
                    "Auto-fetch is off - queued properties wait here to be started."
                )
            )
            return
        chosen = (
            i18n.t("{n} ticked · ", n=len(self.ticked)) if self.ticked else ""
        )
        state = (
            i18n.t("fetching {what}", what=queue.current)
            if queue.running and queue.current
            else i18n.t("running")
            if queue.running
            else i18n.t("{n} held", n=queue.held)
            if queue.held
            else i18n.t("{n} waiting", n=queue.waiting)
            if queue.waiting
            else i18n.t("idle")
        )
        failed = (
            i18n.t(" · {n} failed, r retries", n=queue.failed) if queue.failed else ""
        )
        done = sum(1 for job in queue.jobs if job.state == DONE)
        self._say(
            i18n.t(
                "{chosen}{jobs} jobs, {done} done · {fetched} of {total} "
                "properties · {rows} rows · {state}{failed}",
                chosen=chosen, jobs=len(queue.jobs), done=done,
                fetched=queue.done, total=queue.total, rows=queue.rows,
                state=state, failed=failed,
            )
        )

    def action_back(self) -> None:
        # No longer refuses to leave while a run is going: that is the whole
        # point of the queue living on the application rather than here.
        self.app.action_library()
