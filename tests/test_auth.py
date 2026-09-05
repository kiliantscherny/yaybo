"""What the session module concludes from the register's answers.

The interesting part is not the happy path but the difference between "the
register says you are logged out" and "the register did not answer". Treating
the second as the first throws away a session that is still good, and costs
somebody a login with their phone for no reason - so the distinction is worth
a test of its own.

Nothing here touches the network: the session is a stand-in that returns
whatever the case under test needs.
"""

from __future__ import annotations

from typing import cast

import requests

from yaybo import auth


class Answer:
    """What requests would hand back, reduced to what keep_alive reads."""

    def __init__(self, status_code: int, is_redirect: bool = False) -> None:
        self.status_code = status_code
        self.is_redirect = is_redirect


class Session:
    """A session that always answers the same way, or always raises.

    Not a requests.Session subclass: overriding `get` with a narrower signature
    than the real one is a lie the type checker rightly objects to. It is cast
    at the call site instead, which says "this stands in for one" in one place
    rather than pretending throughout.
    """

    def __init__(self, answer) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def get(self, url, **options):
        self.asked.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def alive(answer):
    """What keep_alive concludes from a register that answers this way."""
    return auth.keep_alive(cast(requests.Session, Session(answer)))


def whoami(session):
    return auth.who_is_logged_in(cast(requests.Session, session))


def test_two_hundred_means_the_session_is_good():
    assert alive(Answer(200)) is True


def test_a_redirect_back_into_nemlogin_means_it_is_gone():
    """A lapsed session is answered with a redirect, not an error."""
    assert alive(Answer(302, is_redirect=True)) is False


def test_being_refused_outright_means_it_is_gone():
    assert alive(Answer(401)) is False
    assert alive(Answer(403)) is False


def test_a_timeout_is_not_an_answer():
    assert alive(requests.Timeout()) is None


def test_a_dropped_connection_is_not_an_answer():
    assert alive(requests.ConnectionError()) is None


def test_the_registers_own_bad_day_is_not_an_answer():
    """A gateway error says nothing about whether we are logged in."""
    assert alive(Answer(502)) is None
    assert alive(Answer(500)) is None


def test_the_status_endpoint_names_who_the_register_thinks_we_are():
    class Status(Session):
        def get(self, url, **options):
            class Reply:
                @staticmethod
                def json():
                    return {"loggedIn": True, "brugernavn": "Ida Testesen"}

            return Reply()

    assert whoami(Status(None)) == "Ida Testesen"


def test_a_private_login_still_counts_as_logged_in():
    """A personal MitID login has no company name attached to it."""

    class Status(Session):
        def get(self, url, **options):
            class Reply:
                @staticmethod
                def json():
                    return {"loggedIn": True}

            return Reply()

    assert whoami(Status(None)) == "(logged in)"


def test_not_logged_in_is_reported_as_nobody():
    class Status(Session):
        def get(self, url, **options):
            class Reply:
                @staticmethod
                def json():
                    return {"loggedIn": False}

            return Reply()

    assert whoami(Status(None)) is None
