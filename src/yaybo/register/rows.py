"""Turn the register's answers into rows, one shape per kind of fact.

A property is not one row. It is a row for the property, a row for each of its
owners, one for each mortgage and each easement, one for everyone named on any
of those, one for every past transfer and everyone named in that. This module
is where each of those shapes is decided, and it is deliberately the only place
that knows the column names - the CSV writer, the database and the TUI all read
them from here rather than each having their own idea.

Column names follow the labels the register's own site shows, not the API's
internal ones: the API calls a document type "haeftelsestype" and a
date/serial "alias", which makes the data unrecognisable to anyone comparing a
row against the page it came from.
"""

from __future__ import annotations

from yaybo.register import historik
from yaybo.register.address import unit_label
from yaybo.register.fields import normalise, plain_number

HISTORIK_FIELDS = [
    "adresse", "dato", "dokumenttype", "koebesum_dkk", "antal_ejere",
    "historiske_ejere", "post_nummer", "ejendom_uuid",
]
HISTORIK_EJER_FIELDS = [
    "dato", "nummer", "navn", "foedselsdato", "cvr", "andel", "note",
    "post_nummer", "ejendom_uuid",
]


def history_rows(history: dict | None, uuid: str, adresse: str):
    """Previous owners, as entries and as the people named in them.

    Returns (entries, owners). The register gives each transfer a block of
    text rather than fields - a price, then a list of names with masked CPR
    numbers - so historik.parse takes it apart and the people become rows of
    their own, joined back on (ejendom_uuid, post_nummer). The block itself is
    kept alongside, because a parser is a reading and the source is the fact.

    Only reachable with a login: the public lookup shows the register as it
    stands today and says nothing about how it got there.
    """
    items = (history or {}).get("items") or []
    entries, owners = [], []
    for post, item in enumerate(
        sorted(items, key=lambda item: item.get("dato", ""), reverse=True), start=1
    ):
        tekst = (item.get("tekst") or "").strip()
        parsed = historik.parse(tekst)
        entries.append(
            {
                "adresse": adresse,
                "dato": item.get("dato", ""),
                "dokumenttype": item.get("dokumenttype", ""),
                "koebesum_dkk": parsed["koebesum_dkk"],
                "antal_ejere": len(parsed["ejere"]),
                "historiske_ejere": tekst,
                "post_nummer": post,
                "ejendom_uuid": uuid,
            }
        )
        for number, owner in enumerate(parsed["ejere"], start=1):
            owners.append(
                {
                    "dato": item.get("dato", ""),
                    "nummer": number,
                    "navn": owner["navn"],
                    "foedselsdato": owner["foedselsdato"],
                    "cvr": owner["cvr"],
                    "andel": owner["andel"],
                    "note": owner.get("note", ""),
                    "post_nummer": post,
                    "ejendom_uuid": uuid,
                }
            )
    return entries, owners


def property_row(
    record: dict, uuid: str, parcel: dict, attest: dict | None = None
) -> dict:
    """One row per property, with joint owners widened into ejer_1/ejer_2 columns."""
    attest = attest or {}
    matrikler = record.get("matrikler") or []
    vurdering = record.get("vurdering") or {}
    row = {
        "adresse": record.get("adresse", ""),
        "lejlighed": unit_label(record.get("adresse", "")),
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
        identity = (attest.get("owners") or {}).get(normalise(name), {})
        row[f"ejer_{number}_foedselsdato"] = identity.get("foedselsdato", "")
    return row


def owner_rows(record: dict, uuid: str, attest: dict | None = None) -> list[dict]:
    """The same owners the CSV widens across a row, as one row each.

    A spreadsheet is happier with ejer_1, ejer_2 columns; a database is happier
    with a table it can join and count. Same data, and this is the shape that
    does not change width when a building turns out to have five co-owners.
    """
    identities = (attest or {}).get("owners") or {}
    rows = []
    for number, ejer in enumerate(record.get("ejere") or [], start=1):
        name = ejer.get("navn", "")
        identity = identities.get(normalise(name), {})
        rows.append(
            {
                "ejendom_uuid": uuid,
                "nummer": number,
                "navn": name,
                "foedselsdato": identity.get("foedselsdato", ""),
                "cvr": identity.get("cvr", ""),
                "andel": ejer.get("andel", ""),
            }
        )
    return rows


def property_fields(
    max_owners: int, *, with_attest: bool = False, with_bolig: bool = False
) -> list[str]:
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
        *(BOLIG_FIELDS if with_bolig else []),
        "uuid",
    ]


