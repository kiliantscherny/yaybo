"""Write schema.dbml from store.TABLES.

The schema lives in one place - `yaybo.store.TABLES` - and everything else is
derived from it: the database, every export's column order, and this. Writing
the DBML by hand would make it a second source of truth that silently falls
behind the first, so it is generated instead, and `tests/test_schema_dbml.py`
fails if the checked-in file no longer matches.

    uv run python scripts/generate_schema_dbml.py

It writes two identical copies: one at the repository root, and one inside the
skill, which ships with the plugin and cannot reach the repository root.

DBML renders at https://dbdiagram.io and https://dbdocs.io. The prose below is
the part that cannot be derived: what a table is for, and which columns are
worked out rather than recorded.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yaybo import store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Two copies, because they are read by different people in different places.
# The one at the repository root is for anyone who cloned this - paste it into
# dbdiagram.io. The one inside the skill travels with the plugin, which is
# installed without a clone and therefore has no repository root to look in.
OUTPUTS = (
    ROOT / "schema.dbml",
    ROOT / "plugin/skills/danish-property-records/reference/schema.dbml",
)
OUTPUT = OUTPUTS[0]

# What each table is, in one line.
TABLES = {
    "ejendomme": "One row per property. The centre of the schema: everything "
    "else joins back to ejendomme.uuid.",
    "ejere": "Who owns the property today.",
    "haeftelser": "Mortgages and charges, one row per document version. The "
    "same document can secure more than one charge, which is why the version "
    "is part of the key.",
    "servitutter": "Easements and covenants recorded against the property.",
    "dokument_parter": "Everyone named on any document, with their role. Needs "
    "a MitID login: this is the table the login is really for.",
    "underpant": "A mortgage deed pledged on in its own right - a charge on a "
    "charge.",
    "handelshistorik": "Recorded sales, from Boligsiden. Overlaps "
    "adkomsthistorik without replacing it: the register knows transfers that "
    "were never a sale, and Boligsiden knows the price per square metre.",
    "bygninger": "The BBR record for the building: year built, rooms, walls, "
    "heating. The land register says none of this.",
    "adkomsthistorik": "Past transfers of the property. Needs a MitID login.",
    "adkomsthistorik_ejere": "The people named in each past transfer. Needs a "
    "MitID login.",
    "rentestatistik": "What each kind of realkredit loan cost, month by month, "
    "from Danmarks Statistik. Not about any one property - it is the series "
    "laantype_estimat was matched against, kept so an estimate can be checked.",
    "attester": "The property's whole register document, as signed and as "
    "queryable JSON. Needs a MitID login.",
    "andele": "One row per co-op share, from the andelsboligbog - a different "
    "book about a different thing. An andel is not real property, so it has "
    "no valuation, no matrikel and no area of its own; ejendom_uuid points at "
    "the association's building in the tingbog, which is where all of that is.",
    "andel_haeftelser": "Charges registered against one share. The same fields "
    "the tingbog uses for a property's mortgages, under a different key.",
    "andel_meddelelser": "Notices noted on a share - a death, a bankruptcy, a "
    "court removing the andelshaver's power to dispose of it. The only place "
    "this book names anyone other than a creditor.",
}

# Columns worth a word of warning, because they are not what they look like.
NOTES = {
    ("ejendomme", "areal_m2"): "the register's tinglyste areal, not the BBR "
    "living area - see boligareal_m2",
    ("ejendomme", "boligareal_m2"): "BBR living area, which is what a listing "
    "quotes",
    ("ejendomme", "ejendomsvurdering_dkk"): "public valuation, which sits well "
    "below market",
    ("ejendomme", "samlet_gaeld_dkk"): "DERIVED: sum of the property's charges",
    ("ejendomme", "frivaerdi_dkk"): "DERIVED against the public valuation, so "
    "treat it as a floor",
    ("ejendomme", "belaaningsgrad_pct"): "DERIVED against the public "
    "valuation, so treat it as a ceiling",
    ("ejendomme", "beriget"): "whether this property was fetched by someone "
    "the register knew - per property, not per run",
    ("ejendomme", "seneste_salg_pris_m2"): "DERIVED: last sale divided by area",
    ("haeftelser", "laantype_estimat"): "ESTIMATED from DST rates, not "
    "recorded. The register gives a rate and never the product",
    ("haeftelser", "laantype_afstand"): "distance to the runner-up loan type; "
    "larger means more confident",
    ("ejere", "foedselsdato"): "needs a MitID login",
    ("dokument_parter", "foedselsdato"): "needs a MitID login",
    ("dokument_parter", "nummer"): "position within the role on one document, "
    "assigned after duplicate readings are merged",
    ("attester", "dokument"): "the bytes the register signed",
    ("attester", "dokument_json"): "the same content as queryable JSON, with "
    "the namespace prefixes dropped",
    ("andele", "uuid"): "the andelsboligbog's own uuid - a different register "
    "from ejendomme.uuid, and the two namespaces never mix",
    ("andele", "ejendom_uuid"): "the association's building in the tingbog, "
    "when the same lookup found exactly one. Empty otherwise",
    ("andele", "samlet_gaeld_dkk"): "DERIVED: sum of what is charged against "
    "this share alone. NOT what living there owes - an andelshaver also owes "
    "a share of the association's own mortgage, which is against the building "
    "in the tingbog and is nowhere in this table",
    ("andele", "boligareal_m2"): "BBR living area, from Boligsiden. The book "
    "records no area at all",
    ("andele", "boligtype"): "Boligsiden's word for it; reads 'cooperative' "
    "for a share",
    ("andel_meddelelser", "debitorer"): "the andelshaver the notice concerns. "
    "The andelsboligbog has no owner register, so this is the nearest it comes "
    "to naming who lives there",
    ("andel_meddelelser", "disponenter"): "whoever may act for them - an "
    "executor, a trustee",
}

# How the tables relate. DuckDB cannot add a foreign key to an existing table,
# so these are documented rather than enforced - see store.py.
REFS = [
    ("ejere", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("haeftelser", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("servitutter", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("bygninger", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("handelshistorik", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("adkomsthistorik", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("dokument_parter", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("underpant", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("attester", "ejendom_uuid", "ejendomme", "uuid", "-"),
    ("underpant", "haeftelse_uuid", "haeftelser", "dokument_uuid", ">"),
    ("andele", "ejendom_uuid", "ejendomme", "uuid", ">"),
    ("andel_haeftelser", "andel_uuid", "andele", "uuid", ">"),
    ("andel_meddelelser", "andel_uuid", "andele", "uuid", ">"),
]

# Relationships DBML cannot state in one line, because the target depends on a
# value in another column.
LOOSE = """
// dokument_parter.dokument_uuid points at haeftelser.dokument_uuid OR
// servitutter.dokument_uuid, and dokument_parter.dokumentart says which
// ('haeftelse', 'servitut', 'adkomst', 'underpant'). DBML cannot express a
// reference whose target depends on another column, so it is written here.
//
// adkomsthistorik_ejere joins adkomsthistorik on BOTH ejendom_uuid and
// post_nummer.
Ref: adkomsthistorik_ejere.(ejendom_uuid, post_nummer) > \
adkomsthistorik.(ejendom_uuid, post_nummer)
"""

DBML_TYPES = {
    store.TEXT: "varchar",
    store.INTEGER: "bigint",
    store.DECIMAL: "double",
    store.DATE: "date",
    store.BOOLEAN: "boolean",
    store.JSON: "json",
}


def escape(text: str) -> str:
    return text.replace("'", "\\'")


def render() -> str:
    out = [
        "// Generated by scripts/generate_schema_dbml.py - do not edit by hand.",
        "// The schema itself lives in src/yaybo/store.py (TABLES).",
        "//",
        "// Render at https://dbdiagram.io or https://dbdocs.io",
        "",
        "Project yaybo {",
        "  database_type: 'DuckDB'",
        "  Note: '''",
        "    Danish property records, fetched from tinglysning.dk and enriched",
        "    from Boligsiden and Danmarks Statistik.",
        "",
        "    Every table joins back to ejendomme.uuid. Primary keys are real and",
        "    enforced; the references below are documented but NOT enforced,",
        "    because DuckDB cannot add a foreign key to an existing table.",
        "  '''",
        "}",
        "",
    ]

    for name, spec in store.TABLES.items():
        out.append(f"Table {name} {{")
        pk = spec["pk"]
        for column, sort in spec["columns"]:
            kind = DBML_TYPES[sort]
            settings = []
            if len(pk) == 1 and column == pk[0]:
                settings.append("pk")
            if note := NOTES.get((name, column)):
                settings.append(f"note: '{escape(note)}'")
            rendered = f" [{', '.join(settings)}]" if settings else ""
            out.append(f"  {column} {kind}{rendered}")
        out.append(f"  {store.FETCHED} timestamp [note: 'when this row was written']")

        if len(pk) > 1:
            out.append("  indexes {")
            out.append(f"    ({', '.join(pk)}) [pk]")
            out.append("  }")
        out.append(f"  Note: '{escape(TABLES[name])}'")
        out.append("}")
        out.append("")

    for child, column, parent, target, kind in REFS:
        out.append(f"Ref: {child}.{column} {kind} {parent}.{target}")
    out.append(LOOSE.rstrip() + "\n")
    return "\n".join(out)


def main() -> int:
    rendered = render()
    for output in OUTPUTS:
        if output.exists() and output.read_text(encoding="utf-8") == rendered:
            print(f"already up to date: {output.relative_to(ROOT)}")
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(f"wrote {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
