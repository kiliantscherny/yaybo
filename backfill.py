"""Re-derive every table that comes out of a stored document, without fetching.

The database already holds the register's own answer for each property: the
whole attest in `attester.dokument`, and each transfer's block of free text in
`adkomsthistorik.historiske_ejere`. Everything else - the charges, the
easements, everyone named on them, the historical owners - is read out of
those two, so improving a reader means the existing rows are simply out of
date rather than missing.

    uv run backfill.py --dry-run          # say what would change
    uv run backfill.py                    # rewrite the derived tables

This costs no MitID login and no request to tinglysning. Rows for properties
whose document is not stored are left exactly as they are.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

import duckdb

import attest_xml
import store
import tinglysning_dl as tl

# Read out of the stored documents; everything else in the database is left
# alone, because nothing here knows how to rebuild it.
DERIVED = [
    "haeftelser", "servitutter", "dokument_parter", "underpant",
    "adkomsthistorik", "adkomsthistorik_ejere", "attester",
]


def collect(path: Path) -> tuple[dict, dict]:
    """Read the stored documents and build every derived row from them."""
    tables: dict = collections.defaultdict(list)
    with duckdb.connect(str(path), read_only=True) as db:
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        before = {name: db.sql(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                  for name in sorted(held)}

        history: dict = collections.defaultdict(list)
        if "adkomsthistorik" in held:
            for uuid, dato, art, tekst in db.sql(
                "SELECT ejendom_uuid, dato, dokumenttype, historiske_ejere "
                "FROM adkomsthistorik"
            ).fetchall():
                history[uuid].append(
                    {"dato": "" if dato is None else str(dato),
                     "dokumenttype": art or "", "tekst": tekst or ""}
                )

        documents = db.sql(
            "SELECT ejendom_uuid, adresse, dokument, format FROM attester "
            "WHERE dokument IS NOT NULL"
        ).fetchall()

    for uuid, adresse, raw, kind in documents:
        record = {"adresse": adresse or ""}
        parsed = attest_xml.parse(raw)
        if not parsed:
            # A record stored before the XML route existed, or a rendered
            # attest. Its charges cannot be re-derived, so its rows stay.
            print(f"  skipped (not the XML we can read): {adresse}", file=sys.stderr)
            continue
        tables["haeftelser"] += tl.haeftelse_rows(record, uuid, parsed)
        tables["servitutter"] += tl.servitut_rows(record, uuid, parsed)
        tables["dokument_parter"] += tl.party_rows(parsed, uuid)
        tables["underpant"] += tl.underpant_rows(parsed, uuid)
        entries, owners = tl.history_rows({"items": history.get(uuid, [])}, uuid, adresse)
        tables["adkomsthistorik"] += entries
        tables["adkomsthistorik_ejere"] += owners
        tables["attester"].append(
            {"ejendom_uuid": uuid, "adresse": adresse, "format": kind or "xml",
             "dokument": raw, "dokument_json": tl.attest_json({"_raw": raw})}
        )
    return dict(tables), before


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(Path("out") / "tinglysning.duckdb"),
                        help="the database to rebuild in place")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written and change nothing")
    args = parser.parse_args()

    path = Path(args.db)
    if not path.exists():
        sys.exit(f"no database at {path}")

    tables, before = collect(path)
    if not tables:
        sys.exit("no stored documents to rebuild from")

    print(f"{len(tables['attester'])} document(s) read from {path}", file=sys.stderr)
    for name in DERIVED:
        was, now = before.get(name, 0), len(tables.get(name) or [])
        change = "new table" if name not in before else f"{was} -> {now}"
        print(f"  {name:24} {change}", file=sys.stderr)

    if args.dry_run:
        print("dry run - nothing written", file=sys.stderr)
        return

    written = store.save(path, tables)
    print("rewrote " + ", ".join(f"{n} {name}" for name, n in written.items() if n),
          file=sys.stderr)


if __name__ == "__main__":
    main()
