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

_ENV_CONFIG_PATH = "ASKSH_CONFIG"

# Keys passed to argparse.set_defaults (CLI always wins when the flag is given).
_ARG_DEFAULT_KEYS = frozenset({"model", "base_url"})
_EXTRA_KEYS = frozenset({})


def default_config_path() -> Path:
    """Return the default path: ``$XDG_CONFIG_HOME/asksh/config.toml`` (or ``~/.config/...``)."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "asksh" / "config.toml"


def config_path() -> Path:
    override = os.environ.get(_ENV_CONFIG_PATH, "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return default_config_path()


def load_user_config() -> tuple[dict[str, Any], dict[str, Any]]:
    """Load config file.

    Returns:
        ``(arg_defaults, extras)`` — ``arg_defaults`` for ``set_defaults``;
        ``extras`` holds non-argparse options (e.g. ``interactive_on_no_args``).
    """
    path = config_path()
    if not path.is_file():
        return {}, {}

    with path.open("rb") as f:
        raw = tomllib.load(f)

    if not isinstance(raw, dict):
        return {}, {}

    unknown = set(raw) - _ARG_DEFAULT_KEYS - _EXTRA_KEYS
    if unknown:
        print(
            f"Warning: ignoring unknown config keys in {path}: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )

    arg_defaults: dict[str, Any] = {}
    for key in _ARG_DEFAULT_KEYS:
        if key not in raw or raw[key] is None:
            continue
        val = raw[key]
        if key in ("model", "base_url", "context") and not isinstance(val, str):
            print(
                f"Warning: config key {key!r} must be a string, got {type(val).__name__}; ignoring.",
                file=sys.stderr,
            )
            continue
        arg_defaults[key] = val

    extras: dict[str, Any] = {}
    if "interactive_on_no_args" in raw and raw["interactive_on_no_args"] is not None:
        v = raw["interactive_on_no_args"]
        if isinstance(v, bool):
            extras["interactive_on_no_args"] = v
        else:
            print(
                "Warning: interactive_on_no_args must be a boolean; ignoring.",
                file=sys.stderr,
            )

    return arg_defaults, extras
