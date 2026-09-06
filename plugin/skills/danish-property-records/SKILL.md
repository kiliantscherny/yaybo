---
name: danish-property-records
description: Fetches and analyses Danish property records with the yaybo CLI - owners, mortgages and charges (haeftelser), easements (servitutter), sale prices, BBR building data and realkredit loan types, from tinglysning.dk (Tingbogen), Boligsiden and Danmarks Statistik. Use when the user asks about a Danish address or property, wants to look up who owns something, what is mortgaged against it, what it sold for, or wants Danish property data queried, compared or exported to Excel.
---

# Danish property records with yaybo

`yaybo` fetches Danish property records into a local DuckDB database, which you
then query. Use the **CLI subcommands**, never the TUI.

Running `yaybo` with no arguments opens an interactive terminal UI that will
hang a non-interactive session. It is for humans. Every subcommand below is
non-interactive except `yaybo login`.

## Workflow

```
- [ ] 1. Check the tool and the database (yaybo --version, yaybo status)
- [ ] 2. Decide whether the task needs a MitID login
- [ ] 3. Fetch the addresses (yaybo fetch "ADDR" "ADDR" ...)
- [ ] 4. Query the database read-only
- [ ] 5. Report, or export to Excel (yaybo export)
```

### 1. Check the tool and pick a working folder

```bash
uvx yaybo --version                # works whether or not yaybo is installed
uvx yaybo status                   # is a MitID session live, and for how long
```

`uvx` needs [uv](https://docs.astral.sh/uv/), which is the only prerequisite —
it installs a suitable Python itself. If `uvx` is missing, ask the user to run
`curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or
`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows). Do not
install anything on their machine without asking.

If `yaybo` is installed as a tool, drop the `uvx` prefix from every command
below. Otherwise keep it.

**Fetched data lands in `out/` relative to the current directory.** Before
fetching, make sure that is somewhere sensible rather than wherever the session
happens to have started:

```bash
mkdir -p ~/property-lookups && cd ~/property-lookups
ls out/tinglysning.duckdb          # is there already data here
```

Use the same folder every time, so lookups accumulate into one database and can
be compared. `--db PATH` overrides it if the user wants the file elsewhere.

### 2. Decide about the login

**Most data needs no login**: address, valuation, owners' names, mortgages and
their interest terms, easements, sale prices, BBR building data, and the whole
of the andelsboligbog.

**Four tables need a MitID login**: `dokument_parter` (everyone named on each
document), `adkomsthistorik` and `adkomsthistorik_ejere` (previous owners), and
`attester` (the signed document). Owners' dates of birth also need it.

**You cannot do the login yourself.** It requires approving a push notification
in the MitID app or scanning a QR code. Do not run `yaybo login` in a
background shell — it will hang waiting for input.

If the task needs logged-in data, ask the user to run it themselves. In Claude
Code they can type this directly in the prompt:

```
! yaybo login --user THEIR_MITID_USER_ID
```

Then confirm with `yaybo status` before continuing. The session is cached and
lasts a while, so this is occasional rather than per-command.

### Andelsboliger are a second register

A co-op flat is not real property, and the two registers describe it
differently. Both are fetched, and both are right:

- `ejendomme` holds **one** row for the whole block — the property the
  association owns.
- `andele` holds **one row per flat** — the shares, joined back by
  `andele.ejendom_uuid`.

So a co-op address that looks like a single anonymous building in `ejendomme`
usually has a dozen rows in `andele`. When a user asks about a specific co-op
flat, `andele` is where the flat is; `ejendomme` is where its building is.

**Nobody owns an andel, as far as the register is concerned.** It records
rights *over* a share, not title *to* one. If asked who lives in or owns a
co-op flat, say so, then offer `andel_haeftelser.kreditorer` — for an
ejerpantebrev, which most of these are, the creditor is the owner issuing to
themselves — and `andel_meddelelser.debitorer`, which names them when a death
or bankruptcy has been noted. Neither is the register stating ownership, and
neither carries a date of birth.

Three answers that look right and are wrong:

- A flat missing from `andele` is **not** evidence it is not an andel — a share
  only enters the book once something is registered against it.
- `andele.samlet_gaeld_dkk` is **not** what living there owes: it excludes the
  andelshaver's portion of the association's own mortgage, which is charged
  against the building.
- There is **no sale price for a share**. Do not reach through the join to
  `ejendomme.seneste_salg_*` and present it as the flat's — it is the
  building's own sale, identical for every flat in the block.

See `reference/data-model.md` for the joins and the rest of the traps.

If the user does not want to log in, carry on and say plainly which parts of
the answer are unavailable without it.

### 3. Fetch

One command takes several addresses and pauses between them:

```bash
yaybo fetch "Prøvegade 1, 9999 Prøveby" "Prøvevej 2, 3. tv, 9999 Prøveby"
```

Rows accumulate in `out/tinglysning.duckdb`. Re-fetching an address replaces
its rows rather than adding a second copy, so re-running is safe.

An address that cannot be resolved is reported and skipped; the others still
get fetched. The exit code is non-zero if any address failed.

**Never remove or shorten the delays.** The registers are public services.

Full flag reference: [reference/cli.md](reference/cli.md)

### 4. Query

Open the database **read-only**, so a query can never damage what was fetched:

```bash
duckdb -readonly out/tinglysning.duckdb -c "SELECT count(*) FROM ejendomme"
```

```python
import duckdb
db = duckdb.connect("out/tinglysning.duckdb", read_only=True)
```

`ejendom_uuid` joins to `ejendomme.uuid` in every table. That one fact covers
most queries.

If a query fails with `IO Error: Conflicting lock is held`, a TUI or a fetch is
mid-write. Wait and retry, or copy the file and query the copy.

- **Column-level schema**: [reference/schema.dbml](reference/schema.dbml) —
  every table, column, type and key, generated from the code
- **How to read it** (grain, joins, which columns are computed rather than
  recorded): [reference/data-model.md](reference/data-model.md)
- **Worked queries**: [reference/queries.md](reference/queries.md)

### 5. Report or export

To Excel, or CSV, or a standalone DuckDB file:

```bash
yaybo export                                   # whole database -> exports/*.xlsx
yaybo export --query "SELECT ..." --name priser
yaybo export --format csv --name priser --query "SELECT ..."
```

The path written is printed on stdout and nothing else is, so
`file=$(yaybo export)` gives you the file.

## Reporting rules

These are the mistakes that matter, because they turn an estimate into a claim
about someone's finances.

**Never present a derived column as a fact from the register.** `frivaerdi_dkk`,
`belaaningsgrad_pct` and `samlet_gaeld_dkk` are computed against the **public
valuation**, which sits well below market. Equity is a **floor**, loan-to-value
a **ceiling**. Say so whenever you quote them.

**`laantype_estimat` is a guess, not a record.** The register gives an interest
rate and never the loan product. Quote `laantype_afstand` alongside it — a small
distance means the runner-up was nearly as good a fit.

**Check `ejendomme.beriget` before concluding something is absent.** It records
whether that property was fetched with a login. "No parties named on this
mortgage" usually means the fetch was anonymous, not that nobody is named.

**The database is not a sample of Denmark.** It holds what someone chose to
fetch. A median over eleven flats in one stairwell is a fact about those eleven
flats.

**This is personal data about named people.** Do not write query results
containing names, birth dates or CVR numbers into the repository — `out/` and
`exports/` are git-ignored for that reason. Do not paste them anywhere public.
