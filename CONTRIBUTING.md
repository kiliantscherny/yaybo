# Contributing

> [!NOTE]
> This is a hobby project. Issues and pull requests are welcome, but there is
> no roadmap and no support commitment.

> [!IMPORTANT]
> Everything this fetches is about real, named people. Please don't put real
> register data in an issue, a pull request, a test fixture or a screenshot.
> The fixtures under `tests/` are real documents with invented people in them,
> and that is the only shape data takes in this repository.

## Setup

You need [uv](https://docs.astral.sh/uv/). Python comes with it.

```sh
git clone https://github.com/kiliantscherny/yaybo.git
cd yaybo
uv sync
uv run yaybo
```

Optionally, install the pre-commit hooks with
[prek](https://github.com/j178/prek):

```sh
prek install
```

That runs ruff, ty and `uv lock --check` before each commit, which is most of
what CI would have told you a few minutes later.

## Running the checks

```sh
uv run tox          # tests on 3.10-3.14, plus lint and types
uv run pytest       # just the tests, on the local interpreter
uvx ruff check      # lint
uvx ty@0.0.37 check # types
```

`.python-version` pins local development to **3.10**, the oldest version the
package supports. Working there means syntax or stdlib calls that need
something newer fail immediately, rather than passing locally and breaking for
someone on the version you claim to support.

For the TUI, `uv run textual console` in one terminal and
`uv run textual run --dev yaybo.app:YayboApp` in another gives you logs and
live CSS reloading.

## Layout

```
src/yaybo/
├── cli.py             the command line: fetch, login, status, backfill
├── app.py             the Textual application, and what every screen shares
├── pipeline.py        one address in, a set of tables out - the TUI and the
│                      CLI both go through here, so neither can drift
├── store.py           the DuckDB schema, saving, and read-only querying
├── export.py          the same tables as DuckDB, Excel or CSV
├── backfill.py        re-derive stored tables without fetching anything
├── auth.py            the MitID login, as tinglysning.dk wants it
├── display.py         formatting numbers, dates and Danish text for a screen
├── register/          tinglysning.dk itself
│   ├── client.py      the HTTP session, its proof-of-work and its tokens
│   ├── address.py     DAWA lookup and address parsing
│   ├── attest.py      the signed attest, as text
│   ├── attest_xml.py  the same document as XML, which says far more
│   ├── historik.py    parsing the historical-owner blobs
│   ├── rows.py        turning all of that into the tables in store.py
│   └── fields.py      the small shared normalisers
├── enrich/            the two public sources that need no login
│   ├── boligsiden.py  sale prices, price per m², and the BBR record
│   └── laantype.py    DST rates, and reading a bare rate as a loan product
├── screens/           library, search, property, queue, sql
├── widgets/           the export dialog
└── styles/            Textual CSS

tests/                 fixtures with invented people, and what they prove
```

## What goes where

**A new column** starts in `store.TABLES`, which is the schema and the column
order for every export at once. Fill it in `register/rows.py` if it comes out
of the attest, or in `enrich/` if it comes from outside.

**A derived column** - anything worked out rather than recorded - belongs in
`register/rows.py` next to the others, and needs a line in the README's warning
about them. `laantype_estimat` is the worked example: it keeps the distance to
the runner-up and the rate series it was read against, so the estimate can be
argued with.

**A screen** goes in `screens/`, inherits `YayboScreen` so `self.app` is typed,
and reads the database for itself rather than being handed rows. That is what
makes a fetch on one screen show up on the next.

**Anything to do with logging in** belongs in
[mitid-client](https://github.com/kiliantscherny/mitid-client), not here.
`auth.py` is only the tinglysning-shaped part: which URL to start at, and what
to do with the cookie that comes back.

## Tests

`uv run pytest`. What is covered is the part that can be: parsing real
documents, the derived columns, the store's round trip, and the TUI driven
through `App.run_test()` with the network stubbed out. There is no coverage
target and there shouldn't be - the registers themselves cannot be tested, so a
percentage would only measure that.

New fixtures should be real documents with the people in them replaced. Don't
add a fixture you would not want indexed.

> [!WARNING]
> The registers are public services, not scraping targets. There is a pause
> between requests and no attempt anywhere to go faster than a person clicking.
> A change that removes a delay or widens a loop needs to say why in the pull
> request.

## Commits and releases

Commit messages are plain prose, written in the imperative, explaining why
rather than what. There is no conventional-commit or changelog automation here.

Releases are cut by hand and published by CI. See [RELEASING.md](RELEASING.md).

## Licence

By contributing you agree that your contributions are licensed under the MIT
Licence, as in [LICENSE](LICENSE).
