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
