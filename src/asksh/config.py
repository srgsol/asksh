"""User defaults from a TOML config file (overridden by CLI flags)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


_VALID_CONFIG_KEYS = frozenset({"MODEL", "BASE_URL", "UPDATE_CHECK"})

# TOML keys -> argparse ``Namespace`` attribute names (``--model``, ``--base-url``).
_CONFIG_TO_ARG_DEST: dict[str, str] = {
    "MODEL": "model",
    "BASE_URL": "base_url",
    "UPDATE_CHECK": "update_check",
}

_CONFIG_KEY_TYPES: dict[str, type] = {
    "MODEL": str,
    "BASE_URL": str,
    "UPDATE_CHECK": bool,
}


def default_config_path() -> Path:
    """Return the default path: ``$XDG_CONFIG_HOME/asksh/config.toml`` (or ``~/.config/...``)."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "asksh" / "config.toml"


def load_user_config() -> dict[str, Any]:
    """Load config file defaults for ``argparse.set_defaults``."""
    path = default_config_path()
    if not path.is_file():
        return {}

    with path.open("rb") as f:
        raw = tomllib.load(f)

    if not isinstance(raw, dict):
        return {}

    unknown = set(raw) - _VALID_CONFIG_KEYS
    if unknown:
        print(
            f"Warning: ignoring unknown config keys in {path}: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )

    arg_defaults: dict[str, Any] = {}
    for key in _VALID_CONFIG_KEYS:
        if key not in raw or raw[key] is None:
            continue
        val = raw[key]
        expected = _CONFIG_KEY_TYPES[key]
        if not isinstance(val, expected):
            print(
                f"Warning: config key {key!r} must be {expected.__name__}, "
                f"got {type(val).__name__}; ignoring.",
                file=sys.stderr,
            )
            continue
        arg_defaults[_CONFIG_TO_ARG_DEST[key]] = val

    return arg_defaults
