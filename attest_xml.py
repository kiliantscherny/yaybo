"""Read the register's OIO XML into something a database can hold.

`rest/ejdsummarisk` answers with a full OIO document - about 75 KB per flat,
across sixteen namespaces - and it is the richest thing logging in buys. It
carries what the public lookup does, plus every mortgage's creditors *and*
debtors with their dates of birth, sub-pledges, endorsement dates, interest
terms, and the subject codes on every easement.

Two readers live here, because a document this shape wants both:

    parse(raw)    the parts worth a column, named and flattened
    outline(raw)  the whole document as nested dict/list, namespaces stripped

`parse` is what the tables are built from. `outline` is what survives into the
`attester` table, so nothing is lost to a field this file forgot to name - it
is the same document, minus the namespace noise, in a form DuckDB can query
with json_extract rather than one that has to be re-parsed to be read at all.

Namespaces are stripped rather than honoured. The document declares sixteen of
them and puts sibling fields in different ones - a mortgage's amount is in ns7
while its type is in ns - so the prefixes describe which agency defined a field
rather than what the field means, and matching on them buys nothing.
"""

from __future__ import annotations

from xml.etree import ElementTree

import fields

# What the register calls each kind of transfer, in the spelling the site shows.
ADKOMST_TYPES = {
    "skoede": "Skøde",
    "endeligtskoede": "Endeligt skøde",
    "betingetskoede": "Betinget skøde",
    "auktionsskoede": "Auktionsskøde",
    "adkomsterklaering": "Adkomsterklæring",
}


def tag(element) -> str:
    """An element's name without its namespace."""
    return element.tag.rsplit("}", 1)[-1]


def dig(element, *names):
    """Follow a path of element names down, ignoring namespaces."""
    nodes = [element]
    for name in names:
        nodes = [child for node in nodes for child in node if tag(child) == name]
    return nodes


def first(nodes):
    return nodes[0] if nodes else None


def text(element, *names) -> str:
    if element is None:
        return ""
    found = dig(element, *names) if names else [element]
    return (found[0].text or "").strip() if found else ""


def number(element, *names):
    """An XML number, parsed here rather than passed on as text.

    These are xs:decimal, where a full stop is a decimal point - "3.500" is
    three and a half. The register's *rendered* attest writes Danish, where the
    same string is three thousand five hundred, and fields.plain_number reads
    it that way. Handing an XML value to that reader turns a 3.5% mortgage rate
    into 3500%, so the two dialects are kept apart: this returns a real number
    and lets the database take it as one.
    """
    raw = text(element, *names)
    if not raw:
        return ""
    try:
        value = float(raw)
    except ValueError:
        return raw
    return int(value) if value.is_integer() else value


def when(element, *names) -> str:
    return fields.iso_date(text(element, *names))


def parse(raw: str) -> dict:
    """The whole record, flattened into the parts a table wants.

    Returns {} when the payload is not the XML we expect, so a caller can fall
    back to reading the attest as a browser is shown it.
    """
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return {}
    ejendom = first(dig(root, "EjendomSummarisk"))
    if ejendom is None:
        return {}

    return {
        "ejendom": _ejendom(first(dig(ejendom, "EjendomStamoplysninger"))),
        "adkomst": _adkomst(first(dig(ejendom, "AdkomstSummariskSamling", "AdkomstSummarisk"))),
        "haeftelser": [
            _haeftelse(h)
            for h in dig(ejendom, "HaeftelseSummariskSamling", "HaeftelseSummarisk")
        ],
        "servitutter": [
            _servitut(s)
            for s in dig(ejendom, "ServitutSummariskSamling", "ServitutSummarisk")
        ],
        "udskrevet": when(first(dig(root, "UdskriftDatoTid"))),
    }


