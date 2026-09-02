"""One property lookup, from a typed address to a full set of rows.

Both front ends run this. The CLI calls `lookup` and writes what comes back;
the TUI calls `resolve` first so it can show the user which properties the
register holds at an address before spending requests on them, then `fetch`.
Neither of them knows how a row is built, and there is exactly one copy of the
order things happen in.

Everything the caller might want to show while it runs leaves through
callbacks - which unit is being fetched, what stage it is at - so a progress
bar and a line on stderr are the same code underneath.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

from yaybo.enrich import boligsiden, laantype
from yaybo.register import attest, attest_xml, rows
from yaybo.register.address import (
    dawa_addresses,
    fetch_parcel,
    floor_and_door,
    resolve_address,
    select_units,
)
from yaybo.register.client import SessionExpired

# The tables a lookup fills, in the order store.TABLES declares them.
TABLE_NAMES = (
    "ejendomme",
    "ejere",
    "haeftelser",
    "servitutter",
    "handelshistorik",
    "rentestatistik",
    "bygninger",
    "adkomsthistorik",
    "adkomsthistorik_ejere",
    "dokument_parter",
    "underpant",
    "attester",
)


@dataclass
class Bundle:
    """Everything one lookup produced, ready to be written or displayed."""

    address: dict
    units: list[dict]
    tables: dict[str, list[dict]] = field(default_factory=dict)
    # Set when the register was asked for a flat it has no separate entry for,
    # and the whole building was fetched instead. Worth showing: the rows are
    # correct but they are not what was asked for.
    warning: str = ""
    # Whether the run was logged in throughout. Decided once, before any row is
    # built, so a session that dies halfway cannot change the shape of a CSV
    # mid-write.
    enriched: bool = False
    # How far the run got, when a lapsed session cut it short.
    fetched: int = 0

    @property
    def properties(self) -> list[dict]:
        return self.tables.get("ejendomme") or []

    def counts(self) -> dict[str, int]:
        return {name: len(gathered) for name, gathered in self.tables.items() if gathered}


def resolve(api, query: str, *, use_dawa: bool = True) -> tuple[dict, list[dict], str]:
    """Turn a typed address into (address, the register's units, a warning).

    Two lookups, and they are not interchangeable. DAWA cleans the address;
    the register then says which legally registered properties sit at it,
    which is anything from one (a rented block) to sixty (a block of flats).
    """
    address = None
    if use_dawa:
        try:
            address = resolve_address(query)
        except requests.RequestException:
            address = None  # DAWA unreachable; the register can do its own
    if address is None:
        address = api.lookup_address(query)

    units = api.find_units(address)
    units, warning = select_units(units, address["etage"], address["doer"])
    return address, units, warning


def units_at(api, address: dict) -> tuple[list[dict], str]:
    """Which legally registered properties sit at an already-resolved address.

    The half of `resolve` that costs a request to the register, for a caller
    that got its address from DAWA directly and has nothing left to clean.
    """
    units = api.find_units(address)
    return select_units(units, address["etage"], address["doer"])


def fetch(
    api,
    address: dict,
    units: list[dict],
    *,
    warning: str = "",
    delay: float = 1.0,
    boligsiden_on: bool = True,
    laantype_on: bool = True,
    on_status=None,
    on_unit=None,
    on_raw=None,
    on_session_expired=None,
    should_stop=None,
) -> Bundle:
    """Fetch every unit and build every row. See `Bundle` for what comes back.

    `on_unit(index, total, unit)` is called before each property is fetched and
    `on_status(message)` for anything else worth saying. `should_stop()` is
    checked between properties, so a user who has seen enough can stop the run
    without losing what it has already gathered.

    `on_session_expired(done, total)` decides what happens when the login lapses
    partway through: True carries on without the logged-in columns, False stops.

    `on_raw(index, record, details, history)` sees each property's payloads
    exactly as the register sent them, before anything reads a row out of them.
    """
    say = on_status or (lambda message: None)
    enriched = api.authenticated
    gathered: dict[str, list[dict]] = {name: [] for name in TABLE_NAMES}

    # One request gives every flat in the building its DAWA uuid, which is the
    # key Boligsiden answers to. Without it there is nothing to ask about.
    addresses = dawa_addresses(address) if boligsiden_on else {}
    parcels: dict = {}
    fetched = 0

    for index, unit in enumerate(units, start=1):
        if should_stop and should_stop():
            break
        if index > 1:
            time.sleep(delay)
        if on_unit:
            on_unit(index, len(units), unit)

        uuid = unit["uuid"]
        record = api.fetch_record(uuid)

        details = history = None
        if api.authenticated:
            try:
                details = api.fetch_details(uuid)
                history = api.fetch_history(uuid)
            except SessionExpired:
                carry_on = (
                    on_session_expired(index - 1, len(units))
                    if on_session_expired
                    else True
                )
                say(
                    f"session expired after {index - 1} of {len(units)}"
                    + (" - continuing without the logged-in columns" if carry_on else "")
                )
                if not carry_on:
                    break

        if on_raw:
            on_raw(index, record, details, history)

        matrikel = (record.get("matrikler") or [{}])[0]
        parcel = fetch_parcel(
            matrikel.get("landsejerlavkode", ""),
            matrikel.get("matrikelnummer", ""),
            parcels,
        )
        # The same document, read twice: `flat` is the slice the property row
        # wants, `parsed` is the whole of it, which the charge tables and
        # everyone named on them are built from.
        flat = attest.attest_details(details)
        parsed = attest_xml.parse(details["_raw"]) if details and "_raw" in details else {}

        adresse = record.get("adresse", "")
        bolig = {}
        if addresses:
            found = addresses.get(floor_and_door(adresse))
            if found:
                bolig = boligsiden.fetch(found)
                gathered["handelshistorik"] += rows.handel_rows(bolig, uuid, adresse)
                gathered["bygninger"] += rows.bygning_rows(bolig, uuid, adresse)

        gathered["ejendomme"].append(
            {**rows.property_row(record, uuid, parcel, flat), **rows.bolig_row(bolig)}
        )
        gathered["ejere"] += rows.owner_rows(record, uuid, flat)
        gathered["haeftelser"] += rows.haeftelse_rows(record, uuid, parsed)
        gathered["servitutter"] += rows.servitut_rows(record, uuid, parsed)
        gathered["dokument_parter"] += rows.party_rows(parsed, uuid)
        gathered["underpant"] += rows.underpant_rows(parsed, uuid)
        entries, owners = rows.history_rows(history, uuid, adresse)
        gathered["adkomsthistorik"] += entries
        gathered["adkomsthistorik_ejere"] += owners

        suffix, document = attest.attest_document(details)
        if document:
            gathered["attester"].append(
                {
                    "ejendom_uuid": uuid,
                    "adresse": adresse,
                    "format": suffix,
                    "dokument": document,
                    "dokument_json": attest.attest_json(details),
                }
            )
        fetched = index

    if laantype_on:
        estimated = laantype.annotate(gathered["haeftelser"])
        gathered["rentestatistik"] = laantype.rate_rows(estimated["renter"])
        if estimated["named"]:
            say(f"named the loan type on {estimated['named']} realkredit charge(s)")

    rows.add_financials(gathered["ejendomme"], gathered["haeftelser"])

    return Bundle(
        address=address,
        units=units,
        tables=gathered,
        warning=warning,
        enriched=enriched,
        fetched=fetched,
    )


def lookup(api, query: str, *, limit: int = 25, use_dawa: bool = True, **options) -> Bundle:
    """Resolve an address and fetch everything at it, in one call.

    `limit` caps how many properties a building is allowed to cost; 0 lifts it.
    """
    say = options.get("on_status") or (lambda message: None)
    address, units, warning = resolve(api, query, use_dawa=use_dawa)
    say(f"resolved: {address['tekst']}")
    say(f"found {len(units)} propert{'y' if len(units) == 1 else 'ies'}")
    if limit and len(units) > limit:
        say(f"fetching the first {limit} - raise the limit for more")
        units = units[:limit]
    return fetch(api, address, units, warning=warning, **options)
