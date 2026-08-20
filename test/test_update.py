"""Unit tests for the startup PyPI update check."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from asksh import update as asksh_update
from asksh.cli import warn_if_update_available


@pytest.fixture()
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_newer_version_returns_it(isolated_cache: Path) -> None:
    with patch(
        "asksh.update.requests.get",
        return_value=_response({"info": {"version": "1.1.0"}}),
    ) as mock_get:
        assert asksh_update.check_for_update("1.0.0") == "1.1.0"

    mock_get.assert_called_once_with(asksh_update._PYPI_JSON_URL, timeout=2.0)
    assert asksh_update._cache_path().exists()


@pytest.mark.parametrize("latest", ["1.0.0", "0.9.9"])
def test_same_or_older_returns_none(latest: str, isolated_cache: Path) -> None:
    with patch(
        "asksh.update.requests.get",
        return_value=_response({"info": {"version": latest}}),
    ):
        assert asksh_update.check_for_update("1.0.0") is None

    assert asksh_update._cache_path().exists()  # successful check still cached


@pytest.mark.parametrize(
    "side_effect",
    [
        requests.exceptions.ConnectionError(),
        requests.exceptions.Timeout(),
    ],
)
def test_request_exceptions_return_none(
    side_effect: Exception, isolated_cache: Path
) -> None:
    with patch("asksh.update.requests.get", side_effect=side_effect):
        assert asksh_update.check_for_update("1.0.0") is None

    assert not asksh_update._cache_path().exists()  # retry next run


def test_http_error_returns_none(isolated_cache: Path) -> None:
    resp = _response({"info": {"version": "1.1.0"}})
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError()
    with patch("asksh.update.requests.get", return_value=resp):
        assert asksh_update.check_for_update("1.0.0") is None

    assert not asksh_update._cache_path().exists()


def test_bad_json_returns_none(isolated_cache: Path) -> None:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError()
    with patch("asksh.update.requests.get", return_value=resp):
        assert asksh_update.check_for_update("1.0.0") is None

    assert not asksh_update._cache_path().exists()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"info": {}},
        {"info": {"version": 123}},
        ["not", "a", "dict"],
    ],
)
def test_malformed_payload_returns_none(payload: object, isolated_cache: Path) -> None:
    with patch("asksh.update.requests.get", return_value=_response(payload)):
        assert asksh_update.check_for_update("1.0.0") is None

    assert not asksh_update._cache_path().exists()


def test_fresh_cache_skips_network(isolated_cache: Path) -> None:
    cache = asksh_update._cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.touch()

    with patch("asksh.update.requests.get") as mock_get:
        assert asksh_update.check_for_update("1.0.0") is None

    mock_get.assert_not_called()


def test_non_packaged_install_skips_network(isolated_cache: Path) -> None:
    with patch("asksh.update.requests.get") as mock_get:
        assert asksh_update.check_for_update("0.0.0") is None

    mock_get.assert_not_called()


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("1.0.1", "1.0.0", True),
        ("1.10.0", "1.9.0", True),
        ("2.0.0", "1.9.9", True),
        ("1.0.0.post1", "1.0.0", False),
        ("1.0.0", "1.0.0.post1", False),
        ("1.0", "1.0.0", False),
        ("1.0.1rc1", "1.0.0", True),
        ("0.9.9", "1.0.0", False),
    ],
)
def test_is_newer(latest: str, current: str, expected: bool) -> None:
    assert asksh_update._is_newer(latest, current) is expected


def test_warn_if_update_available_prints_to_stderr(
    capsys: pytest.CaptureFixture,
) -> None:
    args = argparse.Namespace(update_check=True)
    with patch("asksh.cli.check_for_update", return_value="9.9.9"):
        warn_if_update_available(args)

    err = capsys.readouterr().err
    assert "new asksh version available: 9.9.9" in err
    assert "pipx upgrade asksh" in err


def test_warn_if_update_available_silent_when_disabled(
    capsys: pytest.CaptureFixture,
) -> None:
    with patch("asksh.cli.check_for_update") as mock_check:
        warn_if_update_available(argparse.Namespace(update_check=False))

    mock_check.assert_not_called()
    assert capsys.readouterr().err == ""
