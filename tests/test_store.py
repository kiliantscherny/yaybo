"""Check what the database does with a run's rows.

Run directly - `uv run python tests/test_store.py` - or under pytest.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from yaybo import store


def _one(db, sql):
    """The single value a query answers with.

    Every query here counts something or reads one column of one row, but
    fetchone() is typed for the general case where there is no row at all.
    """
    row = db.sql(sql).fetchone()
    assert row is not None, sql
    return row[0]


def test_coercion():
    """Everything from the rendered attest arrives as text with its unit
    attached, and has to be typed."""
    assert store.coerce("26.000 DKK", store.INTEGER) == 26000
    assert store.coerce("55 kvm", store.INTEGER) == 55
    assert store.coerce("1.234.567", store.INTEGER) == 1234567
    # A comma is the decimal mark here, and a full stop is not.
    assert store.coerce("2,74 %", store.DECIMAL) == 2.74
    assert store.coerce("1.234,5", store.DECIMAL) == 1234.5
    assert store.coerce("2014-07-01", store.DATE) == "2014-07-01"
    # A figure we cannot read becomes nothing, rather than losing the row.
    assert store.coerce("se akt", store.INTEGER) is None
    assert store.coerce("", store.TEXT) is None
    assert store.coerce("Skøde", store.TEXT) == "Skøde"


def test_a_real_number_is_not_reread_as_danish_text():
    """The two dialects, side by side.

    "3.500" written in the attest is three thousand five hundred. The XML says
    3.5 and the reader that knew that hands over a float, which must not then
    be run through the Danish rules and come out as 3500.
    """
    assert store.coerce("3.500", store.DECIMAL) == 3500.0  # text: Danish
    assert store.coerce(3.5, store.DECIMAL) == 3.5         # number: as given
    assert store.coerce(26000, store.INTEGER) == 26000
    assert store.coerce(2.74, store.INTEGER) == 2


def test_booleans_keep_empty_apart_from_false():
    assert store.coerce("true", store.BOOLEAN) is True
    assert store.coerce("false", store.BOOLEAN) is False
    # A field the register left empty is not the same as one it said no to.
    assert store.coerce("", store.BOOLEAN) is None
    assert store.coerce("måske", store.BOOLEAN) is None


def test_json_columns_take_a_list_or_a_rendered_string():
    assert store.coerce(["vej", "andet"], store.JSON) == '["vej", "andet"]'
    assert store.coerce([], store.JSON) is None
    assert store.coerce('{"a": 1}', store.JSON) == '{"a": 1}'


def _rows(uuid, navn):
    return {
        "ejendomme": [{"uuid": uuid, "adresse": "Prøvegade 1", "areal_m2": "55 kvm"}],
        "ejere": [
            {"ejendom_uuid": uuid, "nummer": 1, "navn": navn,
             "foedselsdato": "1957-10-02"}
        ],
    }


def test_rerun_replaces_rather_than_duplicates():
    """Looking an address up twice is a correction, not two observations."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.duckdb"

        store.save(path, _rows("uuid-1", "Testperson Alfa"))
        store.save(path, _rows("uuid-1", "Testperson Beta"))

        with duckdb.connect(str(path), read_only=True) as db:
            assert _one(db, "SELECT count(*) FROM ejendomme") == 1
            assert _one(db, "SELECT navn FROM ejere") == "Testperson Beta"
            # Typed on the way in, so a query can do arithmetic with it.
            assert _one(db, "SELECT areal_m2 FROM ejendomme") == 55

        # A different property is added, not swapped in.
        store.save(path, _rows("uuid-2", "Testperson Gamma"))
        with duckdb.connect(str(path), read_only=True) as db:
            assert _one(db, "SELECT count(*) FROM ejendomme") == 2


def test_empty_run_still_leaves_queryable_tables():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.duckdb"
        store.save(path, {})
        with duckdb.connect(str(path), read_only=True) as db:
            for table in store.TABLES:
                assert _one(db, f"SELECT count(*) FROM {table}") == 0


