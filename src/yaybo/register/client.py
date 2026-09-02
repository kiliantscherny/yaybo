"""Talk to tinglysning.dk, the Danish land register - logged in or not.

The register's "forespørgsel uden login" route is free and unauthenticated,
gated by an ALTCHA proof-of-work challenge rather than a human-solved captcha,
so the whole public half of it works from plain Python.

It shows more of itself to someone who has proved who they are. A logged-in
session (see yaybo.auth) reaches a second, richer copy of the same register:

    unsecrest/ejendomsoeg/soeg          rest/ejendom/adresse
    unsecrest/ejendomsoeg/henttingbog   rest/ejdsummarisk
    (nothing public)                    rest/ejdhistoriskadkomst

Note this says who *owns* a property, not who lives there. Resident data
(CPR/folkeregisteret) is not public in Denmark, with or without a login.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

import requests

from yaybo.register.address import AddressError, split_postcode
from yaybo.register.fields import normalise

BASE = "https://www.tinglysning.dk"
UNSEC = f"{BASE}/tinglysning/unsecrest"
SEC = f"{BASE}/tinglysning/rest"

# These endpoints answer with `content-type: application/javascript`, so an
# exact `Accept: application/json` is refused with 406. Ask for anything.
HEADERS = {
    "User-Agent": "yaybo (personal use; contact via github)",
    "Accept": "*/*",
    "Referer": f"{BASE}/tmv/forespoergul",
}


def solve_challenge(session: requests.Session) -> str:
    """Solve an ALTCHA proof-of-work and return the base64 token the API wants.

    The server publishes sha256(salt + n) for a secret n below maxnumber; we
    find n by brute force and hand it back alongside the server's signature.
    """
    challenge = session.get(f"{UNSEC}/altcha/fetchChallenge", timeout=30).json()
    salt, target = challenge["salt"], challenge["challenge"]

    started = time.monotonic()
    for number in range(challenge["maxnumber"] + 1):
        if hashlib.sha256(f"{salt}{number}".encode()).hexdigest() == target:
            break
    else:
        raise RuntimeError("no solution below maxnumber - challenge format changed?")

    payload = {
        "algorithm": challenge["algorithm"],
        "challenge": target,
        "number": number,
        "salt": salt,
        "signature": challenge["signature"],
        "took": int((time.monotonic() - started) * 1000),
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


class SessionExpired(Exception):
    """Raised when tinglysning stops recognising a logged-in session."""


class Tinglysning:
    """Session against the register, holding one reusable ALTCHA token.

    Pass an authenticated session (see tinglysning_auth) to reach the secured
    half of the API as well. The public half stays available either way, and
    stays in use for the parts it answers just as fully - there is no reason to
    spend a login on data that is free.
    """

    def __init__(self, session: requests.Session | None = None) -> None:
        self.authenticated = session is not None
        self.session = session or requests.Session()
        # An authenticated session already impersonates a browser, and its
        # User-Agent is part of what NemLog-in accepted, so leave it alone.
        headers = dict(HEADERS)
        if self.authenticated:
            headers.pop("User-Agent")
        self.session.headers.update(headers)
        self.token: str | None = None

    def _get(self, path: str, params: dict | None = None, token: bool = True):
        """GET a JSON endpoint, re-solving the challenge if the token was refused.

        A refused token does not come back as 4xx - the API returns 200 with an
        empty body, so status codes are useless here and emptiness is the signal.
        """
        params = dict(params or {})
        for _ in range(2):
            if token:
                if self.token is None:
                    self.token = solve_challenge(self.session)
                params["token"] = self.token

            response = self.session.get(f"{UNSEC}/{path}", params=params, timeout=60)
            response.raise_for_status()
            if response.content:
                return response.json()
            if not token:
                return None
            self.token = None  # refused - solve a fresh one and try once more

        raise RuntimeError(f"{path}: empty response even with a fresh token")

    def _secure(self, path: str, params: dict | None = None):
        """GET one of the endpoints that only exist behind a login.

        These need no ALTCHA token - proving who you are replaces proving you
        are not a robot. An idled-out session is answered with a redirect back
        into NemLog-in, so redirects are refused here: a 302 is the one
        unambiguous sign that the login has lapsed.
        """
        if not self.authenticated:
            raise SessionExpired(f"{path} needs a logged-in session")

        response = self.session.get(
            f"{SEC}/{path}",
            params=params,
            timeout=60,
            allow_redirects=False,
            # The secured API is called from the logged-in half of the site,
            # not the public lookup the default Referer points at.
            headers={"Referer": f"{BASE}/tmv/foresporgsel"},
        )
        if response.is_redirect or response.status_code in (401, 403):
            self.authenticated = False
            raise SessionExpired("the tinglysning session has expired")
        response.raise_for_status()
        if not response.content:
            return None

        # Some of these endpoints can render themselves as a document instead
        # of data. We never ask for that, but say so plainly if it happens.
        try:
            return response.json()
        except ValueError:
            return {
                "_raw": response.text,
                "_content_type": response.headers.get("content-type", ""),
            }

    def lookup_address(self, query: str) -> dict:
        """Resolve an address using tinglysning's own autocomplete.

        Only used when DAWA is unreachable. It matches on a prefix of
        "street no., floor. door", so a trailing postcode has to come off
        first, and it cannot parse th/tv doors at all.
        """
        text, postcode = split_postcode(query)
        data = self._get("address/autocomplete", {"q": text}, token=False) or {}
        matches = [
            match
            for match in data.get("addresses") or []
            if not postcode or match["adresse"].get("postnummer") == postcode
        ]
        if not matches:
            raise AddressError(f"no address matched {query!r}")

        wanted = normalise(text)
        exact = next(
            (m for m in matches if normalise(split_postcode(m["tekst"])[0]) == wanted),
            None,
        )
        if exact:
            found = exact["adresse"]
            return {
                "tekst": exact["tekst"],
                "vejnavn": found["vejnavn"],
                "husnummer": found.get("husnummer", ""),
                "postnummer": found["postnummer"],
                "etage": found.get("etage") or "",
                "doer": found.get("doer") or "",
            }

        # Autocomplete only ever answers with individual units, so an inexact
        # query means the building - report it as such rather than echoing back
        # whichever flat happened to sort first.
        found = matches[0]["adresse"]
        street = f"{found['vejnavn']} {found.get('husnummer', '')}".strip()
        postal = f"{found['postnummer']} {found.get('postdistrikt', '')}".strip()
        return {
            "tekst": f"{street}, {postal}",
            "vejnavn": found["vejnavn"],
            "husnummer": found.get("husnummer", ""),
            "postnummer": found["postnummer"],
            "etage": "",
            "doer": "",
        }

    def find_units(self, address: dict) -> list[dict]:
        """List every separately registered property in a building.

        Deliberately searches at building level and never passes etage or
        sidedoer. The register indexes legally registered properties, not
        postal addresses: a block of owner-occupied flats has one property per
        flat, but a rented or co-op building is a single property no matter how
        many doors it has. Constraining the query by floor therefore returns
        nothing at all for most buildings. Narrowing happens in select_units,
        against what the register actually holds.
        """
        query = {
            "postnummer": address["postnummer"],
            "vejnavn": address["vejnavn"],
            "husnummer": address.get("husnummer", ""),
        }
        # The secured search is the same search without the proof-of-work, so
        # prefer it when we can: one round trip instead of a hash hunt.
        if self.authenticated:
            try:
                found = (self._secure("ejendom/adresse", query) or {}).get("items")
            except SessionExpired:
                found = None  # fall through rather than stopping the run
            # An empty answer is a legitimate "no such address", but it is also
            # what a changed response shape would look like, and one wasted
            # request is a cheap way not to have to tell those apart.
            if found:
                return found
        return (self._get("ejendomsoeg/soeg", query) or {}).get("items") or []

    def fetch_record(self, uuid: str) -> dict:
        record = self._get(f"ejendomsoeg/henttingbog/{uuid}") or {}
        if record.get("statuskode"):  # 0 means OK
            raise RuntimeError(
                f"{uuid}: {record.get('statustekst') or record['statuskode']}"
            )
        return record

    def fetch_details(self, uuid: str) -> dict | None:
        """The logged-in view of a property - the tingbogsattest in full.

        Same property as fetch_record, told to someone the register trusts: the
        flat's own BFE number and floor area, what it last sold for and when,
        and enough of each owner's CPR to date their birth.

        Asks for data before asking for a document. The endpoint renders itself
        as an attest when told to, and a rendered attest has to be read back
        out of its own labels - worth avoiding when the alternative is fields.
        """
        for params in ({}, {"tmvXhtml": "true"}):
            try:
                details = self._secure(f"ejdsummarisk/{uuid}", params)
            except requests.HTTPError:
                continue
            if details:
                return details
        return None

    def fetch_history(self, uuid: str) -> dict | None:
        """Everyone who has owned the property before its current owners.

        There is no public counterpart to this one at all.
        """
        return self._secure(f"ejdhistoriskadkomst/uuid/{uuid}")
