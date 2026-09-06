"""Choose the language the interface is written in, without leaving the screen.

Each language is offered in its own name rather than translated into the one
currently in force: somebody who has landed in the wrong language is looking
for the word they recognise, and "Dansk" is that word whichever side of the
switch they are on.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RadioButton, RadioSet, Static

from yaybo import i18n


class LanguageDialog(ModalScreen[str | None]):
    """Modal: pick English or Danish, and rebuild the interface in it."""

    BINDINGS = [("escape", "dismiss_dialog", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="language-box"):
            yield Label(i18n.t("Language"), id="language-title")
            yield Static(
                i18n.t(
                    "The interface only. Register data stays in Danish, "
                    "because that is the language it was written in."
                ),
                id="language-note",
            )
            with RadioSet(id="language-choice"):
                for code, name in i18n.LANGUAGES:
                    yield RadioButton(
                        name, value=code == i18n.current(), id=f"lang-{code}"
                    )
            with Horizontal(id="language-buttons"):
                yield Button(i18n.t("Cancel"), id="language-cancel")
                yield Button(i18n.t("Switch"), variant="primary", id="language-go")

    def _chosen(self) -> str:
        pressed = self.query_one("#language-choice", RadioSet).pressed_button
        if pressed is None or pressed.id is None:
            return i18n.current()
        return pressed.id.removeprefix("lang-")

    @on(Button.Pressed, "#language-go")
    def _switch(self) -> None:
        self.dismiss(self._chosen())

    @on(Button.Pressed, "#language-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_dismiss_dialog(self) -> None:
        self.dismiss(None)
