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
import time
from pathlib import Path

import duckdb

import attest_xml
import boligsiden
import laantype
import store
import tinglysning_dl as tl

# Read out of the stored documents; everything else in the database is left
# alone, because nothing here knows how to rebuild it.
DERIVED = [
    "haeftelser", "servitutter", "dokument_parter", "underpant",
    "adkomsthistorik", "adkomsthistorik_ejere", "attester",
]
# Rebuilt too, but only when the public sources are asked: Boligsiden fills
# columns on the property row, and the debt totals depend on the charges.
ENRICHED = ["ejendomme", "handelshistorik", "bygninger", "rentestatistik"]


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

        columns = [name for name, _ in store.TABLES["ejendomme"]["columns"]]
        held_columns = {row[0] for row in db.execute('DESCRIBE "ejendomme"').fetchall()}
        readable = [c for c in columns if c in held_columns]
        properties = [
            dict(zip(readable, row))
            for row in db.sql(
                f'SELECT {", ".join(chr(34) + c + chr(34) for c in readable)} FROM ejendomme'
            ).fetchall()
        ]

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
    return dict(tables), before, properties


def enrich(tables: dict, properties: list[dict], *, boligsiden_on: bool,
           laantype_on: bool, delay: float = 0.2) -> None:
    """Add what the public sources know, on top of what the register said.

    Boligsiden answers to a DAWA address UUID, which the older rows do not
    carry, so it is looked up again here - one request per building rather
    than per flat, the same way a fetch does it.
    """
    if laantype_on:
        estimated = laantype.annotate(tables.get("haeftelser") or [])
        tables["rentestatistik"] = laantype.rate_rows(estimated["renter"])
        print(f"  named the loan type on {estimated['named']} realkredit charge(s), "
              f"from {len(estimated['renter'])} months of DST rates", file=sys.stderr)

    if boligsiden_on and properties:
        # Group the flats by the building they are in, so one DAWA lookup
        # serves all of them.
        buildings: dict = collections.defaultdict(list)
        for row in properties:
            parts = tl.address_parts(row.get("adresse") or "")
            buildings[(parts["vejnavn"], parts["husnummer"], parts["postnummer"])].append(row)

        found = 0
        for (vejnavn, husnr, postnr), rows in buildings.items():
            if not (vejnavn and postnr):
                continue
            addresses = tl.dawa_addresses(
                {"vejnavn": vejnavn, "husnummer": husnr, "postnummer": postnr}
            )
            for row in rows:
                adresse = row.get("adresse") or ""
                uuid = addresses.get(tl._floor_and_door(adresse))
                if not uuid:
                    continue
                time.sleep(delay)
                bolig = boligsiden.fetch(uuid)
                if not bolig:
                    continue
                found += 1
                row.update(tl.bolig_row(bolig))
                tables.setdefault("handelshistorik", []).extend(
                    tl.handel_rows(bolig, row["uuid"], adresse))
                tables.setdefault("bygninger", []).extend(
                    tl.bygning_rows(bolig, row["uuid"], adresse))
        print(f"  Boligsiden answered for {found} of {len(properties)} propert"
              f"{'y' if len(properties) == 1 else 'ies'}", file=sys.stderr)

    tl.add_financials(properties, tables.get("haeftelser") or [])
    tables["ejendomme"] = properties


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(Path("out") / "tinglysning.duckdb"),
                        help="the database to rebuild in place")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written and change nothing")
    parser.add_argument("--skip-boligsiden", action="store_true",
                        help="do not ask Boligsiden for sale prices and BBR data")
    parser.add_argument("--skip-laantype", action="store_true",
                        help="do not estimate loan types from DST rates")
    parser.add_argument("--delay", type=float, default=0.2, metavar="SECONDS",
                        help="pause between Boligsiden requests (default: 0.2)")
    args = parser.parse_args()

    path = Path(args.db)
    if not path.exists():
        sys.exit(f"no database at {path}")

    tables, before, properties = collect(path)
    if not tables:
        sys.exit("no stored documents to rebuild from")

    print(f"{len(tables['attester'])} document(s) read from {path}", file=sys.stderr)
    enrich(tables, properties,
           boligsiden_on=not args.skip_boligsiden, laantype_on=not args.skip_laantype,
           delay=args.delay)

    for name in DERIVED + ENRICHED:
        was, now = before.get(name, 0), len(tables.get(name) or [])
        if name not in tables:
            # Not rebuilt this run - a skipped source leaves its rows alone
            # rather than emptying them, so say so instead of printing "-> 0".
            change = f"{was} left as they are"
        elif name not in before:
            change = f"new table, {now}"
        else:
            change = f"{was} -> {now}"
        print(f"  {name:24} {change}", file=sys.stderr)

    if args.dry_run:
        print("dry run - nothing written", file=sys.stderr)
        return

    written = store.save(path, tables)
    print("rewrote " + ", ".join(f"{n} {name}" for name, n in written.items() if n),
          file=sys.stderr)


if __name__ == "__main__":
    main()
