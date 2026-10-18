"""Turn what a person types into an address the register can be asked about.

Everything here talks to Adressevælger and Adressevask (adressevaelger.dk),
Klimadatastyrelsen's front door to DAR, Denmark's official address register.
They replaced DAWA, which closed for good on 1 October 2026 and now answers
every request with 410 Gone. DAR's address UUIDs are the ones DAWA handed out,
and they are what a BBR lookup keys on, so resolving an address here is what
makes that join possible later.

Both services want a `token` on every request but do not yet check who is
asking: the published demo token is accepted, and `ADRESSEVAELGER_TOKEN`
replaces it for whoever has been issued their own, which matters the day the
service starts telling tokens apart.

They are less forgiving than DAWA was. A search answers with a display string
rather than fields, so the fields are read back out of it; Adressevask corrects
nothing and simply refuses what it cannot match exactly. Coordinates arrive in
UTM zone 32 (EPSG:25832) and are turned into degrees here.

The register does not index postal addresses, though - it indexes legally
registered properties. A block of owner-occupied flats has one property per
flat, but a rented or co-op building is a single property no matter how many
doors it has, so narrowing a search by floor happens in `select_units`, against
what the register actually holds, rather than in the query.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata

import requests

from yaybo.register.fields import normalise

ADRESSEVAELGER = "https://adressevaelger.dk"
TOKEN_ENV = "ADRESSEVAELGER_TOKEN"
# Klimadatastyrelsen's own examples use this one, and it is accepted until the
# service starts issuing tokens per user.
PUBLIC_TOKEN = "adressevaelger123"
# The most a search will return; it refuses a request for more.
MAKSIMUM = 200


class AddressError(Exception):
    """Raised when an address cannot be resolved to one place."""


def autocomplete(query: str, limit: int = 12) -> list[dict]:
    """Addresses matching a partial query, for something to pick from.

    Deliberately raw: every match the search returns, in its order, with the
    string it renders itself as. Deciding which of them the user meant is
    `resolve_address`'s job, and only worth doing once they have chosen.

    A house number and a unit address both come back; a street on its own does
    not, because there is nothing at a street for the register to be asked
    about. Only a unit carries an address UUID - a bare house number has none,
    and BBR will be asked about it by its building instead.
    """
    if not query.strip():
        return []
    try:
        found = _search("adresser", query, min(limit, MAKSIMUM))
    except (requests.RequestException, ValueError):
        return []
    return [
        {
            **_from_titel(match["titel"]),
            "adresse_uuid": match["id"] if match.get("type") == "adresse" else "",
        }
        for match in found
        if match.get("type") in ("adresse", "husnummer") and match.get("titel")
    ]


def resolve_address(query: str) -> dict:
    """Clean free-text Danish address input, via the official address register.

    Two services, because they fail in opposite directions. Adressevask is
    built for complete addresses and is strict about them: it either matches
    street, number and postcode exactly or says why it could not, and never
    guesses - asked for the non-existent "Frederiksberg Alle 42" it answers
    that there is no such number rather than offering 12, which is what DAWA's
    wash used to do. Anything it will not vouch for falls through to the
    search, which copes with partial input but wants roughly correct spelling.
    """
    washed = _get("vask/", adresse=query)
    status = (washed.get("vaskestatus") or {}).get("kode")
    result = washed.get("vaskeresultat") or {}
    # A positive code is a match and a negative one is the reason there is
    # none; only the first carries an address worth trusting.
    if isinstance(status, int) and status > 0 and result.get("adressebetegnelse"):
        return _from_titel(result["adressebetegnelse"])

    matches = [
        _from_titel(match["titel"])
        for match in _search("adresser", query, 10)
        if match.get("type") in ("adresse", "husnummer") and match.get("titel")
    ]
    buildings: dict[tuple, dict] = {}
    for found in matches:
        buildings.setdefault(
            (found["vejnavn"], found["husnummer"], found["postnummer"]), found
        )

    if not buildings:
        raise AddressError(f"no address matched {query!r}")
    if len(buildings) > 1:
        listing = "\n  ".join(f"{v} {h}, {p}" for v, h, p in list(buildings)[:5])
        raise AddressError(f"{query!r} is ambiguous - try one of:\n  {listing}")

    # The search answers a flat query with its siblings too ("13. 3" also
    # returns 13. 1, 13. 2), so a result count says nothing about intent. Keep
    # the floor and door only when a result renders the query exactly;
    # otherwise the query named a building and the unit would be a guess.
    wanted = normalise(split_postcode(query)[0])
    exact = next(
        (m for m in matches if normalise(split_postcode(m["tekst"])[0]) == wanted),
        None,
    )
    if exact:
        return exact
    return _from_titel(drop_unit(next(iter(buildings.values()))["tekst"]))


def _get(path: str, **params) -> dict:
    """One request to Adressevælger, with the token it insists on."""
    token = os.environ.get(TOKEN_ENV) or PUBLIC_TOKEN
    found = requests.get(
        f"{ADRESSEVAELGER}/{path}", params={**params, "token": token}, timeout=30
    ).json()
    if not isinstance(found, dict):
        raise ValueError(f"unexpected answer from {path}")
    return found


def _search(kind: str, tekst: str, maksimum: int) -> list[dict]:
    """The search behind every autocomplete: `adresser` or `husnumre`.

    It turns away anything longer than 73 characters, which nobody types as
    an address, so a longer query is cut rather than refused.
    """
    found = _get(f"{kind}/soeg", tekst=tekst[:73], maksimum=maksimum)
    if found.get("status") != "ok":
        raise ValueError(found.get("beskrivelse") or f"{kind} search failed")
    return [match for match in found.get("fund") or [] if isinstance(match, dict)]


def _without_town(titel: str) -> str:
    """Drop the supplerende bynavn from a rendered address, if it has one.

    A village inside a larger postal district is named between the unit and
    the postcode - "Vesterbrogade 1, Humlum, 7600 Struer" - in exactly the slot
    a floor and door take, so read naively it is a flat on the Humlum floor.
    DAWA kept the two apart in separate fields; the search does not. A unit
    always has a digit, a full stop or is a short door like "tv", and a town
    name has none of those, which is what tells them apart. DAWA left the town
    out of the address it rendered, so it is dropped rather than kept.
    """
    segments = titel.split(", ")
    if len(segments) < 3:
        return titel
    town = segments[-2]
    if any(c.isdigit() for c in town) or "." in town or len(town) <= 2:
        return titel
    return ", ".join(segments[:-2] + segments[-1:])


def _from_titel(titel: str) -> dict:
    """Read the fields tinglysning's search expects out of a rendered address.

    The search answers with "Prøvegade 1, 2. tv, 9999 Prøveby" and an id, not
    with the parts, and fetching each hit's record to get them would cost a
    request per row of an autocomplete list. The string is DAR's own rendering,
    so reading it back is safe in a way reading what a person typed is not.
    """
    titel = _without_town(titel)
    parts = address_parts(titel)
    etage, doer = floor_and_door(titel)
    _, postal = titel.rsplit(", ", 1) if ", " in titel else ("", "")
    unit = unit_label(titel)
    return {
        "tekst": ", ".join(
            filter(None, [f"{parts['vejnavn']} {parts['husnummer']}", unit, postal])
        ),
        "vejnavn": parts["vejnavn"],
        "husnummer": parts["husnummer"],
        "postnummer": parts["postnummer"],
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

    wanted = normalise(f"{etage}. {doer}" if doer else etage)
    picked = [u for u in units if normalise(unit_label(u.get("adresse", ""))) == wanted]
    if picked:
        return picked, ""

    unit = f"{etage}. {doer}".strip()
    plural = "property" if len(units) == 1 else "properties"
    return units, (
        f"no separately registered unit for '{unit}' - this building is "
        f"registered as {len(units)} {plural}; showing all"
    )


def unit_label(adresse: str) -> str:
    """Pull "13. 3" out of "Prøvegade 1, 13. 3, 9999 Prøveby"."""
    head, _ = split_postcode(adresse)
    return ", ".join(part.strip() for part in head.split(",")[1:])


def drop_unit(adresse: str) -> str:
    """Strip the flat from an address, leaving the building it belongs to."""
    head, _ = split_postcode(adresse)
    postal = adresse[len(head):].lstrip(", ")
    street = head.split(",")[0].strip()
    return f"{street}, {postal}" if postal else street


def split_postcode(text: str) -> tuple[str, str]:
    """Split "Prøvegade 1, 9999 Prøveby" into the part before the postcode
    and the postcode itself. Returns ("...", "") if there is none."""
    match = re.search(r",?\s*(\d{4})\s+[^,\d]+$", text)
    if not match:
        return text.strip(), ""
    return text[: match.start()].strip().rstrip(","), match.group(1)


def fetch_parcel(ejerlavkode: str, matrikelnr: str, cache: dict) -> dict:
    """The land parcel's BFE number and registered area - for now, nothing.

    Careful what this was: for a whole-building property it is the property's
    own BFE, but for a flat in a subdivided block it is the BFE of the *parcel
    underneath*, and the area is the whole plot. The columns are named grund_*
    so the two never get confused.

    DAWA's jordstykker answered this without a key and went with the rest of
    DAWA. Its successor is Matriklen on Datafordeleren, which needs the same
    optional key BBR does, so until that is wired in the grund_* columns stay
    empty rather than asking a service that only answers 410.
    """
    return cache.setdefault((ejerlavkode, matrikelnr), {})


def dawa_addresses(address: dict) -> dict[tuple[str, str], dict]:
    """Every address at this house number, as (etage, doer) -> what DAR knows.

    The name is older than the source: DAWA answered this until it closed, and
    DAR's UUIDs are the ones DAWA handed out, so nothing stored has to change.

    Two requests for the whole building rather than one per flat: the register
    is searched at building level too, so both sides of the join are gathered
    the same way and a block of sixty flats costs the same as a house. The
    search lists the units and their house number; the house number's own
    record says where it is, which no unit has a place of its own for.

    Two things come back per address. The UUID is what BBR will key on. The
    coordinates are the address register's adgangspunkt - the official answer
    to where the place is - and are the only thing placing a property or a
    share on a map.
    """
    building = f"{address['vejnavn']} {address.get('husnummer', '')}".strip()
    try:
        found = _search("adresser", f"{building}, {address['postnummer']}", MAKSIMUM)
    except (requests.RequestException, ValueError):
        return {}

    units: dict[tuple[str, str], dict] = {}
    for match in found:
        titel = _without_town(match.get("titel") or "")
        if match.get("type") != "adresse" or not match.get("id"):
            continue
        parts = address_parts(titel)
        # The search is fuzzy, so "Prøvegade 1" also finds 10 and 1A, and
        # only an exact house number in the right postcode is this building.
        if (
            normalise(f"{parts['vejnavn']} {parts['husnummer']}") != normalise(building)
            or parts["postnummer"] != str(address["postnummer"])
        ):
            continue
        units[floor_and_door(titel)] = {
            "uuid": match["id"],
            "husnummer": match.get("husnummerId", ""),
        }

    places: dict[str, dict] = {}
    for unit in units.values():
        husnummer = unit["husnummer"]
        if husnummer and husnummer not in places:
            places[husnummer] = _access(husnummer)
        unit.update(places.get(husnummer, {}))
    return units


def _access(husnummer: str) -> dict:
    """Where a house number is, in degrees.

    The husnummer is DAR's adgangsadresse, and its uuid is BBR's `husnummer`,
    which is what a building is looked up by. The address uuid beside it is
    what a *flat* is looked up by, so both are wanted and they are not the same
    thing - this is the half that places them.
    """
    try:
        found = _get(f"husnumre/{husnummer}")
    except (requests.RequestException, ValueError):
        return {}
    point = ((found.get("husnummer") or {}).get("adgangspunkt") or {}).get(
        "koordinater"
    ) or {}
    if point.get("x") is None or point.get("y") is None:
        return {}
    laengdegrad, breddegrad = utm32_to_degrees(point["x"], point["y"])
    return {"laengdegrad": laengdegrad, "breddegrad": breddegrad}


def utm32_to_degrees(easting: float, northing: float) -> tuple[float, float]:
    """ETRS89 / UTM zone 32N metres to (longitude, latitude) in degrees.

    DAWA handed out degrees; DAR hands out the metres Danish mapping works in.
    The stored columns stay in degrees, so older rows and new ones agree. This
    is the standard inverse transverse Mercator series on GRS80, written out
    rather than pulling in pyproj for one projection. It is good to well under a
    metre across Denmark, and ETRS89 and WGS84 differ by less than that, which
    is all the precision a pin on a map needs.
    """
    a, f = 6378137.0, 1 / 298.257222101
    k0, false_easting, central = 0.9996, 500000.0, math.radians(9)
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x, m = easting - false_easting, northing / k0

    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )

    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = a / math.sqrt(1 - e2 * sin1**2)
    r1 = a * (1 - e2) / (1 - e2 * sin1**2) ** 1.5
    t1, c1 = tan1**2, ep2 * cos1**2
    d = x / (n1 * k0)

    lat = phi1 - (n1 * tan1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = central + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / cos1
    return math.degrees(lon), math.degrees(lat)


def address_parts(adresse: str) -> dict:
    """Pull vejnavn, husnummer and postnummer back out of a formatted address.

    The reverse of how the address was assembled. Needed when the only record
    of a property is the string already stored against it, which is the case
    when enriching a database rather than fetching one.
    """
    head, postnr = split_postcode(adresse)
    street = head.split(",")[0].strip()
    vejnavn, _, husnr = street.rpartition(" ")
    return {"vejnavn": vejnavn or street, "husnummer": husnr if vejnavn else "",
            "postnummer": postnr}


def floor_and_door(adresse: str) -> tuple[str, str]:
    """Split "Prøvegade 1, 4. 413, 9999 By" down to ("4", "413")."""
    label = unit_label(adresse)
    if not label:
        return "", ""
    etage, _, doer = label.partition(".")
    return etage.strip().lower(), doer.strip().lower()


def slugify(text: str) -> str:
    """Turn a resolved address into a tidy filename stem."""
    text = text.lower()
    for danish, latin in (("æ", "ae"), ("ø", "oe"), ("å", "aa")):
        text = text.replace(danish, latin)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def street_buildings(vejnavn: str, postnummer: str = "") -> list[dict]:
    """Every house number on one street in one postcode, as addresses to fetch.

    House numbers rather than unit addresses: the register searches at
    building level anyway, and one query per building is the difference between
    forty requests for a street and four hundred.

    Without a postcode, a street name found in one town comes back as its
    house numbers, but one found in several comes back as the streets instead -
    there are four Matthæusgades and the user has not said which. Four are not
    listed by asking once per town, which for a Vestergade would be a request
    for every town in the country: the answer is empty, and the caller falls
    back to an ordinary search.
    """
    try:
        found = _search("husnumre", f"{vejnavn} {postnummer}".strip(), MAKSIMUM)
        streets = {
            str(match.get("postnr"))
            for match in found
            if match.get("type") == "navngivenvejpostnummer"
            and normalise(match.get("vejnavn") or "") == normalise(vejnavn)
        }
        if not postnummer and len(streets) > 1:
            return []
        if not postnummer and streets:
            postnummer = streets.pop()
            found = _search("husnumre", f"{vejnavn} {postnummer}", MAKSIMUM)
    except (requests.RequestException, ValueError):
        return []

    buildings: dict[tuple, dict] = {}
    for match in found:
        if match.get("type") != "husnummer" or not match.get("titel"):
            continue
        building = _from_titel(match["titel"])
        if normalise(building["vejnavn"]) != normalise(vejnavn) or (
            postnummer and building["postnummer"] != str(postnummer)
        ):
            continue
        buildings.setdefault(
            (building["vejnavn"], building["husnummer"], building["postnummer"]),
            building,
        )
    return sorted(buildings.values(), key=lambda a: _house_order(a["husnummer"]))


def _house_order(husnr: str) -> tuple[int, str]:
    """Sort 2, 4, 4A, 4B, 10 the way a person walking down the street would."""
    digits = "".join(c for c in husnr if c.isdigit())
    return (int(digits) if digits else 0, husnr)
