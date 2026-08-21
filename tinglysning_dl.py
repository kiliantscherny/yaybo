"""Pull property ownership from tinglysning.dk into a CSV, logged in or not.

Tinglysning is the Danish land register. Its "forespørgsel uden login" route
(https://www.tinglysning.dk/tmv/forespoergul) is free and unauthenticated, but
gated by an ALTCHA proof-of-work challenge rather than a human-solved captcha,
so the whole flow works from plain Python.

    uv run tinglysning_dl.py "Prøvegade 1, 9999 Prøveby"

The register shows more of itself to someone who has proved who they are. Log
in once with MitID and later runs quietly pick the session back up, adding each
owner's date of birth and the chain of previous owners to the same CSVs:

    uv run tinglysning_dl.py --login --user YourMitIDUserID
    uv run tinglysning_dl.py "Prøvegade 1, 9999 Prøveby"

Note this tells you who *owns* a property, not who lives there. Resident data
(CPR/folkeregisteret) is not public in Denmark, with or without a login.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import logging
import re
import sys
import time
import unicodedata
from datetime import date
from xml.etree import ElementTree
from pathlib import Path

import requests

import console
import mitid
import tinglysning_auth

BASE = "https://www.tinglysning.dk"
UNSEC = f"{BASE}/tinglysning/unsecrest"
SEC = f"{BASE}/tinglysning/rest"
DAWA = "https://api.dataforsyningen.dk"

# These endpoints answer with `content-type: application/javascript`, so an
# exact `Accept: application/json` is refused with 406. Ask for anything.
HEADERS = {
    "User-Agent": "tinglysning_dl.py (personal use; contact via github)",
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
            return {"_raw": response.text, "_content_type": response.headers.get("content-type", "")}

    def lookup_address(self, query: str) -> dict:
        """Resolve an address using tinglysning's own autocomplete.

        Only used when DAWA is unreachable. It matches on a prefix of
        "street no., floor. door", so a trailing postcode has to come off
        first, and it cannot parse th/tv doors at all.
        """
        text, postcode = _split_postcode(query)
        data = self._get("address/autocomplete", {"q": text}, token=False) or {}
        matches = [
            match
            for match in data.get("addresses") or []
            if not postcode or match["adresse"].get("postnummer") == postcode
        ]
        if not matches:
            raise SystemExit(f"no address matched {query!r}")

        wanted = _normalise(text)
        exact = next(
            (m for m in matches if _normalise(_split_postcode(m["tekst"])[0]) == wanted),
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
            raise RuntimeError(f"{uuid}: {record.get('statustekst') or record['statuskode']}")
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


def resolve_address(query: str) -> dict:
    """Clean free-text Danish address input via DAWA, the official address register.

    DAWA is far more forgiving than tinglysning's own autocomplete, which
    cannot parse th/tv doors, a mis-spaced house number ("30 B") or a missing
    diacritic ("Frederiksberg Alle").

    Two DAWA endpoints, because they fail in opposite directions. `datavask`
    is built for messy but *complete* addresses and grades its answer: A exact,
    B corrected, C ambiguous. C must not be trusted - asked for the
    non-existent "Frederiksberg Alle 42" it confidently answers number 12 - so
    anything below B falls through to `autocomplete`, which handles partial
    input but wants roughly correct spelling.
    """
    washed = requests.get(
        f"{DAWA}/datavask/adresser", params={"betegnelse": query}, timeout=30
    ).json()
    results = washed.get("resultater") or []
    if washed.get("kategori") in ("A", "B") and len(results) == 1:
        return _from_dawa(results[0]["aktueladresse"])

    matches = requests.get(
        f"{DAWA}/adresser/autocomplete", params={"q": query, "per_side": 10}, timeout=30
    ).json()
    buildings: dict[tuple, dict] = {}
    for match in matches:
        found = match["adresse"]
        buildings.setdefault((found["vejnavn"], found["husnr"], found["postnr"]), found)

    if not buildings:
        raise SystemExit(f"no address matched {query!r}")
    if len(buildings) > 1:
        listing = "\n  ".join(f"{v} {h}, {p}" for v, h, p in list(buildings)[:5])
        raise SystemExit(f"{query!r} is ambiguous - try one of:\n  {listing}")

    # Autocomplete answers a flat query with its siblings too ("13. 3" also
    # returns 13. 1, 13. 2), so a result count says nothing about intent. Keep
    # the floor and door only when a result renders the query exactly;
    # otherwise the query named a building and the unit would be a guess.
    wanted = _normalise(_split_postcode(query)[0])
    exact = next(
        (m for m in matches if _normalise(_split_postcode(m["tekst"])[0]) == wanted),
        None,
    )
    if exact:
        return _from_dawa(exact["adresse"])
    return _from_dawa({**next(iter(buildings.values())), "etage": None, "dør": None})


def _from_dawa(found: dict) -> dict:
    """Map DAWA's field names onto the ones tinglysning's search expects."""
    etage, doer = found.get("etage") or "", found.get("dør") or ""
    unit = f"{etage}. {doer}".strip() if etage else doer
    postal = f"{found['postnr']} {found.get('postnrnavn', '')}".strip()
    return {
        "tekst": ", ".join(
            filter(None, [f"{found['vejnavn']} {found['husnr']}", unit, postal])
        ),
        "vejnavn": found["vejnavn"],
        "husnummer": found["husnr"],
        "postnummer": found["postnr"],
        "etage": etage,
        "doer": doer,
    }


def select_units(units: list[dict], etage: str, doer: str) -> tuple[list[dict], str]:
    """Narrow a building's properties to one flat, if the register has one.

    Returns the chosen units and a warning to show the user, empty when there
    is nothing to flag. A floor that matches nothing is not an error: it
    usually means the building is registered as a single property, so we hand
    back everything rather than an empty result that reads as "no such address".
    """
    if not etage and not doer:
        return units, ""

    wanted = _normalise(f"{etage}. {doer}" if doer else etage)
    picked = [u for u in units if _normalise(_unit_label(u.get("adresse", ""))) == wanted]
    if picked:
        return picked, ""

    unit = f"{etage}. {doer}".strip()
    plural = "property" if len(units) == 1 else "properties"
    return units, (
        f"no separately registered unit for '{unit}' - this building is "
        f"registered as {len(units)} {plural}; showing all"
    )


def _unit_label(adresse: str) -> str:
    """Pull "13. 3" out of "Prøvegade 1, 13. 3, 9999 Prøveby"."""
    head, _ = _split_postcode(adresse)
    return ", ".join(part.strip() for part in head.split(",")[1:])


def _drop_unit(adresse: str) -> str:
    """Strip the flat from an address, leaving the building it belongs to."""
    head, _ = _split_postcode(adresse)
    postal = adresse[len(head):].lstrip(", ")
    street = head.split(",")[0].strip()
    return f"{street}, {postal}" if postal else street


def _normalise(text: str) -> str:
    return " ".join(text.lower().replace(",", " ").split())


def _split_postcode(text: str) -> tuple[str, str]:
    """Split "Prøvegade 1, 9999 Prøveby" into the part before the
    postcode and the postcode itself. Returns ("...", "") if there is none."""
    match = re.search(r",?\s*(\d{4})\s+[^,\d]+$", text)
    if not match:
        return text.strip(), ""
    return text[: match.start()].strip().rstrip(","), match.group(1)


def fetch_parcel(ejerlavkode: str, matrikelnr: str, cache: dict) -> dict:
    """Look up the land parcel in DAWA for its BFE number and registered area.

    Careful what this is: for a whole-building property it is the property's
    own BFE, but for a flat in a subdivided block it is the BFE of the *parcel
    underneath*, and the area is the whole plot. The per-flat BFE, area and
    ejerlejlighedsnummer live in Matriklen, which needs Datafordeleren
    credentials. The columns are named grund_* so the two never get confused.
    """
    key = (ejerlavkode, matrikelnr)
    if key not in cache:
        try:
            parcel = requests.get(
                f"{DAWA}/jordstykker/{ejerlavkode}/{matrikelnr}", timeout=30
            ).json()
        except (requests.RequestException, ValueError):
            parcel = {}
        cache[key] = {
            "grund_bfe": parcel.get("bfenummer", ""),
            "grund_areal_m2": parcel.get("registreretareal", ""),
        }
    return cache[key]


# The attest prints a label, then its value on the next line, and these are the
# labels it really uses - read off a real one rather than guessed at. Note how
# little they resemble the column names they feed: the flat's own size is
# "Ejerlejlighedens areal" while a bare "Areal" is the whole plot's, and the
# flat's number is a bare "Nummer". Order inside each tuple is preference, for
# the labels the attest gives more than one of.
ATTEST_LABELS = {
    "ejerlejlighedsnr": ("nummer",),
    "bfe_nr": ("bfe-nummer", "bfe-nr", "bfenummer"),
    "areal_m2": ("ejerlejlighedens areal",),
    "opdelingsdato": ("ejerlejlighedens dato",),
    "fordelingstal": ("fordelingstal",),
    "adkomst_dokumenttype": ("dokumenttype",),
    "adkomst_dato_loebenummer": ("dato/løbenummer", "dato/loebenummer"),
    "koebesum_dkk": ("købesum i alt", "kontant købesum", "købesum"),
    "overtagelsesdato": ("dato for overtagelse", "overtagelsesdato"),
}
NAME_LABELS = ("navn", "ejer", "adkomsthaver")
BIRTH_LABELS = ("fødselsdato", "foedselsdato", "fodselsdato", "fødselsdag")
CPR_LABELS = ("cpr-nr", "cpr nr", "cprnr", "cpr", "cprnummer", "personnummer")
NUMERIC_COLUMNS = ("areal_m2", "koebesum_dkk")
DATE_COLUMNS = ("opdelingsdato", "overtagelsesdato")


# What the register calls each thing in its XML. These are element names on a
# path from the record's root, namespaces stripped.
ADKOMST_TYPES = {
    "skoede": "Skøde",
    "endeligtskoede": "Endeligt skøde",
    "betingetskoede": "Betinget skøde",
    "auktionsskoede": "Auktionsskøde",
    "adkomsterklaering": "Adkomsterklæring",
}


def attest_details(details: dict | None) -> dict:
    """Flatten the logged-in record into the columns the CSV wants.

    Returns the property's own fields plus an `owners` map, keyed on the
    normalised name so it can be joined against the public record's owner list,
    which is the only thing the two views share.

    The register answers this one in XML - a full OIO document, far better
    described than the attest a browser is shown. The label readers below stay
    as a fallback for the day it answers with that attest instead.
    """
    if not details:
        return {}
    if "_raw" in details:
        return _from_xml(details["_raw"]) or _collect(
            _pairs_from_document(details["_raw"])
        )
    return _collect(_pairs_from_data(details))


def _from_xml(raw: str) -> dict:
    """Read the property, its current deed and its owners out of the XML."""
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return {}

    ejendom = _first(_dig(root, "EjendomSummarisk"))
    if ejendom is None:
        return {}

    found: dict = {"owners": {}}
    stam = _first(_dig(ejendom, "EjendomStamoplysninger"))
    if stam is not None:
        found["bfe_nr"] = _text(stam, "EjendomIdentifikator", "BestemtFastEjendomNummer")
        found["ejerlejlighedsnr"] = _text(
            stam, "EjendomIdentifikator", "EjendomType", "Ejerlejlighed",
            "Ejerlejlighedsnummer",
        )
        # A share of the co-ownership, printed as the fraction it is.
        over = _text(stam, "Fordelingtal", "Taeller")
        under = _text(stam, "Fordelingtal", "Naevner")
        if over and under:
            found["fordelingstal"] = f"{over}/{under}"

        # The flat's own date and floor area are not fields at all: they are
        # headed free text, the same slot the register uses for a note on an
        # easement. Match on the heading rather than on position.
        for group in _dig(stam, "TillaegstekstSamling", "TekstAngivelse", "TekstGruppe"):
            heading = _label_key(_text(group, "Overskrift"))
            body = _text(group, "Afsnit")
            if "dato" in heading:
                found["opdelingsdato"] = _iso_date(body)
            elif "areal" in heading:
                found["areal_m2"] = _plain_number(body)

    adkomst = _first(_dig(ejendom, "AdkomstSummariskSamling", "AdkomstSummarisk"))
    if adkomst is not None:
        deed = _text(adkomst, "AdkomstType")
        found["adkomst_dokumenttype"] = ADKOMST_TYPES.get(deed, deed)
        found["adkomst_dato_loebenummer"] = _text(
            adkomst, "DokumentAlias", "DokumentAliasIdentifikator"
        ) or _text(adkomst, "DokumentAlias", "AktHistoriskIdentifikator")
        # What was actually paid, over what changed hands in other ways.
        found["koebesum_dkk"] = _text(adkomst, "SkoedeKoebesum", "IAltKoebesum") or _text(
            adkomst, "SkoedeKoebesum", "KontantKoebesum"
        )
        found["overtagelsesdato"] = _iso_date(_text(adkomst, "SkoedeOvertagelsesDato"))

        for holder in _dig(adkomst, "AdkomsthaverSamling", "Adkomsthaver"):
            person = _first(_dig(holder, "PersonSimpelIdentifikator"))
            company = _first(_dig(holder, "VirksomhedSimpelIdentifikator"))
            if person is not None:
                name = _text(person, "PersonName")
                identity = {"foedselsdato": _iso_date(_text(person, "BirthDate"))}
            elif company is not None:
                # A company has no date of birth; its CVR number is the
                # equivalent thing to know about it.
                name = _text(company, "LegalUnitName")
                identity = {"cvr": _text(company, "CVRnumberIdentifier")}
            else:
                continue
            if name:
                found["owners"][_normalise(name)] = identity

    return {key: value for key, value in found.items() if value != ""}


def _tag(element) -> str:
    """An element's name without its namespace."""
    return element.tag.rsplit("}", 1)[-1]


