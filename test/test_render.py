"""Unit tests for reply rendering (inline streaming, final print, abort, non-TTY paths)."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.markdown import Markdown
from rich.text import Text

import asksh.render as render_mod
from asksh.client import ChatStreamChunk
from asksh.history import ConversationHistory
from asksh.last_message import load_last_message, save_last_message
from asksh.render import _drain, print_assistant_reply, print_saved_markdown
from test.term_helpers import has_cursor_up, make_console, simulate_terminal


def _fake_stream(chunks: list[ChatStreamChunk]) -> MagicMock:
    """Return a client whose ``stream_message`` yields the given chunks."""
    client = MagicMock()
    client.stream_message.return_value = iter(chunks)
    return client


def _run_stream(chunks: list[ChatStreamChunk], **kwargs) -> tuple[str, MagicMock]:
    """Run the TTY streaming path against a real, captured console."""
    client = _fake_stream(chunks)
    history = ConversationHistory(system_prompt="test")
    console, buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(client, history, "test-model", True, "hi", **kwargs)

    return buf.getvalue(), client


# -- default ("text") style: append-only, no cursor-up, no Markdown parsing --


def test_stream_tty_never_emits_cursor_up() -> None:
    """Regression test for the whole class of resize/scroll desync bugs."""
    text = "Here is a list:\n\n1. one\n2. two\n3. three\n\n```python\nprint('hi')\n```\n\nDone."
    raw, _ = _run_stream([ChatStreamChunk(text)])
    assert not has_cursor_up(raw)


def test_stream_tty_text_style_shows_source_literally() -> None:
    """Default style streams plain text: no incremental Markdown parsing."""
    raw, _ = _run_stream([ChatStreamChunk("**bold** and 1. not a list\n")])
    screen = simulate_terminal(raw)
    assert any("**bold** and 1. not a list" in line for line in screen)


def test_stream_tty_prints_content_once() -> None:
    raw, client = _run_stream([ChatStreamChunk("Hello world")])

    abort_event = client.stream_message.call_args.kwargs["abort"]
    assert isinstance(abort_event, threading.Event)
    assert not abort_event.is_set()
    screen = simulate_terminal(raw)
    assert screen.count("Hello world") == 1


def test_stream_tty_text_style_ends_with_newline() -> None:
    raw, _ = _run_stream([ChatStreamChunk("Hello world")])
    assert raw.endswith("\n")
    screen = simulate_terminal(raw)
    assert screen[-1] == ""


def test_stream_tty_text_style_preserves_existing_trailing_newline() -> None:
    raw, _ = _run_stream([ChatStreamChunk("Hello world\n")])
    assert raw.endswith("\n")
    assert not raw.endswith("\n\n")


def test_stream_tty_text_style_prints_tokens_without_preview() -> None:
    """Text mode writes tokens as they arrive; it does not hold the last
    line as a truncated live-row preview."""
    text = "a" * 80
    raw, _ = _run_stream([ChatStreamChunk(text)])
    assert text in raw
    assert raw.count(text) == 1
    assert "\u2026" not in raw


def test_stream_tty_prints_thinking_when_think_enabled() -> None:
    raw, _ = _run_stream(
        [
            ChatStreamChunk("Need a listing.", is_thinking=True),
            ChatStreamChunk("ls -la"),
        ],
        think=True,
    )
    screen = simulate_terminal(raw)
    thinking_idx = next(i for i, line in enumerate(screen) if "Thinking..." in line)
    reasoning_idx = next(
        i for i, line in enumerate(screen) if "Need a listing." in line
    )
    content_idx = next(i for i, line in enumerate(screen) if "ls -la" in line)
    assert thinking_idx < reasoning_idx < content_idx
    # a blank separator line sits between the thinking trace and the content
    assert "" in screen[reasoning_idx + 1 : content_idx + 1]


def test_stream_tty_think_disabled_skips_thinking() -> None:
    raw, _ = _run_stream(
        [
            ChatStreamChunk("secret reasoning", is_thinking=True),
            ChatStreamChunk("ls -la"),
        ]
    )
    assert "secret" not in raw
    screen = simulate_terminal(raw)
    assert any("ls -la" in line for line in screen)


def _stream_interrupted() -> Generator[ChatStreamChunk, None, tuple[str, str]]:
    yield ChatStreamChunk("partial reply")
    raise KeyboardInterrupt


def test_stream_tty_abort_keeps_partial_reply_visible() -> None:
    """Append-only rendering cannot un-print: Ctrl-C leaves the partial reply on screen."""
    client = MagicMock()
    client.stream_message.return_value = _stream_interrupted()
    history = ConversationHistory(system_prompt="test")
    console, buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(client, history, "test-model", True, "hi")

    client.abort_active_stream.assert_called_once()
    abort_event = client.stream_message.call_args.kwargs["abort"]
    assert abort_event.is_set()
    raw = buf.getvalue()
    assert not has_cursor_up(raw)
    assert any("partial reply" in line for line in simulate_terminal(raw))


def _stream_error() -> Generator[ChatStreamChunk, None, tuple[str, str]]:
    raise RuntimeError("network down")
    yield ChatStreamChunk("unreachable")


def test_stream_tty_error_is_raised() -> None:
    client = MagicMock()
    client.stream_message.return_value = _stream_error()
    history = ConversationHistory(system_prompt="test")
    console, _buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        with pytest.raises(RuntimeError, match="network down"):
            print_assistant_reply(client, history, "test-model", True, "hi")


def test_stream_tty_empty_reply_prints_nothing() -> None:
    raw, _ = _run_stream([])
    assert [line for line in simulate_terminal(raw) if line.strip()] == []


def _run_delayed_stream(render_style: str) -> str:
    """Run a stream that waits before the first chunk so the wait spinner paints."""

    def delayed_stream():
        time.sleep(0.15)
        yield ChatStreamChunk("Hello")

    client = MagicMock()
    client.stream_message.return_value = delayed_stream()
    history = ConversationHistory(system_prompt="test")
    console, buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(
            client, history, "test-model", True, "hi", render_style=render_style
        )

    return buf.getvalue()


@pytest.mark.parametrize(
    "render_style", ["text", "markdown", "post_markdown", "live_markdown"]
)
def test_stream_tty_shows_spinner_before_first_delta(render_style: str) -> None:
    raw = _run_delayed_stream(render_style)
    assert any(frame in raw for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


# -- "markdown" style: no streamed body, one Markdown print at the end --


def test_stream_tty_markdown_style_defers_body_until_stream_ends() -> None:
    raw, _ = _run_stream(
        [ChatStreamChunk("**bold**"), ChatStreamChunk(" text")],
        render_style="markdown",
    )
    screen = [line for line in simulate_terminal(raw) if line.strip()]
    assert not any("**bold**" in line for line in screen)
    assert any(line.strip() == "bold text" for line in screen)


def test_stream_tty_markdown_style_never_emits_cursor_up() -> None:
    raw, _ = _run_stream(
        [ChatStreamChunk("Here is a list:\n\n1. one\n2. two\n")],
        render_style="markdown",
    )
    assert not has_cursor_up(raw)


def test_stream_tty_markdown_style_interrupt_prints_nothing() -> None:
    """No committed body and no final Markdown print for an aborted reply."""
    client = MagicMock()
    client.stream_message.return_value = _stream_interrupted()
    history = ConversationHistory(system_prompt="test")
    console, buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(
            client, history, "test-model", True, "hi", render_style="markdown"
        )

    raw = buf.getvalue()
    assert not has_cursor_up(raw)
    screen = [line for line in simulate_terminal(raw) if line.strip()]
    assert not any("partial reply" in line for line in screen)


def test_stream_tty_markdown_style_empty_reply_prints_nothing() -> None:
    raw, _ = _run_stream([], render_style="markdown")
    assert [line for line in simulate_terminal(raw) if line.strip()] == []


# -- "post_markdown" style: append-only text, then a second Markdown copy --


def test_stream_tty_post_markdown_style_prints_text_then_markdown_copy() -> None:
    raw, _ = _run_stream(
        [ChatStreamChunk("**bold** text")], render_style="post_markdown"
    )
    screen = [line for line in simulate_terminal(raw) if line.strip()]
    assert any(line.strip() == "**bold** text" for line in screen)
    assert any(line.strip() == "bold text" for line in screen)


def test_stream_tty_post_markdown_style_never_emits_cursor_up() -> None:
    raw, _ = _run_stream([ChatStreamChunk("Hello world")], render_style="post_markdown")
    assert not has_cursor_up(raw)


def test_stream_tty_post_markdown_style_interrupt_skips_markdown_copy() -> None:
    client = MagicMock()
    client.stream_message.return_value = _stream_interrupted()
    history = ConversationHistory(system_prompt="test")
    console, buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(
            client, history, "test-model", True, "hi", render_style="post_markdown"
        )

    raw = buf.getvalue()
    assert not has_cursor_up(raw)
    screen = [line for line in simulate_terminal(raw) if line.strip()]
    assert screen.count("partial reply") == 1


# -- "live_markdown" style: Rich Live, one final Markdown copy --


def test_stream_tty_live_markdown_style_prints_one_markdown_copy() -> None:
    raw, _ = _run_stream(
        [ChatStreamChunk("**bold**"), ChatStreamChunk(" text")],
        render_style="live_markdown",
    )
    screen = [line for line in simulate_terminal(raw) if line.strip()]
    assert screen.count("bold text") == 1


def test_stream_tty_live_markdown_style_interrupt_prints_nothing() -> None:
    client = MagicMock()
    client.stream_message.return_value = _stream_interrupted()
    history = ConversationHistory(system_prompt="test")
    console, buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(
            client, history, "test-model", True, "hi", render_style="live_markdown"
        )

    client.abort_active_stream.assert_called_once()
    screen = [line for line in simulate_terminal(buf.getvalue()) if line.strip()]
    assert not any("partial reply" in line for line in screen)


def test_stream_tty_live_markdown_style_empty_reply_prints_nothing() -> None:
    raw, _ = _run_stream([], render_style="live_markdown")
    assert [line for line in simulate_terminal(raw) if line.strip()] == []


# -- non-TTY drain (unaffected by render_style) --


def test_stream_non_tty_drains_content_and_skips_thinking() -> None:
    client = MagicMock()
    history = ConversationHistory(system_prompt="test")
    client.stream_message.return_value = iter(
        [
            ChatStreamChunk("Hello", is_thinking=False),
            ChatStreamChunk(" secret", is_thinking=True),
            ChatStreamChunk(" world", is_thinking=False),
        ]
    )
    out = StringIO()
    out.isatty = lambda: False  # type: ignore[attr-defined]

    with (
        patch("asksh.render.sys", stdout=out),
        patch("builtins.print") as mock_print,
    ):
        print_assistant_reply(client, history, "test-model", True, "hi")

    assert out.getvalue() == "Hello world"
    mock_print.assert_called_once_with()


def test_drain_writes_chunks_verbatim() -> None:
    gen = iter(
        [
            ChatStreamChunk("a", is_thinking=False),
            ChatStreamChunk("t", is_thinking=True),
            ChatStreamChunk("b", is_thinking=False),
        ]
    )
    out = StringIO()
    _drain(gen, file=out)
    assert out.getvalue() == "ab"


# -- non-streaming TTY/non-TTY paths --


def test_non_stream_tty_text_style_prints_plain_text() -> None:
    client = MagicMock()
    history = ConversationHistory(system_prompt="test")
    client.send_message.return_value = ("ls -la", "")

    status_cm = MagicMock()
    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch("asksh.render.console.status", return_value=status_cm),
        patch("asksh.render.console.print") as mock_console_print,
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(client, history, "test-model", False, "hi")

    status_cm.__enter__.assert_called_once()
    client.send_message.assert_called_once()
    mock_console_print.assert_called_once()
    assert isinstance(mock_console_print.call_args.args[0], Text)


@pytest.mark.parametrize("render_style", ["markdown", "post_markdown", "live_markdown"])
def test_non_stream_tty_non_text_styles_print_markdown(render_style: str) -> None:
    client = MagicMock()
    history = ConversationHistory(system_prompt="test")
    client.send_message.return_value = ("ls -la", "")

    status_cm = MagicMock()
    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch("asksh.render.console.status", return_value=status_cm),
        patch("asksh.render.console.print") as mock_console_print,
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(
            client, history, "test-model", False, "hi", render_style=render_style
        )

    mock_console_print.assert_called_once()
    assert isinstance(mock_console_print.call_args.args[0], Markdown)


def test_non_stream_non_tty_prints_plain_reply() -> None:
    client = MagicMock()
    history = ConversationHistory(system_prompt="test")
    client.send_message.return_value = ("ls -la", "secret")

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch("builtins.print") as mock_print,
    ):
        mock_stdout.isatty.return_value = False
        print_assistant_reply(client, history, "test-model", False, "hi")

    mock_print.assert_called_once_with("ls -la")


# -- persisting the last assistant reply for ``asksh -m`` --


def test_print_assistant_reply_saves_completed_stream_to_last_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    history = ConversationHistory(system_prompt="test")

    def gen():
        yield ChatStreamChunk("Hello world")
        history.add_message("assistant", "Hello world")

    client = MagicMock()
    client.stream_message.return_value = gen()
    console, _buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(client, history, "test-model", True, "hi")

    assert load_last_message() == "Hello world"


def test_print_assistant_reply_abort_does_not_overwrite_saved_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    save_last_message("previous reply")

    client = MagicMock()
    client.stream_message.return_value = _stream_interrupted()
    history = ConversationHistory(system_prompt="test")
    console, _buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(client, history, "test-model", True, "hi")

    assert load_last_message() == "previous reply"


def test_print_assistant_reply_empty_reply_does_not_overwrite_saved_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    save_last_message("previous reply")

    history = ConversationHistory(system_prompt="test")

    def gen():
        yield from ()
        history.add_message("assistant", "")

    client = MagicMock()
    client.stream_message.return_value = gen()
    console, _buf = make_console()

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_assistant_reply(client, history, "test-model", True, "hi")

    assert load_last_message() == "previous reply"


def test_print_assistant_reply_saves_non_stream_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    history = ConversationHistory(system_prompt="test")
    client = MagicMock()

    def send_message(user_input, **kwargs):
        history.add_message("assistant", "ls -la")
        return "ls -la", ""

    client.send_message.side_effect = send_message

    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch("builtins.print"),
    ):
        mock_stdout.isatty.return_value = False
        print_assistant_reply(client, history, "test-model", False, "hi")

    assert load_last_message() == "ls -la"


def test_print_saved_markdown_tty_renders_markdown() -> None:
    console, buf = make_console()
    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch.object(render_mod, "console", console),
    ):
        mock_stdout.isatty.return_value = True
        print_saved_markdown("**bold** text")

    screen = [line for line in simulate_terminal(buf.getvalue()) if line.strip()]
    assert any(line.strip() == "bold text" for line in screen)


def test_print_saved_markdown_non_tty_prints_raw_text() -> None:
    with (
        patch("asksh.render.sys.stdout") as mock_stdout,
        patch("builtins.print") as mock_print,
    ):
        mock_stdout.isatty.return_value = False
        print_saved_markdown("**bold** text")

    mock_print.assert_called_once_with("**bold** text")
