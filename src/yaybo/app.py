"""The yaybo TUI: one application, one database, five ways into it.

    Library   everything already fetched, browsable offline
    Search    an address, resolved as you type, then fetched
    Queue     a whole street at a time, with a progress bar
    SQL       the accumulated database, queried directly
    Property  one property in full, tab by tab

The application object holds the two things every screen needs - the database
path and the register session - and nothing else. Screens read the database for
themselves rather than being handed rows, so a fetch on one screen shows up on
the next without anything having to be told about it.
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.theme import Theme
from textual.widgets import Footer, Header

from yaybo import auth, store
from yaybo.register.client import Tinglysning

# Land-register colours: aged paper, ink, the amber of a stamped seal.
YAYBO_THEME = Theme(
    name="yaybo",
    primary="#e0a458",
    secondary="#7fb3a3",
    accent="#d98757",
    foreground="#e9e3d6",
    background="#14120f",
    surface="#1e1b17",
    panel="#2a251f",
    success="#8fbc6b",
    warning="#e0a458",
    error="#d9635f",
    dark=True,
    variables={
        "footer-key-foreground": "#e0a458",
        "footer-description-foreground": "#a09585",
        "input-selection-background": "#7fb3a3 35%",
        "block-cursor-text-style": "none",
        "block-cursor-foreground": "#14120f",
        "block-cursor-background": "#e0a458",
    },
)

# How often to tell the register the session is still wanted. Its own limit is
# 29 minutes of silence; the site's page pings far more often than this.
KEEPALIVE_SECONDS = 8 * 60


class YayboApp(App):
    """Fetch, explore and export the Danish property registers."""

    TITLE = "yaybo"
    CSS_PATH = "styles/yaybo.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "search", "Search"),
        Binding("l", "library", "Library"),
        Binding("b", "queue", "Queue"),
        Binding("s", "sql", "SQL"),
        Binding("ctrl+l", "login", "Log in", show=False),
    ]

    def __init__(self, *, database: str | Path | None = None) -> None:
        super().__init__()
        self.register_theme(YAYBO_THEME)
        self.theme = "yaybo"
        self.database = Path(database) if database else store.default_path()
        # None until a cached session turns out to be live. Everything that
        # needs the logged-in half of the register goes through `api`, which
        # knows which half it can reach.
        self.session = None
        self.who: str | None = None
        self.user_id: str = ""
        self.api = Tinglysning(None)
        # Cleared the first time the library reports what it holds. An empty
        # database on the first run means the useful screen is Search.
        self._first_run = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        from yaybo.screens.library import LibraryScreen

        self.push_screen(LibraryScreen())
        self._resume_session()
        self.set_interval(KEEPALIVE_SECONDS, self._keep_alive)

    def consume_first_run(self) -> bool:
        """True once, for whoever asks first, then False forever."""
        was, self._first_run = self._first_run, False
        return was

    # ── the register session ────────────────────────────────────────────

    @property
    def logged_in(self) -> bool:
        return self.session is not None

    def _describe_session(self) -> None:
        """Say which database and which half of the register we are on.

        Both belong in the header rather than in a notification: which database
        is being written to and whether the session is logged in are true for as
        long as the application is open, and the second changes what the data
        means.
        """
        state = f"logged in as {self.who}" if self.who else "public lookup only"
        # State first: a long database path is truncated from the right, and
        # which half of the register we are on is the half worth keeping.
        self.sub_title = f"{state}  ·  {self.database}"

    @work(thread=True)
    def _resume_session(self) -> None:
        """Pick up a cached login, if the register still honours it.

        Silent when there is none: the public half of the register answers
        perfectly well without one, and being told off for not logging in every
        time the application starts would be tiresome.
        """
        remembered = auth.restore_session()
        if remembered is None:
            self.call_from_thread(self._describe_session)
            return
        session, saved = remembered
        who = auth.who_is_logged_in(session)
        if who is None:
            self.call_from_thread(self._describe_session)
            return
        auth.save_session(session, saved.get("user_id", ""))
        self.call_from_thread(self._adopt, session, who, saved.get("user_id", ""))

    def _adopt(self, session, who: str, user_id: str) -> None:
        self.session = session
        self.who = who
        self.user_id = user_id or self.user_id
        self.api = Tinglysning(session)
        self._describe_session()

    @work(thread=True)
    def _keep_alive(self) -> None:
        """Say "still here" now and then, the way the register's own page does."""
        if self.session is None:
            return
        if auth.keep_alive(self.session):
            auth.save_session(self.session, self.user_id)
            return
        self.call_from_thread(self._session_lapsed)

    def _session_lapsed(self) -> None:
        self.session = None
        self.who = None
        self.api = Tinglysning(None)
        self._describe_session()
        self.notify(
            "The register ended the session - press ctrl+L to log in again.",
            severity="warning",
        )

    @work
    async def action_login(self) -> None:
        """Log in with MitID, or log out if there is already a session."""
        from mitid.ui.tui import MitIDLoginScreen

        if self.logged_in:
            auth.log_out(self.session)
            self.session = None
            self.who = None
            self.api = Tinglysning(None)
            self._describe_session()
            self.notify("Logged out. The public lookup still works.")
            return

        remembered = auth.restore_session()
        user_id = (remembered[1].get("user_id") if remembered else "") or self.user_id
        result = await self.push_screen_wait(
            MitIDLoginScreen(
                auth.log_in,
                user_id=user_id,
                service="tinglysning.dk, via NemLog-in",
                title="Log in to the land register",
            )
        )
        if not result:
            return
        session, who = result
        self._adopt(session, who, user_id)
        self.notify(f"Logged in as {who}. The register will show more now.")
        if hasattr(self.screen, "action_refresh"):
            self.screen.action_refresh()

    # ── moving between screens ──────────────────────────────────────────

    def action_library(self) -> None:
        from yaybo.screens.library import LibraryScreen

        self._show(LibraryScreen)

    def action_search(self) -> None:
        from yaybo.screens.search import SearchScreen

        self._show(SearchScreen)

    def action_queue(self) -> None:
        from yaybo.screens.queue import QueueScreen

        self._show(QueueScreen)

    def action_sql(self) -> None:
        from yaybo.screens.sql import SqlScreen

        self._show(SqlScreen)

    def _show(self, screen_type) -> None:
        """Switch to a screen, or do nothing if it is already the one on top.

        The four main screens are peers, not a stack: pressing `l` from Search
        should land on the Library, not bury Search underneath it. Everything is
        reachable from the Library, so it stays at the bottom and any switch
        unwinds back to it first.
        """
        from yaybo.screens.library import LibraryScreen

        if isinstance(self.screen, screen_type):
            return
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if screen_type is not LibraryScreen:
            self.push_screen(screen_type())


def run(database: str | Path | None = None) -> int:
    """Start the TUI. Returns a process exit code."""
    YayboApp(database=database).run()
    return 0


def main() -> int:
    """Entry point for `python -m yaybo`; `yaybo` itself goes through the CLI."""
    from yaybo.cli import main as cli_main

    return cli_main()
