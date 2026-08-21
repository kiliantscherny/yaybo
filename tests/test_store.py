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
    """Everything arrives as text with its unit attached, and has to be typed."""
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


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