def test_an_older_database_gains_the_columns_it_is_missing():
    """CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    so without this a database from an older run would reject every insert."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "old.duckdb"
        with duckdb.connect(str(path)) as db:
            db.execute(
                'CREATE TABLE "haeftelser" '
                '("ejendom_uuid" VARCHAR, "hovedstol" VARCHAR, "hentet" TIMESTAMP)'
            )
            db.execute("INSERT INTO haeftelser VALUES ('old-1', '1.000 DKK', now())")

        store.save(path, {"haeftelser": [
            {"ejendom_uuid": "uuid-1", "dokument_uuid": "d1",
             "dokument_version": "1", "hovedstol": "26.000 DKK",
             "hovedstol_dkk": 26000, "rentesats_pct": 3.5, "overfoert": "true",
             "saerlige_vilkaar": ["inkonvertibel"]}
        ]})

        with duckdb.connect(str(path), read_only=True) as db:
            row = db.sql(
                "SELECT hovedstol_dkk, rentesats_pct, overfoert, saerlige_vilkaar "
                "FROM haeftelser WHERE ejendom_uuid = 'uuid-1'"
            ).fetchone()
            assert row == (26000, 3.5, True, '["inkonvertibel"]')
            # The older row is left where it was: a column holding data is not
            # ours to throw away on a schema change.
            assert _one(
                db, "SELECT hovedstol FROM haeftelser WHERE ejendom_uuid = 'old-1'"
            ) == "1.000 DKK"


def _keys(db):
    """Every table's primary key, as the database itself reports it."""
    return {
        name: tuple(columns)
        for name, columns in db.sql(
            "SELECT table_name, constraint_column_names FROM duckdb_constraints() "
            "WHERE constraint_type = 'PRIMARY KEY' "
            "AND database_name = current_database()"
        ).fetchall()
    }


def test_every_table_is_keyed():
    """A row has to be identifiable, and by more than the property it belongs
    to - a property has many charges, and each of them many parties."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "new.duckdb"
        store.save(path, {})
        with duckdb.connect(str(path), read_only=True) as db:
            held = _keys(db)
        assert set(held) == set(store.TABLES)
        for name, spec in store.TABLES.items():
            assert held[name] == tuple(spec["pk"]), name


def test_an_older_database_gains_its_keys():
    """DuckDB cannot add a foreign key after the fact but can add a primary
    key, which is the only reason keying an existing database is possible at
    all - it gains them on the next write instead of being thrown away."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "old.duckdb"
        with duckdb.connect(str(path)) as db:
            db.execute(
                'CREATE TABLE "ejere" ("ejendom_uuid" VARCHAR, "nummer" BIGINT, '
                '"navn" VARCHAR, "hentet" TIMESTAMP)'
            )
            db.execute("INSERT INTO ejere VALUES ('old-1', 1, 'Ida Testesen', now())")

        store.save(path, {"ejere": [
            {"ejendom_uuid": "uuid-1", "nummer": 1, "navn": "Ole Prøvesen"}
        ]})

        with duckdb.connect(str(path), read_only=True) as db:
            assert _keys(db)["ejere"] == ("ejendom_uuid", "nummer")
            assert _one(db, "SELECT count(*) FROM ejere") == 2


def test_a_key_seen_twice_in_one_batch_folds():
    """The register hands the same document over once per charge it secures,
    so the same people arrive two or three times. That is one row described
    repeatedly, not a reason to lose the whole run."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "dupes.duckdb"
        written = store.save(path, {"dokument_parter": [
            {"ejendom_uuid": "u1", "dokument_uuid": "d1", "dokumentart": "haeftelse",
             "rolle": "kreditor", "nummer": 1, "navn": "First reading"},
            {"ejendom_uuid": "u1", "dokument_uuid": "d1", "dokumentart": "haeftelse",
             "rolle": "kreditor", "nummer": 1, "navn": "Second reading"},
        ]})
        assert written["dokument_parter"] == 1
        with duckdb.connect(str(path), read_only=True) as db:
            assert _one(db, "SELECT navn FROM dokument_parter") == "Second reading"


def test_a_row_whose_key_is_incomplete_is_left_out():
    """Every primary key column is NOT NULL, so a row missing one has nowhere
    to go. It is dropped rather than taking the run with it - and said out
    loud, because a row quietly missing is worse than one known to be."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "partial.duckdb"
        written = store.save(path, {"bygninger": [
            {"ejendom_uuid": "u1", "bygning_nr": None, "bygningstype": "no number"},
            {"ejendom_uuid": "u1", "bygning_nr": "1", "bygningstype": "kept"},
        ]})
        assert written["bygninger"] == 1
        with duckdb.connect(str(path), read_only=True) as db:
            assert _one(db, "SELECT bygningstype FROM bygninger") == "kept"


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
