"""Startup check for newer asksh releases on PyPI (best-effort, never raises)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests

_PYPI_JSON_URL = "https://pypi.org/pypi/asksh/json"
_TIMEOUT_SECONDS = 2.0
_CACHE_TTL_SECONDS = 24 * 60 * 60


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(base) / "asksh" / "update_check"


def _is_cache_fresh(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < _CACHE_TTL_SECONDS
    except OSError:
        return False


def _parse_version(value: str) -> tuple[int, ...]:
    """'1.0.0.post1' -> (1, 0, 0); non-numeric parts count as 0."""
    parts: list[int] = []
    for part in str(value).split("."):
        match = re.match(r"(\d+)", part)
        parts.append(int(match.group(1)) if match else 0)
    return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    a, b = _parse_version(latest), _parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def check_for_update(current: str) -> str | None:
    """Return the latest PyPI version of asksh if newer than ``current``, else None.

    Never raises and never blocks longer than ``_TIMEOUT_SECONDS``: every
    exception is swallowed. After a successful response the check is cached
    for 24h via the mtime of a timestamp file (written only on success).
    """
    if current == "0.0.0":  # not installed via a package manager; don't nag
        return None

    path = _cache_path()
    if _is_cache_fresh(path):
        return None

    try:
        response = requests.get(_PYPI_JSON_URL, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return None  # includes Timeout, ConnectionError, HTTPError, bad JSON

    info = payload.get("info") if isinstance(payload, dict) else None
    latest = info.get("version") if isinstance(info, dict) else None
    if not isinstance(latest, str) or not latest:
        return None

    try:  # cache the *successful* check regardless of outcome
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass

    return latest if _is_newer(latest, current) else None
