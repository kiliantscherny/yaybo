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

from yaybo.enrich import bbr, laantype
from yaybo.register import attest, attest_xml, rows
from yaybo.register.address import (
    dawa_addresses,
    fetch_parcel,
    floor_and_door,
    resolve_address,
    select_units,
)
from yaybo.register.client import ANDELSBOG, SessionExpired

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
    "andele",
    "andel_haeftelser",
    "andel_meddelelser",
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

    @property
    def andele(self) -> list[dict]:
        return self.tables.get("andele") or []

    def counts(self) -> dict[str, int]:
        return {name: len(gathered) for name, gathered in self.tables.items() if gathered}


def both_books(api, address: dict, andele_on: bool = True) -> list[dict]:
    """Everything either book holds at an address, as one list.

    The two searches answer in the same {uuid, adresse, bog} shape, so merging
    them costs nothing and `bog` is what every later step dispatches on. One
    list rather than two is what lets a ticked set of rows be fetched, counted
    and exported without any screen having to know which register a row is
    from.

    The second search is one extra request per building, and it is the only
    way to find out: a co-op block looks exactly like a rented one in the
    tingbog - a single property with somebody else's name on it - right up
    until the andelsboligbog answers with a flat per door.
    """
    units = api.find_units(address)
    if not andele_on:
        return units
    try:
        return units + api.find_andele(address)
    except (requests.RequestException, RuntimeError):
        # The tingbog has already answered. Losing the second book makes the
        # result thinner, which is worth far more than making it nothing.
        return units


def narrow(units: list[dict], etage: str, doer: str) -> tuple[list[dict], str]:
    """Cut both books' answers down to the flat that was asked for.

    Each book is narrowed on its own and the two put back together, because
    they are not competing answers. Dropping the tingbog's building because it
    does not match a door would throw away the association's own mortgages,
    its easements and the only valuation anywhere in sight - which is most of
    what makes a co-op flat worth looking up.

    It also settles what to warn about. "No separately registered unit for
    'st. th'" is true of the tingbog and misleading on its own: the flat is
    separately registered, in the other book. So when the andelsboligbog
    answered with exactly that flat, the complaint is dropped.
    """
    if not etage and not doer:
        return units, ""

    properties = [u for u in units if u.get("bog") != ANDELSBOG]
    andele = [u for u in units if u.get("bog") == ANDELSBOG]

    kept, warning = select_units(properties, etage, doer) if properties else ([], "")
    if andele:
        # select_units hands everything back when it matched nothing, so an
        # empty warning is what says the share itself was found.
        andele, missed = select_units(andele, etage, doer)
        if not missed:
            warning = ""
    return kept + andele, warning


def resolve(
    api, query: str, *, use_dawa: bool = True, andele_on: bool = True
) -> tuple[dict, list[dict], str]:
    """Turn a typed address into (address, what the registers hold, a warning).

    Two lookups, and they are not interchangeable. DAWA cleans the address;
    the registers then say what sits at it - anything from one property (a
    rented block) to sixty (a block of flats), plus a co-op share per door
    where the address is an andelsbolig.
    """
    address = None
    if use_dawa:
        try:
            address = resolve_address(query)
        except requests.RequestException:
            address = None  # DAWA unreachable; the register can do its own
    if address is None:
        address = api.lookup_address(query)

    units = both_books(api, address, andele_on)
    units, warning = narrow(units, address["etage"], address["doer"])
    return address, units, warning


def units_at(
    api, address: dict, *, andele_on: bool = True
) -> tuple[list[dict], str]:
    """What the registers hold at an already-resolved address.

    The half of `resolve` that costs requests, for a caller that got its
    address from DAWA directly and has nothing left to clean.
    """
    units = both_books(api, address, andele_on)
    return narrow(units, address["etage"], address["doer"])


def _gather_andel(
    api, uuid: str, gathered: dict, addresses: dict, building: dict, *,
    key: str = "", bbr_cache: dict | None = None,
):
    """Fetch one co-op share and add the two tables' worth of rows it fills.

    Returns the record so the caller can hand it to `on_raw`. There is no
    second payload to go with it the way a property has an attest and a
    history: this book has one page per share and that page is all of it.
    """
    record = api.fetch_andel(uuid)
    adresse = record.get("adresse", "")

    # The book gives no area, no valuation and nothing about the building, so
    # an andel row is an address, a debt, wherever DAWA places it and whatever
    # BBR says the flat is. BBR is doing more work here than for a property,
    # which at least has a tinglyste areal of its own.
    entry = addresses.get(floor_and_door(adresse))
    bolig = _bbr(entry, key, bbr_cache)

    gathered["andele"].append(
        {
            **rows.andel_row(
                record, uuid, building.get("uuid", ""), building.get("adresse", "")
            ),
            **rows.bbr_row(bolig),
            **rows.dawa_row(entry),
        }
    )
    gathered["andel_haeftelser"] += rows.andel_haeftelse_rows(record, uuid)
    gathered["andel_meddelelser"] += rows.andel_meddelelse_rows(record, uuid)
    return record


