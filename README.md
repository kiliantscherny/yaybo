# yaybo

[![PyPI](https://img.shields.io/pypi/v/yaybo?logo=pypi&logoColor=white)](https://pypi.org/project/yaybo/)
[![CI](https://github.com/kiliantscherny/yaybo/actions/workflows/ci.yml/badge.svg)](https://github.com/kiliantscherny/yaybo/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/yaybo?logo=python&logoColor=white)](https://pypi.org/project/yaybo/)

A terminal application for the Danish property registers. Look an address up,
see what the land register holds against it, and keep the answers in a database
you can query later.

```sh
uvx yaybo                                          # the TUI, without installing it
uvx yaybo fetch "Prøvegade 1, 9999 Prøveby"
```

---

> [!CAUTION]
> **This is a hobby project. It is not built for production use, and nothing
> about it is supported.**
>
> It is not affiliated with, endorsed by, or connected to tinglysning.dk,
> Domstolsstyrelsen, MitID, NemLog-in, Boligsiden, Danmarks Statistik or
> Dataforsyningen. Those names appear here only to say where the data comes
> from.
>
> Provided as-is, with no warranty of any kind. **Use it at your own risk.** The
> author accepts no liability for any loss, damage, or misuse arising from it,
> and none of it is financial, legal or property advice.

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

> [!NOTE]
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
  row for a rented block, a hundred-odd for a block of owner-occupied flats. It
  is a multi-select —
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

> [!WARNING]
> **Some columns are worked out, not recorded, and they can be wrong.**
>
> `samlet_gaeld_dkk`, `frivaerdi_dkk` and `belaaningsgrad_pct` are derived, and
> they run against the *public valuation*, which sits well below market. Treat
> the equity as a floor and the loan-to-value as a ceiling, never as a figure.
>
> `laantype_estimat` is an estimate and not a record at all. The register gives
> an interest rate and never the product, so this is that rate matched against
> what each kind of loan actually cost in the months around it. The distance to
> the runner-up is in `laantype_afstand` and the whole rate series is kept in
> `rentestatistik`, so an estimate can be argued with rather than taken on
> trust.

## Installing

```sh
uv tool install yaybo    # or: pip install yaybo
yaybo
```

`uvx yaybo` runs it without installing anything at all. Python 3.10 or newer.

To work on it, see [CONTRIBUTING.md](CONTRIBUTING.md).

## mitid-client — the login, on its own

The MitID half is a library of its own:
[mitid-client](https://github.com/kiliantscherny/mitid-client). It knows nothing
about property - it is a Python stand-in for MitID's JavaScript core client, the
NemLog-in broker that fronts the Danish public sector, a store for keeping a
login's cookies between runs, and two ways of showing a login to whoever is
doing it: a few lines on stderr, or a Textual screen.

```python
from mitid.brokers import nemlogin
from mitid.ui.tui import MitIDLoginScreen

session = nemlogin.new_session()
result = await self.push_screen_wait(
    MitIDLoginScreen(partial(nemlogin.log_in, session, START_URL))
)
```

Point it at any NemLog-in-protected URL and it comes back with the session
cookie that URL was guarding. It installs as a dependency of this, so there is
nothing to do about it; it is worth knowing about separately because a login is
the reusable half, and property is not.

## What you are taking on

> [!WARNING]
> **Everything this fetches is about real, named people** — what they paid for
> their home, what they still owe on it, and, once logged in, when they were
> born. It is public record. That is not the same as it being yours to do
> whatever you like with.
>
> - The moment you fetch it, you are the one holding it. In the EU that comes
>   with obligations, and "it was already public" is not an answer for what you
>   do next.
> - `out/` and `exports/` are git-ignored on purpose, and so is every data file
>   anywhere in the tree. Keep it that way — a committed database is a
>   published one.
> - Look up addresses you have a reason to look at.

> [!IMPORTANT]
> The registers are public services, not scraping targets. There is a pause
> between requests and no attempt anywhere to go faster than a person clicking,
> and the queue is rate-limited for the same reason. Please leave it that way.

Logging in means logging in as you, to a government register, with MitID. See
[mitid-client](https://github.com/kiliantscherny/mitid-client) for what that
part carries with it.