def _ejendom(stam) -> dict:
    """The property itself: what it is, where it is, and what it is worth."""
    if stam is None:
        return {}
    found = {
        "bfe_nr": text(stam, "EjendomIdentifikator", "BestemtFastEjendomNummer"),
        "ejerlejlighedsnr": text(
            stam, "EjendomIdentifikator", "EjendomType", "Ejerlejlighed",
            "Ejerlejlighedsnummer",
        ),
        "landsejerlav": text(
            stam, "EjendomIdentifikator", "Matrikel", "CadastralDistrictName"
        ),
        "landsejerlavkode": text(
            stam, "EjendomIdentifikator", "Matrikel", "CadastralDistrictIdentifier"
        ),
        "matrikelnr": text(stam, "EjendomIdentifikator", "Matrikel", "Matrikelnummer"),
        "akt_filnavn": text(stam, "EjendomIndskannetAktSamling", "DokumentFilnavnTekst"),
    }
    found.update(_adresse(first(dig(stam, "AdresseStruktur"))))

    # A share of the co-ownership, printed as the fraction it is.
    found["fordelingstal"] = _brøk(first(dig(stam, "Fordelingtal")))

    parcel = first(dig(stam, "MatrikelStrukturSamling", "MatrikelStruktur"))
    if parcel is not None:
        found["grund_areal_m2"] = number(parcel, "SpecificParcelAreaMeasure")
        found["vej_areal_m2"] = number(parcel, "RoadAreaMeasure")
        found["udstykningsdato"] = when(parcel, "LandParcelRegistrationDate")
        found["retskreds"] = text(parcel, "JurisdictionCode")

    vurdering = first(dig(stam, "EjendomVurderingSamling", "EjendomVurderingStruktur"))
    if vurdering is not None:
        found["kommunalt_ejendomsnr"] = text(
            vurdering, "RealPropertyStructure", "MunicipalRealPropertyIdentifier"
        )
        found["ejendomsvurdering_dkk"] = number(vurdering, "EjendomVaerdi")
        found["grundvaerdi_dkk"] = number(
            vurdering, "ParcelLandValueAssessmentCalculationAmount"
        )
        found["vurderingsdato"] = when(vurdering, "AssessmentChangedDate")

    # The flat's own date and floor area are not fields at all: they are headed
    # free text, the same slot the register uses for a note on an easement.
    # Match on the heading rather than on position.
    for heading, body in _tillaegstekst(stam):
        key = " ".join(heading.lower().split())
        if "dato" in key:
            found["opdelingsdato"] = fields.iso_date(body)
        elif "areal" in key:
            found["areal_m2"] = fields.plain_number(body)
    return found


def _adresse(node) -> dict:
    """The property's address. Unlike a party's, this one names its street."""
    if node is None:
        return {}
    specific = first(dig(node, "AddressSpecific"))
    return {
        "vejnavn": text(node, "StreetName"),
        "postnr": text(node, "PostCodeIdentifier"),
        "bynavn": text(node, "DistrictName"),
        "husnr": text(specific, "AddressAccess", "StreetBuildingIdentifier"),
        "etage": text(specific, "FloorIdentifier"),
        "doer": text(specific, "SuiteIdentifier"),
        "kommunekode": text(specific, "AddressAccess", "MunicipalityCode"),
        "vejkode": text(specific, "AddressAccess", "StreetCode"),
    }


def _adkomst(node) -> dict:
    """The deed in force: who owns the place, what they paid, and when."""
    if node is None:
        return {}
    deed = text(node, "AdkomstType")
    found = {
        "dokumenttype": ADKOMST_TYPES.get(deed, deed),
        "dokumenttype_kode": deed,
        "koebesum_dkk": number(node, "SkoedeKoebesum", "IAltKoebesum")
        or number(node, "SkoedeKoebesum", "KontantKoebesum"),
        "kontant_koebesum_dkk": number(node, "SkoedeKoebesum", "KontantKoebesum"),
        "valuta": text(node, "ValutaKode"),
        "overtagelsesdato": when(node, "SkoedeOvertagelsesDato"),
        "afgift_dkk": number(node, "TinglysningAfgiftBetalt"),
        "ejere": [_part(h, andel=_brøk(first(dig(h, "AndelIdeel"))))
                  for h in dig(node, "AdkomsthaverSamling", "Adkomsthaver")],
    }
    found.update(_dokument(node))
    return found


