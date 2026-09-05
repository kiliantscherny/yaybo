# Changelog

The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
versions follow [semantic versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-09-05

The command line, the exported formats and the table names have been stable
for long enough to say so. Nothing here renames or removes a column, and a
0.2.1 database opens unchanged; what changes is how much the application can
tell you about what it already holds.

### Added

- **Bygninger**, a sixth screen: the library grouped one building to a row,
  with how many of its properties are held, how many were fetched with a
  login, median area, valuation per m², debt and belåningsgrad, and when the
  newest of them was fetched.
- **Nøgletal**, figures across a set of properties rather than about one. Pick
  a scope with dropdowns (by, postnummer, vej, bygning), then an analysis:
  prices over time, a comparison by floor or building or postcode, gæld and
  friværdi, the owners, the buildings. Sixteen measures, five ways of
  combining them and seven groupings, all composable.
- Tabs across the top of every screen. The screens were already peers; now
  they say so.
- A fetch queue owned by the application rather than by a screen, so a
  building of thirty flats can be set going and left while you look something
  else up. Every screen carries the same progress bar, and the queue screen
  starts, retries, removes or exports whichever jobs are ticked.
- An auto-fetch toggle, for gathering a list of buildings to fetch in one go.
- A session bar naming the MitID login and how long the register will honour
  it, coloured green, amber or red.
- A `beriget` column recording, per property, whether it was fetched while
  logged in - and a MitID column in the library saying so.
- The library sorts on any column, filters by dropdown, and takes a selection
  of properties to re-fetch or export together.
- `yaybo fetch` takes more than one address, and `yaybo export` writes a
  stored query out without opening the TUI.
- `schema.dbml`, generated from `store.TABLES` and checked by a test, plus a
  Claude Code plugin that ships it as a skill.

### Changed

- Searching a street without a house number returns the whole street rather
  than DAWA's first thirty matches, and every row says what it would cost
  before you pick it: how many properties the register holds there, whether
  there are none at all, and whether the database already has it.
- The address list is split into whole buildings and individual flats, because
  the same count means different things for each.
- Every table has a real primary key. Parties the register returns once per
  charge are folded per document and role instead of stored repeatedly, and a
  row whose key is incomplete is dropped with a warning rather than taking the
  write down.
- The palette is dark and light blue on near-white, and the charts follow it.

### Fixed

- Sessions expired far sooner than the register's own 29 minute limit: any
  failure to reach it - a timeout, a dropped connection, a 5xx - was read as
  "logged out" and the session discarded. Only an actual refusal counts now,
  and it is confirmed a second way before acting on it.
- Logging out from the TUI kept the MitID user ID in memory, so logging back in
  silently reused it and never asked.
- Ticking a row in the library sent the cursor back to the first row.
- Charts were drawn in plotext's own colours rather than the theme's.
- `__init__.py` reported a version that had drifted from `pyproject.toml`; it
  reads the installed metadata now.
- A single-table CSV export named after its own query came out as
  `priser-priser-...csv`.

### Notes

- The figures describe only what you have fetched. A median over four flats is
  not a statistic, and the screen says so.
- Python 3.10 and newer.


## [0.2.1] - 2026-09-02

First published release. Everything below is what it arrives with, rather than
what changed since a version anyone could install.

### Added

- `yaybo fetch ADDRESS`: an address in, and the register's answer stored in
  `out/tinglysning.duckdb` and exported as DuckDB, Excel or CSV.
- A Textual TUI with five screens: **Library** (everything already fetched,
  searchable offline), **Search** (DAWA autocomplete, then the properties the
  register actually holds at that address), **Property** (one property tab by
  tab, including a merged chronology and a price-per-m² plot), **Queue** (a
  whole street at a time, pausable) and **SQL** (the accumulated database,
  read-only, with eight worked queries).
- MitID login through
  [mitid-client](https://github.com/kiliantscherny/mitid-client), which adds
  each owner's date of birth, everyone named on every mortgage, and the chain
  of previous owners. Sessions are picked back up between runs and held open
  while the application is running.
- Enrichment from two sources that need no login: Boligsiden for sale prices,
  price per m² and the BBR record, and Danmarks Statistik for the realkredit
  rates that let a bare interest rate be read as an F3 or a fixed loan.
- `yaybo backfill`: re-derive every stored table from the documents already
  held, without a login and without a single request to the register.
- `yaybo login`, `status`, `keepalive` and `logout`.

### Notes

- `samlet_gaeld_dkk`, `frivaerdi_dkk`, `belaaningsgrad_pct` and
  `laantype_estimat` are worked out, not recorded, and the README says what
  that costs you.
- Python 3.10 and newer.

[Unreleased]: https://github.com/kiliantscherny/yaybo/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/kiliantscherny/yaybo/releases/tag/v0.2.1
