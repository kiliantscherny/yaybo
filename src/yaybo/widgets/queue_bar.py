"""A line along the bottom saying what is being fetched, on every screen.

The queue outlives the screen that filled it, so the thing reporting on it has
to live on every screen too. Each one reads the same object, which is what makes
a screen opened halfway through a run show the run's real position rather than
an empty one of its own.

Hidden entirely when there is nothing to say. A progress bar that is always
there but usually empty teaches people to stop looking at it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, ProgressBar, Static

from yaybo import i18n

if TYPE_CHECKING:
    from yaybo.app import YayboApp


class QueueBar(Horizontal):
    """What the fetch queue is doing, and the one button that stops it."""

    if TYPE_CHECKING:

        @property
        def app(self) -> YayboApp: ...

    def compose(self) -> ComposeResult:
        yield Static("", id="queue-bar-label")
        yield ProgressBar(
            id="queue-bar-progress", show_eta=False, show_percentage=False
        )
        yield Button(i18n.t("Stop"), id="queue-bar-stop", variant="error")

    def on_mount(self) -> None:
        self.refresh_state()

    def refresh_state(self) -> None:
        """Re-read the queue. Called on mount and on every move it makes."""
        queue = self.app.fetching
        self.display = queue.active
        if not queue.active:
            return

        total, done = queue.total, queue.done
        self.query_one("#queue-bar-progress", ProgressBar).update(
            total=max(total, 1), progress=done
        )
        if queue.stopping:
            state = i18n.t("stopping after this one…")
        elif queue.running:
            state = queue.current or i18n.t("starting…")
        else:
            state = i18n.t("{n} waiting", n=queue.waiting)
        failed = (
            i18n.t("  ·  {n} failed", n=queue.failed) if queue.failed else ""
        )
        self.query_one("#queue-bar-label", Static).update(
            f"⟳  {done}/{total}  {state}{failed}"
        )

    @on(Button.Pressed, "#queue-bar-stop")
    def _stop(self) -> None:
        self.app.action_stop_fetching()