def _dig(element, *names):
    """Follow a path of element names down, ignoring namespaces."""
    nodes = [element]
    for name in names:
        nodes = [child for node in nodes for child in node if _tag(child) == name]
    return nodes


def _first(nodes):
    return nodes[0] if nodes else None


def _text(element, *names) -> str:
    found = _dig(element, *names) if names else [element]
    return (found[0].text or "").strip() if found else ""


def _collect(pairs) -> dict:
    """Turn a stream of label/value pairs into one property's worth of columns.

    Two things make this a stream rather than a lookup. A person's identifying
    line is printed *under* their name, not beside it, so the last name seen
    owns whatever comes next; and the same label recurs down the document -
    "Dokumenttype" belongs to the adkomst the first time and to a mortgage
    every time after - so earlier wins.
    """
    found: dict = {"owners": {}}
    ranks: dict = {}
    owner = ""
    for label, value in pairs:
        key = _label_key(label)
        value = str(value).strip()
        if not value:
            continue

        for column, spellings in ATTEST_LABELS.items():
            if key in spellings:
                rank = spellings.index(key)
                if rank < ranks.get(column, len(spellings)):
                    ranks[column], found[column] = rank, value

        if key in NAME_LABELS:
            owner = value
            found["owners"].setdefault(_normalise(value), {})
        elif owner and key in BIRTH_LABELS:
            found["owners"][_normalise(owner)]["foedselsdato"] = _iso_date(value)
        elif owner and key in CPR_LABELS:
            born = _birth_from_cpr(value)
            if born:
                found["owners"][_normalise(owner)]["foedselsdato"] = born

    for column in NUMERIC_COLUMNS:
        if column in found:
            found[column] = _plain_number(found[column])
    for column in DATE_COLUMNS:
        if column in found:
            found[column] = _iso_date(found[column])
    return found


