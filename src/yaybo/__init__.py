"""yaybo - fetch, explore and export the Danish property registers."""

from importlib.metadata import PackageNotFoundError, version

try:
    # pyproject.toml is the one place the version is written. Reading it back
    # from the installed metadata is what stops this file drifting from it -
    # which it had, sitting at 0.2.0 while the package shipped 0.2.1.
    __version__ = version("yaybo")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
