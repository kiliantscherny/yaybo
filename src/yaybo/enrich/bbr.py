"""What BBR knows about a building, which the land register does not.

The tingbog records who owns a property and what is charged against it. It says
nothing about the thing itself - no year of construction, no number of rooms, no
heating, and no living area a listing would recognise. That is BBR's job, and
BBR is distributed by Datafordeleren:

    https://graphql.datafordeler.dk/BBR/v3?apiKey=...

Unlike every other source here, this one needs a credential. BBR is public data,
but every official distribution of it is behind either a key or a robots rule -
Datafordeleren's REST and GraphQL both refuse an anonymous request, BBR's own
map component disallows robots outright, and OIS disallows the two endpoints
that serve a BBR-meddelelse. So there is no keyless route, and the honest answer
is to ask for a key rather than to go looking for a gap.

The key is free: an account on Datafordeler Administration created with an email
address, and an API key valid two years. It is **optional**. Without one every
other table fills exactly as before and the BBR columns stay empty, because a
lookup that runs on `uvx yaybo` and nothing else is worth more than a complete
`bygninger` table.

Two things worth knowing about the credential, both learned the hard way:

- The API key works on **GraphQL only**. The REST services answer 403 to it;
  they want the older service-user, which Datafordeleren is retiring. REST is
  itself being retired at the end of 2026, so GraphQL is where this points.
- A newly created key is **not live for about 15 minutes**. Until then the
  service answers 401 `DAF-AUTH-0005`, which reads like a wrong key and is not.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from yaybo.enrich import bbr_codes

ENDPOINT = "https://graphql.datafordeler.dk/BBR/v3"
ENV_VAR = "DATAFORDELER_API_KEY"

# What a building row is made of. BBR's names on the right; the register's own
# vocabulary is kept on the left, as everywhere else here.
BYGNING_FELTER = {
    "bygning_nr": "byg007Bygningsnummer",
    "bygningstype": "byg021BygningensAnvendelse",
    "opfoerelsesaar": "byg026Opfoerelsesaar",
    "ombygningsaar": "byg027OmTilbygningsaar",
    "etager": "byg054AntalEtager",
    "boligareal_m2": "byg039BygningensSamledeBoligAreal",
    "erhvervsareal_m2": "byg040BygningensSamledeErhvervsAreal",
    "andet_areal_m2": "byg048AndetAreal",
    "samlet_areal_m2": "byg038SamletBygningsareal",
    "ydervaeg": "byg032YdervaeggensMateriale",
    "tagdaekning": "byg033Tagdaekningsmateriale",
    "varmeinstallation": "byg056Varmeinstallation",
    "supplerende_varme": "byg058SupplerendeVarme",
}

# What the flat itself contributes. A building's boligareal is the whole
# block's; this is the one door's, and they must not be confused.
ENHED_FELTER = {
    "vaerelser": "enh031AntalVaerelser",
    "badevaerelser": "enh066AntalBadevaerelser",
    "toiletter": "enh065AntalVandskylledeToiletter",
    "koekken": "enh034Koekkenforhold",
    "badeforhold": "enh033Badeforhold",
    "toiletforhold": "enh032Toiletforhold",
}

# Written with placeholders rather than interpolation: the bodies are GraphQL,
# which is mostly braces, and every way of formatting one into the other costs
# more in escaping than it saves.
#
# Three queries rather than one, because the service refuses both aliases and
# more than one root field per operation - so a building, a flat and a floor
# cannot be asked for together however tempting it looks.
BYGNING_QUERY = """
query Bygning($hus: String!, $t: DafDateTime!) {
  BBR_Bygning(first: 50, virkningstid: $t, where: {husnummer: {eq: $hus}}) {
    nodes { __FIELDS__ }
  }
}""".replace("__FIELDS__", " ".join(["id_lokalId", "status", *BYGNING_FELTER.values()]))

BYGNING_BY_ID_QUERY = """
query BygningById($id: String!, $t: DafDateTime!) {
  BBR_Bygning(first: 5, virkningstid: $t, where: {id_lokalId: {eq: $id}}) {
    nodes { __FIELDS__ }
  }
}""".replace("__FIELDS__", " ".join(["id_lokalId", "status", *BYGNING_FELTER.values()]))

ENHED_QUERY = """
query Enhed($adr: String!, $t: DafDateTime!) {
  BBR_Enhed(first: 20, virkningstid: $t,
            where: {adresseIdentificerer: {eq: $adr}}) {
    nodes { __FIELDS__ }
  }
}""".replace(
    "__FIELDS__",
    " ".join(
        [
            "id_lokalId",
            "bygning",
            "enh027ArealTilBeboelse",
            "enh023Boligtype",
            *ENHED_FELTER.values(),
        ]
    ),
)

KAELDER_QUERY = """
query Etage($byg: String!, $t: DafDateTime!) {
  BBR_Etage(first: 50, virkningstid: $t, where: {bygning: {eq: $byg}}) {
    nodes { id_lokalId eta022Kaelderareal }
  }
}"""


def api_key(env_file: Path | None = None) -> str:
    """The Datafordeler API key, or "" when there is none to be had.

    The environment wins, and a `.env` in the working directory is read as a
    fallback. That is the same reasoning as `out/` living where you are working
    rather than somewhere central: a key belongs to whoever is doing the
    looking up, and a folder per project is how this is already used.

    Deliberately not a dependency on python-dotenv. This reads one name out of
    one file; it is not a general implementation of the format, and the seven
    packages in pyproject.toml are all doing more than fifteen lines of work.
    """
    found = os.environ.get(ENV_VAR, "").strip()
    if found:
        return found

    path = env_file if env_file is not None else Path(".env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""

    for line in lines:
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == ENV_VAR:
            # Both quoting styles, because a key pasted out of a web page
            # arrives wrapped as often as not.
            return value.strip().strip('"').strip("'")
    return ""


def configured(env_file: Path | None = None) -> bool:
    """Whether a BBR lookup can be attempted at all."""
    return bool(api_key(env_file))


def _now() -> str:
    """The bitemporal timestamp every BBR query is required to carry.

    The service refuses a query with neither `registreringstid` nor
    `virkningstid`, so "as it stands right now" has to be said out loud.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _post(query: str, variables: dict, key: str) -> dict:
    """One GraphQL request, or {} when the service will not answer.

    A BBR outage, a lapsed key or a changed schema must not take a fetch down
    with it: everything the register itself gave is already worth keeping, and
    this is an enrichment, and the caller has no better answer than carrying on
    without it.
    """
    if not key:
        return {}
    try:
        response = requests.post(
            ENDPOINT,
            params={"apiKey": key},
            json={"query": query, "variables": variables},
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return {}
    # GraphQL answers 200 with an `errors` array rather than a status code.
    if payload.get("errors") or not isinstance(payload.get("data"), dict):
        return {}
    return payload["data"]


def _current(payload: dict, root: str) -> list[dict]:
    """One row per object, out of a bitemporal answer.

    BBR returns a node per version of a thing, so the same building arrives
    several times over - five nodes for one Copenhagen block, in the case that
    made this necessary. The service was asked for a single moment in time,
    which makes those duplicates equivalent rather than different, so the first
    of each id is as good as any.
    """
    nodes = ((payload.get(root) or {}).get("nodes")) or []
    seen: dict[str, dict] = {}
    for node in nodes:
        seen.setdefault(node.get("id_lokalId") or "", node)
    return list(seen.values())


def _read(node: dict, felter: dict[str, str]) -> dict:
    """One BBR object as a row, with its codes turned into the register's words."""
    return {
        column: bbr_codes.label(field, node.get(field))
        if field in bbr_codes.FOR_FIELD
        else node.get(field)
        for column, field in felter.items()
    }


def fetch(
    husnummer: str, adresse_uuid: str, key: str, cache: dict | None = None
) -> dict:
    """What BBR holds for one address: its building, and the flat itself.

    `husnummer` is DAWA's adgangsadresse uuid and `adresse_uuid` its address
    uuid - both already fetched for every property, which is what lets this
    work without a login of its own.

    The flat is asked for first, and its `bygning` is what the building is then
    looked up by. Going the other way - husnummer straight to BBR_Bygning -
    looks more direct and quietly answers nothing for a block whose building is
    registered against a different entrance than the flat's own, which is most
    of them. That route is kept only as a fallback for an address BBR holds no
    unit for at all.

    Three requests, because the service allows one root field per query. The
    building and its floors are cached by their own id, so a block of sixty
    doors costs sixty flat lookups and one building lookup.
    """
    if not key or not (adresse_uuid or husnummer):
        return {}
    cache = {} if cache is None else cache
    now = _now()

    found: dict = {}
    bygning_id = ""
    if adresse_uuid:
        units = _current(
            _post(ENHED_QUERY, {"adr": adresse_uuid, "t": now}, key), "BBR_Enhed"
        )
        if units:
            unit = units[0]
            bygning_id = unit.get("bygning") or ""
            found["boligareal_m2"] = unit.get("enh027ArealTilBeboelse")
            found["boligtype"] = bbr_codes.label(
                "enh023Boligtype", unit.get("enh023Boligtype")
            )
            found["enhed"] = _read(unit, ENHED_FELTER)

    found["bygninger"] = _buildings(bygning_id, husnummer, key, now, cache)
    return found


def _buildings(bygning_id: str, husnummer: str, key: str, now: str, cache: dict):
    """The flat's building, by its own id, or every building at the address."""
    if bygning_id:
        if bygning_id not in cache:
            answer = _post(BYGNING_BY_ID_QUERY, {"id": bygning_id, "t": now}, key)
            cache[bygning_id] = _rows(answer, key, now)
        return cache[bygning_id]
    if not husnummer:
        return []
    if husnummer not in cache:
        answer = _post(BYGNING_QUERY, {"hus": husnummer, "t": now}, key)
        cache[husnummer] = _rows(answer, key, now)
    return cache[husnummer]


def _rows(answer: dict, key: str, now: str) -> list[dict]:
    """Every building in one answer, as rows, with its basement area added."""
    buildings = []
    for node in _current(answer, "BBR_Bygning"):
        row = _read(node, BYGNING_FELTER)
        row["kaelderareal_m2"] = _kaelder(node.get("id_lokalId") or "", key, now)
        buildings.append(row)
    return buildings


def _kaelder(bygning_id: str, key: str, now: str):
    """The building's basement area, which lives on its floors rather than on it.

    BBR keeps an area per etage, so a building with a basement under two of its
    sections has two rows to add up. None of Bygning's eleven other area fields
    is this one - they are garage, carport, udhus, udestue and overdaekning.
    """
    if not bygning_id:
        return None
    answer = _post(KAELDER_QUERY, {"byg": bygning_id, "t": now}, key)
    floors = _current(answer, "BBR_Etage")
    areas = [f.get("eta022Kaelderareal") for f in floors]
    numbers = [a for a in areas if isinstance(a, (int, float))]
    return sum(numbers) if numbers else None
