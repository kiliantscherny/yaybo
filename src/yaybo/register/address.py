"""Turn what a person types into an address the register can be asked about.

Everything here talks to DAWA (api.dataforsyningen.dk), Denmark's official
address register, which is far more forgiving than tinglysning's own
autocomplete: it copes with th/tv doors, a mis-spaced house number ("30 B") and
a missing diacritic ("Frederiksberg Alle"), none of which tinglysning will
parse. DAWA's address UUID is also what Boligsiden answers to, so resolving an
address here is what makes that join possible later.

The register does not index postal addresses, though - it indexes legally
registered properties. A block of owner-occupied flats has one property per
flat, but a rented or co-op building is a single property no matter how many
doors it has, so narrowing a search by floor happens in `select_units`, against
what the register actually holds, rather than in the query.
"""

from __future__ import annotations

import re
import unicodedata

import requests

from yaybo.register.fields import normalise

DAWA = "https://api.dataforsyningen.dk"


class AddressError(Exception):
    """Raised when an address cannot be resolved to one place."""


def autocomplete(query: str, limit: int = 12) -> list[dict]:
    """Addresses matching a partial query, for something to pick from.

    Deliberately raw: every match DAWA returns, in its order, with the string
    it renders itself as. Deciding which of them the user meant is
    `resolve_address`'s job, and only worth doing once they have chosen.
    """
    if not query.strip():
        return []
    try:
        found = requests.get(
            f"{DAWA}/adresser/autocomplete",
            params={"q": query, "per_side": limit},
            timeout=15,
        ).json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(found, list):
        return []
    return [
        # DAWA's own uuid comes along because it is what Boligsiden answers to,
        # and having it here saves looking the same address up twice.
        {**_from_dawa(match["adresse"]), "adresse_uuid": match["adresse"].get("id", "")}
        for match in found
        if match.get("adresse")
    ]


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
        raise AddressError(f"no address matched {query!r}")
    if len(buildings) > 1:
        listing = "\n  ".join(f"{v} {h}, {p}" for v, h, p in list(buildings)[:5])
        raise AddressError(f"{query!r} is ambiguous - try one of:\n  {listing}")

    # Autocomplete answers a flat query with its siblings too ("13. 3" also
    # returns 13. 1, 13. 2), so a result count says nothing about intent. Keep
    # the floor and door only when a result renders the query exactly;
    # otherwise the query named a building and the unit would be a guess.
    wanted = normalise(split_postcode(query)[0])
    exact = next(
        (m for m in matches if normalise(split_postcode(m["tekst"])[0]) == wanted),
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

def dawa_addresses(address: dict) -> dict[tuple[str, str], str]:
    """Every address at this house number, as (etage, doer) -> DAWA uuid.

    One request for the whole building rather than one per flat: the register
    is searched at building level too, so both sides of the join are gathered
    the same way and a block of sixty flats costs a single call.

    The UUID is what Boligsiden keys on, which is the only reason it is wanted.
    """
    try:
        found = requests.get(
            f"{DAWA}/adresser",
            params={
                "vejnavn": address["vejnavn"],
                "husnr": address.get("husnummer", ""),
                "postnr": address["postnummer"],
            },
            timeout=30,
        ).json()
    except (requests.RequestException, ValueError):
        return {}
    if not isinstance(found, list):
        return {}
    return {
        ((entry.get("etage") or "").lower(), (entry.get("dør") or "").lower()): entry[
            "id"
        ]
        for entry in found
        if entry.get("id")
    }


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


def street_buildings(vejnavn: str, postnummer: str) -> list[dict]:
    """Every house number on one street in one postcode, as addresses to fetch.

    Access addresses rather than unit addresses: the register searches at
    building level anyway, and one query per building is the difference between
    forty requests for a street and four hundred.
    """
    try:
        found = requests.get(
            f"{DAWA}/adgangsadresser",
            params={"vejnavn": vejnavn, "postnr": postnummer, "struktur": "mini"},
            timeout=30,
        ).json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(found, list):
        return []

    buildings: dict[tuple, dict] = {}
    for entry in found:
        key = (entry.get("vejnavn"), entry.get("husnr"), entry.get("postnr"))
        if not all(key):
            continue
        buildings.setdefault(
            key,
            {
                "tekst": f"{entry['vejnavn']} {entry['husnr']}, "
                f"{entry['postnr']} {entry.get('postnrnavn', '')}".strip(),
                "vejnavn": entry["vejnavn"],
                "husnummer": entry["husnr"],
                "postnummer": str(entry["postnr"]),
                "etage": "",
                "doer": "",
            },
        )
    return sorted(buildings.values(), key=lambda a: _house_order(a["husnummer"]))


def _house_order(husnr: str) -> tuple[int, str]:
    """Sort 2, 4, 4A, 4B, 10 the way a person walking down the street would."""
    digits = "".join(c for c in husnr if c.isdigit())
    return (int(digits) if digits else 0, husnr)
