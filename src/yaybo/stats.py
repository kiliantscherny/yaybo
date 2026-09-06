"""Figures over a set of properties, rather than about one of them.

A property's own page answers "what is this worth". These answer "what is this
worth compared with", which needs both a set to compare against and a way to say
which set: everything held, one postcode, one building, one floor of one
building. That is the whole design - a filter picks a subset, and every figure
is computed over whatever it picked.

Two derived fields do most of the work, because the register does not record
either. A building is the address with the flat taken off it, so every flat at
Islands Brygge 30B groups together however the register spells their floors. A
floor is the part of the flat designation before the door, so the third floor of
one building can be set against the tenth.

Nothing here reads the database or draws anything. It takes rows and gives back
numbers, so what it computes can be checked without a terminal.
"""

from __future__ import annotations

import re
import shlex
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from yaybo.display import boligtype, parse_date
from yaybo.register.address import drop_unit, split_postcode, unit_label
from yaybo.register.fields import normalise

# The tables a figure on this screen can be built from. `attester` is left out
# deliberately: it holds the whole register document per property, hundreds of
# kilobytes each, and nothing here counts them.
TABLES = ("ejendomme", "handelshistorik", "ejere", "haeftelser", "bygninger")

# What can be typed on the left of a colon in the filter box, and the derived
# or stored field each one narrows on.
FILTERS = {
    "postnr": "_postnr",
    "by": "_by",
    "vej": "_vej",
    "bygning": "_bygning",
    "etage": "_etage",
    "type": "_type",
    "mitid": "_mitid",
    "adresse": "adresse",
}


def annotate(properties: list[dict]) -> list[dict]:
    """Add the fields the register does not record but every grouping needs.

    Done once, on the way in, rather than inside each aggregation: a building
    parsed six different ways in six different places is six chances for the
    same two flats to end up in different buildings.
    """
    for row in properties:
        address = row.get("adresse") or ""
        head, postcode = split_postcode(address)
        row["_bygning"] = drop_unit(address)
        row["_postnr"] = postcode
        row["_by"] = _town(address, postcode)
        row["_vej"] = _street(head)
        row["_etage"] = _floor(row)
        # Boligsiden's key made readable; the register's own ejendomstype
        # left exactly as it wrote it. Without the first, a dropdown offers
        # "condo" - an API token, and an American one - to somebody choosing
        # what kind of home to look at.
        row["_type"] = (
            boligtype(row.get("boligtype")) or row.get("ejendomstype") or ""
        )
        row["_mitid"] = "ja" if row.get("beriget") else "nej"
        row["_areal"] = row.get("boligareal_m2") or row.get("areal_m2")
    return properties


def _town(address: str, postcode: str) -> str:
    if not postcode:
        return ""
    _, _, tail = address.rpartition(postcode)
    return tail.strip(" ,")


def _street(head: str) -> str:
    """"Islands Brygge 30B" from the part of the address before the postcode."""
    first = head.split(",")[0].strip()
    return re.sub(r"\s+\d+[A-Za-z]?$", "", first).strip() or first


def _floor(row: dict) -> str:
    """The floor a flat is on: "st", "1", "2"… or "" for a whole property.

    Read from `lejlighed` where the register gave one, and otherwise off the
    address, which is where it lives for anything Boligsiden filled in.
    """
    unit = (row.get("lejlighed") or "").strip()
    if not unit:
        unit = unit_label(row.get("adresse") or "")
    if not unit:
        return ""
    first = unit.split(".")[0].strip().lower()
    return first if first else ""


@dataclass
class Scope:
    """One set of properties and the rows belonging to it."""

    properties: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    owners: list[dict] = field(default_factory=list)
    charges: list[dict] = field(default_factory=list)
    buildings: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.properties)


def scope_of(tables: dict[str, list[dict]], query: str = "") -> Scope:
    """Everything matching the filter, with the child tables narrowed to match."""
    properties = [
        row for row in annotate(list(tables.get("ejendomme") or []))
        if _matches(row, query)
    ]
    keep = {row["uuid"] for row in properties if row.get("uuid")}

    def narrow(name: str) -> list[dict]:
        return [
            row for row in tables.get(name) or [] if row.get("ejendom_uuid") in keep
        ]

    return Scope(
        properties=properties,
        trades=narrow("handelshistorik"),
        owners=narrow("ejere"),
        charges=narrow("haeftelser"),
        buildings=narrow("bygninger"),
    )


def tokens(query: str) -> list[str]:
    """Split a filter into tokens, keeping quoted values in one piece.

    `bygning:"Islands Brygge 30B"` has to survive as one condition; splitting
    it on spaces would turn one exact building into four loose words that
    happen to co-occur.
    """
    try:
        return shlex.split(query)
    except ValueError:  # an unbalanced quote, i.e. still being typed
        return query.split()


