"""What the interface says, in English or in Danish.

The data is never translated, and that is the whole shape of this module. A
register record is Danish - "Ejerpantebrev", "Almindeligt salg", the wording of
an easement - and reading it is a Danish-language job however the buttons
around it are labelled. Anglicising a value would make the database disagree
with the register it came from, and `store.TABLES` keeps the register's own
vocabulary for exactly that reason.

So only the chrome moves: headings, column labels, tabs, footer keys, the
sentences a screen writes about what it is showing. Everything a row contains
stays as the register wrote it.

English is the source language. The string in the code is the English one and
the catalogue below translates it into Danish, which has two consequences
worth having: the default language needs no lookup at all, and a translation
nobody has written yet degrades into readable English rather than into a key.

Nothing here may be resolved at import time. Screens declare their labels as
module constants - `COLUMNS`, `PLACES`, `MEASURES` - and those are built once,
before anyone has chosen a language. Translation therefore happens where a
string is *used*, and changing language rebuilds the screens rather than the
constants.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

DEFAULT = "en"

# Each language in its own name, because that is what somebody looking for it
# will recognise. Order is the order they are offered in.
LANGUAGES = (("en", "English"), ("da", "Dansk"))
CODES = tuple(code for code, _ in LANGUAGES)

_current = DEFAULT


# The catalogue: English as written in the code, Danish as it should read.
# Grouped the way the screens are, because that is how it gets maintained.
DANISH: dict[str, str] = {
    # ── where you are: the tabs, and the footer keys that reach them ──
    "Properties": "Ejendomme",
    "Co-op shares": "Andele",
    "Buildings": "Bygninger",
    "Figures": "Nøgletal",
    "Queue": "Kø",
    "Search": "Søg",
    "Find new": "Find ny",
    "Quit": "Afslut",
    "Log in": "Log ind",
    "Language": "Sprog",
    "Stop fetching": "Stop hentning",
    "Auto-fetch on/off": "Auto-hent til/fra",
    # ── things every screen says: the footer verbs and the shared words ──
    "Open": "Åbn",
    "Tick": "Markér",
    "Tick all": "Markér alle",
    "Tick none": "Fjern markering",
    "Re-fetch": "Hent igen",
    "Export": "Eksportér",
    "Sort by": "Sortér efter",
    "Reverse": "Omvendt",
    "Reload": "Genindlæs",
    "Clear filter": "Ryd filter",
    "Back": "Tilbage",
    "all": "alle",

    # ── column headings. The register's own words, which is why several of
    # these are shorter in Danish than the English they translate. ──
    "Address": "Adresse",
    "Postcode": "Postnr",
    "Town": "By",
    "Area": "Areal",
    "Valuation": "Vurdering",
    "Debt": "Gæld",
    "LTV": "Belånt",
    "Owners": "Ejere",
    "Fetched": "Hentet",
    "Floor": "Etage",
    "Building": "Bygning",
    "Street": "Vej",

    # ── the properties screen ──
    "address or owner": "adresse eller ejer",
    "＋ Find new property": "＋ Find ny ejendom",
    "Sorted by {name}.": "Sorteret efter {name}.",
    "{n} ticked · ": "{n} markeret · ",
    "{n} property": "{n} ejendom",
    "{n} properties": "{n} ejendomme",
    "{shown} of {held}": "{shown} af {held}",
    "Already fetched — searching here never leaves the database. "
    "Sorted by {name} {arrow} (o, i) · ＋ for a new address.":
        "Allerede hentet — en søgning her forlader aldrig databasen. "
        "Sorteret efter {name} {arrow} (o, i) · ＋ for en ny adresse.",
    "Nothing fetched yet.\n\nPress / to look an address up, "
    "or b for the queue.\n\n{path} does not exist.":
        "Der er ikke hentet noget endnu.\n\nTryk / for at slå en adresse op, "
        "eller b for køen.\n\n{path} findes ikke.",
    "Nothing in your library matches {typed!r}.\n\n"
    "Press enter to search the register for it,\n"
    "or escape to clear the filter.":
        "Intet i din database passer på {typed!r}.\n\n"
        "Tryk enter for at søge i registret efter det,\n"
        "eller escape for at rydde filteret.",
    "Nothing matches that filter.": "Intet passer på det filter.",
    "Nothing to re-fetch.": "Der er intet at hente igen.",
    "Queued {n} to re-fetch. {note}": "Lagt {n} i kø til ny hentning. {note}",
    "Those are already in the queue.": "De ligger allerede i køen.",
    "Export {n} ticked": "Eksportér {n} markeret",
    "Export the whole database": "Eksportér hele databasen",
    "There is nothing to export yet.": "Der er ikke noget at eksportere endnu.",

    # ── the andelsboligbog: the shares list and one share in full ──
    "Charges": "Hæftelser",
    "Notices": "Meddelelser",
    "Debt/m²": "Gæld/m²",
    "For sale": "Til salg",
    "Its building": "Dens bygning",
    "address": "adresse",
    "yes": "ja",
    "no": "nej",
    "share": "andel",
    "{n} share": "{n} andel",
    "{n} shares": "{n} andele",
    "{shares} · {owed} charged against them. Not what they owe: a "
    "share of the association's own mortgage sits against the "
    "building in the tingbog. enter opens the share and everyone "
    "named on its charges, g opens the building.":
        "{shares} · {owed} hæftet på dem. Ikke hvad de skylder: en andel af "
        "foreningens eget lån ligger på bygningen i tingbogen. enter åbner "
        "andelen og alle, der er nævnt på dens hæftelser, g åbner bygningen.",
    "Nothing from the andelsboligbog yet.\n\n"
    "Look an address up on Search - a co-op building answers "
    "with its shares as well as the association's property.":
        "Intet fra andelsboligbogen endnu.\n\n"
        "Slå en adresse op under Søg - en andelsejendom svarer med sine "
        "andele såvel som foreningens ejendom.",
    "No share matches that.": "Ingen andel passer på det.",
    "No building found for this share.": "Ingen bygning fundet til denne andel.",
    "No building recorded for this share.": "Ingen bygning noteret på denne andel.",
    "Queued 1 share. {note}": "Lagt 1 andel i kø. {note}",
    "Nothing stored for this share yet.": "Der er intet gemt om denne andel endnu.",
    "That share is no longer in the database.":
        "Den andel findes ikke længere i databasen.",
    "Export this share": "Eksportér denne andel",

    # ── a charge or a notice, column by column ──
    "Date/serial": "Dato/løbenr.",
    "Principal": "Hovedstol",
    "Rate type": "Rentetype",
    "Rate": "Rente",
    "Creditors": "Kreditorer",
    "Decided": "Afgjort",
    "Debtors": "Debitorer",
    "Authorised": "Disponenter",
    "Additional text": "Tillægstekst",

    # ── one share's own screen ──
    "Loading…": "Indlæser…",
    "Overview": "Oversigt",
    "fetched {when}": "hentet {when}",
    "The share": "Andelsboligen",
    "Floor/door": "Etage/dør",
    "Municipality code": "Kommunekode",
    "Street code": "Vejkode",
    "Living area": "Boligareal",
    "Property type": "Boligtype",
    "Charges on the share": "Hæftelser på andelen",
    "Count": "Antal",
    "Total debt": "Samlet gæld",
    "The association's property": "Foreningens ejendom",
    "Property": "Ejendom",
    "Ejerpantebrev issued to": "Udstedt ejerpantebrev til",
    "Name": "Navn",
    "What the book does not hold": "Hvad bogen ikke har",
    "An andel is not real property, so the andelsboligbog "
    "records no valuation, no matrikel, no area and no "
    "easements for it, and no owner: it registers rights over "
    "a share, not title to one. Who holds it is the "
    "association's record.\n\n"
    "The total debt above is what is charged against this "
    "share alone. It is not what living here owes - an "
    "andelshaver also owes a portion of the association's own "
    "mortgage, which is registered against the building. "
    "Press g for that.":
        "En andel er ikke fast ejendom, så andelsboligbogen noterer hverken "
        "vurdering, matrikel, areal eller servitutter for den - og ingen "
        "ejer: den tinglyser rettigheder over en andel, ikke adkomst til "
        "den. Hvem der har den, står i foreningens egne papirer.\n\n"
        "Samlet gæld ovenfor er, hvad der hæfter på denne andel alene. Det "
        "er ikke, hvad det koster at bo her - en andelshaver skylder også "
        "sin del af foreningens eget lån, som er tinglyst på bygningen. "
        "Tryk g for den.",

    # ── things more than one screen says ──
    "Re-fetching {address}…": "Henter {address} igen…",
    "Could not re-fetch: {error}": "Kunne ikke hente igen: {error}",
    "Re-fetched.": "Hentet igen.",

    # ── the buildings screen ──
    "Val./m²": "Vurd./m²",
    "Re-fetch all": "Hent alle igen",
    "Its properties": "Dens ejendomme",
    "✓ yes": "✓ ja",
    "✗ no": "✗ nej",
    "{n} building": "{n} bygning",
    "{n} buildings": "{n} bygninger",
    "{properties} in {buildings} · enter opens a building's "
    "properties, k its figures, f re-fetches all of it.":
        "{properties} i {buildings} · enter åbner en bygnings ejendomme, "
        "k dens nøgletal, f henter det hele igen.",
    "Nothing fetched yet.\n\nPress / to look an address up.":
        "Der er ikke hentet noget endnu.\n\nTryk / for at slå en adresse op.",
    "No building matches that.": "Ingen bygning passer på det.",
    "Queued {n} from {where}. {note}": "Lagt {n} fra {where} i kø. {note}",

    # ── the queue ──
    "Rows": "Rækker",
    "Start": "Start",
    "Retry": "Prøv igen",
    "Remove": "Fjern",
    "Auto-fetch": "Auto-hent",
    "▶  Start": "▶  Start",
    "■  Stop": "■  Stop",
    "Auto-fetch: on": "Auto-hent: til",
    "Auto-fetch: off": "Auto-hent: fra",
    "Those have not fetched anything yet.": "De har ikke hentet noget endnu.",
    "Nothing in the database for those yet.":
        "Der er intet i databasen for dem endnu.",
    "Export {n} from the queue": "Eksportér {n} fra køen",
    "Nothing queued.\n\nPress / to find a property, tick what you "
    "want\nand press f to send it here.":
        "Intet i kø.\n\nTryk / for at finde en ejendom, markér hvad du vil "
        "have\nog tryk f for at sende det hertil.",
    "Auto-fetch is on - queued properties start straight away.":
        "Auto-hent er til - ejendomme i kø starter med det samme.",
    "Auto-fetch is off - queued properties wait here to be started.":
        "Auto-hent er fra - ejendomme i kø venter her på at blive startet.",
    "fetching {what}": "henter {what}",
    "running": "kører",
    "{n} held": "{n} parkeret",
    "{n} waiting": "{n} venter",
    "idle": "inaktiv",
    " · {n} failed, r retries": " · {n} fejlede, r prøver igen",
    "{chosen}{jobs} jobs, {done} done · {fetched} of {total} "
    "properties · {rows} rows · {state}{failed}":
        "{chosen}{jobs} job, {done} færdige · {fetched} af {total} "
        "ejendomme · {rows} rækker · {state}{failed}",

    # ── the progress bar every screen carries, and the session line ──
    "Stop": "Stop",
    "stopping after this one…": "stopper efter denne…",
    "starting…": "starter…",
    "  ·  {n} failed": "  ·  {n} fejlede",
    "Not logged in - public register only, ctrl+L to log in":
        "Ikke logget ind - kun det offentlige register, ctrl+L for at logge ind",
    "MitID: {who}": "MitID: {who}",
    "MitID: {who} - session lapsing now": "MitID: {who} - sessionen udløber nu",
    "MitID: {who} - {n} min left": "MitID: {who} - {n} min tilbage",

    # ── the SQL screen ──
    "Run": "Kør",
    "Export result": "Eksportér resultat",
    "ctrl+R runs · ctrl+E exports the result":
        "ctrl+R kører · ctrl+E eksporterer resultatet",
    "Run a query first.": "Kør en forespørgsel først.",

    # ── the figures: measures, how they are combined, what they group by ──
    "Valuation per m²": "Vurdering pr. m²",
    "Equity": "Friværdi",
    "Loan-to-value": "Belåningsgrad",
    "Sale price per m²": "Salgspris pr. m²",
    "Sale price": "Salgspris",
    "Number of sales": "Antal handler",
    "Owners' age": "Ejernes alder",
    "Owners per property": "Ejere pr. ejendom",
    "Interest rate": "Rentesats",
    "Charges per property": "Hæftelser pr. ejendom",
    "Year built": "Opførelsesår",
    "Rooms": "Værelser",
    "Median": "Median",
    "Mean": "Gennemsnit",
    "Total": "I alt",
    "Per property": "Pr. ejendom",
    "MitID data": "MitID-data",

    # ── the pre-made analyses ──
    "Prices over time": "Priser over tid",
    "Compare groups": "Sammenlign grupper",
    "Debt and equity": "Gæld og friværdi",
    "The owners": "Ejerne",
    "The buildings": "Bygningerne",
    "Value over time": "Værdi over tid",
    "The headline numbers for this selection, all on one page.":
        "Hovedtallene for dette udvalg, samlet på én side.",
    "What a square metre has cost, year by year, from the recorded sales.":
        "Hvad en kvadratmeter har kostet, år for år, ud fra de tinglyste handler.",
    "The same figure side by side - floor against floor, or street "
    "against street.":
        "Det samme tal side om side - etage mod etage, eller vej mod vej.",
    "What is owed against these, what is left over, and how heavily "
    "they are borrowed against.":
        "Hvad der hæfter på dem, hvad der er tilbage, og hvor hårdt de er "
        "belånt.",
    "How many people own each of these, and how old they are.":
        "Hvor mange der ejer hver af dem, og hvor gamle de er.",
    "When they were built and how many rooms they hold.":
        "Hvornår de er opført, og hvor mange værelser de har.",
    "Sale prices as a series, for one building or a whole postcode.":
        "Salgspriser som en serie, for én bygning eller et helt postnummer.",

    # ── the overview's headline figures ──
    "Without MitID data": "Uden MitID-data",
    "Area, median": "Areal, median",
    "Valuation, median": "Vurdering, median",
    "Debt, median": "Gæld, median",
    "Equity, median": "Friværdi, median",
    "Loan-to-value, median": "Belåningsgrad, median",
    "Loan-to-value, mean": "Belåningsgrad, gennemsnit",
    "Owners in total": "Ejere i alt",
    "Of those, companies": "Heraf selskaber",
    "Age, mean": "Alder, gennemsnit",
    "Age, median": "Alder, median",
    "Youngest owner": "Yngste ejer",
    "Oldest owner": "Ældste ejer",
    "Charges in total": "Hæftelser i alt",
    "Properties with a charge": "Ejendomme med hæftelse",
    "Principal, median": "Hovedstol, median",
    "Principal in total": "Hovedstol i alt",
    "Interest rate, median": "Rentesats, median",
    "Most common loan type": "Hyppigste låntype",
    "Built, median": "Opført, median",
    "Oldest": "Ældste",
    "Newest": "Nyeste",
    "Rooms, mean": "Værelser, gennemsnit",

    # ── the figures screen, and one analysis opened over a selection ──
    "All properties": "Alle ejendomme",
    "Everything held": "Alt hvad der er hentet",
    "enter opens one over the selection above · c resets it to "
    "everything · r reloads from the database":
        "enter åbner én over udvalget ovenfor · c nulstiller det til alt · "
        "r genindlæser fra databasen",
    "  ·  nothing matches this combination - press c to reset":
        "  ·  intet passer på denne kombination - tryk c for at nulstille",
    "  ·  too few to read much into": "  ·  for få til at læse meget ud af",
    "{where}   ·   {shown} of {properties} in {buildings}{thin}":
        "{where}   ·   {shown} af {properties} i {buildings}{thin}",
    "Nothing in that selection to compute anything over.":
        "Intet i det udvalg at regne noget ud fra.",
    "All years": "Hele perioden",
    "Last 5 years": "Sidste 5 år",
    "Last 10 years": "Sidste 10 år",
    "Last 20 years": "Sidste 20 år",
    "Close": "Luk",
    "Year": "År",
    "Sales": "Handler",
    "Group": "Gruppe",
    "Figure": "Tal",
    "Value": "Værdi",
    "per": "pr.",
    "{n} group": "{n} gruppe",
    "{n} groups": "{n} grupper",
    "Every figure below is over the selection named above.":
        "Hvert tal nedenfor gælder det udvalg, der er nævnt ovenfor.",
    "Not enough recorded sales in this selection to plot. Sale "
    "history is the register's own historical access, which "
    "needs a login, and a price per m\u00b2 also needs the "
    "property's registered area.":
        "For få tinglyste handler i dette udvalg til at tegne en kurve. "
        "Handelshistorikken er registrets egen historiske adkomst, som "
        "kræver login, og en kvadratmeterpris kræver desuden ejendommens "
        "tinglyste areal.",
    "Nothing in this selection has that figure recorded.":
        "Intet i dette udvalg har det tal noteret.",
    "{groups} over {properties}. Groups with nothing recorded are "
    "listed but not plotted.":
        "{groups} over {properties}. Grupper uden noget noteret står på "
        "listen, men er ikke tegnet.",
    "{properties} in this selection. A figure reading – is one "
    "nothing in the selection records.":
        "{properties} i dette udvalg. Et tal, der viser –, er et, som intet "
        "i udvalget har noteret.",

    # ── one property in full ──
    "Timeline": "Forløb",
    "Chart": "Kurve",
    "Easements": "Servitutter",
    "Parties": "Parter",
    "Document": "Dokument",
    "Born": "Født",
    "Share": "Andel",
    "Loan type": "Låntype",
    "Creditor": "Kreditor",
    "About": "Om",
    "Enforceable by": "Påtaleberettiget",
    "Role": "Rolle",
    "Pledgee": "Panthaver",
    "Amount": "Beløb",
    "Date": "Dato",
    "Sale type": "Handelstype",
    "Registered": "Tinglyst",
    "What": "Hvad",
    "Detail": "Detalje",
    "The property": "Ejendommen",
    "Registered area": "Tinglyst areal",
    "BFE number": "BFE-nummer",
    "Flat number": "Ejerlejlighedsnr.",
    "Share of the block": "Fordelingstal",
    "Cadastral number": "Matrikel",
    "Cadastral district": "Landsejerlav",
    "Municipality": "Kommune",
    "No owners recorded": "Ingen ejere registreret",
    "Value and debt": "Værdi og gæld",
    "Public valuation": "Offentlig vurdering",
    "Sale price per m² (tinglyst areal)": "Kvadratmeterpris (tinglyst areal)",
    "BBR living area": "BBR boligareal",
    "Price per m² (tinglyst areal)": "Kvadratmeterpris (tinglyst areal)",
    "Land value": "Grundværdi",
    "Valued on": "Vurderingsdato",
    "Equity (at least)": "Friværdi (mindst)",
    "Loan-to-value (at most)": "Belåningsgrad (højst)",
    "Latest sale": "Seneste handel",
    "Price per m²": "Pris pr. m²",
    "Purchase sum (deed)": "Købesum (skøde)",
    "Handover": "Overtagelse",
    "On the market": "Til salg nu",
    "Equity and loan-to-value are worked out against the "
    "public valuation, which runs below market. Treat the "
    "first as a floor and the second as a ceiling.":
        "Friværdi og belåningsgrad er regnet mod den offentlige vurdering, "
        "som ligger under markedet. Læs den første som et gulv og den anden "
        "som et loft.",
    "  (no. {n})": "  (nr. {n})",
    "Built": "Opført",
    "Rebuilt": "Ombygget",
    "Floors": "Etager",
    "Bathrooms": "Badeværelser",
    "Toilets": "Toiletter",
    "Basement": "Kælder",
    "Commercial": "Erhverv",
    "Total area": "Samlet areal",
    "External wall": "Ydervæg",
    "Roof": "Tag",
    "Heating": "Varme",
    "Additional heating": "Supplerende varme",
    "Kitchen": "Køkken",
    "Bathing": "Bad",
    "Toilet": "Toilet",
    "Charge": "Hæftelse",
    "land value {amount}": "grundværdi {amount}",

    # ── finding a new address ──
    "＋  Find a new property": "＋  Find en ny ejendom",
    "Prøvegade 1, 9999 Prøveby": "Prøvegade 1, 9999 Prøveby",
    "◀  Addresses": "◀  Adresser",
    "Select all": "Markér alle",
    "Select none": "Fjern markering",
    "＋  Add to queue": "＋  Læg i kø",
    "1 · find the address": "1 · find adressen",
    "2 · pick its properties": "2 · vælg dens ejendomme",
    "3 · fetch": "3 · hent",
    "Type an address to look it up in the land register. DAWA will "
    "clean up the spelling, the spacing and the floor, so a rough "
    "one is fine.":
        "Skriv en adresse for at slå den op i tingbogen. DAWA retter "
        "stavning, mellemrum og etage, så en omtrentlig adresse er nok.",
    "Type an address to look it up in the land register.":
        "Skriv en adresse for at slå den op i tingbogen.",
    "DAWA knows no address like {query!r}.":
        "DAWA kender ingen adresse som {query!r}.",
    " · {n} with nothing registered": " · {n} uden noget tinglyst",
    "{n} address(es){capped}{whole}{empty}.\n↓ or enter moves to "
    "the list · then enter opens one, or a takes its whole building":
        "{n} adresse(r){capped}{whole}{empty}.\n↓ eller enter går til listen "
        "· derefter åbner enter én, eller a tager hele bygningen",
    "{where} holds nothing in the land register - already "
    "checked, so there is nothing to fetch. Pick another of "
    "the addresses below.":
        "Der er intet tinglyst på {where} - allerede kontrolleret, så der er "
        "intet at hente. Vælg en anden af adresserne nedenfor.",
    "Asking the register what is registered at {where}…":
        "Spørger registret, hvad der er tinglyst på {where}…",
    "Nothing on this list has anything registered - try another "
    "address.":
        "Intet på denne liste har noget tinglyst - prøv en anden adresse.",
    "Struck-through rows are the ones already known to be empty.":
        "Overstregede rækker er dem, vi allerede ved er tomme.",
    "Nothing is registered at {where}.\nThe address is real, but "
    "the land register holds no property there. {nothing}":
        "Der er intet tinglyst på {where}.\nAdressen findes, men tingbogen "
        "har ingen ejendom der. {nothing}",
    "{properties} registered here · {chosen} ticked\nspace ticks "
    "one · a ticks all · n clears · f queues them · ← back to the "
    "addresses":
        "{properties} tinglyst her · {chosen} markeret\nspace markerer én · "
        "a markerer alle · n rydder · f lægger dem i kø · ← tilbage til "
        "adresserne",
    "Type an address first. a then takes its whole building.":
        "Skriv en adresse først. a tager så hele dens bygning.",
    "Pick an address first - enter on one, or a for its whole "
    "building.":
        "Vælg en adresse først - enter på én, eller a for hele dens bygning.",
    "Nothing ticked. space ticks the one under the cursor; a "
    "ticks all.":
        "Intet markeret. space markerer den under markøren; a markerer alle.",
    "Queued against the public register. ctrl+L logs in, for "
    "owners' dates of birth and the chain of previous owners.":
        "Lagt i kø mod det offentlige register. ctrl+L logger ind og giver "
        "ejernes fødselsdatoer og rækken af tidligere ejere.",
    "Queued {held} from {where}.\n{note}  ← goes back to the "
    "addresses to queue more, b shows the queue, l the library.":
        "Lagt {held} fra {where} i kø.\n{note}  ← går tilbage til adresserne "
        "for at lægge flere i kø, b viser køen, l ejendommene.",
    "Queued {held}. {note}": "Lagt {held} i kø. {note}",
    "nothing registered here": "intet tinglyst her",
    "no property of its own · whole building: {n}":
        "ingen egen ejendom · hele bygningen: {n}",

    # ── logging in, and what the queue does next ──
    "The register ended the session - press ctrl+L to log in again.":
        "Registret afsluttede sessionen - tryk ctrl+L for at logge ind igen.",
    "Logged out. The public lookup still works, and ctrl+L "
    "will ask for your MitID user ID again.":
        "Logget ud. Det offentlige opslag virker stadig, og ctrl+L spørger "
        "efter dit MitID-bruger-id igen.",
    "Log in to the land register": "Log ind i tingbogen",
    "Logged in as {who}. The register will show more now.":
        "Logget ind som {who}. Registret viser mere nu.",
    "It fetches in the background.": "Den hentes i baggrunden.",
    "Auto-fetch is off, so it waits in the queue - press b, then "
    "f to start it.":
        "Auto-hent er fra, så den venter i køen - tryk b og derefter f for "
        "at starte den.",

    # ── the export dialog ──
    "Nothing to export.": "Der er intet at eksportere.",
    ", and {n} more": ", og {n} mere",
    "{rows} rows across {tables} tables": "{rows} rækker fordelt på {tables} tabeller",
    "Writing…": "Skriver…",
    "Failed: {error}": "Mislykkedes: {error}",
    "Wrote {path}": "Skrev {path}",
    "Wrote {n} files to {where}": "Skrev {n} filer til {where}",
    "Nothing to write.": "Der er intet at skrive.",

    # ── how a value is written out: ages, yes/no, what kind of home ──
    "just now": "lige nu",
    "{n} min ago": "{n} min siden",
    "{n} h ago": "{n} t siden",
    "{n} d ago": "{n} d siden",
    "{n} w ago": "{n} u siden",
    "{n} mo ago": "{n} md siden",
    "{n} y ago": "{n} år siden",
    "Owner-occupied flat": "Ejerlejlighed",
    "House": "Villa",
    "Villa flat": "Villalejlighed",
    "Terraced house": "Rækkehus",
    "Co-op flat": "Andelsbolig",
    "Holiday house": "Sommerhus",
    "Holiday plot": "Sommerhusgrund",
    "Building plot": "Helårsgrund",
    "Farm": "Landejendom",
    "Hobby farm": "Hobbylandbrug",
    "Houseboat": "Husbåd",

    # ── footer keys that only one screen has ──
    # "Filter" and "SQL" are the same word in both, and are left out on
    # purpose: an entry mapping a string to itself is a line that has to be
    # kept in step with nothing.
    "Addresses": "Adresser",
    "Queue ticked": "Læg markerede i kø",
    "Start over": "Forfra",
    "To the list": "Til listen",

    # ── the language picker ──
    "Cancel": "Annullér",
    "Switch": "Skift",
    "The interface only. Register data stays in Danish, "
    "because that is the language it was written in.":
        "Kun brugerfladen. Data fra registrene bliver på dansk, "
        "fordi det er det sprog, de er skrevet på.",
}


def use(code: str) -> str:
    """Switch language. Returns the code actually in force.

    An unknown code falls back to the default rather than raising: this is
    read from a settings file a person can edit, and a typo there should not
    stop the application starting.
    """
    global _current
    _current = code if code in CODES else DEFAULT
    return _current


def current() -> str:
    return _current


def name_of(code: str) -> str:
    return dict(LANGUAGES).get(code, code)


def t(text: str, **fields) -> str:
    """The English `text` in the language now in force, formatted if asked.

    Formatting goes through here rather than through an f-string so that a
    translation can put the values in a different order from the English -
    which Danish regularly wants - and so the catalogue holds whole sentences
    rather than fragments glued together at the call site.
    """
    said = DANISH.get(text, text) if _current == "da" else text
    return said.format(**fields) if fields else said


def translate_bindings(node) -> None:
    """Rewrite one screen's or the application's footer labels.

    Textual reads BINDINGS off the class, which is evaluated on import - long
    before anybody has chosen a language - so the descriptions arrive in
    English and have to be replaced on the instance. `Binding` is frozen, so
    each one is copied rather than edited.

    This reaches into `_bindings`, which is Textual's own. There is no public
    way to restate a binding's description after the class exists, and the
    alternative is a footer that stays English in a Danish interface. A test
    asserts the footer actually changes language, so an upgrade that moves
    this fails out loud rather than quietly reverting.
    """
    held = getattr(node, "_bindings", None)
    if held is None or not hasattr(held, "key_to_bindings"):
        return

    # What the descriptions said in English, kept the first time through.
    # Without it this is one-way: the second call would look a Danish
    # description up in a catalogue keyed by English, find nothing, and leave
    # the footer in the language you were trying to leave. Screens are rebuilt
    # and so start from the class again, but the application is not - it is
    # the same object across every switch.
    source = getattr(node, "_i18n_source", None)
    if source is None:
        source = {
            key: [binding.description for binding in bindings]
            for key, bindings in held.key_to_bindings.items()
        }
        node._i18n_source = source

    for key, bindings in held.key_to_bindings.items():
        english = source.get(key)
        if english is None or len(english) != len(bindings):
            # Bindings added or removed since; take them as they stand rather
            # than pairing them up wrongly.
            english = [binding.description for binding in bindings]
        held.key_to_bindings[key] = [
            replace(binding, description=t(said)) if said else binding
            for binding, said in zip(bindings, english, strict=True)
        ]


# ── remembering the choice ──────────────────────────────────────────────


def settings_path() -> Path:
    """Where the chosen language is kept, beside the cached MitID session.

    Read at call time rather than at import, so a test that points
    XDG_CONFIG_HOME somewhere empty gets a fresh setting rather than the one
    belonging to whoever is running the test.
    """
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "yaybo" / "settings.json"


def load() -> str:
    """The remembered language, or the default when there is none.

    Anything unreadable is treated as no setting at all. This runs before the
    application has a screen to complain on, and a corrupt settings file is
    not a reason to refuse to start.
    """
    try:
        held = json.loads(settings_path().read_text(encoding="utf-8"))
        return use(str(held.get("language", DEFAULT)))
    except (OSError, ValueError, AttributeError):
        return use(DEFAULT)


def save(code: str) -> None:
    """Remember the language for next time, without losing other settings."""
    path = settings_path()
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(held, dict):
            held = {}
    except (OSError, ValueError):
        held = {}
    held["language"] = code
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # Not being able to remember the choice is not a reason to refuse to
        # act on it: the language is already switched by the time this runs.
        pass
