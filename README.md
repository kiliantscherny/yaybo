# yaybo

A terminal application for the Danish property registers. Look an address up,
see what the land register holds against it, and keep the answers in a database
you can query later.

```sh
uv run yaybo                                          # the TUI
uv run yaybo fetch "Prøvegade 1, 9999 Prøveby"
```

Everything is public data, from three sources that answer different questions:

| source | what it knows |
| --- | --- |
| **tinglysning.dk** | who owns a property, what is charged against it, every easement, every past transfer |
| **Boligsiden** | what it last sold for and per m², plus the BBR record — year built, rooms, walls, heating |
| **Danmarks Statistik** | what each kind of realkredit loan cost month by month, which is what lets a bare interest rate be read as an F3 or a fixed loan |

The register shows more of itself to someone who has proved who they are. Log in
once with MitID and later runs quietly pick the session back up, adding each
owner's date of birth, everyone named on every mortgage, and the chain of
previous owners.

> This tells you who *owns* a property, not who lives there. Resident data
> (CPR/folkeregisteret) is not public in Denmark, with or without a login.

## The TUI

Five screens, one database, and nothing that has to be typed twice.

**Library** — where it opens. Everything ever fetched, searchable offline, with
how stale each row is and its valuation, debt and loan-to-value at a glance.
`enter` opens a property, `f` re-fetches it, `e` exports the whole database.

**Search** — an address box that resolves against DAWA as you type, then asks the
register which properties actually sit there. Two lists, on purpose:

- **the addresses** are DAWA's, and picking one costs nothing. Whole buildings
  come first — asked about a house number DAWA answers with its flats, and the
  register would be asked the same question about any of them. `↓` moves from the
  box into the list, `enter` opens one, `a` takes its whole building.
- **the properties** are the register's, and this is the list that matters: one
  row for a rented block, 118 for Prøvegade 1. It is a multi-select —
  `space` ticks one, `a` ticks all, `n` clears, `f` fetches the ticked ones, and
  `ctrl+C` stops a long run without losing what it already has.

While the address box has focus a letter is a letter, because addresses contain
"30A". The status line says which keys are live at each step.

**Property** — one property, tab by tab, read back out of the database so it
costs nothing and works offline:

- **Oversigt** — what it is, who owns it, what it is worth, what it owes
- **Ejere · Hæftelser · Servitutter · Parter · Handler** — the tables in full,
  with each charge's interest terms and the loan product they imply
- **Forløb** — sales, transfers, mortgages, easements and valuations merged onto
  one chronology. The register keeps these as four unrelated lists; putting them
  in date order is what makes a property's story legible.
- **Kurve** — price per square metre over time, plotted
- **Bygning** — the BBR record; **Dokument** — the signed attest itself

**Queue** — a whole street at a time. Type "Prøvegade, 9999", press *Whole
street*, and it queues every house number DAWA knows; or point it at a file with
one address per line. Progress bar, live per-row status, pause with `space`, and
whatever it has already fetched is already saved.

**SQL** — the accumulated database, queried directly, for the questions that only
exist once there is more than one property in it: price per m² by postcode,
owners of more than one flat, the most heavily mortgaged, buildings by age and
heating. Eight of those come ready to load and edit. Read-only, enforced by the
connection rather than by inspecting the SQL. `ctrl+E` exports any result.

Press `ctrl+L` anywhere to log in with MitID, or to log out.

## Exporting

`e` on any screen, or `--format` on the command line. Three formats, and each
does the obvious thing with a set of related tables:

- **DuckDB** — one table each, the shape they were already in
- **Excel** — one sheet per table, header row frozen
- **CSV** — one file per table, in a folder named after the address

Column order follows the schema, so the same table exported twice has the same
columns in the same places.

## On the command line

```sh
yaybo fetch ADDRESS [--format duckdb,csv,xlsx] [--limit N] [--anonymous]
yaybo login --user YourMitIDUserID
yaybo status                  # is the session still good, and for how long
yaybo keepalive [MINUTES]     # hold it open without another trip to the phone
yaybo backfill                # re-derive the stored tables, fetching nothing
yaybo logout
```

`fetch` and the TUI share one pipeline, so neither can drift from the other.
Results accumulate in `out/tinglysning.duckdb`, replaced in place when an address
is looked up again — running the same address twice is a correction, not two
observations.

`backfill` rebuilds every table that is derived from a stored document — the
charges, the easements, everyone named on them, the historical owners — without
a login and without a single request to the register. Improving a reader makes
the existing rows out of date, not missing.

## The tables

| table | one row per |
| --- | --- |
| `ejendomme` | property, with what it is worth, what it owes and what is left over |
| `ejere` | current owner |
| `haeftelser` | mortgage or charge, with its interest terms and estimated loan type |
| `servitutter` | easement, and what it is about |
| `dokument_parter` | person named on any document, with their role and date of birth or CVR |
| `underpant` | deed pledged on in its own right |
| `handelshistorik` | recorded sale, with the price per m² |
| `bygninger` | building in the BBR record |
| `adkomsthistorik` | past transfer, with what was paid |
| `adkomsthistorik_ejere` | person named in one of those transfers |
| `rentestatistik` | month of DST realkredit rates, kept so an estimate can be checked rather than taken on trust |
| `attester` | property's whole register document, as signed and as queryable JSON |

`dokument_parter`, `adkomsthistorik` and `attester` need a login. Everything else
does not.

Three columns on `ejendomme` are worked out rather than fetched:
`samlet_gaeld_dkk`, `frivaerdi_dkk` and `belaaningsgrad_pct`. They run against the
public valuation, which sits well below market, so treat the equity as a floor
and the loan-to-value as a ceiling.

The loan type on a charge is an estimate, not a record. The register gives an
interest rate and never the product, so `laantype_estimat` is that rate matched
against what each kind of loan actually cost in the months around it. The
distance to the runner-up is in `laantype_afstand` and the whole rate series is
in `rentestatistik`, so an estimate can be argued with.

## Installing

```sh
git clone …/yaybo && cd yaybo
uv sync
uv run yaybo
```

Python 3.13 or newer.

## packages/mitid — the login, on its own

The MitID half is a separate package, `mitid-client`, in `packages/mitid/`. It
knows nothing about property: it is a Python stand-in for MitID's JavaScript core
client, the NemLog-in broker that fronts the Danish public sector, a cookie store
for keeping a session between runs, and two ways of showing a login to the person
doing it — a few lines on stderr, or a Textual screen.

```python
from mitid.brokers import nemlogin
from mitid.ui.tui import MitIDLoginScreen

session = nemlogin.new_session()
result = await self.push_screen_wait(
    MitIDLoginScreen(partial(nemlogin.log_in, session, START_URL))
)
```

Point it at any NemLog-in-protected URL and it comes back with the session cookie
that URL was guarding. See `packages/mitid/README.md`. It is a uv workspace
member, so it can be split into its own repository later without a line of it
changing.

## Please be sensible

This logs in as you, to a government register, about real people's homes. Use it
for addresses you have a reason to look at. The `out/` and `exports/` folders are
git-ignored on purpose, and so is every data file anywhere in the tree — every
row names somebody and says what they paid for their house.

The register is a public service, not a scraping target: there is a pause between
requests, and no attempt anywhere to go faster than a person clicking.