def _matches(row: dict, query: str) -> bool:
    """Bare words match anywhere; `name:value` narrows one field.

    Same shape as the library's filter on purpose - somebody who has learned
    `postnr:2300` there should not have to learn it again here.
    """
    for token in tokens(query):
        name, _, value = token.partition(":")
        field_name = FILTERS.get(name.lower()) if value else None
        if field_name:
            if normalise(value) not in normalise(str(row.get(field_name) or "")):
                return False
            continue
        wanted = normalise(token)
        haystack = " ".join(
            str(row.get(name) or "") for name in (*FILTERS.values(), "ejendomstype")
        )
        if wanted not in normalise(haystack):
            return False
    return True


def choices(properties: list[dict], name: str) -> list[tuple[str, int]]:
    """What values one field takes across a set, commonest first.

    For telling someone what they could usefully filter on, which is otherwise
    guesswork against a database only they have.
    """
    field_name = FILTERS.get(name, name)
    counts: dict[str, int] = {}
    for row in properties:
        value = str(row.get(field_name) or "").strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


# ── the figures ─────────────────────────────────────────────────────────


def _numbers(rows, key) -> list[float]:
    found = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found.append(float(value))
    return found


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def overview(scope: Scope) -> list[tuple[str, list[tuple[str, str, object]]]]:
    """Every headline figure, in named sections, for the Oversigt analysis."""
    return [
        ("Properties", summary(scope)),
        ("Owners", owner_figures(scope)),
        ("Charges", loan_figures(scope)),
        ("Buildings", building_figures(scope)),
    ]


def summary(scope: Scope) -> list[tuple[str, str, object]]:
    """The headline numbers, as (label, unit, value) with value None if unknown.

    Formatting is the screen's business; this says what the number is and what
    kind of thing it is, so the same figure reads the same wherever it is put.
    """
    properties = scope.properties
    areas = _numbers(properties, "_areal")
    valuations = _numbers(properties, "ejendomsvurdering_dkk")
    debts = _numbers(properties, "samlet_gaeld_dkk")
    equity = _numbers(properties, "frivaerdi_dkk")
    ltv = _numbers(properties, "belaaningsgrad_pct")
    per_m2 = [
        row["ejendomsvurdering_dkk"] / row["_areal"]
        for row in properties
        if _numbers([row], "ejendomsvurdering_dkk") and _numbers([row], "_areal")
    ]
    buildings = {row["_bygning"] for row in properties if row.get("_bygning")}
    incomplete = sum(1 for row in properties if not row.get("beriget"))

    return [
        ("Properties", "count", len(properties)),
        ("Buildings", "count", len(buildings)),
        ("Without MitID data", "count", incomplete),
        ("Area, median", "m2", _median(areas)),
        ("Valuation, median", "kr", _median(valuations)),
        ("Valuation per m²", "kr", _median(per_m2)),
        ("Debt, median", "kr", _median(debts)),
        ("Equity, median", "kr", _median(equity)),
        ("Loan-to-value, median", "pct", _median(ltv)),
        ("Loan-to-value, mean", "pct", _mean(ltv)),
    ]


def owner_figures(scope: Scope) -> list[tuple[str, str, object]]:
    """Who owns these: how many each, how old, and how many are companies."""
    per_property: dict[str, int] = {}
    ages: list[float] = []
    companies = 0
    today = date.today()
    for owner in scope.owners:
        uuid = owner.get("ejendom_uuid")
        if uuid:
            per_property[uuid] = per_property.get(uuid, 0) + 1
        if owner.get("cvr"):
            companies += 1
            continue
        born = parse_date(owner.get("foedselsdato"))
        if born:
            ages.append((today - born).days / 365.25)

    counts = [float(n) for n in per_property.values()]
    return [
        ("Owners in total", "count", len(scope.owners)),
        ("Owners per property", "num", _mean(counts)),
        ("Of those, companies", "count", companies),
        ("Age, mean", "num", _mean(ages)),
        ("Age, median", "num", _median(ages)),
        ("Youngest owner", "num", min(ages) if ages else None),
        ("Oldest owner", "num", max(ages) if ages else None),
    ]