# What Boligsiden adds to a property row, plus the three worked out from it
# and the charges. Kept together so the CSV and the database agree on them.
BOLIG_FIELDS = [
    "boligtype", "boligareal_m2", "boligsiden_vurdering_dkk", "til_salg",
    "seneste_salg_dato", "seneste_salg_dkk", "seneste_salg_pris_m2",
    "samlet_gaeld_dkk", "frivaerdi_dkk", "belaaningsgrad_pct",
    "breddegrad", "laengdegrad", "boligsiden_url", "adresse_uuid",
]
RENTE_FIELDS = [
    "maaned", "laantype", "rentfix_kode", "effektiv_rente_pct", "bidrag_pct",
    "kupon_pct",
]
HANDEL_FIELDS = [
    "adresse", "dato", "beloeb_dkk", "areal_m2", "pris_pr_m2", "handelstype",
    "handelstype_kode", "registrering_id", "ejendom_uuid",
]
BYGNING_FIELDS = [
    "adresse", "bygning_nr", "bygningstype", "opfoerelsesaar", "ombygningsaar",
    "etager", "vaerelser", "badevaerelser", "toiletter", "boligareal_m2",
    "kaelderareal_m2", "erhvervsareal_m2", "andet_areal_m2", "samlet_areal_m2",
    "ydervaeg", "tagdaekning", "varmeinstallation", "supplerende_varme",
    "koekken", "badeforhold", "toiletforhold", "ejendom_uuid",
]

def bolig_row(bolig: dict) -> dict:
    """The Boligsiden fields that belong on the property's own row."""
    if not bolig:
        return {}
    latest = (bolig.get("salg") or [{}])[0]
    return {
        "adresse_uuid": bolig.get("adresse_uuid", ""),
        "boligtype": bolig.get("boligtype") or "",
        "boligareal_m2": bolig.get("boligareal_m2"),
        "boligsiden_vurdering_dkk": bolig.get("boligsiden_vurdering_dkk"),
        "til_salg": bolig.get("til_salg", ""),
        "boligsiden_url": bolig.get("boligsiden_url", ""),
        "breddegrad": bolig.get("breddegrad"),
        "laengdegrad": bolig.get("laengdegrad"),
        "seneste_salg_dato": latest.get("dato", ""),
        "seneste_salg_dkk": latest.get("beloeb_dkk"),
        "seneste_salg_pris_m2": latest.get("pris_pr_m2"),
    }


def handel_rows(bolig: dict, uuid: str, adresse: str) -> list[dict]:
    """Every recorded sale of the address, one row each."""
    return [
        {"ejendom_uuid": uuid, "adresse": adresse, **sale}
        for sale in (bolig or {}).get("salg") or []
    ]


def bygning_rows(bolig: dict, uuid: str, adresse: str) -> list[dict]:
    """The BBR record for the building, one row per building."""
    return [
        {"ejendom_uuid": uuid, "adresse": adresse, **building}
        for building in (bolig or {}).get("bygninger") or []
    ]


def add_financials(properties: list[dict], charges: list[dict]) -> None:
    """Total what is charged against each property, and what is left over.

    Against the public valuation, which runs well below what a place would
    fetch, so the equity is a floor and the loan-to-value a ceiling. Both are
    left empty when there is no valuation to divide by rather than being
    quietly computed against zero.
    """
    debt: dict[str, int] = {}
    for charge in charges:
        amount = _amount(charge.get("hovedstol_dkk"))
        if amount is not None:
            debt[charge["ejendom_uuid"]] = debt.get(charge["ejendom_uuid"], 0) + amount

    for row in properties:
        owed = debt.get(row.get("uuid", ""), 0)
        row["samlet_gaeld_dkk"] = owed
        valuation = _amount(row.get("ejendomsvurdering_dkk")) or 0
        if valuation > 0:
            row["frivaerdi_dkk"] = valuation - owed
            row["belaaningsgrad_pct"] = round(100 * owed / valuation, 1)