def _haeftelse(node) -> dict:
    """One charge against the property, with everyone attached to it."""
    found = {
        "dokumenttype": text(node, "HaeftelseType"),
        "formularkode": text(node, "HaeftelsePantebrevFormularLovpligtigKode"),
        "hovedstol_dkk": number(node, "HaeftelseBeloeb", "BeloebValuta", "BeloebVaerdi"),
        "valuta": text(node, "HaeftelseBeloeb", "BeloebValuta", "ValutaKode"),
        "laantype": text(node, "HaeftelseLaantypeKode"),
        "afgift_dkk": number(node, "TinglysningAfgiftBetalt"),
        "afgift_overfoert": _ja(node, "TinglysningAfgiftOverfoerselIndikator"),
        "konverteret_pantebrev": _ja(node, "KonverteretDigitalPantebrevIndikator"),
        "dokumenttype_beskrivelse": text(
            node, "DokumentInformationOverfoert", "DokumentTypeBeskrivelse"
        ),
        # A document may be registered behind any number of earlier rights.
        # The count is the useful part; the identifiers join to the easements.
        "respekterer": [
            text(r, "RettighedIdentifikator") for r in dig(node, "RespektSamling", "Respekt")
        ],
        "saerlige_vilkaar": [
            text(v) for v in dig(
                node, "HaeftelseSaerligeLaanevilkaarstypeSamling",
                "HaeftelseSaerligeLaanevilkaarstype",
            )
        ],
        "kreditorbetegnelse": " / ".join(
            text(a) for a in dig(node, "HaeftelseLaanKreditorbetegnelseTekst",
                                 "TekstGruppe", "Afsnit") if text(a)
        ),
        "kreditorer": [_part(r) for r in dig(node, "KreditorInformationSamling", "RolleInformation")],
        "debitorer": [_part(r) for r in dig(node, "DebitorInformationSamling", "RolleInformation")],
        "meddelelseshavere": [
            _part(r) for r in dig(node, "MeddelelseshaverInformationSamling", "RolleInformation")
        ],
        "fuldmagtshavere": [
            _part(r) for r in dig(node, "ImplicitFuldmagtSamling", "ImplicitFuldmagt",
                                  "FuldmagtHaverInformation")
        ],
        "underpant": [_underpant(u) for u in dig(node, "UnderpantrettighedSamling",
                                                 "Underpantrettighed")],
        "tekst": _fritekst(node),
    }
    found.update(_dokument(node))
    found.update(_rente(node))
    found.update(_rettighed(first(dig(node, "Pantrettighed"))))
    return found


def _rente(node) -> dict:
    """Interest, which the register states as either a fixed or a variable rate.

    A variable rate is a reference rate plus or minus a margin, and both halves
    matter: "CITA6 + 0.45" is a different loan from a flat 2.74 that happens to
    stand at the same number today.
    """
    fast = first(dig(node, "HaeftelseRente", "HaeftelseRenteFast"))
    variabel = first(dig(node, "HaeftelseRente", "HaeftelseRenteVariabel"))
    if fast is not None:
        return {
            "rentetype": "fast",
            "rentesats_pct": number(fast, "HaeftelseRentePaalydendeSats"),
            "rente_foreloebig": _ja(fast, "HaeftelseRenteSatsForeloebigIndikator"),
        }
    if variabel is not None:
        margin = first(dig(variabel, "HaeftelseReferenceRente",
                           "ReferenceRenteTillaegFradrag"))
        sign = -1 if text(margin, "TillaegFradragIndikator") == "fradrag" else 1
        pct = number(margin, "Procentsats") if margin is not None else ""
        return {
            "rentetype": "variabel",
            "rentesats_pct": number(variabel, "HaeftelseRentePaalydendeSats"),
            "reference_rente": text(variabel, "HaeftelseReferenceRente", "ReferenceRenteNavn"),
            "reference_rente_pct": number(
                variabel, "HaeftelseReferenceRente", "ReferenceRenteSats"
            ),
            "rente_margin_pct": sign * pct if pct != "" else "",
        }
    # Charges carried over from the paper register state a rate and nothing else.
    overfoert = number(node, "HaeftelseRenteOverfoert")
    return {"rentetype": "overfoert", "rentesats_pct": overfoert} if overfoert else {}


def _underpant(node) -> dict:
    """A pledge of the mortgage deed itself - a charge on a charge."""
    found = {
        "beloeb_dkk": number(node, "UnderpantBeloeb", "BeloebVaerdi"),
        "valuta": text(node, "UnderpantBeloeb", "ValutaKode"),
        "prioritet": text(node, "PrioritetNummer"),
        "rettighed_uuid": text(node, "RettighedIdentifikator"),
        "panthavere": [
            _part(r) for r in dig(node, "UnderpanthaverInformationSamling", "RolleInformation")
        ],
    }
    found.update(_dokument(node))
    return found


