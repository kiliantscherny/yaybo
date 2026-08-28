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

import json
import re
from datetime import datetime
from pathlib import Path

TEXT, INTEGER, DECIMAL, DATE = "VARCHAR", "BIGINT", "DOUBLE", "DATE"
BOOLEAN, JSON = "BOOLEAN", "JSON"

# Every table records what it was keyed on, so a re-run can replace exactly the
# properties it fetched and leave the rest of the database alone.
TABLES = {
    "ejendomme": {
        "key": "uuid",
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
        ],
    },
    "ejere": {
        "key": "ejendom_uuid",
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
        ],
    },
    "servitutter": {
        "key": "ejendom_uuid",
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
    "adkomsthistorik": {
        "key": "ejendom_uuid",
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
    "attester": {
        "key": "ejendom_uuid",
        "columns": [
            ("ejendom_uuid", TEXT),
            ("adresse", TEXT),
            ("format", TEXT),
            # The whole document, namespaces stripped, as JSON rather than as
            # the XML it arrived in: same content, minus the prefixes, in a
            # form json_extract can reach into. The verbatim XML still goes to
            # a file - this is the copy meant to be queried.
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

    with duckdb.connect(str(path)) as db:
        for name, spec in TABLES.items():
            columns = spec["columns"]
            definition = ", ".join(f'"{column}" {sort}' for column, sort in columns)
            db.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" '
                f'({definition}, "{FETCHED}" TIMESTAMP)'
            )
            _add_new_columns(db, name, columns)

            rows = tables.get(name) or []
            keys = sorted({str(row.get(spec["key"], "")) for row in rows} - {""})
            if keys:
                # Replace rather than append: running the same address twice is
                # a correction, not two observations.
                holes = ", ".join("?" * len(keys))
                db.execute(f'DELETE FROM "{name}" WHERE "{spec["key"]}" IN ({holes})', keys)

            if rows:
                values = [
                    [_coerce(row.get(column), sort) for column, sort in columns] + [stamped]
                    for row in rows
                ]
                named = ", ".join(f'"{column}"' for column, _ in columns)
                holes = ", ".join("?" * (len(columns) + 1))
                db.executemany(
                    f'INSERT INTO "{name}" ({named}, "{FETCHED}") VALUES ({holes})', values
                )
            written[name] = len(rows)

    return written


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


def _coerce(value, sort: str):
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
        return int(value) if sort == INTEGER else float(value) if sort == DECIMAL else str(value)

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
