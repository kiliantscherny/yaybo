"""Check that schema.dbml still describes the schema it was generated from.

The DBML is documentation, and documentation derived from code goes stale the
moment someone adds a column and forgets. Regenerating it here and comparing is
what turns that from a thing you notice months later into a failing test.

Run directly - `uv run python tests/test_schema_dbml.py` - or under pytest.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from yaybo import store  # noqa: E402


def _generator():
    """Load the generator, which lives outside the package.

    The source archive carries the tests but not the repository around them,
    so anyone repackaging this finds the generator missing. That is not a
    failure - what these check is that the checked-in DBML matches the code,
    which is a question about the repository rather than about the package.
    """
    path = ROOT / "scripts" / "generate_schema_dbml.py"
    if not path.is_file():
        pytest.skip("the schema generator is not in this archive")
    spec = importlib.util.spec_from_file_location("generate_schema_dbml", path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schema_dbml_is_current():
    """If this fails, run: uv run python scripts/generate_schema_dbml.py

    Both copies are checked. The one bundled with the plugin is the one an
    agent reads when yaybo was installed rather than cloned, so it going stale
    would be invisible to anyone working in the repository.
    """
    generator = _generator()
    expected = generator.render()
    for output in generator.OUTPUTS:
        assert output.exists(), f"{output} is missing"
        assert output.read_text(encoding="utf-8") == expected, (
            f"{output.name} is out of date in {output.parent} - "
            "run `uv run python scripts/generate_schema_dbml.py`"
        )


def test_every_table_is_described():
    """A table without a note renders as an unexplained box on the diagram."""
    generator = _generator()
    assert set(generator.TABLES) == set(store.TABLES)


def test_declared_relationships_point_at_real_columns():
    """A reference to a column that no longer exists is worse than none."""
    generator = _generator()
    for child, column, parent, target, _ in generator.REFS:
        for table, field in ((child, column), (parent, target)):
            names = [name for name, _ in store.TABLES[table]["columns"]]
            assert field in names, f"{table}.{field} does not exist"


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
