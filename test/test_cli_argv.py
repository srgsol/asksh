"""CLI argument parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from asksh.cli import parse_args, run


def test_chat_and_explain_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "-e", "-c", "query"])
    with pytest.raises(SystemExit):
        parse_args()


def test_explain_without_query_does_not_enable_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "-e", "why ls -la"])
    args = parse_args()
    assert args.explain is True
    assert args.chat is False


def test_show_thinking_without_think_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "--show-thinking", "query"])
    args = parse_args()
    assert args.show_thinking is True
    assert args.think is None


def test_update_check_default_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["asksh", "query"])
    args = parse_args()
    assert args.update_check is True


def test_no_update_check_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["asksh", "--no-update-check", "query"])
    args = parse_args()
    assert args.update_check is False


def test_config_update_check_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "asksh" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("UPDATE_CHECK = false\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["asksh", "query"])
    args = parse_args()
    assert args.update_check is False


def test_markdown_flag_sets_args_and_does_not_enable_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "-m"])
    args = parse_args()
    assert args.markdown is True
    assert args.chat is False
    assert args.explain is False


@pytest.mark.parametrize("other_flag", ["-c", "-e"])
def test_markdown_is_mutually_exclusive_with_chat_and_explain(
    monkeypatch: pytest.MonkeyPatch, other_flag: str
) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "-m", other_flag])
    with pytest.raises(SystemExit):
        parse_args()


def test_markdown_with_query_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "-m", "some query"])
    with pytest.raises(SystemExit):
        parse_args()


def test_run_markdown_mode_prints_saved_reply_without_contacting_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "-m"])
    args = parse_args()

    with (
        patch("asksh.cli.load_last_message", return_value="# hi") as mock_load,
        patch("asksh.cli.print_saved_markdown") as mock_print,
        patch("asksh.cli.verify_ollama_status") as mock_verify,
    ):
        run(args)

    mock_load.assert_called_once_with()
    mock_print.assert_called_once_with("# hi")
    mock_verify.assert_not_called()


def test_run_markdown_mode_without_saved_reply_exits_with_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["asksh", "-m"])
    args = parse_args()

    with (
        patch("asksh.cli.load_last_message", return_value=None),
        patch("asksh.cli.verify_ollama_status") as mock_verify,
    ):
        with pytest.raises(SystemExit) as excinfo:
            run(args)

    assert excinfo.value.code == 1
    assert "no previous assistant message" in capsys.readouterr().err
    mock_verify.assert_not_called()


def test_render_style_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["asksh", "query"])
    args = parse_args()
    assert args.oneshot_render == "text"
    assert args.explain_render == "text"
    assert args.chat_render == "text"


def test_render_style_config_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "asksh" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        'ONESHOT_RENDER = "live_markdown"\nCHAT_RENDER = "post_markdown"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["asksh", "query"])
    args = parse_args()
    assert args.oneshot_render == "live_markdown"
    assert args.explain_render == "text"
    assert args.chat_render == "post_markdown"