def loan_figures(scope: Scope) -> list[tuple[str, str, object]]:
    """What is charged against these, and on what terms."""
    principals = _numbers(scope.charges, "hovedstol_dkk")
    rates = _numbers(scope.charges, "rentesats_pct")
    kinds: dict[str, int] = {}
    for charge in scope.charges:
        kind = (charge.get("laantype_estimat") or charge.get("laantype") or "").strip()
        if kind:
            kinds[kind] = kinds.get(kind, 0) + 1
    commonest = max(kinds.items(), key=lambda pair: pair[1])[0] if kinds else None
    with_charges = len(
        {c["ejendom_uuid"] for c in scope.charges if c.get("ejendom_uuid")}
    )

    return [
        ("Charges in total", "count", len(scope.charges)),
        ("Properties with a charge", "count", with_charges),
        (
            "Charges per property",
            "num",
            len(scope.charges) / len(scope.properties) if scope.properties else None,
        ),
        ("Principal, median", "kr", _median(principals)),
        ("Principal in total", "kr", sum(principals) if principals else None),
        ("Interest rate, median", "pct", _median(rates)),
        ("Most common loan type", "text", commonest),
    ]


def building_figures(scope: Scope) -> list[tuple[str, str, object]]:
    """When these were built, and how big they are."""
    years = _numbers(scope.buildings, "opfoerelsesaar")
    rooms = _numbers(scope.buildings, "vaerelser")
    return [
        ("Built, median", "year", _median(years)),
        ("Oldest", "year", min(years) if years else None),
        ("Newest", "year", max(years) if years else None),
        ("Rooms, mean", "num", _mean(rooms)),
    ]


def _floor_order(name: str):
    """Order floors the way a building does, and anything else alphabetically."""
    lowered = name.lower()
    if lowered in ("kl", "kælder", "kaelder"):
        return (0, -1, "")
    if lowered == "st":
        return (0, 0, "")
    if lowered.isdigit():
        return (0, int(lowered), "")
    return (1, 0, lowered)


# ── the pre-made analyses ───────────────────────────────────────────────
#
# A measure says what one number is and where it is read from; an analysis is
# a named bundle of measures that make sense together. Everything on the
# figures screen is one of these two things, so adding a new question means
# adding a row here rather than a screen.


@dataclass(frozen=True)
class Measure:
    """One number, and how to get it off whichever table holds it."""

    key: str
    label: str
    unit: str
    source: str  # property | trade | owner | charge | building
    read: Callable[[dict], float | None]
    default: str = "median"


def _get(field: str) -> Callable[[dict], float | None]:
    def read(row: dict) -> float | None:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    return read


def _per_m2(row: dict) -> float | None:
    value, area = row.get("ejendomsvurdering_dkk"), row.get("_areal")
    if not isinstance(value, (int, float)) or not isinstance(area, (int, float)):
        return None
    return float(value) / float(area) if area else None


def _age(row: dict) -> float | None:
    if row.get("cvr"):
        return None  # a company has no age worth averaging
    born = parse_date(row.get("foedselsdato"))
    return (date.today() - born).days / 365.25 if born else None


MEASURES: tuple[Measure, ...] = (
    Measure("vurdering_m2", "Valuation per m²", "kr", "property", _per_m2),
    Measure("vurdering", "Valuation", "kr", "property",
            _get("ejendomsvurdering_dkk")),
    Measure("areal", "Area", "m2", "property", _get("_areal")),
    Measure("gaeld", "Debt", "kr", "property", _get("samlet_gaeld_dkk")),
    Measure("frivaerdi", "Equity", "kr", "property", _get("frivaerdi_dkk")),
    Measure("belaant", "Loan-to-value", "pct", "property",
            _get("belaaningsgrad_pct")),
    Measure("salg_m2", "Sale price per m²", "kr", "trade", _get("pris_pr_m2")),
    Measure("salg", "Sale price", "kr", "trade", _get("beloeb_dkk")),
    Measure("handler", "Number of sales", "count", "trade", lambda row: 1.0,
            default="sum"),
    Measure("alder", "Owners' age", "num", "owner", _age),
    Measure("ejere", "Owners per property", "num", "owner", lambda row: 1.0,
            default="per_property"),
    Measure("hovedstol", "Principal", "kr", "charge", _get("hovedstol_dkk")),
    Measure("rente", "Interest rate", "pct", "charge", _get("rentesats_pct")),
    Measure("haeftelser", "Charges per property", "num", "charge",
            lambda row: 1.0, default="per_property"),
    Measure("opfoert", "Year built", "year", "building",
            _get("opfoerelsesaar")),
    Measure("vaerelser", "Rooms", "num", "building", _get("vaerelser")),
)
BY_KEY = {measure.key: measure for measure in MEASURES}

# How a set of values becomes one number.
HOWS = (
    ("Median", "median"),
    ("Mean", "mean"),
    ("Total", "sum"),
    ("Count", "count"),
    ("Per property", "per_property"),
)

