"""Skip integration tests when Ollama is not reachable (offline / CI without server)."""

from __future__ import annotations

import os

import pytest
import requests

BASE_URL = os.environ.get("ASKSH_TEST_BASE_URL", "http://localhost:11434")


def _ollama_reachable() -> bool:
    try:
        r = requests.get(f"{BASE_URL.rstrip('/')}/api/tags", timeout=3)
        return r.ok
    except OSError:
        return False


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if _ollama_reachable():
        return
    skip = pytest.mark.skip(
        reason=(
            f"Ollama not reachable at {BASE_URL} "
            "(start Ollama or set ASKSH_TEST_BASE_URL)"
        )
    )
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip)
