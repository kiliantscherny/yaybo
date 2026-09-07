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

from datetime import date

from yaybo.register import attest_xml, historik
from yaybo.register.address import unit_label
from yaybo.register.fields import iso_date, normalise, plain_number

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
    max_owners: int, *, with_attest: bool = False, with_afledt: bool = False
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
        *(AFLEDT_FIELDS if with_afledt else []),
        "uuid",
    ]


# What is worked out for a property rather than read off it: the totals
# against the charges, the newest transfer lifted off the history, and where
# DAWA says the address is. Kept together so the CSV and the database agree.
AFLEDT_FIELDS = [
    "boligtype", "boligareal_m2",
    "seneste_salg_dato", "seneste_salg_dkk", "seneste_salg_pris_m2",
    "samlet_gaeld_dkk", "frivaerdi_dkk", "belaaningsgrad_pct",
    "breddegrad", "laengdegrad", "adresse_uuid",
]
RENTE_FIELDS = [
    "maaned", "laantype", "rentfix_kode", "effektiv_rente_pct", "bidrag_pct",
    "kupon_pct",
]
HANDEL_FIELDS = [
    "adresse", "dato", "beloeb_dkk", "areal_m2", "boligareal_m2", "pris_pr_m2",
    "pris_pr_m2_tinglyst", "handelstype", "registrering_id", "ejendom_uuid",
]

def dawa_row(entry: dict | None) -> dict:
    """Where DAWA says the address is, for a property or a share alike.

    Written with the empties left out, so an address DAWA has no adgangspunkt
    for leaves the row alone rather than blanking it.
    """
    if not entry:
        return {}
    found = {
        "adresse_uuid": entry.get("uuid", ""),
        "breddegrad": entry.get("breddegrad"),
        "laengdegrad": entry.get("laengdegrad"),
    }
    return {key: value for key, value in found.items() if value not in (None, "")}


def handel_rows(
    entries: list[dict],
    uuid: str,
    adresse: str,
    areal_m2=None,
    boligareal_m2=None,
    current: dict | None = None,
) -> list[dict]:
    """Every recorded transfer of the property, newest first, read as a sale.

    Two sources, because the register keeps the present and the past apart.
    `entries` is the historisk adkomst, which lists **previous** owners only.
    The transfer that put the current owner there is not in it - it is the
    adkomst in force, on the property's own row - so `current` is that row and
    without it every property is missing its most recent sale, which is the one
    anybody actually wants.

    `handelstype` is the register's own word for the document that made the
    transfer, expanded from the code it states it as: "Endeligt skoede" reads
    as "Endeligt skøde", the same way the attest reader already renders the
    adkomst in force.

    Two prices per square metre, because there are two areas and they are not
    the same measure. `pris_pr_m2` divides by the BBR living area, which is
    what a listing quotes; `pris_pr_m2_tinglyst` by the register's own
    tinglyste areal. Either is empty when its area is, rather than being
    computed against the other one and quietly answering a different question.

    Needs a login, because both halves of it do.
    """
    tinglyst, bolig = _amount(areal_m2), _amount(boligareal_m2)
    sales = [
        _sale(entry, uuid, adresse, tinglyst, bolig)
        for entry in entries
        if entry.get("dato") or entry.get("koebesum_dkk")
    ]
    now = _current_sale(current or {}, uuid, adresse, tinglyst, bolig)
    if now:
        sales.append(now)
    # Newest first, and anything undated sinks rather than being dropped.
    sales.sort(key=lambda sale: sale.get("dato") or "", reverse=True)
    return sales


def _current_sale(row: dict, uuid: str, adresse: str, tinglyst, bolig) -> dict | None:
    """The adkomst in force, as a sale row, or None when it was not a purchase.

    An adkomst with no price behind it - an inheritance, a division - is a
    transfer but not a sale, and belongs in adkomsthistorik rather than here.
    """
    paid = _amount(row.get("koebesum_dkk"))
    alias = str(row.get("adkomst_dato_loebenummer") or "")
    dato = _alias_date(alias) or str(row.get("overtagelsesdato") or "")
    if not paid or not dato:
        return None
    return {
        "ejendom_uuid": uuid,
        "adresse": adresse,
        "dato": dato,
        "beloeb_dkk": paid,
        "areal_m2": tinglyst,
        "boligareal_m2": bolig,
        "pris_pr_m2": round(paid / bolig) if bolig and paid else None,
        "pris_pr_m2_tinglyst": round(paid / tinglyst) if tinglyst and paid else None,
        "handelstype": row.get("adkomst_dokumenttype") or "",
        # The register's own identifier for the document, which is stable and
        # cannot collide with the history's position numbers.
        "registrering_id": alias or "aktuel",
    }