HAEFTELSE_FIELDS = [
    "adresse", "dato_loebenummer", "prioritet", "dokumenttype",
    "dokumenttype_beskrivelse", "formularkode", "hovedstol", "hovedstol_dkk",
    "valuta", "rentetype", "rentesats_pct", "reference_rente",
    "reference_rente_pct", "rente_margin_pct", "rente_foreloebig", "laantype",
    "saerlige_vilkaar", "kreditorbetegnelse", "kreditorer", "tinglysningsdato",
    "senest_paategnet", "overfoert", "konverteret_pantebrev", "afgift_dkk",
    "afgift_overfoert", "antal_respekt", "antal_underpant", "tekst",
    "laantype_estimat", "laantype_afstand", "laantype_alternativ",
    "laantype_afgjort_af", "laantype_kilde",
    "rettighed_uuid", "dokument_version", "dokument_uuid", "ejendom_uuid",
]
SERVITUT_FIELDS = [
    "adresse", "dato_loebenummer", "prioritet", "dokumenttype", "indhold",
    "tekst", "paataleberettigede", "ogsaa_lyst_paa", "uden_ejers_tiltraedelse",
    "prioritet_forud", "betydning_for_vaerdi", "tinglysningsdato",
    "senest_paategnet", "overfoert", "afgift_dkk", "akt_filnavn",
    "rettighed_uuid", "dokument_version", "dokument_uuid", "ejendom_uuid",
]
PART_FIELDS = [
    "dokumentart", "rolle", "nummer", "navn", "foedselsdato", "cvr", "andel",
    "adresse_kode", "dokument_uuid", "ejendom_uuid",
]
UNDERPANT_FIELDS = [
    "dato_loebenummer", "beloeb_dkk", "valuta", "prioritet", "panthavere",
    "rettighed_uuid", "dokument_uuid", "haeftelse_uuid", "ejendom_uuid",
]

# What each end of a mortgage is called, singular, for the rolle column.
HAEFTELSE_ROLLER = {
    "kreditorer": "kreditor",
    "debitorer": "debitor",
    "meddelelseshavere": "meddelelseshaver",
    "fuldmagtshavere": "fuldmagtshaver",
}


def haeftelse_rows(record: dict, uuid: str, document: dict | None = None) -> list[dict]:
    """Mortgages and charges - one row each, linked back by ejendom_uuid.

    Read from the XML when we have it, which is only when logged in. That copy
    states the amount as a number, separates a fixed rate from a variable one
    and its margin, and counts the sub-pledges; the public lookup gives a
    formatted string and a single rate, so the fallback below fills what it can
    and leaves the rest empty rather than guessing.
    """
    adresse = record.get("adresse", "")
    charges = (document or {}).get("haeftelser")
    if charges:
        return [
            {
                "adresse": adresse,
                "hovedstol": _dkk(h["hovedstol_dkk"], h["valuta"]),
                "kreditorer": _names(h["kreditorer"]),
                "antal_respekt": len(h["respekterer"]),
                "antal_underpant": len(h["underpant"]),
                "ejendom_uuid": uuid,
                **{key: h.get(key, "") for key in HAEFTELSE_FIELDS
                   if key in h and key not in ("adresse", "kreditorer")},
            }
            for h in charges
        ]
    return [
        {
            "adresse": adresse,
            "dato_loebenummer": h.get("alias", ""),
            "prioritet": h.get("prioritet", ""),
            "dokumenttype": h.get("haeftelsestype", ""),
            "hovedstol": h.get("hovedstol", ""),
            "hovedstol_dkk": h.get("hovedstol", ""),
            "rentesats_pct": h.get("rente", ""),
            "rentetype": h.get("fastvariabel", ""),
            "kreditorer": "; ".join(h.get("kreditorer") or []),
            "dokument_version": h.get("version", ""),
            "dokument_uuid": h.get("uuid", ""),
            "ejendom_uuid": uuid,
        }
        for h in record.get("haeftelser") or []
    ]


