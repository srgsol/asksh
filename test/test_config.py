"""Tests for user config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from asksh import config as asksh_config


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "asksh" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_load_user_config_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = asksh_config.load_user_config()
    assert d == {}


def test_load_user_config_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = _config_file(tmp_path)
    p.write_text(
        'MODEL = "m1"\nBASE_URL = "http://example:11434"\n',
        encoding="utf-8",
    )
    d = asksh_config.load_user_config()
    assert d["model"] == "m1"
    assert d["base_url"] == "http://example:11434"


def test_default_config_path_respects_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert asksh_config.default_config_path() == Path("/xdg/asksh/config.toml")


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
def test_load_user_config_update_check_bool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str, expected: bool
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = _config_file(tmp_path)
    p.write_text(f"UPDATE_CHECK = {value}\n", encoding="utf-8")
    d = asksh_config.load_user_config()
    assert d["update_check"] is expected


def test_load_user_config_update_check_non_bool_warns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = _config_file(tmp_path)
    p.write_text("UPDATE_CHECK = 1\n", encoding="utf-8")
    d = asksh_config.load_user_config()
    assert "update_check" not in d
    assert "must be bool" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("key", "dest"),
    [
        ("ONESHOT_RENDER", "oneshot_render"),
        ("EXPLAIN_RENDER", "explain_render"),
        ("CHAT_RENDER", "chat_render"),
    ],
)
@pytest.mark.parametrize(
    "style", ["text", "markdown", "post_markdown", "live_markdown"]
)
def test_load_user_config_render_style_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key: str,
    dest: str,
    style: str,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = _config_file(tmp_path)
    p.write_text(f'{key} = "{style}"\n', encoding="utf-8")
    d = asksh_config.load_user_config()
    assert d[dest] == style


@pytest.mark.parametrize("key", ["ONESHOT_RENDER", "EXPLAIN_RENDER", "CHAT_RENDER"])
def test_load_user_config_render_style_invalid_value_warns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    key: str,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = _config_file(tmp_path)
    p.write_text(f'{key} = "fancy"\n', encoding="utf-8")
    d = asksh_config.load_user_config()
    assert not d
    assert "must be one of" in capsys.readouterr().err


def test_load_user_config_render_style_wrong_type_warns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = _config_file(tmp_path)
    p.write_text("ONESHOT_RENDER = true\n", encoding="utf-8")
    d = asksh_config.load_user_config()
    assert "oneshot_render" not in d
    assert "must be str" in capsys.readouterr().err