def _servitut(node) -> dict:
    """One easement: what it restricts, and who may enforce it."""
    info = first(dig(node, "ServitutInformation"))
    found = {
        "dokumenttype": text(node, "ServitutType"),
        "ogsaa_lyst_paa": text(node, "OgsaaLystPaaSamling", "OgsaaLystPaaAntal"),
        "afgift_dkk": number(node, "TinglysningAfgiftBetalt"),
        "paataleberettigede": [
            _part(p) for p in dig(node, "PaataleberettigetSamling", "Paataleberettiget")
        ],
        "tekst": text(node, "ServitutTekstSummarisk") or _fritekst(node),
        "akt_filnavn": text(node, "DokumentInformationOverfoert", "DokumentFilnavnTekst"),
    }
    found.update(_dokument(node))
    found.update(_rettighed(first(dig(node, "Servitutrettighed"))))
    found.update(_servitut_indhold(info))
    return found


def _servitut_indhold(info) -> dict:
    """What an easement is *about*, as the register's own subject codes.

    The codes sit two levels down under a per-subject wrapper - færdsel,
    bebyggelse, ledninger and so on - and the register adds subjects over time.
    Collecting every element whose name ends in "Kode" picks up the ones added
    after this was written, which naming them one by one would not.
    """
    if info is None:
        return {}
    codes = [
        (node.text or "").strip()
        for afsnit in info
        for indhold in afsnit
        for node in indhold
        if tag(node).endswith("Kode") and (node.text or "").strip()
    ]
    return {
        "indhold": sorted(set(codes)),
        "uden_ejers_tiltraedelse": _ja(
            info, "ServitutKanTinglysesUdenEjersTiltraedelseIndikator"
        ),
        "prioritet_forud": _ja(
            info, "ServitutKanTinglysesMedPrioritetForudForGaeldOgServitutterIndikator"
        ),
        "betydning_for_vaerdi": _ja(
            info, "ServitutHarBetydningForEjendommensVaerdiIndikator"
        ),
    }


def _part(node, *, andel: str = "") -> dict:
    """Whoever is on one end of a document: a person or a company.

    A person is identified by a date of birth and a company by its CVR number,
    and the register never gives both, so which key is filled says which one
    this is.
    """
    person = first(dig(node, "PersonSimpelIdentifikator"))
    company = first(dig(node, "VirksomhedSimpelIdentifikator"))
    found = {"navn": "", "foedselsdato": "", "cvr": "", "andel": andel}
    if person is not None:
        found["navn"] = text(person, "PersonName")
        found["foedselsdato"] = when(person, "BirthDate")
    elif company is not None:
        found["navn"] = text(company, "LegalUnitName")
        found["cvr"] = text(company, "CVRnumberIdentifier")
    else:
        # A påtaleberettiget carries its name inline rather than wrapped.
        found["navn"] = text(node, "LegalUnitName") or text(node, "PersonName")
        found["cvr"] = text(node, "CVRnumberIdentifier")
        found["foedselsdato"] = when(node, "BirthDate")
    found["adresse_kode"] = _adresse_kode(first(dig(node, "AddressSpecific")))
    return found


def _adresse_kode(node) -> str:
    """A party's address, which the register gives only as codes.

    There is no street name on these - just a municipality code, a street code
    and a building number - so this cannot be turned into a postal address. It
    is kept because it is what tells two people of the same name apart.
    """
    if node is None:
        return ""
    access = first(dig(node, "AddressAccess"))
    parts = [
        text(access, "MunicipalityCode"), text(access, "StreetCode"),
        text(access, "StreetBuildingIdentifier"), text(node, "FloorIdentifier"),
        text(node, "SuiteIdentifier"),
    ]
    return "-".join(p for p in parts if p)


def _dokument(node) -> dict:
    """The identifiers every document in the register carries."""
    return {
        "dokument_uuid": text(node, "DokumentRevisionIdentifikator", "DokumentIdentifikator"),
        "dokument_version": text(node, "DokumentRevisionIdentifikator", "RevisionNummer"),
        "tinglysningsdato": when(node, "TinglysningsDato"),
        "senest_paategnet": when(node, "SenestPaategnetDato"),
        "dato_loebenummer": text(node, "DokumentAlias", "DokumentAliasIdentifikator")
        or text(node, "DokumentAlias", "AktHistoriskIdentifikator"),
        "overfoert": _ja(node, "DokumentOverfoertIndikator"),
    }


