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

from datetime import datetime, timedelta
from pathlib import Path

import requests
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.theme import Theme
from textual.widgets import Footer, Header

from yaybo import auth, store
from yaybo.fetching import FetchQueue
from yaybo.register.client import Tinglysning

# Deep navy for the ground, a light blue for anything that can be acted on,
# near-white for the data itself. Three jobs, three values: a reader scanning a
# screen should be able to tell those apart without knowing the palette.
YAYBO_THEME = Theme(
    name="yaybo",
    primary="#5eb0ea",
    secondary="#9ad0f0",
    accent="#7fd8f0",
    foreground="#eef5fb",
    background="#0b1524",
    surface="#132133",
    panel="#1c2f47",
    success="#5bc9a5",
    warning="#e8b96a",
    error="#e8746f",
    dark=True,
    variables={
        "footer-key-foreground": "#5eb0ea",
        "footer-description-foreground": "#93a7bd",
        "input-selection-background": "#5eb0ea 35%",
        "block-cursor-text-style": "none",
        "block-cursor-foreground": "#0b1524",
        "block-cursor-background": "#5eb0ea",
    },
)

# How often to tell the register the session is still wanted. Its own limit is
# 29 minutes of silence; the site's page pings far more often than this.
KEEPALIVE_SECONDS = 8 * 60