def fetch(
    api,
    address: dict,
    units: list[dict],
    *,
    warning: str = "",
    delay: float = 1.0,
    laantype_on: bool = True,
    bbr_on: bool = True,
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

    # One request gives every flat in the building its official coordinates,
    # and the DAWA uuid that BBR is keyed on.
    addresses = dawa_addresses(address)
    # Empty when no Datafordeler key is configured, which is the ordinary case
    # and costs nothing but the BBR columns.
    bbr_key = bbr.api_key() if bbr_on else ""
    bbr_cache: dict = {}
    parcels: dict = {}
    fetched = 0

    # The association's property, when this lookup found exactly one. Every
    # share at the address joins to it, and through it to the block's
    # valuation and the association's own mortgages, none of which the
    # andelsboligbog holds. More than one property here means the address is
    # something other than a plain co-op block, and guessing which of them a
    # share belongs to would be worse than leaving the join empty.
    properties = [u for u in units if u.get("bog") != ANDELSBOG]
    building = properties[0] if len(properties) == 1 else {}

    for index, unit in enumerate(units, start=1):
        if should_stop and should_stop():
            break
        if index > 1:
            time.sleep(delay)
        if on_unit:
            on_unit(index, len(units), unit)

        uuid = unit["uuid"]
        if unit.get("bog") == ANDELSBOG:
            record = _gather_andel(
                api, uuid, gathered, addresses, building,
                key=bbr_key, bbr_cache=bbr_cache,
            )
            if on_raw:
                on_raw(index, record, None, None)
            fetched = index
            continue

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
        raw = details.get("_raw") if details else None
        parsed = attest_xml.parse(raw) if raw is not None else {}

        adresse = record.get("adresse", "")
        entry = addresses.get(floor_and_door(adresse))

        # Read before the property row is built, because the newest transfer
        # in it is what the row's seneste_salg_* columns are, and the sales
        # table is the same list read a second way.
        entries, owners = rows.history_rows(history, uuid, adresse)

        # BBR first, because the living area it gives is what the sale prices
        # divide by. The register's own tinglyste areal prices them too, in a
        # column of its own - see rows.handel_rows on why both.
        bolig = _bbr(entry, bbr_key, bbr_cache)
        property_row = rows.property_row(record, uuid, parcel, flat)
        areal_m2 = property_row.get("areal_m2")
        boligareal_m2 = bolig.get("boligareal_m2")
        gathered["bygninger"] += rows.bygning_rows(bolig, uuid, adresse)

        gathered["ejendomme"].append(
            {
                **property_row,
                **rows.bbr_row(bolig),
                **rows.latest_sale_row(entries, areal_m2, boligareal_m2),
                **rows.dawa_row(entry),
                # Per property, not per run. A session that lapses halfway
                # leaves a database where some rows have owners' dates of birth
                # and previous owners and some do not, and the only useful
                # answer to "is this row complete" is the one recorded here.
                "beriget": details is not None,
            }
        )
        gathered["ejere"] += rows.owner_rows(record, uuid, flat)
        gathered["haeftelser"] += rows.haeftelse_rows(record, uuid, parsed)
        gathered["servitutter"] += rows.servitut_rows(record, uuid, parsed)
        gathered["dokument_parter"] += rows.party_rows(parsed, uuid)
        gathered["underpant"] += rows.underpant_rows(parsed, uuid)
        gathered["adkomsthistorik"] += entries
        gathered["adkomsthistorik_ejere"] += owners
        gathered["handelshistorik"] += rows.handel_rows(
            entries, uuid, adresse, areal_m2, boligareal_m2
        )

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

    # Only the tingbog's charges. What is secured on a share is a bank loan,
    # and the DST series behind the estimate is realkredit rates, which a bank
    # loan is not priced against - so a match there would be a coincidence
    # dressed up as a reading.
    if laantype_on:
        estimated = laantype.annotate(gathered["haeftelser"])
        gathered["rentestatistik"] = laantype.rate_rows(estimated["renter"])
        if estimated["named"]:
            say(f"named the loan type on {estimated['named']} realkredit charge(s)")

    rows.add_financials(gathered["ejendomme"], gathered["haeftelser"])
    rows.add_andel_debt(gathered["andele"], gathered["andel_haeftelser"])

    return Bundle(
        address=address,
        units=units,
        tables=gathered,
        warning=warning,
        enriched=enriched,
        fetched=fetched,
    )


def describe(units: list[dict]) -> str:
    """"1 property and 10 co-op shares" - what a lookup is about to cost.

    Counted separately because they are not the same thing and the difference
    is the point: one of those numbers is buildings and flats registered as
    real property, the other is shares in an association that owns one.
    """
    shares = sum(1 for unit in units if unit.get("bog") == ANDELSBOG)
    properties = len(units) - shares
    parts = []
    if properties or not shares:
        parts.append(f"{properties} propert{'y' if properties == 1 else 'ies'}")
    if shares:
        parts.append(f"{shares} co-op share{'' if shares == 1 else 's'}")
    return " and ".join(parts)


def lookup(
    api,
    query: str,
    *,
    limit: int = 25,
    use_dawa: bool = True,
    andele_on: bool = True,
    **options,
) -> Bundle:
    """Resolve an address and fetch everything at it, in one call.

    `limit` caps how many properties a building is allowed to cost; 0 lifts it.
    """
    say = options.get("on_status") or (lambda message: None)
    address, units, warning = resolve(
        api, query, use_dawa=use_dawa, andele_on=andele_on
    )
    say(f"resolved: {address['tekst']}")
    say(f"found {describe(units)}")
    if limit and len(units) > limit:
        say(f"fetching the first {limit} - raise the limit for more")
        units = units[:limit]
    return fetch(api, address, units, warning=warning, **options)


def _bbr(entry: dict | None, key: str, cache: dict | None) -> dict:
    """What BBR holds for one address, or {} when there is no key for it.

    Both uuids come off the DAWA lookup that has already happened, so this
    costs no extra address resolution - only BBR's own requests, which are
    cached per building.
    """
    if not entry or not key:
        return {}
    return bbr.fetch(entry.get("husnummer", ""), entry.get("uuid", ""), key, cache)
