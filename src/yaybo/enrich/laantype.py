"""Work out what kind of mortgage a charge is, from the rate it carries.

The register records a realkreditpantebrev's interest rate but never the
product behind it, so a row says 2.74% and not whether that is an F1 resetting
next year or a fixed loan running to 2050 - which is most of what the figure
means. The rate is not arbitrary though: it is the coupon on a bond series, and
Danmarks Statistik publishes the average effective rate and the bidrag for each
loan type, month by month, in table DNRNURI:

    coupon = effective rate - bidrag

So a charge registered in a given month can be matched against what each loan
type was actually priced at around then, and the nearest one named. It is an
estimate and is labelled as one: two types are often within a few hundredths of
each other, and the answer says so rather than picking.

    laantype_estimat      the nearest match, blank when nothing is close
    laantype_afstand      how far off it was, in percentage points
    laantype_alternativ   the runner-up, when it is too close to call
    laantype_kilde        "DST" - or "ISIN" if a definitive lookup is ever
                          wired in; see the note at the bottom of this file

Only realkreditpantebreve are classified. An ejerpantebrev at 10% is not a bond
loan and naming it F5 would be a fabrication, not an estimate.
"""

from __future__ import annotations

import math
from datetime import date

import requests

DST_URL = "https://api.statbank.dk/v1/data"
DST_INFO_URL = "https://api.statbank.dk/v1/tableinfo"

# DST's own codes for how long a loan's rate is fixed, and what people call them.
RENTFIX = {
    "1M3M": "F-kort",
    "1A": "F1",
    "3A": "F3",
    "5A": "F5",
    "S10A": "Fastforrentet",
}
# AL51EFFR is the effective rate, AL51BIDS the bidrag; the coupon is the gap.
MEASURES = ["AL51EFFR", "AL51BIDS"]

# Two candidates within this of each other are too close to separate, so both
# are reported. A best match further than this is not worth naming at all.
CLOSE = 0.4
UNCERTAIN = 1.0

# A charge older than the series has nothing to be matched against. DNRNURI
# lists periods back to 2003, but a coupon needs both the effective rate and
# the bidrag and the early periods are missing one, so in practice the usable
# series starts around 2013. This is only a cheap pre-filter; what actually
# governs is which months come back with both figures.
FIRST_MONTH = date(2003, 1, 1)

# The register only ever gives a rate, so this is an estimate. A definitive
# answer needs the bond's ISIN, which tinglysning does not publish anywhere in
# the attest - it would have to be supplied by hand, per mortgage, from a
# lender's own paperwork, and then looked up in ESMA FIRDS.
KILDE_DST = "DST"

# Which loan types reset their rate and which do not. The register's own
# fast/variabel flag is read against these to settle a close call.
VARIABLE = {"F-kort", "F1", "F3", "F5"}
FASTE = {"Fastforrentet"}

# Charge types that are bond loans. Nothing else is classified.
REALKREDIT = {"realkreditpantebrev"}

# Filled on first use by published(); DST publishes monthly, so once is enough.
_PUBLISHED: set[str] | None = None
_SERIES: dict | None = None


def series(session=None) -> dict:
    """The whole published DNRNURI series, fetched once and remembered.

    Every month rather than only the ones this run happens to need. It is one
    request either way, and a rate series is worth having whole: it is what
    makes an estimate auditable afterwards - the alternative is a column
    saying "F3" with nothing to check it against.
    """
    global _SERIES
    if _SERIES is None:
        _SERIES = _rates(sorted(published(session)), session=session)
    return _SERIES


def rate_rows(table: dict) -> list[dict]:
    """The series as rows: one per month per loan type."""
    return [
        {
            "maaned": month,
            "rentfix_kode": code,
            "laantype": RENTFIX[code],
            **figures,
        }
        for month, rates in sorted(table.items())
        for code, figures in rates.items()
    ]


def annotate(charges: list[dict], *, months: int = 6, session=None) -> dict:
    """Name the loan type on every realkredit charge that can carry one.

    Writes into the rows in place. Returns how many were named, and the rate
    series they were named against, so it can be stored beside them.
    """
    table = series(session=session)
    candidates = []
    for charge in charges:
        if charge.get("dokumenttype") not in REALKREDIT:
            continue
        when = str(charge.get("tinglysningsdato") or "")
        window = _months_before(when, months)
        # The register does not always put a number in the rate field: a
        # charge carried over from paper can say "ktl" there, meaning the
        # kontantlaan it once was. There is no rate to match, so it is skipped.
        rate = _as_rate(charge.get("rentesats_pct"))
        if rate is None or not window:
            continue
        candidates.append((charge, rate, window))

    if not table:
        return {"named": 0, "renter": {}}

    named = 0
    for charge, rate, window in candidates:
        found = classify(rate, window, table, str(charge.get("rentetype") or ""))
        charge.update(found)
        named += bool(found.get("laantype_estimat"))
    return {"named": named, "renter": table}