def _birth_from_cpr(cpr: str) -> str:
    """Read a date of birth out of a CPR number, masked or not.

    The attest gives no date of birth as such - it prints "Cpr-nr.:
    010195-****", and a CPR opens with the birth date as DDMMYY. Only that half
    is legible, which is also the only half worth keeping, so the serial is
    never carried into the CSV even on the day the register stops masking it.

    The century lives in the masked digits and has to be inferred: a two-digit
    year later than this one belongs to the last century, since nobody buying
    property today was born in the 2090s.
    """
    digits = re.sub(r"\D", "", cpr.split("-")[0])
    if len(digits) != 6:
        return ""
    day, month, year = int(digits[:2]), int(digits[2:4]), int(digits[4:])
    century = 1900 if year > date.today().year % 100 else 2000
    try:
        return date(century + year, month, day).isoformat()
    except ValueError:
        return ""


def _plain_number(value: str) -> str:
    """"1.234.567 DKK" becomes "1234567"; "55 kvm" becomes "55".

    Danish thousands separators are full stops, so a spreadsheet reads the
    attest's own figures as text. Anything that is not purely a number and a
    unit is left exactly as it was.
    """
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d{3})*(?:,\d+)?)\s*(?:kvm|m2|m²|dkk|kr\.?|%)?\s*", value, re.I
    )
    if not match:
        return value
    return match.group(1).replace(".", "").replace(",", ".")


