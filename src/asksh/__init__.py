"""AI in your terminal: turn plain English into shell commands."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("asksh")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"
