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
ejendom_uuid.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

TEXT, INTEGER, DECIMAL, DATE = "VARCHAR", "BIGINT", "DOUBLE", "DATE"

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
            ("adresse", TEXT),
            ("dato_loebenummer", TEXT),
            ("prioritet", INTEGER),
            ("dokumenttype", TEXT),
            ("hovedstol", TEXT),
            ("hovedstol_dkk", INTEGER),
            ("rentesats_pct", DECIMAL),
            ("rentetype", TEXT),
            ("kreditorer", TEXT),
            ("dokument_version", TEXT),
        ],
    },
    "servitutter": {
        "key": "ejendom_uuid",
        "columns": [
            ("ejendom_uuid", TEXT),
            ("dokument_uuid", TEXT),
            ("adresse", TEXT),
            ("dato_loebenummer", TEXT),
            ("prioritet", INTEGER),
            ("dokumenttype", TEXT),
            ("tekst", TEXT),
            ("dokument_version", TEXT),
        ],
    },
    "adkomsthistorik": {
        "key": "ejendom_uuid",
        "columns": [
            ("ejendom_uuid", TEXT),
            ("adresse", TEXT),
            ("dato", DATE),
            ("dokumenttype", TEXT),
            ("historiske_ejere", TEXT),
        ],
    },
    "attester": {
        "key": "ejendom_uuid",
        "columns": [
            ("ejendom_uuid", TEXT),
            ("adresse", TEXT),
            ("format", TEXT),
            ("dokument", TEXT),
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
                holes = ", ".join("?" * (len(columns) + 1))
                db.executemany(f'INSERT INTO "{name}" VALUES ({holes})', values)
            written[name] = len(rows)

    return written


def _coerce(value, sort: str):
    """Turn a scraped string into something the column can hold, or nothing.

    The register mixes numbers with their units ("26.000 DKK", "55 kvm") and
    writes thousands with full stops, so everything arrives as text and has to
    be talked into a type. Anything that will not go becomes NULL rather than
    stopping the run - a figure we cannot read is not worth losing the row over.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if sort == TEXT:
        return text

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
