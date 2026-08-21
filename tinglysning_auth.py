"""Hold a logged-in tinglysning.dk session between runs.

Logging in with MitID takes a phone in your hand, so it must not happen once
per lookup. This module runs the login when asked, keeps the resulting cookies
in a file only you can read, and hands them back on later runs until the server
decides the session has idled out.

What logging in buys is a second, richer copy of the register:

    unsecrest/ejendomsoeg/soeg          rest/ejendom/adresse
    unsecrest/ejendomsoeg/henttingbog   rest/ejdsummarisk
    (nothing public)                    rest/ejdhistoriskadkomst
    (nothing public)                    rest/soegpersonbog

The authenticated endpoints need no ALTCHA proof-of-work, name owners' dates of
birth, and can look backwards through previous owners.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import requests

import mitid
import nemlogin

TINGLYSNING = "https://www.tinglysning.dk"
# The "forespørgsel med login" link. It starts no SAML flow of its own - it
# sets up the server-side session and bounces to the lookup page, whose guard
# is what actually sends the browser off to log in. Visiting it first is the
# difference between arriving the way a person does and arriving sideways.
LOOKUP_ENTRY = f"{TINGLYSNING}/tinglysning/forespoergsel-logon.jsp"
# tmv-logon.jsp is where that guard sends them: it 302s straight to NemLog-in,
# and `url` is where it sends the browser once we come back.
LOGIN_URL = f"{TINGLYSNING}/tinglysning/tmv-logon.jsp?url=%2Fforesporgsel"
STATUS_URL = f"{TINGLYSNING}/tinglysning/unsecrest/util/isuserloggedin/"
LOGOUT_URL = f"{TINGLYSNING}/tinglysning/logout"
# What the site's own page calls to say "still here". It pings this at most
# once a minute while someone has the tab open.
ALIVE_URL = f"{TINGLYSNING}/tinglysning/rest/alive/nu"

# The register drops a session that has sat idle. Its own timers, read off the
# site: a warning at 25 minutes, the door at 29. Anything we send resets the
# clock, so the session outlives any number of lookups - it is the gaps in
# between that end it.
IDLE_LIMIT = timedelta(minutes=29)

SESSION_FILE = "tinglysning-session.json"
REPORT_FILE = "last-login-report.json"


class AuthError(Exception):
    """Raised when we cannot get or keep a logged-in session."""


def session_path() -> Path:
    """Where the cached cookies live, following the XDG config convention."""
    root = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(root).expanduser() / "yaybo" / SESSION_FILE


def save_session(session: requests.Session, user_id: str = "") -> Path:
    """Write the session's cookies out, readable by nobody else.

    These cookies are a live login to a government register in your name, so
    the file is created 0600 and the mode is re-applied on every write in case
    an older run left it looser.
    """
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id,
        "cookies": [
            {
                "name": cookie.name,
                "value": cookie.value or "",
                "domain": cookie.domain,
                "path": cookie.path,
            }
            for cookie in session.cookies
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def restore_session() -> tuple[requests.Session, dict] | None:
    """Rebuild a session from the cache. Returns None when there is nothing to
    restore; says nothing about whether the server still honours it."""
    path = session_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    session = nemlogin.new_session()
    for cookie in payload.get("cookies") or []:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain") or "",
            path=cookie.get("path") or "/",
        )
    return session, payload


def forget_session() -> bool:
    """Delete the cached cookies. Returns whether there was anything to delete."""
    path = session_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def who_is_logged_in(session: requests.Session) -> str | None:
    """Ask tinglysning who it thinks we are; None when the session is dead.

    This endpoint is deliberately on the *unsecured* side of the API, so it
    answers rather than bouncing us into a login redirect - which makes it the
    one honest way to test a restored session.
    """
    try:
        status = session.get(STATUS_URL, timeout=30).json()
    except (requests.RequestException, ValueError):
        return None
    if not status.get("loggedIn"):
        return None
    # A private MitID login has no company name attached, so brugernavn can be
    # missing on a session that is perfectly valid.
    return status.get("brugernavn") or "(logged in)"


def log_in(
    user_id: str,
    *,
    method: str = mitid.APP,
    password: str | None = None,
    choose_identity=None,
    ask_token_code=None,
    on_status=None,
    on_qr=None,
    on_otp=None,
) -> tuple[requests.Session, str]:
    """Run the MitID login and return the session plus who we ended up as."""
    session = nemlogin.new_session()
    trace: list = []
    try:
        session.get(LOOKUP_ENTRY, timeout=30)
        final = nemlogin.log_in(
            session,
            LOGIN_URL,
            user_id,
            method=method,
            password=password,
            choose_identity=choose_identity,
            ask_token_code=ask_token_code,
            on_status=on_status,
            on_qr=on_qr,
            on_otp=on_otp,
            trace=trace,
        )
    except (nemlogin.NemLogInError, mitid.MitIDError) as error:
        write_report(trace)
        raise AuthError(str(error)) from error

    # NemLog-in can hand back a valid assertion that tinglysning then declines,
    # so the login is only real once tinglysning itself says so.
    who = who_is_logged_in(session)
    if who is None:
        # The SPA route is the page a browser would have landed on. Some
        # servers bind the session there rather than at the assertion consumer,
        # so give that one navigation a chance before calling it a failure.
        session.get(f"{TINGLYSNING}/tmv/foresporgsel", timeout=30)
        who = who_is_logged_in(session)

    if who is None:
        # Keep the cookies anyway. They cost a tap on a phone, and if the
        # session turns out to be halfway usable that is worth knowing before
        # asking for another one.
        save_session(session, user_id)
        report = write_report(trace, final)
        raise AuthError(
            f"MitID accepted the login but tinglysning did not start a session.\n"
            f"  last stop: {final.url}\n"
            f"  what happened, hop by hop: {report}"
        )

    save_session(session, user_id)
    return session, who


def write_report(trace: list, final=None) -> Path:
    """Write the login's hop-by-hop trace next to the session file.

    A failed login is only diagnosable from the pages it passed through, and
    those pages can carry a name and a CPR number, so this lands 0600 in the
    same private directory as the session itself.
    """
    path = session_path().with_name(REPORT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {"trace": trace}
    if final is not None:
        report["final_url"] = final.url
        report["final_status"] = final.status_code
        report["final_html"] = final.text
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def keep_alive(session: requests.Session) -> bool:
    """Tell the register the session is still wanted; True while it agrees."""
    try:
        response = session.get(ALIVE_URL, timeout=30, allow_redirects=False)
    except requests.RequestException:
        return False
    # A lapsed session is answered with a redirect back into NemLog-in rather
    # than an error, so a 200 is the whole of the good news.
    return response.status_code == 200


def idle_for(saved_at: str | None) -> timedelta | None:
    """How long since we last used the session, as far as this machine knows.

    Every run rewrites the session file, so its timestamp is a record of last
    use rather than of the login - which is the thing the idle limit measures.
    """
    if not saved_at:
        return None
    try:
        return datetime.now() - datetime.fromisoformat(saved_at)
    except ValueError:
        return None


def log_out(session: requests.Session) -> None:
    """End the server-side session and drop the cached cookies."""
    try:
        session.get(LOGOUT_URL, timeout=30)
    except requests.RequestException:
        pass  # the cookies go either way; a dead session is the goal
    forget_session()
