"""Unit tests for the append-only streaming renderer (asksh.stream_render)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.text import Text

from asksh.stream_render import AppendOnlyWriter, LiveRow, PreviewWriter, PrintWriter
from test.term_helpers import has_cursor_up, make_console, simulate_terminal


def _writer(console):
    return AppendOnlyWriter(console, LiveRow(console))


def _stream_text(
    writer: AppendOnlyWriter | PrintWriter, text: str, *, chunk_size: int
) -> None:
    for i in range(0, len(text), chunk_size):
        writer.update(text[: i + chunk_size])
    writer.finish()


DOCUMENT = (
    "Here is a list:\n\n"
    "1. one\n2. two\n3. three\n\n"
    "And some code:\n\n"
    "```python\nprint('hi')\n```\n\n"
    "Done bold and em text."
)


def test_never_emits_cursor_up() -> None:
    console, buf = make_console(width=50)
    _stream_text(_writer(console), DOCUMENT, chunk_size=1)
    assert not has_cursor_up(buf.getvalue())


def test_char_by_char_matches_single_chunk_final_text() -> None:
    console_a, buf_a = make_console(width=50)
    _stream_text(_writer(console_a), DOCUMENT, chunk_size=1)

    console_b, buf_b = make_console(width=50)
    _stream_text(_writer(console_b), DOCUMENT, chunk_size=len(DOCUMENT))

    assert simulate_terminal(buf_a.getvalue()) == simulate_terminal(buf_b.getvalue())


def test_completed_lines_stream_verbatim_as_plain_text() -> None:
    """Text streaming never parses Markdown: markup stays literal on screen."""
    console, buf = make_console(width=50)
    writer = _writer(console)
    writer.update("**bold** and 1. not-a-list\n")
    writer.update("**bold** and 1. not-a-list\nsecond line\n")
    writer.finish()
    screen = simulate_terminal(buf.getvalue())
    assert any("**bold** and 1. not-a-list" in line for line in screen)
    assert any("second line" in line for line in screen)


def test_completed_lines_commit_immediately() -> None:
    """A physical line commits as soon as its trailing newline arrives --
    no holdback for tables/lists/fences (that logic no longer exists)."""
    console, buf = make_console(width=50)
    writer = _writer(console)
    writer.update("first line\n")
    mid_screen = simulate_terminal(buf.getvalue())
    assert any("first line" in line for line in mid_screen)
    writer.update("first line\nsecond line")
    writer.finish()
    final_screen = simulate_terminal(buf.getvalue())
    assert any("second line" in line for line in final_screen)


def test_spinner_shown_before_first_delta_and_gone_after() -> None:
    console, buf = make_console(width=50)
    writer = _writer(console)
    writer.update("")
    assert "\u280b" in buf.getvalue() or any(
        frame in buf.getvalue() for frame in "\u280b\u2819\u2839\u2838"
    )
    writer.update("Hello")
    writer.finish()
    screen = simulate_terminal(buf.getvalue())
    assert not any("\u280b" in line for line in screen)
    assert any("Hello" in line for line in screen)


def test_incomplete_line_preview_matches_committed_style() -> None:
    """The still-growing line uses the same style as committed text -- not a
    dim grey preview that flashes to the final color when the newline arrives."""
    buf = StringIO()
    console = Console(
        file=buf,
        width=50,
        force_terminal=True,
        color_system="truecolor",
        _environ={},
    )
    grey_ref = StringIO()
    grey_console = Console(
        file=grey_ref,
        width=50,
        force_terminal=True,
        color_system="truecolor",
        _environ={},
    )
    grey_console.print(Text("Hello", style="grey50"), end="")
    grey_ansi = grey_ref.getvalue()
    # Isolate the SGR that paints grey50 so we can assert it is absent.
    grey_sgr = grey_ansi[: grey_ansi.find("Hello")]
    assert grey_sgr.startswith("\x1b[")

    writer = AppendOnlyWriter(console, LiveRow(console))
    writer.update("Hello")
    assert grey_sgr not in buf.getvalue()
    writer.update("Hello world\n")
    writer.finish()
    assert grey_sgr not in buf.getvalue()


def test_finish_flushes_a_still_growing_last_line() -> None:
    console, buf = make_console(width=50)
    writer = _writer(console)
    writer.update("Hello wor")
    writer.update("Hello world")
    writer.finish()
    screen = simulate_terminal(buf.getvalue())
    assert any("Hello world" in line for line in screen)


def test_live_row_hands_off_between_writers_without_leftovers() -> None:
    """A writer taking over the live row must erase the previous owner's row."""
    console, buf = make_console(width=50)
    live_row = LiveRow(console)
    spinner_writer = AppendOnlyWriter(console, live_row)
    other_writer = AppendOnlyWriter(console, live_row)

    spinner_writer.update("")  # paints the spinner
    other_writer.update("thinking text")  # must erase the spinner row first
    other_writer.finish()

    screen = simulate_terminal(buf.getvalue())
    assert not any("\u280b" in line and "thinking" in line for line in screen)
    assert any("thinking text" in line for line in screen)


