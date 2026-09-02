"""What Boligsiden knows about an address, which is what BBR knows plus prices.

The land register says who owns a property and what is charged against it. It
says almost nothing about the thing itself - no year of construction, no number
of rooms, no heating - and its idea of a price is whatever the deed recorded.
Boligsiden's public address endpoint fills both gaps from one request:

    GET https://api.boligsiden.dk/addresses/{dawa adresse-uuid}

    registrations[]   every recorded sale: date, amount, area, price per m2,
                      and whether it was an ordinary sale, a family transfer
                      or a forced auction
    buildings[]       the BBR record: year built and renovated, rooms, floors,
                      areas, wall and roof material, heating, kitchen and bath
    latestValuation   Boligsiden's own figure
    livingArea        the flat's living area, which is not the same measure as
                      the register's "tinglyste areal"
    coordinates       where it is
    isOnMarket, slug  whether it is for sale now, and where to look

No key and no proof-of-work. The address UUID is DAWA's, which is also the one
tinglysning's addresses resolve to, so the join costs nothing.

Sale registrations are append-only public records, so a few minutes of
staleness is fine and the cache spares the API a request per re-run.
"""

from __future__ import annotations

import requests

ADDRESS_URL = "https://api.boligsiden.dk/addresses/{uuid}"
LISTING_URL = "https://www.boligsiden.dk/adresse/{slug}"

# What Boligsiden calls each kind of transfer, in the register's own words.
HANDELSTYPER = {
    "normal": "Almindeligt salg",
    "family": "Familiehandel",
    "auction": "Tvangsauktion",
    "other": "Andet",
}

# BBR's field names, in the order a person would ask about them.
BYGNING_FELTER = {
    "bygning_nr": "buildingNumber",
    "bygningstype": "buildingName",
    "opfoerelsesaar": "yearBuilt",
    "ombygningsaar": "yearRenovated",
    "etager": "numberOfFloors",
    "vaerelser": "numberOfRooms",
    "badevaerelser": "numberOfBathrooms",
    "toiletter": "numberOfToilets",
    "boligareal_m2": "housingArea",
    "kaelderareal_m2": "basementArea",
    "erhvervsareal_m2": "businessArea",
    "andet_areal_m2": "otherArea",
    "samlet_areal_m2": "totalArea",
    "ydervaeg": "externalWallMaterial",
    "tagdaekning": "roofingMaterial",
    "varmeinstallation": "heatingInstallation",
    "supplerende_varme": "supplementaryHeating",
    "koekken": "kitchenCondition",
    "badeforhold": "bathroomCondition",
    "toiletforhold": "toiletCondition",
}


def fetch(adresse_uuid: str, session: requests.Session | None = None) -> dict:
    """Everything Boligsiden holds for one address, or {} when it holds none.

    A 404 is an ordinary answer here - plenty of addresses have never been
    sold through an agent - so it is reported as nothing found rather than as
    a failure, and the run carries on.
    """
    if not adresse_uuid:
        return {}
    get = (session or requests).get
    try:
        response = get(ADDRESS_URL.format(uuid=adresse_uuid), timeout=30)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return {}
    return parse(payload, adresse_uuid)


def parse(payload: dict, adresse_uuid: str = "") -> dict:
    """Everything worth keeping out of one address payload."""
    slug = payload.get("slug")
    found = {
        "adresse_uuid": adresse_uuid,
        "boligareal_m2": payload.get("livingArea"),
        "boligsiden_vurdering_dkk": payload.get("latestValuation"),
        "boligtype": payload.get("addressType"),
        "til_salg": "true" if payload.get("isOnMarket") else "false",
        "boligsiden_url": LISTING_URL.format(slug=slug) if slug else "",
        "salg": [_salg(r) for r in payload.get("registrations") or []],
        "bygninger": [_bygning(b) for b in payload.get("buildings") or []],
    }
    where = payload.get("coordinates") or {}
    found["breddegrad"], found["laengdegrad"] = where.get("lat"), where.get("lon")
    # Newest first: the dates are ISO, so sorting them as text is correct.
    found["salg"].sort(key=lambda s: s["dato"] or "", reverse=True)
    return found


def _salg(registration: dict) -> dict:
    """One recorded sale, with the price per square metre worked out.

    Boligsiden gives perAreaPrice already, but not always, and it is worth
    having on every row that has both halves of it.
    """
    amount, area = registration.get("amount"), registration.get("area") or registration.get("livingArea")
    kind = registration.get("type") or ""
    return {
        "dato": registration.get("date") or "",
        "beloeb_dkk": amount,
        "areal_m2": area,
        "pris_pr_m2": registration.get("perAreaPrice")
        or (round(amount / area) if amount and area else None),
        "handelstype": HANDELSTYPER.get(kind, kind.capitalize() or "Ukendt"),
        "handelstype_kode": kind,
        "registrering_id": registration.get("registrationID") or "",
    }


def _bygning(building: dict) -> dict:
    return {key: building.get(source) for key, source in BYGNING_FELTER.items()}
