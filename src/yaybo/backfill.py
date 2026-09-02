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

from yaybo import store
from yaybo.enrich import boligsiden, laantype
from yaybo.register import attest, attest_xml
from yaybo.register import rows as build
from yaybo.register.address import address_parts, dawa_addresses, floor_and_door

# Read out of the stored documents; everything else in the database is left
# alone, because nothing here knows how to rebuild it.
DERIVED = [
    "haeftelser", "servitutter", "dokument_parter", "underpant",
    "adkomsthistorik", "adkomsthistorik_ejere", "attester",
]
# Rebuilt too, but only when the public sources are asked: Boligsiden fills
# columns on the property row, and the debt totals depend on the charges.
ENRICHED = ["ejendomme", "handelshistorik", "bygninger", "rentestatistik"]


def _count(db, name: str) -> int:
    """How many rows a table holds. count(*) always answers, so the None that
    fetchone() is allowed to return cannot happen here."""
    row = db.sql(f'SELECT count(*) FROM "{name}"').fetchone()
    return row[0] if row else 0


def collect(path: Path) -> tuple[dict, dict, list[dict]]:
    """Read the stored documents and build every derived row from them."""
    tables: dict = collections.defaultdict(list)
    with duckdb.connect(str(path), read_only=True) as db:
        held = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        before = {name: _count(db, name) for name in sorted(held)}

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
            dict(zip(readable, row, strict=True))
            for row in db.sql(
                f'SELECT {", ".join(chr(34) + c + chr(34) for c in readable)} '
                "FROM ejendomme"
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
        tables["haeftelser"] += build.haeftelse_rows(record, uuid, parsed)
        tables["servitutter"] += build.servitut_rows(record, uuid, parsed)
        tables["dokument_parter"] += build.party_rows(parsed, uuid)
        tables["underpant"] += build.underpant_rows(parsed, uuid)
        entries, owners = build.history_rows(
            {"items": history.get(uuid, [])}, uuid, adresse
        )
        tables["adkomsthistorik"] += entries
        tables["adkomsthistorik_ejere"] += owners
        tables["attester"].append(
            {"ejendom_uuid": uuid, "adresse": adresse, "format": kind or "xml",
             "dokument": raw, "dokument_json": attest.attest_json({"_raw": raw})}
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
            parts = address_parts(row.get("adresse") or "")
            key = (parts["vejnavn"], parts["husnummer"], parts["postnummer"])
            buildings[key].append(row)

        found = 0
        for (vejnavn, husnr, postnr), rows in buildings.items():
            if not (vejnavn and postnr):
                continue
            addresses = dawa_addresses(
                {"vejnavn": vejnavn, "husnummer": husnr, "postnummer": postnr}
            )
            for row in rows:
                adresse = row.get("adresse") or ""
                uuid = addresses.get(floor_and_door(adresse))
                if not uuid:
                    continue
                time.sleep(delay)
                bolig = boligsiden.fetch(uuid)
                if not bolig:
                    continue
                found += 1
                row.update(build.bolig_row(bolig))
                tables.setdefault("handelshistorik", []).extend(
                    build.handel_rows(bolig, row["uuid"], adresse))
                tables.setdefault("bygninger", []).extend(
                    build.bygning_rows(bolig, row["uuid"], adresse))
        print(f"  Boligsiden answered for {found} of {len(properties)} propert"
              f"{'y' if len(properties) == 1 else 'ies'}", file=sys.stderr)

    build.add_financials(properties, tables.get("haeftelser") or [])
    tables["ejendomme"] = properties


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", help="the database to rebuild in place")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written and change nothing")
    parser.add_argument("--skip-boligsiden", action="store_true",
                        help="do not ask Boligsiden for sale prices and BBR data")
    parser.add_argument("--skip-laantype", action="store_true",
                        help="do not estimate loan types from DST rates")
    parser.add_argument("--delay", type=float, default=0.2, metavar="SECONDS",
                        help="pause between Boligsiden requests (default: 0.2)")
    return run(parser.parse_args(argv))


def run(args) -> int:
    """Rebuild the derived tables of one database. Shared with `yaybo backfill`."""
    path = Path(args.db) if args.db else store.default_path()
    if not path.exists():
        raise SystemExit(f"no database at {path}")

    tables, before, properties = collect(path)
    if not tables:
        raise SystemExit("no stored documents to rebuild from")

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
        return 0

    written = store.save(path, tables)
    print("rewrote " + ", ".join(f"{n} {name}" for name, n in written.items() if n),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