def servitut_rows(record: dict, uuid: str, document: dict | None = None) -> list[dict]:
    """Easements - one row each. A flat can carry a dozen, so they get their
    own file rather than being crushed into a cell of the main CSV."""
    adresse = record.get("adresse", "")
    easements = (document or {}).get("servitutter")
    if easements:
        return [
            {
                "adresse": adresse,
                "paataleberettigede": _names(s["paataleberettigede"]),
                "ejendom_uuid": uuid,
                **{key: s.get(key, "") for key in SERVITUT_FIELDS
                   if key in s and key not in ("adresse", "paataleberettigede")},
            }
            for s in easements
        ]
    return [
        {
            "adresse": adresse,
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


def party_rows(document: dict | None, uuid: str) -> list[dict]:
    """Everyone named on any document against the property, one row each.

    This is the table the login is really for. A mortgage names a creditor and
    a debtor and often a notice-holder and an agent besides, each with a date
    of birth or a CVR number, and flattening them into one semicolon-joined
    cell throws away both who is which and how to find them again.
    """
    document = document or {}
    rows: list[dict] = []

    def add(art: str, doc_uuid: str, rolle: str, parties):
        for number, party in enumerate(parties or [], start=1):
            if party.get("navn") or party.get("cvr"):
                rows.append({
                    "dokumentart": art, "dokument_uuid": doc_uuid, "rolle": rolle,
                    "nummer": number, "ejendom_uuid": uuid,
                    **{key: party.get(key, "") for key in
                       ("navn", "foedselsdato", "cvr", "andel", "adresse_kode")},
                })

    adkomst = document.get("adkomst") or {}
    add("adkomst", adkomst.get("dokument_uuid", ""), "adkomsthaver", adkomst.get("ejere"))
    for h in document.get("haeftelser") or []:
        for key, rolle in HAEFTELSE_ROLLER.items():
            add("haeftelse", h["dokument_uuid"], rolle, h.get(key))
        for pledge in h.get("underpant") or []:
            add("underpant", pledge["dokument_uuid"], "underpanthaver",
                pledge.get("panthavere"))
    for s in document.get("servitutter") or []:
        add("servitut", s["dokument_uuid"], "paataleberettiget",
            s.get("paataleberettigede"))
    return rows


def underpant_rows(document: dict | None, uuid: str) -> list[dict]:
    """Sub-pledges: a mortgage deed pledged on in its own right."""
    return [
        {
            "ejendom_uuid": uuid,
            "haeftelse_uuid": h["dokument_uuid"],
            "panthavere": _names(pledge["panthavere"]),
            **{key: pledge.get(key, "") for key in
               ("dokument_uuid", "dato_loebenummer", "rettighed_uuid",
                "beloeb_dkk", "valuta", "prioritet")},
        }
        for h in (document or {}).get("haeftelser") or []
        for pledge in h.get("underpant") or []
    ]


def _amount(value) -> int | None:
    """A figure as a number, however the register happened to write it.

    The logged-in record states an amount as a number. The public lookup writes
    it the way the site prints it - "40.000 DKK" - and a formatted figure is
    still a figure, so both have to total the same.
    """
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(plain_number(str(value))))
    except ValueError:
        return None


def _names(parties) -> str:
    """The people on a document, joined for the eye rather than for a join."""
    return "; ".join(p["navn"] for p in parties or [] if p.get("navn"))


def _dkk(amount: str, valuta: str = "DKK") -> str:
    """26000 becomes "26.000 DKK" - the way the register writes it back."""
    if not amount:
        return ""
    try:
        number = f"{int(float(amount)):,}".replace(",", ".")
        return number + (f" {valuta}" if valuta else "")
    except ValueError:
        return str(amount)