def _alias_date(alias: str) -> str:
    """The date out of a "20260515-1017732059" document alias.

    That leading eight is the day the document was registered, which is the
    same thing adkomsthistorik dates its entries by - so the two halves of the
    sales table are dated the same way rather than one by registration and one
    by handover.
    """
    head = alias.split("-")[0]
    if len(head) != 8 or not head.isdigit():
        return ""
    try:
        return date(int(head[:4]), int(head[4:6]), int(head[6:])).isoformat()
    except ValueError:
        return ""


def _sale(entry: dict, uuid: str, adresse: str, tinglyst, bolig) -> dict:
    """One historical transfer as a sale row, priced against both areas."""
    paid = _amount(entry.get("koebesum_dkk"))
    return {
        "ejendom_uuid": uuid,
        "adresse": adresse,
        "dato": entry.get("dato", ""),
        "beloeb_dkk": entry.get("koebesum_dkk"),
        "areal_m2": tinglyst,
        "boligareal_m2": bolig,
        "pris_pr_m2": round(paid / bolig) if bolig and paid else None,
        "pris_pr_m2_tinglyst": round(paid / tinglyst) if tinglyst and paid else None,
        "handelstype": attest_xml.adkomst_type(entry.get("dokumenttype", "")),
        "registrering_id": str(entry.get("post_nummer", "")),
    }


def latest_sale_row(sales: list[dict]) -> dict:
    """The newest of the sales already built, flattened onto the property row.

    Takes the rows rather than the raw history, so the adkomst in force is
    considered too - it is normally the newest sale there is, and leaving it
    out was what made seneste_salg_* the second-newest for nearly every
    property.
    """
    dated = [sale for sale in sales if sale.get("dato")]
    if not dated:
        return {}
    latest = max(dated, key=lambda sale: sale["dato"])
    return {
        "seneste_salg_dato": latest.get("dato", ""),
        "seneste_salg_dkk": latest.get("beloeb_dkk"),
        "seneste_salg_pris_m2": latest.get("pris_pr_m2"),
        "seneste_salg_pris_m2_tinglyst": latest.get("pris_pr_m2_tinglyst"),
    }


def bbr_row(bolig: dict) -> dict:
    """What BBR adds to the property's own row: the flat's area and its type."""
    if not bolig:
        return {}
    found = {
        "boligareal_m2": bolig.get("boligareal_m2"),
        "boligtype": bolig.get("boligtype") or "",
    }
    return {key: value for key, value in found.items() if value not in (None, "")}


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
    # A party belongs to a document and a role, not to a charge, and the two
    # are not the same thing: the register lists one document once per charge
    # it secures, and an amended document appears under each of its versions.
    # An ejerpantebrev raised twice therefore hands the same four people over
    # twice - and, seen in the wild, with the creditors in a different order
    # each time. Gathering them per (art, document, role) merges those repeats
    # while keeping anyone who really is only on one of them.
    groups: dict[tuple[str, str, str], dict[tuple, dict]] = {}

    def add(art: str, doc_uuid: str, rolle: str, parties):
        found = groups.setdefault((art, doc_uuid, rolle), {})
        for party in parties or []:
            if party.get("navn") or party.get("cvr"):
                # First spelling of a person wins; later ones are the same
                # person read again, not new information.
                found.setdefault(_same_party(party), party)

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

    # Numbered only now that every reading of a document has been folded in, so
    # the number counts people rather than the order one reading happened to
    # arrive in - which is what makes it safe to key the stored table on.
    return [
        {
            "dokumentart": art, "dokument_uuid": doc_uuid, "rolle": rolle,
            "nummer": number, "ejendom_uuid": uuid,
            **{key: party.get(key, "") for key in
               ("navn", "foedselsdato", "cvr", "andel", "adresse_kode")},
        }
        for (art, doc_uuid, rolle), found in groups.items()
        for number, party in enumerate(found.values(), start=1)
    ]