def _as_rate(value) -> float | None:
    """A rate as a number, or nothing when the register wrote a word there."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify(rate: float, window: list[str], table: dict, rentetype: str = "") -> dict:
    """The loan type whose coupon sat nearest this rate, over these months.

    Bond coupons cluster, so the nearest match is often barely nearer than the
    next one: on a real building more than half the charges have a runner-up
    within CLOSE of the winner. Where the register itself says whether the rate
    is fixed or variable, that settles those cases - a rate that resets is not
    a fastforrentet loan whatever the arithmetic says. It is only ever used to
    choose between candidates that were already close, never to override a
    clear winner, because the register records the rate in force today and a
    rentetilpasningslaan inside its fixed period is written down as fixed.
    """
    best: dict[str, float] = {}
    for month in window:
        for code, figures in (table.get(month) or {}).items():
            distance = abs(figures["kupon_pct"] - rate)
            name = RENTFIX[code]
            if name not in best or distance < best[name]:
                best[name] = distance
    if not best:
        return {}

    ranked = sorted(best.items(), key=lambda pair: pair[1])
    if ranked[0][1] > UNCERTAIN:
        # Nothing was close enough for a name to mean anything.
        return {"laantype_estimat": "", "laantype_afstand": round(ranked[0][1], 4),
                "laantype_kilde": KILDE_DST}

    near = [pair for pair in ranked if pair[1] - ranked[0][1] < CLOSE]
    name, distance = ranked[0]
    settled = ""
    if len(near) > 1:
        prefer = FASTE if rentetype == "fast" else VARIABLE if rentetype == "variabel" else set()
        agrees = [pair for pair in near if pair[0] in prefer]
        if agrees and agrees[0][0] != name:
            name, distance = agrees[0]
            settled = rentetype
    alternatives = [other for other, _ in near if other != name]
    return {
        "laantype_estimat": name,
        "laantype_afstand": round(distance, 4),
        "laantype_alternativ": "; ".join(alternatives),
        "laantype_afgjort_af": settled,
        "laantype_kilde": KILDE_DST,
    }


def _months_before(when: str, count: int) -> list[str]:
    """The DST time codes for the `count` months ending at this date.

    A loan is priced when it is taken out, not when it is looked up, so the
    window sits at the registration date. Several months rather than one
    because the bond behind a charge is often issued a little before the deed
    reaches the register.
    """
    try:
        day = date.fromisoformat(when[:10])
    except ValueError:
        return []
    if day < FIRST_MONTH:
        return []
    months = []
    year, month = day.year, day.month
    for _ in range(count):
        if date(year, month, 1) >= FIRST_MONTH:
            months.append(f"{year}M{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def published(session=None) -> set[str]:
    """The months DST has actually published, asked for once and remembered.

    This has to be checked rather than assumed. A request naming one month
    that does not exist yet is rejected in full - "Kan ikke finde vaerdien:
    2026M08 (Tid)" - and takes the other 193 months with it, so a run in the
    first weeks of a month would otherwise get nothing at all. Guessing a
    couple of months of lag would work most of the time and quietly throw away
    the newest published month the rest of it.
    """
    global _PUBLISHED
    if _PUBLISHED is None:
        post = (session or requests).post
        try:
            response = post(DST_INFO_URL,
                            json={"table": "DNRNURI", "format": "JSON", "lang": "da"},
                            timeout=30)
            response.raise_for_status()
            _PUBLISHED = {
                value["id"]
                for variable in response.json()["variables"]
                if variable["id"].lower() == "tid"
                for value in variable["values"]
            }
        except (requests.RequestException, ValueError, KeyError):
            _PUBLISHED = set()
    return _PUBLISHED


def _rates(months: list[str], session=None) -> dict:
    """Ask DST for the coupon on each loan type in each month.

    Returns {month: {rentfix code: coupon}}. Empty when DST cannot be reached,
    which leaves every charge unnamed rather than stopping the run.
    """
    known = published(session)
    months = sorted(set(months) & known) if known else []
    if not months:
        return {}
    post = (session or requests).post
    try:
        response = post(DST_URL, json={
            "table": "DNRNURI", "format": "JSONSTAT", "lang": "da",
            "variables": [
                {"code": "DATA", "values": MEASURES},
                {"code": "INDSEK", "values": ["1430"]},
                {"code": "VALUTA", "values": ["DKK"]},
                {"code": "LØBETID1", "values": ["ALLE"]},
                {"code": "RENTFIX", "values": list(RENTFIX)},
                {"code": "LAANSTR", "values": ["ALLE"]},
                {"code": "Tid", "values": months},
            ]}, timeout=60)
        response.raise_for_status()
        dataset = response.json()["dataset"]
    except (requests.RequestException, ValueError, KeyError):
        return {}
    return _read(dataset)


def _read(dataset: dict) -> dict:
    """Pull the values out of JSON-stat by name rather than by position.

    The response carries dimensions this query never asked for - ContentsCode
    is in there at size 1 - so working out an offset from the order the
    variables were sent in only works by luck. The layout declares itself in
    dimension.id and dimension.size, so it is read from there instead.
    """
    dimension = dataset["dimension"]
    order, sizes, values = dimension["id"], dimension["size"], dataset["value"]
    # Row-major: each dimension's stride is the product of the sizes after it.
    stride = {
        name: math.prod(sizes[position + 1:])
        for position, name in enumerate(order)
    }
    index = {name: dimension[name]["category"]["index"] for name in order}

    def at(**where) -> float | None:
        # A dimension we do not name has exactly one member, at position 0,
        # so it contributes nothing to the offset.
        offset = sum(
            index[name][key] * stride[name]
            for name, key in ((name, where.get(name)) for name in order)
            if key is not None
        )
        value = values[offset] if offset < len(values) else None
        return value if isinstance(value, (int, float)) else None

    found: dict = {}
    for month in index["Tid"]:
        rates = {}
        for code in index["RENTFIX"]:
            effective = at(DATA=MEASURES[0], RENTFIX=code, Tid=month)
            bidrag = at(DATA=MEASURES[1], RENTFIX=code, Tid=month)
            if effective is not None and bidrag is not None:
                rates[code] = {
                    "effektiv_rente_pct": effective,
                    "bidrag_pct": bidrag,
                    # What the bond itself pays, which is the number the
                    # register writes down against a charge.
                    "kupon_pct": round(effective - bidrag, 4),
                }
        if rates:
            found[month] = rates
    return found