class YayboApp(App[None]):
    """Fetch, explore and export the Danish property registers."""

    TITLE = "yaybo"
    CSS_PATH = "styles/yaybo.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "search", "Find new"),
        Binding("l", "library", "Library"),
        Binding("b", "queue", "Queue"),
        Binding("s", "sql", "SQL"),
        Binding("ctrl+l", "login", "Log in", show=False),
        # The queue bar carries a Stop button, which is where anyone will
        # actually reach for this - so it stays out of an already busy footer.
        Binding("ctrl+x", "stop_fetching", "Stop fetching", show=False),
        Binding("ctrl+t", "toggle_auto_fetch", "Auto-fetch on/off", show=False),
    ]

    def __init__(self, *, database: str | Path | None = None) -> None:
        super().__init__()
        self.register_theme(YAYBO_THEME)
        self.theme = "yaybo"
        self.database = Path(database) if database else store.default_path()
        # None until a cached session turns out to be live. Everything that
        # needs the logged-in half of the register goes through `api`, which
        # knows which half it can reach.
        self.session: requests.Session | None = None
        self.who: str | None = None
        self.user_id: str = ""
        # When the session was last known good. The register measures its own
        # limit from the last request, so this is what a countdown counts from.
        self.session_touched: datetime | None = None
        self.api = Tinglysning(None)
        # Owned here rather than by a screen so that a run started on Search
        # keeps going while the user reads something on Library.
        self.fetching = FetchQueue()
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
        # Only so the countdown moves; the session itself is kept alive above.
        self.set_interval(30, self.refresh_session_views)

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

    def session_state(self) -> tuple[str, str]:
        """A line describing the login, and how urgently to colour it.

        The tone is one of "in", "soon", "out". Which half of the register is
        answering changes what every row on every screen means, so it is worth
        a colour rather than a word buried in a subtitle.
        """
        if self.session is None or self.who is None:
            return ("Not logged in - public register only, ctrl+L to log in", "out")
        left = self.session_expires_in()
        if left is None:
            return (f"MitID: {self.who}", "in")
        minutes = int(left.total_seconds() // 60)
        if minutes <= 0:
            return (f"MitID: {self.who} - session lapsing now", "soon")
        tone = "soon" if minutes <= 5 else "in"
        return (f"MitID: {self.who} - {minutes} min left", tone)

    def session_expires_in(self) -> timedelta | None:
        """How long the register will keep answering if nothing else is asked."""
        if self.session is None or self.session_touched is None:
            return None
        return auth.IDLE_LIMIT - (datetime.now() - self.session_touched)

    def refresh_session_views(self) -> None:
        from yaybo.widgets.session_bar import SessionBar

        for bar in self.screen.query(SessionBar):
            bar.refresh_state()

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

    def _adopt(self, session: requests.Session, who: str, user_id: str) -> None:
        self.session = session
        self.who = who
        self.user_id = user_id or self.user_id
        self.api = Tinglysning(session)
        self.session_touched = datetime.now()
        self._describe_session()

    @work(thread=True)
    def _keep_alive(self) -> None:
        """Say "still here" now and then, the way the register's own page does."""
        if self.session is None:
            return
        if auth.keep_alive(self.session):
            auth.save_session(self.session, self.user_id)
            self.session_touched = datetime.now()
            self.call_from_thread(self.refresh_session_views)
            return
        self.call_from_thread(self._session_lapsed)

    def _session_lapsed(self) -> None:
        self.session = None
        self.who = None
        self.session_touched = None
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

        if self.session is not None:
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
        self.refresh_session_views()
        self.notify(f"Logged in as {who}. The register will show more now.")
        # Only the screens that read the database have one, and logging in
        # changes what the database is allowed to say.
        refresh = getattr(self.screen, "action_refresh", None)
        if callable(refresh):
            refresh()

    # ── the fetch queue ─────────────────────────────────────────────────

    def enqueue_units(self, label: str, address: dict, units: list[dict]) -> None:
        """Queue properties the register has already listed for an address."""
        self.fetching.add_units(label, address, units)
        self._after_enqueue()

    def enqueue_queries(self, queries: list[str]) -> int:
        """Queue raw addresses, skipping any already waiting. Returns how many."""
        added = sum(1 for query in queries if self.fetching.add_query(query))
        self._after_enqueue()
        return added

    def _after_enqueue(self) -> None:
        """Start it now, or leave it parked for the queue screen to start."""
        if self.fetching.auto:
            self.start_fetching()
        else:
            self.refresh_queue_views()

    def action_toggle_auto_fetch(self) -> bool:
        """Switch between fetching on arrival and piling up to be started.

        Turning it back on releases whatever piled up while it was off: having
        asked for things to be fetched automatically, being left with a parked
        list would be the surprising outcome.
        """
        self.fetching.auto = not self.fetching.auto
        if self.fetching.auto:
            self.fetching.start()
            self.start_fetching()
        else:
            self.refresh_queue_views()
        self.notify(
            "Auto-fetch on: queued properties start straight away."
            if self.fetching.auto
            else "Auto-fetch off: queued properties wait to be started."
        )
        return self.fetching.auto

    def start_fetching(self) -> None:
        self.refresh_queue_views()
        if not self.fetching.running and self.fetching.waiting:
            self._drain()

    def action_stop_fetching(self) -> None:
        if not self.fetching.running:
            return
        self.fetching.stop()
        self.refresh_queue_views()
        self.notify("Stopping after the property being fetched now…")

    @work(thread=True, group="fetchqueue")
    def _drain(self) -> None:
        # FetchQueue takes its own lock, so a second start while one is already
        # running costs a thread that returns immediately and nothing worse.
        self.fetching.run(
            self.api,
            self.database,
            lambda: self.call_from_thread(self.refresh_queue_views),
        )
        self.call_from_thread(self._drained)

    def refresh_queue_views(self) -> None:
        """Tell whatever is on screen what the queue is doing.

        Called when the queue moves, and by every screen as it surfaces: a
        screen that was mounted before anything was queued still holds a bar
        from that emptier moment, and would otherwise sit there showing it.

        Pushed rather than polled, and pushed at whoever happens to be mounted:
        screens come and go during a run, and none of them should have to
        register or unregister anything to stay honest about it.
        """
        from yaybo.widgets.queue_bar import QueueBar

        for bar in self.screen.query(QueueBar):
            bar.refresh_state()
        watching = getattr(self.screen, "queue_changed", None)
        if callable(watching):
            watching()

    def _drained(self) -> None:
        queue = self.fetching
        self.refresh_queue_views()
        if not queue.jobs:
            return
        failed = f", {queue.failed} failed" if queue.failed else ""
        self.notify(
            f"Fetched {queue.done} propert{'y' if queue.done == 1 else 'ies'}, "
            f"{queue.rows} rows{failed}."
        )
        # The library is the one screen whose contents the queue changes behind
        # its back, so it gets told rather than left showing a stale list.
        refresh = getattr(self.screen, "action_refresh", None)
        if callable(refresh):
            refresh()

    # ── moving between screens ──────────────────────────────────────────

    def action_library(self) -> None:
        from yaybo.screens.library import LibraryScreen

        self._show(LibraryScreen)

    def action_search(self) -> None:
        self.search_for("")

    def search_for(self, query: str) -> None:
        """Open the search screen, optionally with the box already filled in.

        The library's filter is the one place someone reliably types an address
        that is not in the library yet. Carrying that text across turns a dead
        end into the search they meant.
        """
        from yaybo.screens.search import SearchScreen

        self._show(SearchScreen, query=query)

    def action_queue(self) -> None:
        from yaybo.screens.queue import QueueScreen

        self._show(QueueScreen)

    def action_sql(self) -> None:
        from yaybo.screens.sql import SqlScreen

        self._show(SqlScreen)

    def _show(self, screen_type, **kwargs) -> None:
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
            self.push_screen(screen_type(**kwargs))


def run(database: str | Path | None = None) -> int:
    """Start the TUI. Returns a process exit code."""
    YayboApp(database=database).run()
    return 0


def main() -> int:
    """Entry point for `python -m yaybo`; `yaybo` itself goes through the CLI."""
    from yaybo.cli import main as cli_main

    return cli_main()
