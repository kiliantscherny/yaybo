<p align="center">
  <img src="static/yaybo-logo.png" alt="yaybo" width="200" />
</p>

<h1 align="center">yaybo</h1>

<p align="center">
  A terminal application for the Danish property registers. Look an address up, see what the land register (Tingbogen) has on it, and store the data in a simple local database you can explore in a terminal UI (TUI).
  <br>
  <a href="https://pypi.org/project/yaybo/"><img alt="PyPI - Version" src="https://img.shields.io/pypi/v/yaybo?style=flat&logo=python&logoColor=orange&label=yaybo&labelColor=teal&color=navy"></a>
</p>

<p align="center">
<img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white" alt="Python versions" />
<a href="https://github.com/j178/prek"><img src="https://img.shields.io/badge/prek-enabled-brightgreen?logo=pre-commit&logoColor=white" alt="prek" style="max-width:100%;"></a>
<a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv" style="max-width:100%;"></a>
<a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff" style="max-width:100%;"></a>
<a href="https://github.com/astral-sh/ty"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json" alt="ty" style="max-width:100%;"></a>
<a href="https://github.com/tox-dev/tox-uv"><img src="https://img.shields.io/badge/tox-testing-1C1C1C?logo=tox&logoColor=white" alt="tox" alt="tox" style="max-width:100%;"></a>
<a href="https://github.com/kiliantscherny/yaybo/actions/workflows/ci.yml"><img src="https://github.com/kiliantscherny/yaybo/actions/workflows/ci.yml/badge.svg" alt="CI" style="max-width:100%;"></a>
<a href="https://github.com/kiliantscherny/yaybo/actions/workflows/release.yml"><img src="https://github.com/kiliantscherny/yaybo/actions/workflows/release.yml/badge.svg" alt="Release to PyPI" style="max-width:100%;"></a>

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

Everything is publicly available data, from three sources that answer different questions:

| source                 | what it knows                                                                                                                   |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **tinglysning.dk**     | who owns a property, what is charged against it, every easement, every past transfer                                            |
| **Boligsiden**         | what it last sold for and per m², plus the BBR record – year built, rooms, walls, heating                                       |
| **Danmarks Statistik** | what each kind of realkredit loan cost month by month, which is what lets a bare interest rate be read as an F3 or a fixed loan |

You can only get some of this data without logging in with MitId. When you do that, you get the owner's/owners' date(s) of birth, everyone named on every mortgage, and the history of previous owners.

> [!NOTE]
> This tells you who _owns_ a property, not who lives there. Resident data
> (CPR/folkeregisteret) is not public in Denmark, with or without a login.
> If someone is renting that property, you can't get any data about them,
> but you can get the data about the owner(s), which in some cases is a company
> (with a CVR number)and not a private person.

## Quickstart

```sh
uvx yaybo # run the TUI in a temporary, isolated environment
uvx yaybo fetch "Prøvegade 1, 9999 Prøveby" # fetch a property's data
```

## The TUI

> [!WARNING]
> The TUI is still a work in progress and will likely change a lot in the future.

This application is a simple TUI that allows you to browse the data in your terminal. It is split into several screens, each with its own purpose.

**Library**: this is where the TUI opens. Everything you have ever fetched is stored in a database, searchable offline, with info on how stale each row is and its valuation, debt and loan-to-value at a glance. `enter` opens a property, `f` re-fetches it, `e` exports the whole database.

**Search**: an address box that resolves against [DAWA](https://dawadocs.dataforsyningen.dk/) ("Danmarks Adressers Web API") as you type, then asks the register which properties actually sit there.

**Property** – one property, tab by tab, read back out of the database so it
costs nothing and works offline:

- **Oversigt** – what it is, who owns it, what it is worth, what it owes
- **Ejere · Hæftelser · Servitutter · Parter · Handler** – the tables in full,
  with each charge's interest terms and the loan product they imply
- **Forløb** – sales, transfers, mortgages, easements and valuations merged onto
  one chronology. The register keeps these as four unrelated lists; putting them
  in date order is what makes a property's story legible.
- **Kurve** – price per square metre over time, plotted
- **Bygning** – the BBR record; **Dokument** – the signed attest itself

**Queue** – a whole street at a time. Type "Prøvegade, 9999", press _Whole
street_, and it queues every house number DAWA knows; or point it at a file with
one address per line. Progress bar, live per-row status, pause with `space`, and
whatever it has already fetched is already saved.

**SQL** – the accumulated database, queried directly, for the questions that only
exist once there is more than one property in it: price per m² by postcode,
owners of more than one flat, the most heavily mortgaged, buildings by age and
heating. Eight of those come ready to load and edit. Read-only, enforced by the
connection rather than by inspecting the SQL. `ctrl+E` exports any result.

Press `ctrl+L` anywhere to log in with MitID, or to log out.

## Exporting

`e` on any screen, or `--format` on the command line. Three formats, and each
does the obvious thing with a set of related tables:

- **DuckDB** – one table each, the shape they were already in
- **Excel** – one sheet per table, header row frozen
- **CSV** – one file per table, in a folder named after the address

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
is looked up again – running the same address twice is a correction, not two
observations.

`backfill` rebuilds every table that is derived from a stored document – the
charges, the easements, everyone named on them, the historical owners – without
a login and without a single request to the register. Improving a reader makes
the existing rows out of date, not missing.

## The tables

| table                   | one row per                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------------------- |
| `ejendomme`             | property, with what it is worth, what it owes and what is left over                          |
| `ejere`                 | current owner                                                                                |
| `haeftelser`            | mortgage or charge, with its interest terms and estimated loan type                          |
| `servitutter`           | easement, and what it is about                                                               |
| `dokument_parter`       | person named on any document, with their role and date of birth or CVR                       |
| `underpant`             | deed pledged on in its own right                                                             |
| `handelshistorik`       | recorded sale, with the price per m²                                                         |
| `bygninger`             | building in the BBR record                                                                   |
| `adkomsthistorik`       | past transfer, with what was paid                                                            |
| `adkomsthistorik_ejere` | person named in one of those transfers                                                       |
| `rentestatistik`        | month of DST realkredit rates, kept so an estimate can be checked rather than taken on trust |
| `attester`              | property's whole register document, as signed and as queryable JSON                          |

`dokument_parter`, `adkomsthistorik` and `attester` need a login. Everything else
does not.

> [!WARNING]
> **Some columns are worked out, not recorded, and they can be wrong.**
>
> `samlet_gaeld_dkk`, `frivaerdi_dkk` and `belaaningsgrad_pct` are derived, and
> they run against the _public valuation_, which sits well below market. Treat
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

## mitid-client – the login, on its own

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
> **Everything this fetches is about real, named people** – what they paid for
> their home, what they still owe on it, and, once logged in, when they were
> born. It is public record. That is not the same as it being yours to do
> whatever you like with.
>
> - The moment you fetch it, you are the one holding it. In the EU that comes
>   with obligations, and "it was already public" is not an answer for what you
>   do next.
> - `out/` and `exports/` are git-ignored on purpose, and so is every data file
>   anywhere in the tree. Keep it that way – a committed database is a
>   published one.
> - Look up addresses you have a reason to look at.

> [!IMPORTANT]
> The registers are public services, not scraping targets. There is a pause
> between requests and no attempt anywhere to go faster than a person clicking,
> and the queue is rate-limited for the same reason. Please leave it that way.

Logging in means logging in as you, to a government register, with MitID. See
[mitid-client](https://github.com/kiliantscherny/mitid-client) for what that
part carries with it.
