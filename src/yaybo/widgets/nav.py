"""The places this application has, as tabs along the top of every screen.

The screens were always peers - the Library, the Search, the Queue - but the
only way to learn that was to read the footer and find the letter that went
there. Tabs say it outright: here are the six things, this is the one you are
on, and clicking another goes to it.

They are a navigation bar rather than a TabbedContent: each place is still its
own Screen, with its own bindings and its own worker, and putting six of them
inside one screen would mean six screens' worth of widgets mounted at once for
the sake of a border.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.widgets import Tab, Tabs

from yaybo import i18n

if TYPE_CHECKING:
    from yaybo.app import YayboApp

# tab id, what it is called, the application action that goes there.
# The labels are English here and translated where they are used, because
# this is built on import - before anyone has chosen a language.
PLACES = (
    ("ejendomme", "Properties", "library"),
    ("andele", "Co-op shares", "andele"),
    ("bygninger", "Buildings", "buildings"),
    ("noegletal", "Figures", "stats"),
    ("koe", "Queue", "queue"),
    ("soeg", "Search", "search"),
)
ACTIONS = {key: action for key, _, action in PLACES}


class NavTabs(Tabs):
    """Where you are, and one click to anywhere else."""

    if TYPE_CHECKING:

        @property
        def app(self) -> YayboApp: ...

    def __init__(self, here: str) -> None:
        super().__init__(
            *[Tab(i18n.t(label), id=key) for key, label, _ in PLACES], active=here
        )
        self.here = here

    @on(Tabs.TabActivated)
    def _go(self, event: Tabs.TabActivated) -> None:
        # Mounting a Tabs with an active tab activates it, and so does coming
        # back to a screen. Only a tab that is not the one we are already on
        # means somebody asked to go somewhere.
        if event.tab.id is None or event.tab.id == self.here:
            return
        getattr(self.app, f"action_{ACTIONS[event.tab.id]}")()