def _iso_date(value: str) -> str:
    """Reduce a date to just the day it names.

    The attest writes "01.03.2024" and the XML writes "2014-07-01+02:00" - a
    timestamp with an offset, whose time of day says when a record was touched
    rather than anything about the date itself.
    """
    value = value.strip()
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", value)
    if match:
        return f"{match[3]}-{match[2]}-{match[1]}"
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match[1] if match else value


def _pairs_from_document(html: str):
    """Read label/value pairs off the attest when it arrives rendered.

    Two shapes, because the attest uses both: table rows whose first cell is
    the label, and running lines where the label ends in a colon and the value
    is on the line below it.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0]:
            yield cells[0], cells[1]

    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        label, separator, value = line.partition(":")
        if separator:
            yield label, value.strip() or (
                lines[index + 1] if index + 1 < len(lines) else ""
            )


def _pairs_from_data(payload):
    """The same pairs, should the attest arrive as data instead.

    Walks rather than reaching for known paths, and yields a level's own values
    before descending, so a name and the identifying line beside it stay
    adjacent in the stream even when the object sits in a list of owners.
    """
    if isinstance(payload, list):
        for item in payload:
            yield from _pairs_from_data(item)
        return
    if not isinstance(payload, dict):
        return

    nested = []
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            nested.append(value)
        elif value is not None:
            yield key, value
    for value in nested:
        yield from _pairs_from_data(value)


def _label_key(label: str) -> str:
    """Normalise a label: "Cpr-nr.:", "Areal (m2)" and "areal" all settle down."""
    text = re.sub(r"\([^)]*\)", " ", str(label)).replace(":", " ")
    return " ".join(text.lower().split()).rstrip(".")


def attest_document(details: dict | None) -> tuple[str, str]:
    """The whole record as it arrived, and the extension to file it under.

    The columns take the fields worth sorting and filtering on; this keeps
    everything else - the creditors and debtors behind each mortgage with
    their own dates of birth, sub-pledges, endorsement dates, the free text on
    every easement - which is a great deal more than a row can hold.

    Kept verbatim rather than rendered down to its values: the names of the
    elements are half the meaning, and a record stripped of them is what a
    date of birth with nothing to attach it to looks like.
    """
    if not details:
        return "", ""
    if "_raw" not in details:
        return "json", json.dumps(details, ensure_ascii=False, indent=2)
    raw = details["_raw"]
    return ("xml" if raw.lstrip().startswith("<") else "txt"), raw


# Column names follow the labels the site's own table uses.
HISTORIK_FIELDS = [
    "adresse", "dato", "dokumenttype", "historiske_ejere", "ejendom_uuid",
]


def history_rows(history: dict | None, uuid: str, adresse: str) -> list[dict]:
    """Previous owners - one row per historical entry, newest first.

    Only reachable with a login: the public lookup shows the register as it
    stands today and says nothing about how it got there.
    """
    items = (history or {}).get("items") or []
    return [
        {
            "adresse": adresse,
            "dato": item.get("dato", ""),
            "dokumenttype": item.get("dokumenttype", ""),
            # The site renders this in a <pre>: it is a block of names, one per
            # line, not a single owner.
            "historiske_ejere": (item.get("tekst") or "").strip(),
            "ejendom_uuid": uuid,
        }
        for item in sorted(items, key=lambda item: item.get("dato", ""), reverse=True)
    ]


def property_row(record: dict, uuid: str, parcel: dict, attest: dict | None = None) -> dict:
    """One row per property, with joint owners widened into ejer_1/ejer_2 columns."""
    attest = attest or {}
    matrikler = record.get("matrikler") or []
    vurdering = record.get("vurdering") or {}
    row = {
        "adresse": record.get("adresse", ""),
        "lejlighed": _unit_label(record.get("adresse", "")),
        "ejendomstype": record.get("ejendomstype", ""),
        "landsejerlav": "; ".join(m.get("landsejerlavnavn", "") for m in matrikler),
        "matrikel": "; ".join(m.get("matrikelnummer", "") for m in matrikler),
        "grund_bfe": parcel.get("grund_bfe", ""),
        "grund_areal_m2": parcel.get("grund_areal_m2", ""),
        # The property's own BFE and area, as opposed to the parcel's above.
        # For a whole-building property the two agree; for a flat they do not,
        # and reading the plot's 800 m2 as the flat's would be badly wrong.
        "ejerlejlighedsnr": attest.get("ejerlejlighedsnr", ""),
        "bfe_nr": attest.get("bfe_nr", ""),
        "areal_m2": attest.get("areal_m2", ""),
        "opdelingsdato": attest.get("opdelingsdato", ""),
        "fordelingstal": attest.get("fordelingstal", ""),
        "adkomst_dokumenttype": attest.get("adkomst_dokumenttype", ""),
        "adkomst_dato_loebenummer": attest.get("adkomst_dato_loebenummer", ""),
        "koebesum_dkk": attest.get("koebesum_dkk", ""),
        "overtagelsesdato": attest.get("overtagelsesdato", ""),
        "kommune": vurdering.get("kommune", ""),
        "kommunalt_ejendomsnr": vurdering.get("ejendomsnummer", ""),
        "ejendomsvurdering_dkk": vurdering.get("ejendomsvaerdi", ""),
        "grundvaerdi_dkk": vurdering.get("grundvaerdi", ""),
        "vurderingsdato": vurdering.get("vurderingsdato", ""),
        "antal_haeftelser": len(record.get("haeftelser") or []),
        "antal_servitutter": len(record.get("servitutter") or []),
        "uuid": uuid,
    }
    for number, ejer in enumerate(record.get("ejere") or [], start=1):
        name = ejer.get("navn", "")
        row[f"ejer_{number}_navn"] = name
        row[f"ejer_{number}_andel"] = ejer.get("andel", "")
        identity = (attest.get("owners") or {}).get(_normalise(name), {})
        row[f"ejer_{number}_foedselsdato"] = identity.get("foedselsdato", "")
    return row


def property_fields(max_owners: int, *, with_attest: bool = False) -> list[str]:
    """Column order, widened to however many co-owners the run actually found.

    Leads with the columns the register's own attest leads with, and keeps the
    supporting detail after them. The attest-only columns appear on a logged-in
    run and not otherwise: a column that is structurally empty reads as missing
    data rather than as data we never had the right to see.
    """
    owners: list[str] = []
    for number in range(1, max(max_owners, 1) + 1):
        owners.append(f"ejer_{number}_navn")
        if with_attest:
            owners.append(f"ejer_{number}_foedselsdato")
        owners.append(f"ejer_{number}_andel")

    identity = [
        "ejerlejlighedsnr",
        "bfe_nr",
        "areal_m2",
        "opdelingsdato",
        "fordelingstal",
    ]
    adkomst = [
        "adkomst_dokumenttype",
        "adkomst_dato_loebenummer",
        "koebesum_dkk",
        "overtagelsesdato",
    ]
    return [
        "adresse",
        "lejlighed",
        *(identity if with_attest else []),
        *owners,
        *(adkomst if with_attest else []),
        "ejendomsvurdering_dkk",
        "grundvaerdi_dkk",
        "vurderingsdato",
        "kommunalt_ejendomsnr",
        "ejendomstype",
        "landsejerlav",
        "matrikel",
        "grund_bfe",
        "grund_areal_m2",
        "kommune",
        "antal_haeftelser",
        "antal_servitutter",
        "uuid",
    ]


# Column names follow the labels the website shows, not the API's internal
# ones: the API calls a document type "haeftelsestype" and a date/serial
# "alias", which makes the data unrecognisable to anyone comparing against
# the site.
HAEFTELSE_FIELDS = [
    "adresse", "dato_loebenummer", "prioritet", "dokumenttype", "hovedstol",
    "rentesats_pct", "rentetype", "kreditorer", "dokument_version",
    "dokument_uuid", "ejendom_uuid",
]
SERVITUT_FIELDS = [
    "adresse", "dato_loebenummer", "prioritet", "dokumenttype", "tekst",
    "dokument_version", "dokument_uuid", "ejendom_uuid",
]


def haeftelse_rows(record: dict, uuid: str) -> list[dict]:
    """Mortgages and charges - one row each, linked back by ejendom_uuid."""
    return [
        {
            "adresse": record.get("adresse", ""),
            "dato_loebenummer": h.get("alias", ""),
            "prioritet": h.get("prioritet", ""),
            "dokumenttype": h.get("haeftelsestype", ""),
            "hovedstol": h.get("hovedstol", ""),
            "rentesats_pct": h.get("rente", ""),
            "rentetype": h.get("fastvariabel", ""),
            "kreditorer": "; ".join(h.get("kreditorer") or []),
            "dokument_version": h.get("version", ""),
            "dokument_uuid": h.get("uuid", ""),
            "ejendom_uuid": uuid,
        }
        for h in record.get("haeftelser") or []
    ]


def servitut_rows(record: dict, uuid: str) -> list[dict]:
    """Easements - one row each. A flat can carry a dozen, so they get their
    own file rather than being crushed into a cell of the main CSV."""
    return [
        {
            "adresse": record.get("adresse", ""),
            "dato_loebenummer": s.get("alias", ""),
            "prioritet": s.get("prioritet", ""),
            "dokumenttype": s.get("servituttype", ""),
            "tekst": s.get("tekst", ""),
            "dokument_version": s.get("version", ""),
            "dokument_uuid": s.get("uuid", ""),
            "ejendom_uuid": uuid,
        }
        for s in record.get("servitutter") or []
    ]


def slugify(text: str) -> str:
    """Turn a resolved address into a tidy filename stem."""
    text = text.lower()
    for danish, latin in (("æ", "ae"), ("ø", "oe"), ("å", "aa")):
        text = text.replace(danish, latin)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def start_login(user_id: str | None, method: str, password: str | None):
    """Run the MitID login on the terminal and return the live session."""
    screen = console.LoginConsole()
    if not user_id:
        # The user ID is the only thing we cannot guess, and typing it once per
        # machine is enough - the session file remembers it afterwards.
        remembered = tinglysning_auth.restore_session()
        user_id = (remembered[1].get("user_id") if remembered else "") or screen.ask(
            "MitID user ID:"
        )

    session, who = tinglysning_auth.log_in(
        user_id,
        method=method,
        password=password,
        on_status=screen.status,
        on_qr=screen.qr,
        on_otp=screen.otp,
        ask_token_code=screen.ask,
        choose_identity=screen.choose,
    )
    print(f"\n  logged in as {who}", file=sys.stderr)
    print(f"  session cached in {tinglysning_auth.session_path()}\n", file=sys.stderr)
    return session


def resume_login():
    """Pick a cached session back up, or return None if there is none to use."""
    remembered = tinglysning_auth.restore_session()
    if remembered is None:
        return None

    session, saved = remembered
    who = tinglysning_auth.who_is_logged_in(session)
    if who is None:
        print(
            "cached session has expired - run --login for the extra columns",
            file=sys.stderr,
        )
        return None

    idle = tinglysning_auth.idle_for(saved.get("saved_at"))
    since = f", idle {int(idle.total_seconds() // 60)} min" if idle else ""
    print(f"authenticated as {who}{since}", file=sys.stderr)
    # Rewrite the file so its timestamp tracks last use, not the login.
    tinglysning_auth.save_session(session, saved.get("user_id", ""))
    return session


def show_status() -> None:
    """Say whether there is a usable session, and how much of it is left."""
    remembered = tinglysning_auth.restore_session()
    if remembered is None:
        print("not logged in - run --login to start a session", file=sys.stderr)
        return

    session, saved = remembered
    who = tinglysning_auth.who_is_logged_in(session)
    idle = tinglysning_auth.idle_for(saved.get("saved_at"))
    if who is None:
        print(f"session for {saved.get('user_id') or 'unknown user'} has expired",
              file=sys.stderr)
        return

    print(f"logged in as {who} ({saved.get('user_id', '')})", file=sys.stderr)
    if idle:
        left = tinglysning_auth.IDLE_LIMIT - idle
        minutes = int(left.total_seconds() // 60)
        print(
            f"  last used {int(idle.total_seconds() // 60)} min ago"
            f" - about {max(minutes, 0)} min before it lapses",
            file=sys.stderr,
        )
    print(f"  cookies in {tinglysning_auth.session_path()}", file=sys.stderr)


def hold_session(session, minutes: int, user_id: str = "") -> None:
    """Keep a session alive without logging in again.

    The register ends a session that has gone quiet, so this does what the
    site's own page does while someone has it open: says "still here", now and
    then, for as long as asked. Cheaper than another trip to the phone.
    """
    ping_every = 10 * 60  # comfortably inside the register's idle limit
    deadline = time.monotonic() + minutes * 60
    print(
        f"holding the session open for {minutes} min - Ctrl-C to stop",
        file=sys.stderr,
    )
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(ping_every, remaining))
            if not tinglysning_auth.keep_alive(session):
                print("  the register ended the session anyway", file=sys.stderr)
                return
            tinglysning_auth.save_session(session, user_id)
            left = int((deadline - time.monotonic()) / 60)
            print(f"  still logged in, {max(left, 0)} min to go", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n  stopped holding; the session stays valid for a while yet",
              file=sys.stderr)
        return
    print("  done holding - the session lapses shortly unless used", file=sys.stderr)


def handle_expired_session(index: int, total: int) -> bool:
    """Decide what happens when the login lapses partway through a run.

    Tinglysning ends a session that has sat idle, and a long run of properties
    can outlast one. By the time this is called, `index` of `total` properties
    already have their extra columns and the rest would not.

    Returning True carries on without the logged-in extras, so the run still
    produces the public data for every property, with the enriched columns
    filled in for the first few and blank after that. Returning False stops the
    run instead, so nothing half-enriched ever reaches a CSV.
    """
    # TODO(you): the trade-off is whether a partly-enriched CSV is useful or
    # misleading. Carrying on keeps the run's work; stopping keeps the output
    # honest about being one thing throughout.
    print(
        f"  session expired after {index} of {total} - "
        "continuing without the logged-in columns",
        file=sys.stderr,
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "address", nargs="?", help='e.g. "Prøvegade 1, 9999 Prøveby"'
    )
    parser.add_argument("--out", help="explicit CSV path (default: named after the address)")
    parser.add_argument("--outdir", default=".", help="directory for the generated CSVs")
    parser.add_argument(
        "--limit", type=int, default=25, help="max properties to fetch (0 = no limit)"
    )
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between fetches")
    parser.add_argument("--dump", metavar="PATH", help="write the first raw record as JSON")
    parser.add_argument(
        "--no-dawa",
        action="store_true",
        help="skip DAWA address cleaning and use tinglysning's own autocomplete",
    )

    login = parser.add_argument_group(
        "MitID login",
        "Logging in adds owners' dates of birth and previous owners. The "
        "session is remembered, so this is a once-in-a-while thing.",
    )
    login.add_argument("--login", action="store_true", help="log in with MitID now")
    login.add_argument(
        "--logout", action="store_true", help="end the session and forget the cookies"
    )
    login.add_argument("--user", help="your MitID user ID (not your CPR number)")
    login.add_argument(
        "--method",
        choices=[mitid.APP, mitid.TOKEN],
        default=mitid.APP,
        help="approve in the MitID app (default) or with a code token",
    )
    login.add_argument("--password", help="MitID password, needed only with --method TOKEN")
    login.add_argument(
        "--status",
        action="store_true",
        help="say whether a session is still good, and for how much longer",
    )
    login.add_argument(
        "--keepalive",
        nargs="?",
        type=int,
        const=60,
        metavar="MINUTES",
        help="hold the session open (default 60 minutes) instead of exiting",
    )
    login.add_argument(
        "--anonymous",
        action="store_true",
        help="ignore any cached session and use only the public lookup",
    )
    login.add_argument(
        "--debug",
        action="store_true",
        help="show the protocol chatter, for when a login stops working",
    )
    args = parser.parse_args()

    # The vendored MitID client narrates itself through the logging module and
    # says the same things the console already shows, so it stays quiet unless
    # something has gone wrong enough to want the detail.
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.CRITICAL,
        format="%(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.status:
        show_status()
        if not args.address:
            return

    if args.logout:
        remembered = tinglysning_auth.restore_session()
        if remembered:
            tinglysning_auth.log_out(remembered[0])
        print(
            "logged out" if remembered else "no session to log out of", file=sys.stderr
        )
        if not args.address:
            return

    session = None
    try:
        if args.login:
            session = start_login(args.user, args.method, args.password)
        elif not args.anonymous:
            session = resume_login()
    except tinglysning_auth.AuthError as error:
        raise SystemExit(f"login failed: {error}")
    except KeyboardInterrupt:
        raise SystemExit("\nlogin cancelled")

    if args.keepalive and session is None:
        raise SystemExit("nothing to hold open - log in first")

    if not args.address:
        if args.keepalive:
            hold_session(session, args.keepalive, args.user or "")
        if args.login or args.keepalive or args.status:
            return
        parser.error("an address is required (or use --login on its own)")

    # ISO 8601 basic, local time. Stamped once when the run starts, so all
    # files from one request share it and sort together chronologically.
    stamp = time.strftime("%Y%m%dT%H%M%S")

    api = Tinglysning(session)
    address = None
    if not args.no_dawa:
        try:
            address = resolve_address(args.address)
        except requests.RequestException as error:
            print(f"DAWA unreachable ({error}); falling back", file=sys.stderr)
    if address is None:
        address = api.lookup_address(args.address)
    print(f"resolved: {address['tekst']}", file=sys.stderr)

    units = api.find_units(address)
    units, warning = select_units(units, address["etage"], address["doer"])
    if warning:
        print(f"note: {warning}", file=sys.stderr)
    print(f"found {len(units)} propert{'y' if len(units) == 1 else 'ies'}", file=sys.stderr)
    if args.limit and len(units) > args.limit:
        print(f"fetching the first {args.limit} - raise --limit for more", file=sys.stderr)
        units = units[: args.limit]

    # Whether the run counts as logged in is decided here, once, so a session
    # that dies halfway cannot change the shape of the CSV mid-write.
    enriched = api.authenticated
    properties, haeftelser, servitutter, historik, attester = [], [], [], [], []
    parcels: dict = {}
    for index, unit in enumerate(units, start=1):
        if index > 1:
            time.sleep(args.delay)
        record = api.fetch_record(unit["uuid"])

        details = history = None
        if api.authenticated:
            try:
                details = api.fetch_details(unit["uuid"])
                history = api.fetch_history(unit["uuid"])
            except SessionExpired:
                if not handle_expired_session(index - 1, len(units)):
                    break
        if args.dump and index == 1:
            _dump(args.dump, record, details, history)

        matrikel = (record.get("matrikler") or [{}])[0]
        parcel = fetch_parcel(
            matrikel.get("landsejerlavkode", ""), matrikel.get("matrikelnummer", ""), parcels
        )
        properties.append(
            property_row(record, unit["uuid"], parcel, attest_details(details))
        )
        haeftelser.extend(haeftelse_rows(record, unit["uuid"]))
        servitutter.extend(servitut_rows(record, unit["uuid"]))
        historik.extend(history_rows(history, unit["uuid"], record.get("adresse", "")))
        suffix, document = attest_document(details)
        if document:
            attester.append((record.get("adresse", ""), suffix, document))
        print(f"  [{index}/{len(units)}] {unit.get('adresse', '')}", file=sys.stderr)

    # Name the files after what was actually fetched. If the requested flat had
    # no separate entry we fell back to the whole building, so the flat must not
    # appear in the filename.
    label = _drop_unit(address["tekst"]) if warning else address["tekst"]
    # An explicit --out is taken literally - the caller named the file, so
    # appending a stamp would hand them back a path they did not ask for.
    stem = Path(args.out).with_suffix("") if args.out else Path(args.outdir) / slugify(label)
    stamped = "" if args.out else f"-{stamp}"
    stem.parent.mkdir(parents=True, exist_ok=True)

    max_owners = max(
        (sum(1 for key in row if key.endswith("_navn")) for row in properties), default=1
    )
    outputs = [
        ("", property_fields(max_owners, with_attest=enriched), properties),
        ("-haeftelser", HAEFTELSE_FIELDS, haeftelser),
        ("-servitutter", SERVITUT_FIELDS, servitutter),
        ("-adkomsthistorik", HISTORIK_FIELDS, historik),
    ]
    for suffix, fields, rows in outputs:
        path, count = _write_csv(f"{stem}{suffix}{stamped}.csv", fields, rows)
        if count:
            print(f"wrote {count:>4} row(s) to {path}", file=sys.stderr)

    if attester:
        # One file each: these are whole documents, and concatenating XML
        # produces something no parser will read back.
        folder = Path(f"{stem}-attester{stamped}")
        folder.mkdir(parents=True, exist_ok=True)
        for adresse, suffix, document in attester:
            (folder / f"{slugify(adresse)}.{suffix}").write_text(
                document, encoding="utf-8"
            )
        print(f"wrote {len(attester):>4} attest(er) to {folder}/", file=sys.stderr)

    # Last of all, so everything is safely written before we sit and wait.
    if args.keepalive and session is not None:
        hold_session(session, args.keepalive, args.user or "")


def _dump(path: str, record: dict, details, history) -> None:
    """Write the raw payloads for one property, side by side.

    The public record's shape is known; the two logged-in ones are not, so this
    is how their fields get named properly rather than guessed at.
    """
    payload = {"public": record}
    if details is not None:
        payload["ejdsummarisk"] = details
    if history is not None:
        payload["ejdhistoriskadkomst"] = history
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _write_csv(path, fields: list[str], rows: list[dict]):
    """Write rows, leaving absent columns blank rather than raising."""
    if not rows:
        return path, 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path, len(rows)


if __name__ == "__main__":
    main()
