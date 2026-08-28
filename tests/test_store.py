"""Check what the database does with a run's rows.

Run directly - `uv run python tests/test_store.py` - or under pytest.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

import store


def test_coercion():
    """Everything from the rendered attest arrives as text with its unit
    attached, and has to be typed."""
    assert store._coerce("26.000 DKK", store.INTEGER) == 26000
    assert store._coerce("55 kvm", store.INTEGER) == 55
    assert store._coerce("1.234.567", store.INTEGER) == 1234567
    # A comma is the decimal mark here, and a full stop is not.
    assert store._coerce("2,74 %", store.DECIMAL) == 2.74
    assert store._coerce("1.234,5", store.DECIMAL) == 1234.5
    assert store._coerce("2014-07-01", store.DATE) == "2014-07-01"
    # A figure we cannot read becomes nothing, rather than losing the row.
    assert store._coerce("se akt", store.INTEGER) is None
    assert store._coerce("", store.TEXT) is None
    assert store._coerce("Skøde", store.TEXT) == "Skøde"


def test_a_real_number_is_not_reread_as_danish_text():
    """The two dialects, side by side.

    "3.500" written in the attest is three thousand five hundred. The XML says
    3.5 and the reader that knew that hands over a float, which must not then
    be run through the Danish rules and come out as 3500.
    """
    assert store._coerce("3.500", store.DECIMAL) == 3500.0  # text: Danish
    assert store._coerce(3.5, store.DECIMAL) == 3.5         # number: as given
    assert store._coerce(26000, store.INTEGER) == 26000
    assert store._coerce(2.74, store.INTEGER) == 2


def test_booleans_keep_empty_apart_from_false():
    assert store._coerce("true", store.BOOLEAN) is True
    assert store._coerce("false", store.BOOLEAN) is False
    # A field the register left empty is not the same as one it said no to.
    assert store._coerce("", store.BOOLEAN) is None
    assert store._coerce("måske", store.BOOLEAN) is None


def test_json_columns_take_a_list_or_a_rendered_string():
    assert store._coerce(["vej", "andet"], store.JSON) == '["vej", "andet"]'
    assert store._coerce([], store.JSON) is None
    assert store._coerce('{"a": 1}', store.JSON) == '{"a": 1}'


def _rows(uuid, navn):
    return {
        "ejendomme": [{"uuid": uuid, "adresse": "Prøvegade 1", "areal_m2": "55 kvm"}],
        "ejere": [
            {"ejendom_uuid": uuid, "nummer": 1, "navn": navn, "foedselsdato": "1957-10-02"}
        ],
    }


def test_rerun_replaces_rather_than_duplicates():
    """Looking an address up twice is a correction, not two observations."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.duckdb"

        store.save(path, _rows("uuid-1", "Testperson Alfa"))
        store.save(path, _rows("uuid-1", "Testperson Beta"))

        with duckdb.connect(str(path), read_only=True) as db:
            assert db.sql("SELECT count(*) FROM ejendomme").fetchone()[0] == 1
            assert db.sql("SELECT navn FROM ejere").fetchone()[0] == "Testperson Beta"
            # Typed on the way in, so a query can do arithmetic with it.
            assert db.sql("SELECT areal_m2 FROM ejendomme").fetchone()[0] == 55

        # A different property is added, not swapped in.
        store.save(path, _rows("uuid-2", "Testperson Gamma"))
        with duckdb.connect(str(path), read_only=True) as db:
            assert db.sql("SELECT count(*) FROM ejendomme").fetchone()[0] == 2


def test_empty_run_still_leaves_queryable_tables():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.duckdb"
        store.save(path, {})
        with duckdb.connect(str(path), read_only=True) as db:
            for table in store.TABLES:
                assert db.sql(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


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
            {"ejendom_uuid": "uuid-1", "hovedstol": "26.000 DKK", "hovedstol_dkk": 26000,
             "rentesats_pct": 3.5, "overfoert": "true", "saerlige_vilkaar": ["inkonvertibel"]}
        ]})

        with duckdb.connect(str(path), read_only=True) as db:
            row = db.sql(
                "SELECT hovedstol_dkk, rentesats_pct, overfoert, saerlige_vilkaar "
                "FROM haeftelser WHERE ejendom_uuid = 'uuid-1'"
            ).fetchone()
            assert row == (26000, 3.5, True, '["inkonvertibel"]')
            # The older row is left where it was: a column holding data is not
            # ours to throw away on a schema change.
            assert db.sql(
                "SELECT hovedstol FROM haeftelser WHERE ejendom_uuid = 'old-1'"
            ).fetchone()[0] == "1.000 DKK"


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