# What a set can be broken down by. `_etage` first because comparing floors of
# one building is the question this screen was built to answer.
GROUPS = (
    ("Floor", "_etage"),
    ("Building", "_bygning"),
    ("Street", "_vej"),
    ("Postcode", "_postnr"),
    ("Town", "_by"),
    ("Property type", "_type"),
    ("MitID data", "_mitid"),
)


@dataclass(frozen=True)
class Analysis:
    """A named question, and the measures that answer it."""

    key: str
    name: str
    blurb: str
    kind: str  # "time" for a series by year, "group" for a breakdown
    measures: tuple[str, ...]


ANALYSES: tuple[Analysis, ...] = (
    Analysis(
        "oversigt", "Overview",
        "The headline numbers for this selection, all on one page.",
        "summary", (),
    ),
    Analysis(
        "priser", "Prices over time",
        "What a square metre has cost, year by year, from the recorded sales.",
        "time", ("salg_m2", "salg", "handler"),
    ),
    Analysis(
        "sammenlign", "Compare groups",
        "The same figure side by side - floor against floor, or street "
        "against street.",
        "group", ("vurdering_m2", "salg_m2", "vurdering", "areal", "gaeld",
                  "frivaerdi", "belaant"),
    ),
    Analysis(
        "gaeld", "Debt and equity",
        "What is owed against these, what is left over, and how heavily "
        "they are borrowed against.",
        "group", ("belaant", "gaeld", "frivaerdi", "hovedstol", "rente",
                  "haeftelser"),
    ),
    Analysis(
        "ejere", "The owners",
        "How many people own each of these, and how old they are.",
        "group", ("ejere", "alder"),
    ),
    Analysis(
        "bygninger", "The buildings",
        "When they were built and how many rooms they hold.",
        "group", ("opfoert", "vaerelser", "areal"),
    ),
    Analysis(
        "udvikling", "Value over time",
        "Sale prices as a series, for one building or a whole postcode.",
        "time", ("salg", "salg_m2"),
    ),
)


def _rows_for(scope: Scope, source: str) -> list[dict]:
    return {
        "property": scope.properties,
        "trade": scope.trades,
        "owner": scope.owners,
        "charge": scope.charges,
        "building": scope.buildings,
    }[source]


def _combine(values: list[float], how: str, properties: int) -> float | None:
    if how == "count":
        return float(len(values))
    if not values:
        return None
    if how == "sum":
        return float(sum(values))
    if how == "mean":
        return statistics.fmean(values)
    if how == "per_property":
        return len(values) / properties if properties else None
    return statistics.median(values)


def aggregate(
    scope: Scope, by: str, measure: str, how: str = ""
) -> list[tuple[str, int, float | None]]:
    """One number per group, as (group, how many properties, value).

    Rows that are not properties - owners, charges, sales - are attributed to
    the group their property is in, which is what lets "average owner age by
    floor" mean anything.
    """
    spec = BY_KEY[measure]
    how = how or spec.default
    where = {
        row["uuid"]: str(row.get(by) or "—")
        for row in scope.properties
        if row.get("uuid")
    }
    counts: dict[str, int] = {}
    for row in scope.properties:
        counts[str(row.get(by) or "—")] = counts.get(str(row.get(by) or "—"), 0) + 1

    values: dict[str, list[float]] = {name: [] for name in counts}
    for row in _rows_for(scope, spec.source):
        name = (
            str(row.get(by) or "—")
            if spec.source == "property"
            else where.get(row.get("ejendom_uuid", ""))
        )
        if name is None:
            continue
        found = spec.read(row)
        if found is not None:
            values.setdefault(name, []).append(found)

    return sorted(
        (
            (name, counts.get(name, 0), _combine(values[name], how, counts.get(name, 0)))
            for name in values
        ),
        key=lambda group: _floor_order(group[0]) if by == "_etage" else (0, 0, group[0]),
    )


def over_time(
    scope: Scope, measure: str, how: str = "", since: int = 0
) -> list[tuple[int, int, float | None]]:
    """One number per year, as (year, how many sales, value).

    Only the sale tables carry a date, so this is always a series over what
    changed hands - which is the only thing the register dates finely enough
    to plot.
    """
    spec = BY_KEY[measure]
    how = how or spec.default
    years: dict[int, list[float]] = {}
    for row in _rows_for(scope, spec.source):
        when = parse_date(row.get("dato"))
        if when is None or (since and when.year < since):
            continue
        found = spec.read(row)
        if found is not None:
            years.setdefault(when.year, []).append(found)
    return [
        (year, len(values), _combine(values, how, len(values)))
        for year, values in sorted(years.items())
    ]
