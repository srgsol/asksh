"""Tests for persisting/loading the last assistant reply (``asksh -m``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from asksh.last_message import (
    default_state_dir,
    last_message_path,
    load_last_message,
    save_last_message,
)


def test_default_state_dir_respects_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg-state")
    assert default_state_dir() == Path("/xdg-state/asksh")


def test_last_message_path_respects_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg-state")
    assert last_message_path() == Path("/xdg-state/asksh/last_reply")


def test_load_last_message_missing_file_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert load_last_message() is None


def test_save_and_load_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    save_last_message("# Heading\n\nSome **markdown**.")
    assert load_last_message() == "# Heading\n\nSome **markdown**."


def test_save_last_message_creates_parent_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert not (tmp_path / "asksh").exists()
    save_last_message("hello")
    assert last_message_path().is_file()


def test_save_last_message_overwrites_previous_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    save_last_message("first reply")
    save_last_message("second reply")
    assert load_last_message() == "second reply"
