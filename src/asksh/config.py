"""User defaults from config file and environment (overridden by CLI flags)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal, get_args

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


RenderStyle = Literal["text", "markdown", "post_markdown", "live_markdown"]
_RENDER_STYLES: frozenset[str] = frozenset(get_args(RenderStyle))

_VALID_CONFIG_KEYS = frozenset(
    {
        "MODEL",
        "BASE_URL",
        "UPDATE_CHECK",
        "ONESHOT_RENDER",
        "EXPLAIN_RENDER",
        "CHAT_RENDER",
    }
)

_RENDER_STYLE_KEYS = frozenset({"ONESHOT_RENDER", "EXPLAIN_RENDER", "CHAT_RENDER"})

# TOML keys -> argparse ``Namespace`` attribute names (``--model``, ``--base-url``).
_CONFIG_TO_ARG_DEST: dict[str, str] = {
    "MODEL": "model",
    "BASE_URL": "base_url",
    "UPDATE_CHECK": "update_check",
    "ONESHOT_RENDER": "oneshot_render",
    "EXPLAIN_RENDER": "explain_render",
    "CHAT_RENDER": "chat_render",
}

_CONFIG_KEY_TYPES: dict[str, type] = {
    "MODEL": str,
    "BASE_URL": str,
    "UPDATE_CHECK": bool,
    "ONESHOT_RENDER": str,
    "EXPLAIN_RENDER": str,
    "CHAT_RENDER": str,
}


def default_config_path() -> Path:
    """Return the default path: ``$XDG_CONFIG_HOME/asksh/config.toml`` (or ``~/.config/...``)."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "asksh" / "config.toml"


def _canonical_config_key(key: str) -> str:
    return key.upper()


def _normalize_raw_config(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Map keys to canonical uppercase form; warn on conflicting duplicates."""
    normalized: dict[str, Any] = {}
    for key, val in raw.items():
        if not isinstance(key, str):
            continue
        canon = _canonical_config_key(key)
        if canon in normalized and normalized[canon] != val:
            print(
                f"Warning: conflicting values for config key {canon!r} in {source}; "
                f"using {key!r}.",
                file=sys.stderr,
            )
        normalized[canon] = val
    return normalized


def _read_env_config() -> dict[str, str]:
    """Read config values from ``ASKSH_*`` / ``asksh_*`` environment variables."""
    raw: dict[str, str] = {}
    for key in _VALID_CONFIG_KEYS:
        for env_name in (f"ASKSH_{key}", f"asksh_{key.lower()}"):
            val = os.environ.get(env_name)
            if val is not None and val != "":
                raw[key] = val
                break
    return raw


def _coerce_config_value(key: str, val: Any) -> Any | None:
    expected = _CONFIG_KEY_TYPES[key]
    if expected is bool and isinstance(val, str):
        lowered = val.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return val
    return val


def _apply_config_values(
    raw: dict[str, Any],
    *,
    source: str,
    arg_defaults: dict[str, Any],
) -> None:
    unknown = set(raw) - _VALID_CONFIG_KEYS
    if unknown:
        print(
            f"Warning: ignoring unknown config keys in {source}: "
            f"{', '.join(sorted(unknown))}",
            file=sys.stderr,
        )

    for key in _VALID_CONFIG_KEYS:
        if key not in raw or raw[key] is None:
            continue
        val = _coerce_config_value(key, raw[key])
        expected = _CONFIG_KEY_TYPES[key]
        if not isinstance(val, expected):
            print(
                f"Warning: config key {key!r} must be {expected.__name__}, "
                f"got {type(val).__name__}; ignoring.",
                file=sys.stderr,
            )
            continue
        if key in _RENDER_STYLE_KEYS and val not in _RENDER_STYLES:
            print(
                f"Warning: config key {key!r} must be one of "
                f"{', '.join(sorted(_RENDER_STYLES))}; got {val!r}; ignoring.",
                file=sys.stderr,
            )
            continue
        arg_defaults[_CONFIG_TO_ARG_DEST[key]] = val


def load_user_config() -> dict[str, Any]:
    """Load config file and environment defaults for ``argparse.set_defaults``.

    Environment variables override the config file. CLI flags override both.
    Config keys and ``ASKSH_*`` env vars accept either uppercase or lowercase.
    """
    arg_defaults: dict[str, Any] = {}

    path = default_config_path()
    if path.is_file():
        with path.open("rb") as f:
            file_raw = tomllib.load(f)
        if isinstance(file_raw, dict):
            _apply_config_values(
                _normalize_raw_config(file_raw, source=str(path)),
                source=str(path),
                arg_defaults=arg_defaults,
            )

    env_raw = _normalize_raw_config(_read_env_config(), source="environment")
    if env_raw:
        _apply_config_values(
            env_raw,
            source="environment",
            arg_defaults=arg_defaults,
        )

    return arg_defaults
