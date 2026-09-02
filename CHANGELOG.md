# Changelog

The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
versions follow [semantic versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-09-02

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

[Unreleased]: https://github.com/kiliantscherny/yaybo/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kiliantscherny/yaybo/releases/tag/v0.2.0
