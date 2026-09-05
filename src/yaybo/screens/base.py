"""What every screen in this application can assume about the one above it.

Textual types `self.app` as the base `App`, so `self.app.database` reads
statically as an attribute that might not be there - true of Textual apps in
general, and not true of these screens, which only ever run inside `YayboApp`.
Saying so once here is what lets the type checker be useful about the rest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.screen import Screen

if TYPE_CHECKING:
    from yaybo.app import YayboApp


class YayboScreen(Screen[None]):
    """A screen that knows which application it belongs to."""

    if TYPE_CHECKING:

        @property
        def app(self) -> YayboApp: ...  # type: ignore[override]

    def on_screen_resume(self) -> None:
        """Catch up with the fetch queue on the way in.

        Defined here rather than on each screen because no screen overrides it,
        and because the one that needs it most is the Library: it is mounted
        once, at the bottom of the stack, usually before anything has been
        queued at all. Coming back to it must not show the queue as it was then.

        Deferred, because a screen is resumed before its children are mounted:
        asking a half-built screen about its widgets finds nothing there.
        """
        self.app.call_after_refresh(self.app.refresh_queue_views)
        self.app.call_after_refresh(self.app.refresh_session_views)