def test_preview_writer_never_commits_text() -> None:
    """PreviewWriter (the ``markdown`` style's live row) never prints the
    body -- only the spinner/preview, cleared away on finish()."""
    console, buf = make_console(width=50)
    writer = PreviewWriter(console, LiveRow(console))
    writer.update("some streaming text\nmore text")
    writer.finish()
    screen = simulate_terminal(buf.getvalue())
    assert not any("streaming text" in line for line in screen)
    assert not any("more text" in line for line in screen)


def test_preview_writer_shows_spinner_then_last_line_preview() -> None:
    console, buf = make_console(width=50)
    writer = PreviewWriter(console, LiveRow(console))
    writer.update("")
    assert "\u280b" in buf.getvalue() or any(
        frame in buf.getvalue() for frame in "\u280b\u2819\u2839\u2838"
    )
    writer.update("partial line")
    assert "partial line" in buf.getvalue()
    writer.finish()
    assert not has_cursor_up(buf.getvalue())


def _print_writer(console):
    return PrintWriter(console, LiveRow(console))


def test_print_writer_writes_tokens_once_without_preview() -> None:
    console, buf = make_console(width=50)
    writer = _print_writer(console)
    writer.update("Hello")
    writer.update("Hello world")
    writer.finish()
    raw = buf.getvalue()
    assert raw.count("Hello world") == 1
    assert any("Hello world" in line for line in simulate_terminal(raw))
    assert "\u2026" not in raw


def test_print_writer_long_line_is_not_truncated() -> None:
    """Unlike the live-row preview, a line wider than the terminal is
    written in full rather than ellipsized to one row."""
    console, buf = make_console(width=20)
    writer = _print_writer(console)
    text = "abcdefghijklmnopqrstuvwxyz"
    writer.update(text)
    writer.finish()
    raw = buf.getvalue()
    assert text in raw
    assert "\u2026" not in raw


def test_print_writer_adds_trailing_newline_only_when_missing() -> None:
    console, buf = make_console(width=50)
    writer = _print_writer(console)
    writer.update("Hello world")
    writer.finish()
    assert buf.getvalue().endswith("\n")
    assert not buf.getvalue().endswith("\n\n")

    console, buf = make_console(width=50)
    writer = _print_writer(console)
    writer.update("Hello world\n")
    writer.finish()
    assert buf.getvalue().endswith("\n")
    assert not buf.getvalue().endswith("\n\n")


def test_print_writer_spinner_cleared_before_first_text() -> None:
    console, buf = make_console(width=50)
    writer = _print_writer(console)
    writer.update("")
    assert "\u280b" in buf.getvalue() or any(
        frame in buf.getvalue() for frame in "\u280b\u2819\u2839\u2838"
    )
    writer.update("Hello")
    writer.finish()
    screen = simulate_terminal(buf.getvalue())
    assert not any("\u280b" in line for line in screen)
    assert any("Hello" in line for line in screen)


def test_print_writer_never_emits_cursor_up() -> None:
    console, buf = make_console(width=50)
    _stream_text(_print_writer(console), DOCUMENT, chunk_size=1)
    assert not has_cursor_up(buf.getvalue())