def _rettighed(node) -> dict:
    """A right's own identity and its place in the queue of claims."""
    if node is None:
        return {}
    return {
        "rettighed_uuid": text(node, "RettighedIdentifikator"),
        "prioritet": text(node, "PrioritetNummer"),
    }


def _tillaegstekst(node):
    """Every (heading, body) pair in a document's free-text section."""
    for group in dig(node, "TillaegstekstSamling", "TekstAngivelse", "TekstGruppe"):
        heading = text(group, "Overskrift")
        body = "\n".join(text(a) for a in dig(group, "Afsnit") if text(a))
        if heading or body:
            yield heading, body


def _fritekst(node) -> str:
    """A document's free text, headings and all, as one readable block."""
    blocks = []
    for heading, body in _tillaegstekst(node):
        # "Tillægstekst" is the generic heading and says nothing; a real one
        # like "Afgiftspantebrev" changes what the text below it means.
        if heading and heading.lower() not in ("tillægstekst", "tillaegstekst"):
            blocks.append(f"{heading}: {body}" if body else heading)
        elif body:
            blocks.append(body)
    return "\n".join(blocks)


def _brøk(node) -> str:
    """"1/300" out of a Taeller and a Naevner."""
    if node is None:
        return ""
    over, under = text(node, "Taeller"), text(node, "Naevner")
    return f"{over}/{under}" if over and under else ""


def _ja(node, *names) -> str:
    """An OIO boolean, kept as "true"/"false" text so empty stays distinct."""
    value = text(node, *names).lower()
    return value if value in ("true", "false") else ""


def outline(raw: str):
    """The whole document as nested dict/list, with the namespaces dropped.

    This is what keeps the record honest: `parse` names the fields worth a
    column, and this keeps everything else, so a field nobody thought to name
    is still there to be found later. Repeated siblings become a list, an
    element with only text becomes that text, and an empty one becomes None.
    """
    try:
        return _outline(ElementTree.fromstring(raw))
    except ElementTree.ParseError:
        return None


def _outline(element):
    children = list(element)
    if not children:
        return (element.text or "").strip() or None
    found: dict = {}
    for child in children:
        name = tag(child)
        value = _outline(child)
        if name in found:
            # A repeated tag is a list, and stays a list once it becomes one.
            if not isinstance(found[name], list):
                found[name] = [found[name]]
            found[name].append(value)
        else:
            found[name] = value
    return found


def summary(raw: str) -> dict:
    """The flat shape the CSV columns and the owner join were built around.

    Kept because two readers feed those columns - this one and the reader for
    the attest as a browser is shown it - and they have to agree on the names.
    """
    record = parse(raw)
    if not record:
        return {}
    ejendom, adkomst = record["ejendom"], record["adkomst"]
    # The flat columns have always been text, and a CSV cannot tell the
    # difference anyway; the typed copies go to the database from parse().
    def as_text(value):
        return "" if value == "" else str(value)

    found = {
        "bfe_nr": ejendom.get("bfe_nr", ""),
        "ejerlejlighedsnr": ejendom.get("ejerlejlighedsnr", ""),
        "fordelingstal": ejendom.get("fordelingstal", ""),
        "opdelingsdato": ejendom.get("opdelingsdato", ""),
        "areal_m2": as_text(ejendom.get("areal_m2", "")),
        "adkomst_dokumenttype": adkomst.get("dokumenttype", ""),
        "adkomst_dato_loebenummer": adkomst.get("dato_loebenummer", ""),
        "koebesum_dkk": as_text(adkomst.get("koebesum_dkk", "")),
        "overtagelsesdato": adkomst.get("overtagelsesdato", ""),
        "owners": {
            fields.normalise(owner["navn"]): (
                {"cvr": owner["cvr"]} if owner["cvr"]
                else {"foedselsdato": owner["foedselsdato"]}
            )
            for owner in adkomst.get("ejere") or []
            if owner["navn"]
        },
    }
    return {key: value for key, value in found.items() if value != ""}
