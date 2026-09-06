"""Keep a run's results in a DuckDB database.

A CSV is a fine thing to hand someone, but a poor thing to accumulate: every
lookup writes another file, and answering "which flats in this postcode changed
hands last year" means gluing dozens of them back together. The database is the
same data with the joins already possible, and re-running an address updates it
in place rather than leaving another copy behind.

The shape differs from the CSVs in one way. A spreadsheet wants a property's
owners widened across the row - ejer_1_navn, ejer_2_navn - which means the
columns change with however many co-owners a building happens to have. A
database wants them as rows, so `ejere` is its own table and joins back on
ejendom_uuid. The same reasoning gives everyone attached to a mortgage their
own rows in `dokument_parter`, and every historical owner theirs in
`adkomsthistorik_ejere`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import TypedDict

# DuckDB will not open a second connection to a file that is already open with a
# different configuration, so a read that overlaps a write fails outright. In the
# TUI those happen on different threads - a queue writing while the library reads
# - so every connection this module opens is taken under one lock. They are all
# short, and the alternative is an empty screen or a lost fetch.
_ACCESS = threading.RLock()

log = logging.getLogger(__name__)

TEXT, INTEGER, DECIMAL, DATE = "VARCHAR", "BIGINT", "DOUBLE", "DATE"
BOOLEAN, JSON = "BOOLEAN", "JSON"


class TableSpec(TypedDict):
    """What one table is: how it is replaced, what identifies a row, its columns.

    `key` is the column a re-run deletes on - always the property, because a
    property is what gets fetched. `pk` is what makes a row unique within the
    table, which is a different question and usually needs more columns.
    """

    key: str
    pk: list[str]
    columns: list[tuple[str, str]]


# Every table records what it was keyed on, so a re-run can replace exactly the
# properties it fetched and leave the rest of the database alone.
TABLES: dict[str, TableSpec] = {
    "ejendomme": {
        "key": "uuid",
        "pk": ["uuid"],
        "columns": [
            ("uuid", TEXT),
            ("adresse", TEXT),
            ("lejlighed", TEXT),
            ("ejendomstype", TEXT),
            ("ejerlejlighedsnr", TEXT),
            ("bfe_nr", TEXT),
            ("areal_m2", INTEGER),
            ("opdelingsdato", DATE),
            ("fordelingstal", TEXT),
            ("adkomst_dokumenttype", TEXT),
            ("adkomst_dato_loebenummer", TEXT),
            ("koebesum_dkk", INTEGER),
            ("overtagelsesdato", DATE),
            ("ejendomsvurdering_dkk", INTEGER),
            ("grundvaerdi_dkk", INTEGER),
            ("vurderingsdato", DATE),
            ("kommune", TEXT),
            ("kommunalt_ejendomsnr", TEXT),
            ("landsejerlav", TEXT),
            ("matrikel", TEXT),
            ("grund_bfe", TEXT),
            ("grund_areal_m2", INTEGER),
            ("antal_haeftelser", INTEGER),
            ("antal_servitutter", INTEGER),
            # DAWA's address UUID, which is also Boligsiden's key.
            ("adresse_uuid", TEXT),
            ("boligtype", TEXT),
            # Not the same measure as the register's "tinglyste areal": this is
            # the BBR living area, which is what a listing quotes.
            ("boligareal_m2", INTEGER),
            ("boligsiden_vurdering_dkk", INTEGER),
            ("til_salg", BOOLEAN),
            ("boligsiden_url", TEXT),
            ("breddegrad", DECIMAL),
            ("laengdegrad", DECIMAL),
            ("seneste_salg_dato", DATE),
            ("seneste_salg_dkk", INTEGER),
            ("seneste_salg_pris_m2", INTEGER),
            # Worked out from the rows above rather than fetched. The public
            # valuation is the base, and it runs well below market, so treat
            # frivaerdi as a floor and belaaningsgrad as a ceiling.
            ("samlet_gaeld_dkk", INTEGER),
            ("frivaerdi_dkk", INTEGER),
            ("belaaningsgrad_pct", DECIMAL),
            # Whether this property was fetched by someone the register knew.
            # Per property, not per run: a session that lapses partway leaves
            # some rows with owners' birth dates and previous owners and some
            # without, and only the row itself can say which it is.
            ("beriget", BOOLEAN),
        ],
    },
    "ejere": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "nummer"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("nummer", INTEGER),
            ("navn", TEXT),
            ("foedselsdato", DATE),
            ("cvr", TEXT),
            ("andel", TEXT),
        ],
    },
    "haeftelser": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "dokument_uuid", "dokument_version"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("dokument_uuid", TEXT),
            ("dokument_version", TEXT),
            ("adresse", TEXT),
            ("dato_loebenummer", TEXT),
            ("prioritet", INTEGER),
            ("rettighed_uuid", TEXT),
            ("dokumenttype", TEXT),
            ("dokumenttype_beskrivelse", TEXT),
            ("formularkode", TEXT),
            ("hovedstol", TEXT),
            ("hovedstol_dkk", INTEGER),
            ("valuta", TEXT),
            # A fixed rate is one number; a variable one is a named reference
            # rate plus a margin, and the three columns together are the term.
            ("rentetype", TEXT),
            ("rentesats_pct", DECIMAL),
            ("reference_rente", TEXT),
            ("reference_rente_pct", DECIMAL),
            ("rente_margin_pct", DECIMAL),
            ("rente_foreloebig", BOOLEAN),
            ("laantype", TEXT),
            ("saerlige_vilkaar", JSON),
            ("kreditorbetegnelse", TEXT),
            ("kreditorer", TEXT),
            ("tinglysningsdato", DATE),
            ("senest_paategnet", DATE),
            ("overfoert", BOOLEAN),
            ("konverteret_pantebrev", BOOLEAN),
            ("afgift_dkk", INTEGER),
            ("afgift_overfoert", BOOLEAN),
            ("antal_respekt", INTEGER),
            ("antal_underpant", INTEGER),
            ("tekst", TEXT),
            # Estimated, not recorded: the register gives a rate and never the
            # product. See laantype.py for how far that can be trusted.
            ("laantype_estimat", TEXT),
            ("laantype_afstand", DECIMAL),
            ("laantype_alternativ", TEXT),
            ("laantype_afgjort_af", TEXT),
            ("laantype_kilde", TEXT),
        ],
    },
    "servitutter": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "dokument_uuid", "dokument_version"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("dokument_uuid", TEXT),
            ("dokument_version", TEXT),
            ("adresse", TEXT),
            ("dato_loebenummer", TEXT),
            ("prioritet", INTEGER),
            ("rettighed_uuid", TEXT),
            ("dokumenttype", TEXT),
            ("indhold", JSON),
            ("tekst", TEXT),
            ("paataleberettigede", TEXT),
            ("ogsaa_lyst_paa", INTEGER),
            ("uden_ejers_tiltraedelse", BOOLEAN),
            ("prioritet_forud", BOOLEAN),
            ("betydning_for_vaerdi", BOOLEAN),
            ("tinglysningsdato", DATE),
            ("senest_paategnet", DATE),
            ("overfoert", BOOLEAN),
            ("afgift_dkk", INTEGER),
            ("akt_filnavn", TEXT),
        ],
    },
    # Everyone named on a document, whatever end of it they are on. A mortgage
    # names a creditor and a debtor, often a notice-holder and an agent as
    # well, and each of them carries a date of birth or a CVR number - which
    # is the whole reason the logged-in record is worth fetching.
    "dokument_parter": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "dokument_uuid", "dokumentart", "rolle", "nummer"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("dokument_uuid", TEXT),
            ("dokumentart", TEXT),
            ("rolle", TEXT),
            ("nummer", INTEGER),
            ("navn", TEXT),
            ("foedselsdato", DATE),
            ("cvr", TEXT),
            ("andel", TEXT),
            ("adresse_kode", TEXT),
        ],
    },
    # A pledge of the mortgage deed itself - a charge on a charge, with its own
    # amount, its own priority and its own holder.
    "underpant": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "haeftelse_uuid", "rettighed_uuid"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("haeftelse_uuid", TEXT),
            ("dokument_uuid", TEXT),
            ("dato_loebenummer", TEXT),
            ("rettighed_uuid", TEXT),
            ("beloeb_dkk", INTEGER),
            ("valuta", TEXT),
            ("prioritet", INTEGER),
            ("panthavere", TEXT),
        ],
    },
    # The second book. An andel is not real property, so it is not an
    # ejendom: the association owns the building - one row in `ejendomme`
    # however many doors it has - and an andelshaver owns a share in the
    # association carrying the right to one flat. Every column an ejendom has
    # because it is real property - the valuation, the matrikel, the BFE
    # number, the tinglyste areal - has no counterpart here at all, which is
    # why these are their own tables rather than rows in those.
    #
    # There is no `beriget` here as there is on `ejendomme`. Only the public
    # book is read, and it answers a logged-in session exactly as it answers
    # nobody, so an andel row is never the thinner of two possible readings.
    "andele": {
        "key": "uuid",
        "pk": ["uuid"],
        "columns": [
            # The andelsboligbog's own uuid, from a different register than
            # ejendomme.uuid. The two namespaces never mix.
            ("uuid", TEXT),
            ("adresse", TEXT),
            ("lejlighed", TEXT),
            # How the book addresses a flat, and the only key its own search
            # by municipality accepts.
            ("kommunekode", TEXT),
            ("vejkode", TEXT),
            # The association's property in the tingbog, when the same lookup
            # found it. This is what joins an andel to the building it is in,
            # and through it to the association's own mortgages and easements.
            ("ejendom_uuid", TEXT),
            ("bygning_adresse", TEXT),
            ("antal_haeftelser", INTEGER),
            ("antal_meddelelser", INTEGER),
            # What is charged against this share alone. Not what the flat
            # owes: an andelshaver also owes a share of the association's own
            # mortgage, which is registered against the building in the
            # tingbog and is nowhere in this table.
            ("samlet_gaeld_dkk", INTEGER),
            # Boligsiden, keyed on DAWA's address uuid. The book says nothing
            # whatever about the flat itself, so without this an andel is an
            # address and a debt and no more. Its boligtype reads
            # "cooperative", which is a second opinion on what this is.
            ("adresse_uuid", TEXT),
            ("boligtype", TEXT),
            ("boligareal_m2", INTEGER),
            ("boligsiden_vurdering_dkk", INTEGER),
            ("til_salg", BOOLEAN),
            ("boligsiden_url", TEXT),
            ("breddegrad", DECIMAL),
            ("laengdegrad", DECIMAL),
            # No seneste_salg_* here, though Boligsiden offers one. A share is
            # not sold as real property, so the only transfer ever recorded at
            # a co-op address is the building's own sale to the association -
            # the same date and amount on every door in the block, divided by
            # each flat's area into a price per square metre that means
            # nothing. That sale is a fact about the building, and is already
            # stored as one on the ejendomme row this andel joins to.
        ],
    },
    # Notices noted on a share, which is the only place this book names anyone
    # other than a creditor. They are the register recording that something has
    # happened to the andelshaver rather than to the andel: a death, a
    # bankruptcy, a gældssanering, a court removing their power to dispose of
    # it. Hence the two lists of names - the debtor the notice concerns, and
    # whoever may act for them.
    #
    # Read from the labels and bindings of the register's own public view of a
    # share rather than from an observed payload: none of the andele sampled
    # while writing this had a notice on them, which is what one would hope.
    # Every field is therefore optional and absent means absent.
    "andel_meddelelser": {
        "key": "andel_uuid",
        # No document uuid to key on the way a charge has: the view reads only
        # the date and serial, which identifies one registration.
        "pk": ["andel_uuid", "dato_loebenummer"],
        "columns": [
            ("andel_uuid", TEXT),
            ("dato_loebenummer", TEXT),
            ("adresse", TEXT),
            ("prioritet", INTEGER),
            ("dokumenttype", TEXT),
            ("afgoerelsesdato", DATE),
            ("debitorer", TEXT),
            ("disponenter", TEXT),
            ("tillaegstekst", TEXT),
            ("dokument_uuid", TEXT),
            ("dokument_version", TEXT),
        ],
    },
    # Charges registered against one share. The book states these in exactly
    # the fields the tingbog uses for a property's mortgages, so these columns
    # are the public half of `haeftelser` under a different key - and only
    # that half, because there is no andel counterpart to the attest the
    # logged-in columns are read out of.
    "andel_haeftelser": {
        "key": "andel_uuid",
        "pk": ["andel_uuid", "dokument_uuid", "dokument_version"],
        "columns": [
            ("andel_uuid", TEXT),
            ("dokument_uuid", TEXT),
            ("dokument_version", TEXT),
            ("adresse", TEXT),
            ("dato_loebenummer", TEXT),
            ("prioritet", INTEGER),
            ("dokumenttype", TEXT),
            ("hovedstol", TEXT),
            ("hovedstol_dkk", INTEGER),
            ("rentetype", TEXT),
            ("rentesats_pct", DECIMAL),
            ("kreditorer", TEXT),
        ],
    },
    # Every recorded sale of the address, from Boligsiden. This overlaps
    # adkomsthistorik and does not replace it: the register knows transfers
    # that were never a sale, and Boligsiden knows the area and the price per
    # square metre, which the register does not record.
    "handelshistorik": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "registrering_id"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("adresse", TEXT),
            ("dato", DATE),
            ("beloeb_dkk", INTEGER),
            ("areal_m2", INTEGER),
            ("pris_pr_m2", INTEGER),
            ("handelstype", TEXT),
            ("handelstype_kode", TEXT),
            ("registrering_id", TEXT),
        ],
    },
    # The BBR record for the building the property sits in: what it is made
    # of, when it went up, and how it is heated. The land register says none
    # of this.
    "bygninger": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "bygning_nr"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("adresse", TEXT),
            ("bygning_nr", TEXT),
            ("bygningstype", TEXT),
            ("opfoerelsesaar", INTEGER),
            ("ombygningsaar", INTEGER),
            ("etager", INTEGER),
            ("vaerelser", INTEGER),
            ("badevaerelser", INTEGER),
            ("toiletter", INTEGER),
            ("boligareal_m2", INTEGER),
            ("kaelderareal_m2", INTEGER),
            ("erhvervsareal_m2", INTEGER),
            ("andet_areal_m2", INTEGER),
            ("samlet_areal_m2", INTEGER),
            ("ydervaeg", TEXT),
            ("tagdaekning", TEXT),
            ("varmeinstallation", TEXT),
            ("supplerende_varme", TEXT),
            ("koekken", TEXT),
            ("badeforhold", TEXT),
            ("toiletforhold", TEXT),
        ],
    },
    "adkomsthistorik": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "post_nummer"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("post_nummer", INTEGER),
            ("adresse", TEXT),
            ("dato", DATE),
            ("dokumenttype", TEXT),
            ("koebesum_dkk", INTEGER),
            ("antal_ejere", INTEGER),
            ("historiske_ejere", TEXT),
        ],
    },
    # The owners named in each history entry, read out of the block of text
    # the register prints there. Joins back on (ejendom_uuid, post_nummer).
    "adkomsthistorik_ejere": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid", "post_nummer", "nummer"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("post_nummer", INTEGER),
            ("dato", DATE),
            ("nummer", INTEGER),
            ("navn", TEXT),
            ("foedselsdato", DATE),
            ("cvr", TEXT),
            ("andel", TEXT),
            ("note", TEXT),
        ],
    },
    # What each kind of realkredit loan cost, month by month, straight from
    # Danmarks Statistik's DNRNURI. Not about any one property: it is the
    # series the laantype_estimat column was read against, kept so an estimate
    # can be checked rather than taken on trust.
    "rentestatistik": {
        "key": "maaned",
        "pk": ["maaned", "rentfix_kode"],
        "columns": [
            ("maaned", TEXT),
            ("rentfix_kode", TEXT),
            ("laantype", TEXT),
            ("effektiv_rente_pct", DECIMAL),
            ("bidrag_pct", DECIMAL),
            ("kupon_pct", DECIMAL),
        ],
    },
    "attester": {
        "key": "ejendom_uuid",
        "pk": ["ejendom_uuid"],
        "columns": [
            ("ejendom_uuid", TEXT),
            ("adresse", TEXT),
            ("format", TEXT),
            # The document twice over. `dokument` is the bytes the register
            # signed, kept because a re-rendered copy is no longer the thing it
            # signed and a duckdb-only run writes no file to keep it in.
            # `dokument_json` is the same content with the namespace prefixes
            # dropped, which is the copy meant to be queried - json_extract can
            # reach into it, and it is smaller than the XML because those
            # prefixes were most of the bytes.
            ("dokument", TEXT),
            ("dokument_json", JSON),
        ],
    },
}

# Stamped on every row. Two runs a year apart leave the newer one in place, and
# this is how you tell when what you are looking at was true.
FETCHED = "hentet"


def save(path: str | Path, tables: dict[str, list[dict]]) -> dict[str, int]:
    """Write each table's rows, replacing whatever the same properties left.

    Returns the row count written per table. Tables with nothing to write are
    still created, so a query against an empty run does not fail.
    """
    import duckdb  # imported here so a CSV-only run needs no database at all

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = datetime.now()
    written = {}

    with _ACCESS, duckdb.connect(str(path)) as db:
        for name, spec in TABLES.items():
            columns = spec["columns"]
            definition = ", ".join(f'"{column}" {sort}' for column, sort in columns)
            primary = ", ".join(f'"{column}"' for column in spec["pk"])
            db.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" '
                f'({definition}, "{FETCHED}" TIMESTAMP, PRIMARY KEY ({primary}))'
            )
            _add_new_columns(db, name, columns)

            rows = tables.get(name) or []
            keys = sorted({str(row.get(spec["key"], "")) for row in rows} - {""})
            if keys:
                # Replace rather than append: running the same address twice is
                # a correction, not two observations.
                holes = ", ".join("?" * len(keys))
                db.execute(
                    f'DELETE FROM "{name}" WHERE "{spec["key"]}" IN ({holes})', keys
                )

            values = _insertable(name, spec, rows)
            if values:
                named = ", ".join(f'"{column}"' for column, _ in columns)
                holes = ", ".join("?" * (len(columns) + 1))
                db.executemany(
                    f'INSERT INTO "{name}" ({named}, "{FETCHED}") VALUES ({holes})',
                    [record + [stamped] for record in values],
                )
            # After the write, not before it. A database from an older version
            # may hold rows this file would now consider duplicates, and the
            # write is what clears them - asking first would refuse the key on
            # data that is about to be replaced, and leave it a run behind.
            _add_primary_key(db, name, spec["pk"])
            written[name] = len(values)

    return written


def _insertable(name: str, spec: TableSpec, rows: list[dict]) -> list[list]:
    """Coerce a table's rows and reduce them to one row per primary key.

    Two things have to happen before a keyed table will accept a batch, and
    both are about the batch rather than about what is already stored.

    A row whose key is not complete has nowhere to go: DuckDB makes every
    primary key column NOT NULL, so one unreadable field would otherwise take
    the whole run down with it. Those rows are dropped and said out loud,
    because a row quietly missing is worse than a row known to be missing.

    A key seen twice in one batch is the same row described twice - the same
    document reached by two routes - so the last description wins. Without
    this the insert fails outright, which is a poor answer to "the register
    listed this document under two of its versions".
    """
    columns = spec["columns"]
    places = {column: index for index, (column, _) in enumerate(columns)}
    wanted = [places[column] for column in spec["pk"]]

    seen: dict[tuple, list] = {}
    dropped = 0
    for row in rows:
        record = [coerce(row.get(column), sort) for column, sort in columns]
        identity = tuple(record[index] for index in wanted)
        if any(part is None for part in identity):
            dropped += 1
            continue
        seen[identity] = record

    if dropped:
        log.warning(
            "%s: dropped %d row(s) with an incomplete %s",
            name, dropped, " + ".join(spec["pk"]),
        )
    if (folded := len(rows) - dropped - len(seen)) > 0:
        log.debug("%s: folded %d row(s) onto a key already in the batch", name, folded)
    return list(seen.values())


def _add_primary_key(db, name: str, pk: list[str]) -> None:
    """Give a table written before this file declared keys its primary key.

    CREATE TABLE IF NOT EXISTS leaves an existing table exactly as it was, so a
    database from an earlier version has the columns and none of the key.
    DuckDB can add one after the fact - ALTER TABLE ... ADD PRIMARY KEY - which
    is the only reason enforcing keys here is worth doing at all: an existing
    database gains them on the next write rather than needing to be thrown away.

    It refuses when the stored rows already contain duplicates or a NULL in a
    key column, and that refusal must not stop the write. Such a database keeps
    working exactly as before, unkeyed, and `yaybo backfill` rebuilds the
    derived tables cleanly enough for the key to take.
    """
    held = db.execute(
        "SELECT count(*) FROM duckdb_constraints() "
        "WHERE database_name = current_database() "
        "AND table_name = ? AND constraint_type = 'PRIMARY KEY'",
        [name],
    ).fetchone()
    if held and held[0]:
        return
    primary = ", ".join(f'"{column}"' for column in pk)
    try:
        db.execute(f'ALTER TABLE "{name}" ADD PRIMARY KEY ({primary})')
    except Exception as error:  # noqa: BLE001 - any refusal is the same answer
        log.warning(
            "%s: keeping the table unkeyed - %s. Run `yaybo backfill` to rebuild it.",
            name, str(error).splitlines()[0],
        )


def _add_new_columns(db, name: str, columns) -> None:
    """Bring an existing table up to the current schema.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    a database written by an older version keeps its old columns and the insert
    below would not match. Columns are only ever added: one this file no longer
    declares is left alone rather than dropped, because a column holding data
    is not ours to throw away on a schema change.
    """
    present = {row[0] for row in db.execute(f'DESCRIBE "{name}"').fetchall()}
    for column, sort in columns:
        if column not in present:
            db.execute(f'ALTER TABLE "{name}" ADD COLUMN "{column}" {sort}')


def coerce(value, sort: str):
    """Turn a scraped string into something the column can hold, or nothing.

    The register mixes numbers with their units ("26.000 DKK", "55 kvm") and
    writes thousands with full stops, so everything arrives as text and has to
    be talked into a type. Anything that will not go becomes NULL rather than
    stopping the run - a figure we cannot read is not worth losing the row over.
    """
    if value is None:
        return None
    if sort == JSON:
        # A list or dict is rendered here; a string is assumed to be JSON
        # already, which is how the whole-document column arrives.
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False) if value else None
        return str(value) or None

    if isinstance(value, bool):
        return value if sort == BOOLEAN else None
    if isinstance(value, (int, float)):
        # Already a number, from a reader that knew which dialect it was in.
        # Passing it back through the Danish text rules below would reread
        # "3.5" as thirty-five hundred.
        if sort == INTEGER:
            return int(value)
        return float(value) if sort == DECIMAL else str(value)

    text = str(value).strip()
    if not text:
        return None
    if sort == TEXT:
        return text
    if sort == BOOLEAN:
        # The register writes OIO booleans as "true"/"false"; anything else is
        # a field it left empty, which is not the same as False.
        return {"true": True, "false": False}.get(text.lower())

    if sort == DATE:
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
        return f"{match[1]}-{match[2]}-{match[3]}" if match else None

    number = re.sub(r"[^\d,.-]", "", text)
    # Danish thousands separators are full stops and the decimal mark is a
    # comma - the exact opposite of what float() expects.
    if "," in number:
        number = number.replace(".", "").replace(",", ".")
    elif number.count(".") > 1 or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", number):
        number = number.replace(".", "")
    try:
        return int(float(number)) if sort == INTEGER else float(number)
    except ValueError:
        return None


# ── Reading it back ─────────────────────────────────────────────────────
#
# A database that only ever gets written to is a folder of CSVs with extra
# steps. These are what the TUI browses: the properties it already holds, one
# property in full, and whatever SQL somebody types.

# Where a run leaves its results unless told otherwise. Git-ignored, because
# every row in it names a real person and says what they paid for their home.
OUTDIR = "out"
DATABASE = "tinglysning.duckdb"


def default_path(outdir: str | Path = OUTDIR) -> Path:
    return Path(outdir) / DATABASE


@contextlib.contextmanager
def _reading(path: str | Path):
    """A read-only connection, or None when there is nothing to read.

    Holds the access lock for as long as the connection is open, so a write
    cannot start underneath it.
    """
    import duckdb

    path = Path(path)
    with _ACCESS:
        if not path.exists():
            yield None
            return
        try:
            db = duckdb.connect(str(path), read_only=True)
        except Exception:
            # A database another process holds open for writing, or one from a
            # duckdb too new to read. Either way there is nothing to show.
            yield None
            return
        try:
            yield db
        finally:
            db.close()


def _rows(db, sql: str, *args) -> list[dict]:
    """Run a query and return its rows as dicts, keyed on the column names."""
    result = db.execute(sql, args)
    names = [column[0] for column in result.description or []]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def held_tables(path: str | Path) -> list[tuple[str, int]]:
    """Every table in the database and how many rows it holds."""
    with _reading(path) as db:
        if db is None:
            return []
        names = [row[0] for row in db.execute("SHOW TABLES").fetchall()]
        return [
            (name, db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
            for name in names
        ]


def library(path: str | Path) -> list[dict]:
    """One row per property held, with enough on it to choose from a list.

    Owners are counted and named from `ejere` rather than read off the widened
    columns, because the widened ones stop at however many the run found and
    this has to be right for a property with five.
    """
    with _reading(path) as db:
        if db is None:
            return []
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        if "ejendomme" not in held:
            return []
        owners = (
            """
            LEFT JOIN (
                SELECT ejendom_uuid,
                       count(*) AS antal_ejere,
                       string_agg(navn, '; ' ORDER BY nummer) AS ejere
                FROM ejere GROUP BY ejendom_uuid
            ) o ON o.ejendom_uuid = e.uuid
            """
            if "ejere" in held
            else ""
        )
        columns = (
            "o.antal_ejere, o.ejere,"
            if owners
            else "NULL AS antal_ejere, NULL AS ejere,"
        )
        # A database written before this column existed still has to open. It
        # gains the column the next time anything is saved into it; until then
        # every row honestly reports "not known" rather than failing to load.
        present = {row[0] for row in db.execute("DESCRIBE ejendomme").fetchall()}
        beriget = "e.beriget," if "beriget" in present else "NULL AS beriget,"
        return _rows(
            db,
            f"""
            SELECT e.uuid, e.adresse, e.lejlighed, e.boligtype, e.ejendomstype,
                   e.boligareal_m2, e.areal_m2, e.ejendomsvurdering_dkk,
                   e.samlet_gaeld_dkk, e.frivaerdi_dkk, e.belaaningsgrad_pct,
                   e.seneste_salg_dato, e.seneste_salg_dkk, e.seneste_salg_pris_m2,
                   e.til_salg, e.antal_haeftelser, e.antal_servitutter,
                   e.boligsiden_url, e.breddegrad, e.laengdegrad,
                   {beriget}
                   {columns}
                   e."{FETCHED}" AS hentet
            FROM ejendomme e
            {owners}
            ORDER BY e."{FETCHED}" DESC NULLS LAST, e.adresse
            """,
        )


def andele(path: str | Path) -> list[dict]:
    """One row per co-op share held, with enough on it to choose from a list.

    Joined out to the association's property wherever one was found, because
    nearly everything a share does not have is on that row: the block's public
    valuation, the association's own mortgages, its easements. On its own a
    share is an address, an area and a debt, and the join is what makes the
    first of those mean anything.
    """
    with _reading(path) as db:
        if db is None:
            return []
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        if "andele" not in held:
            return []
        # Named columns rather than a star, so every column a database is
        # missing is asked for by name and answered with NULL. A table only
        # gains a column when something is next saved into it, and reading is
        # not writing - opening a screen must not depend on having fetched
        # since the column was added. This is a young table and will gain more,
        # so the whole list is checked rather than the newest of them.
        present = {row[0] for row in db.execute("DESCRIBE andele").fetchall()}

        def column(name: str) -> str:
            return f'a."{name}"' if name in present else f'NULL AS "{name}"'

        # A database fetched with --no-andele, or written before either table
        # existed, still has to open.
        building = "LEFT JOIN ejendomme e ON e.uuid = a.ejendom_uuid"
        columns = (
            "e.adresse AS bygning, e.ejendomsvurdering_dkk AS bygning_vurdering_dkk,"
            " e.samlet_gaeld_dkk AS bygning_gaeld_dkk,"
        )
        if "ejendomme" not in held:
            building = ""
            fallback = (
                'a."bygning_adresse"' if "bygning_adresse" in present else "NULL"
            )
            columns = (
                f"{fallback} AS bygning, NULL AS bygning_vurdering_dkk,"
                " NULL AS bygning_gaeld_dkk,"
            )
        wanted = (
            "uuid", "adresse", "lejlighed", "boligtype", "boligareal_m2",
            "samlet_gaeld_dkk", "antal_haeftelser", "antal_meddelelser",
            "til_salg", "boligsiden_url", "ejendom_uuid", "bygning_adresse",
            "kommunekode", "vejkode", "breddegrad", "laengdegrad",
        )
        picked = ", ".join(column(name) for name in wanted)
        return _rows(
            db,
            f"""
            SELECT {picked},
                   {columns}
                   a."{FETCHED}" AS hentet
            FROM andele a
            {building}
            ORDER BY a."{FETCHED}" DESC NULLS LAST, a.adresse
            """,
        )


def property_tables(path: str | Path, uuid: str) -> dict[str, list[dict]]:
    """Every row in the database belonging to one property.

    Keyed the way TABLES is, so the same dict can be handed straight to an
    exporter or to a screen. Tables the property has no rows in are left out
    rather than present and empty.
    """
    found: dict[str, list[dict]] = {}
    with _reading(path) as db:
        if db is None:
            return {}
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        for name, spec in TABLES.items():
            if name not in held or spec["key"] not in ("uuid", "ejendom_uuid"):
                continue
            rows = _rows(db, f'SELECT * FROM "{name}" WHERE "{spec["key"]}" = ?', uuid)
            if rows:
                found[name] = rows
    return found


def andel_tables(path: str | Path, uuid: str) -> dict[str, list[dict]]:
    """Every row in the database belonging to one co-op share.

    property_tables' counterpart for the other book, and a separate function
    because the keys are different: `andele` is keyed on its own uuid and
    everything hanging off it on andel_uuid, and neither is the ejendom_uuid
    the property tables all join on.
    """
    found: dict[str, list[dict]] = {}
    with _reading(path) as db:
        if db is None:
            return {}
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        for name in ("andele", "andel_haeftelser", "andel_meddelelser"):
            if name not in held:
                continue
            key = TABLES[name]["key"]
            rows = _rows(db, f'SELECT * FROM "{name}" WHERE "{key}" = ?', uuid)
            if rows:
                found[name] = rows
    return found


def held_addresses(path: str | Path) -> list[tuple[str, datetime | None]]:
    """Every property held, as (address, when it was fetched).

    Deliberately thin: the search screen wants to know whether it already has
    an address before spending a request on it, and that question needs two
    columns rather than the whole library row.
    """
    with _reading(path) as db:
        if db is None:
            return []
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        if "ejendomme" not in held:
            return []
        return [
            (row["adresse"] or "", row["hentet"])
            for row in _rows(
                db, f'SELECT adresse, "{FETCHED}" AS hentet FROM ejendomme'
            )
        ]


def tables_for(path: str | Path, uuids: list[str]) -> dict[str, list[dict]]:
    """Every row belonging to any of these properties, shaped like `everything`.

    The multi-property counterpart of `property_tables`, for exporting a chosen
    handful rather than one or the lot. An empty list of uuids gives nothing
    back rather than everything: "export what I ticked" with nothing ticked is
    a question for the caller, not a licence to dump the database.
    """
    if not uuids:
        return {}
    found: dict[str, list[dict]] = {}
    holes = ", ".join("?" * len(uuids))
    with _reading(path) as db:
        if db is None:
            return {}
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        for name, spec in TABLES.items():
            key = spec["key"]
            if name not in held or key not in ("uuid", "ejendom_uuid"):
                continue
            rows = _rows(
                db, f'SELECT * FROM "{name}" WHERE "{key}" IN ({holes})', *uuids
            )
            if rows:
                found[name] = rows
    return found


def stats_tables(path: str | Path, wanted: tuple[str, ...]) -> dict[str, list[dict]]:
    """Whole tables, by name, for aggregating across every property at once.

    `everything` would do, except that it also reads `attester`, which holds
    the register's own document for each property - hundreds of kilobytes
    apiece, and nothing that counts properties has any use for them.
    """
    with _reading(path) as db:
        if db is None:
            return {}
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        return {
            name: _rows(db, f'SELECT * FROM "{name}"')
            for name in wanted
            if name in held
        }


def everything(path: str | Path) -> dict[str, list[dict]]:
    """The whole database as rows, for exporting it somewhere else entirely."""
    with _reading(path) as db:
        if db is None:
            return {}
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        return {
            name: _rows(db, f'SELECT * FROM "{name}"')
            for name in TABLES
            if name in held
        }


class QueryError(Exception):
    """Raised when a typed query will not run."""


def run_query(path: str | Path, sql: str) -> tuple[list[str], list[tuple]]:
    """Run one read-only query and return its columns and rows.

    Read-only is enforced by the connection, not by reading the SQL: DuckDB
    refuses a write on a read-only handle, which is a far better guarantee than
    looking for the word "delete".
    """
    with _reading(path) as db:
        if db is None:
            raise QueryError(f"no database at {path}")
        try:
            result = db.execute(sql)
            names = [column[0] for column in result.description or []]
            return names, result.fetchall()
        except Exception as error:
            raise QueryError(str(error)) from error