def _same_party(party: dict) -> tuple[str, str, str]:
    """What makes two named parties on one document the same party.

    A name alone is not enough - two people can share one, and the register
    does not promise to spell either the same way twice. A date of birth or a
    CVR number is what actually distinguishes them, and it is exactly what the
    logged-in record is fetched for.
    """
    return (
        normalise(str(party.get("navn") or "")),
        str(party.get("foedselsdato") or ""),
        str(party.get("cvr") or ""),
    )


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


# The andelsboligbog, which is a different book about a different thing. A
# share is not land: it has no valuation, no matrikel and no easements, so it
# gets two tables rather than the twelve a property fills, and both are thin
# on purpose rather than for want of reading.


def andel_row(
    record: dict,
    uuid: str,
    ejendom_uuid: str = "",
    bygning_adresse: str = "",
) -> dict:
    """One co-op share, as a row.

    `ejendom_uuid` is the association's property in the tingbog, when the same
    address lookup found it. It is the whole reason the two books are worth
    fetching together: on its own a share is an address and a debt, but joined
    to the building it carries the association's own mortgages, its easements
    and the public valuation of the block those are charged against.
    """
    adresse = record.get("adresse", "")
    kommune_vej = record.get("kommuneVej") or {}
    return {
        "uuid": uuid,
        "adresse": adresse,
        "lejlighed": unit_label(adresse),
        "kommunekode": kommune_vej.get("kommuneKode", ""),
        "vejkode": kommune_vej.get("vejKode", ""),
        "ejendom_uuid": ejendom_uuid,
        "bygning_adresse": bygning_adresse,
        "antal_haeftelser": len(record.get("haeftelser") or []),
        "antal_meddelelser": len(record.get("meddelelser") or []),
    }


def andel_haeftelse_rows(record: dict, uuid: str) -> list[dict]:
    """Charges registered against one share, one row each.

    The same reading as the public half of `haeftelse_rows`, because the book
    states these in exactly the same fields. There is no logged-in half to
    fall back from: the tingbog's separated rates and counted sub-pledges are
    read out of the attest XML, and a share has no attest.
    """
    adresse = record.get("adresse", "")
    return [
        {
            "andel_uuid": uuid,
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
        }
        for h in record.get("haeftelser") or []
    ]


def andel_meddelelse_rows(record: dict, uuid: str) -> list[dict]:
    """Notices noted on one share, one row each.

    The only place the andelsboligbog names anyone but a creditor: a notice is
    the register recording something that has happened to the andelshaver -
    a death, a bankruptcy, a court taking away their power to dispose of the
    share - and it names them, and whoever may now act for them.

    Built from the fields the register's own public view of a share reads,
    because no share sampled while writing this carried a notice. Everything
    is optional in consequence, which is also how it should behave: a book
    that has nothing to say about somebody says nothing.
    """
    adresse = record.get("adresse", "")
    return [
        {
            "andel_uuid": uuid,
            "adresse": adresse,
            "dato_loebenummer": m.get("alias", ""),
            "prioritet": m.get("prioritet", ""),
            "dokumenttype": m.get("dokumenttype", ""),
            # The one date here, and the format it arrives in is unknown - the
            # register writes dates three ways. iso_date knows all three.
            "afgoerelsesdato": iso_date(str(m.get("afgoerelsesdato") or "")),
            "debitorer": "; ".join(m.get("debitorer") or []),
            "disponenter": "; ".join(m.get("disponenter") or []),
            "tillaegstekst": m.get("tillaegstekst", ""),
            "dokument_uuid": m.get("uuid", ""),
            "dokument_version": m.get("version", ""),
        }
        for m in record.get("meddelelser") or []
    ]


def add_andel_debt(andele: list[dict], charges: list[dict]) -> None:
    """Total what is charged against each share.

    Only the total, where a property also gets equity and a loan-to-value.
    Both of those divide by the public valuation and a share has none: what an
    andel may be sold for is set by the association's own accounts under
    andelsboligloven, which is not a register and not something this reads.

    Nor is this what living there owes. An andelshaver also owes their share
    of the association's own mortgage, which is registered against the
    building in the tingbog and never appears in this book at all.
    """
    debt: dict[str, int] = {}
    for charge in charges:
        amount = _amount(charge.get("hovedstol_dkk"))
        if amount is not None:
            key = charge["andel_uuid"]
            debt[key] = debt.get(key, 0) + amount

    for row in andele:
        row["samlet_gaeld_dkk"] = debt.get(row.get("uuid", ""), 0)


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
