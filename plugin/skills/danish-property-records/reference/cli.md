# yaybo CLI reference

## Contents

- Running it without installing
- `yaybo fetch` — look addresses up
- `yaybo export` — get data out
- MitID login: `login`, `status`, `keepalive`, `logout`
- `yaybo backfill` — rebuild derived tables
- Global options
- Exit codes and output streams
- Troubleshooting

## Running it without installing

```bash
uvx yaybo fetch "Prøvegade 1, 9999 Prøveby"    # no install
uv tool install yaybo                           # or install once
```

Requires Python 3.10+. `uvx` needs [uv](https://docs.astral.sh/uv/).

**Never run bare `yaybo`** in a non-interactive session — it opens the TUI and
will hang. Always give a subcommand.

## `yaybo fetch` — look addresses up

```bash
yaybo fetch ADDRESS [ADDRESS ...]
```

Takes one or more addresses, pauses between them, and writes into
`out/tinglysning.duckdb`. Re-fetching an address replaces its rows.

| option | default | what it does |
| --- | --- | --- |
| `--format LIST` | `duckdb` | comma-separated: `duckdb`, `csv`, `xlsx` |
| `--limit N` | `25` | most properties per address (`0` = no limit) |
| `--delay SECONDS` | `1.0` | pause between properties and between addresses |
| `--outdir DIR` | `out/` | where results go |
| `--out PATH` | — | explicit output path; only valid with **one** address |
| `--anonymous` | — | ignore any cached session, public lookup only |
| `--login` | — | log in with MitID first (interactive — avoid) |
| `--no-boligsiden` | — | skip sale prices, BBR data and the equity columns |
| `--no-laantype` | — | skip estimating loan types from DST rates |
| `--no-dawa` | — | skip DAWA address cleaning |
| `--keepalive [MIN]` | `60` | hold the session open afterwards |
| `--dump PATH` | — | write the first raw record as JSON, for debugging |

**Address format**: `"Street Number, Postcode Town"`, with an optional unit:
`"Prøvegade 1, 3. tv, 9999 Prøveby"`. Addresses are resolved through DAWA, so
minor spelling differences are usually tolerated.

**One address can mean many properties.** A block of owner-occupied flats has
one registered property per flat, and `--limit` caps how many are fetched.
A building with 118 flats hits the default limit of 25.

Examples:

```bash
# several addresses, one command
yaybo fetch "Prøvegade 1, 9999 Prøveby" "Prøvevej 2, 9999 Prøveby"

# public data only, no cached session used
yaybo fetch "Prøvegade 1, 9999 Prøveby" --anonymous

# a whole small building, and a spreadsheet at the same time
yaybo fetch "Prøvegade 1, 9999 Prøveby" --limit 0 --format duckdb,xlsx
```

## `yaybo export` — get data out

```bash
yaybo export [--format LIST] [--query SQL | --query-file PATH] [--name STEM]
```

Exports what is **already stored**, unlike `fetch --format`, which exports only
what it has just fetched.

| option | default | what it does |
| --- | --- | --- |
| `--format LIST` | `xlsx` | comma-separated: `xlsx`, `csv`, `duckdb` |
| `--query SQL` | — | export this query's result instead of every table |
| `--query-file PATH` | — | read the query from a file (better for long SQL) |
| `--name STEM` | `yaybo` | filename stem, and the Excel sheet name for a query |
| `--outdir DIR` | `exports/` | where the file goes |

The written path goes to **stdout**; progress goes to stderr. So:

```bash
file=$(yaybo export --name rapport)
```

Excel gets one sheet per table with the header row frozen. `attester` is left
out of `csv` and `xlsx` because a signed document is hundreds of kilobytes and
Excel refuses a cell over 32767 characters; a `duckdb` export keeps it.

Examples:

```bash
yaybo export                                        # everything -> one workbook
yaybo export --name priser --query "
  SELECT adresse, boligareal_m2, seneste_salg_pris_m2
  FROM ejendomme WHERE seneste_salg_pris_m2 IS NOT NULL
  ORDER BY seneste_salg_pris_m2 DESC"
yaybo export --format csv,xlsx --query-file report.sql --name rapport
```

## MitID login

**Interactive. Do not run these in a background or non-interactive shell.**
`yaybo login` waits for the user to approve a push notification in the MitID
app, or to scan a QR code. It will hang.

Ask the user to run it themselves. In Claude Code they can type:

```
! yaybo login --user THEIR_MITID_USER_ID
```

| command | interactive | what it does |
| --- | --- | --- |
| `yaybo login --user ID` | **yes** | log in and cache the session |
| `yaybo status` | no | is a session live, and roughly how long left |
| `yaybo keepalive [MIN]` | no (blocks) | hold a session open, default 60 min |
| `yaybo logout` | no | end the session and forget the cookies |

`yaybo status` is the one to run before and after. It exits `0` when a session
is live and `1` when there is none or it has lapsed, so it works in a condition:

```bash
if yaybo status >/dev/null 2>&1; then echo "logged in"; fi
```

The MitID **user ID** is not a CPR number. It is remembered after the first
login, so `yaybo login` alone works afterwards.

`--method TOKEN` with `--password` uses a code-display token instead of the
app. Still interactive.

## `yaybo backfill` — rebuild derived tables

```bash
yaybo backfill [--dry-run] [--skip-boligsiden] [--skip-laantype]
```

Re-derives every table that comes from a stored document — charges, easements,
the people named on them, previous owners — with **no login and no requests to
the register**. Run it after upgrading yaybo, when a parser has improved.

`--skip-boligsiden --skip-laantype` makes it fully offline.

It also gives older databases their primary keys, which is why it is the fix
when a table reports as unkeyed.

## Global options

Work before or after the subcommand:

| option | what it does |
| --- | --- |
| `--db PATH` | use a different database (default `out/tinglysning.duckdb`) |
| `--debug` | show protocol chatter, for when a login stops working |
| `--version`, `-V` | print the installed version |

## Exit codes and output streams

- `0` success; `1` failure. `fetch` returns `1` if **any** address failed, and
  names the failures in its summary.
- Progress and warnings go to **stderr**. `yaybo export` prints the written
  file path to **stdout**, and nothing else, so it can be captured.
- Warnings worth reading: a row dropped for an incomplete key, or a table left
  unkeyed. Both are printed even without `--debug`.

## Troubleshooting

| symptom | cause and fix |
| --- | --- |
| the command hangs | bare `yaybo` opened the TUI, or `yaybo login` is waiting for the app. Use a subcommand; have the user do the login |
| `no database at out/...` | nothing fetched yet — run `yaybo fetch` first |
| `cached session has expired` | the login lapsed; ask the user to run `yaybo login` again |
| `dokument_parter` etc. empty | the fetch was anonymous. Check `ejendomme.beriget` |
| only 25 properties for a big block | the `--limit` default; pass `--limit 0` |
| `--out names one file` | `--out` takes a single address; use `--outdir` instead |
| a table reports as unkeyed | an older database — run `yaybo backfill` |
| `IO Error: Conflicting lock is held` | another process is writing — the TUI, or a fetch. Wait and retry, or copy the file and query the copy |
