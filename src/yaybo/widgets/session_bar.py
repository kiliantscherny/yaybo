"""Which half of the register is answering, along the top of every screen.

This decides what every row on every screen means. Without a login the owners
have no dates of birth, there is no chain of previous owners and no one is
named on a mortgage; with one, the same address gives all three. That is too
important to be a word in a subtitle, so it gets a line and a colour: green
while the session is good, amber as it runs out, red when there is none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

if TYPE_CHECKING:
    from yaybo.app import YayboApp

# The mark carries the same meaning as the colour, for anyone who cannot use
# the colour: filled, half, hollow.
MARKS = {"in": "●", "soon": "◐", "out": "○"}
TONES = {"in": "session-in", "soon": "session-soon", "out": "session-out"}


class SessionBar(Static):
    """The MitID session, and how long the register will keep honouring it."""

    if TYPE_CHECKING:

        @property
        def app(self) -> YayboApp: ...

    def on_mount(self) -> None:
        self.refresh_state()

    def refresh_state(self) -> None:
        line, tone = self.app.session_state()
        # Swap the class rather than the style: the colours belong in the
        # stylesheet with every other colour in the application.
        for name in TONES.values():
            self.remove_class(name)
        self.add_class(TONES[tone])
        self.update(f"{MARKS[tone]}  {line}")
