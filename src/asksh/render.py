"""Rich console output and streaming/non-streaming reply rendering."""

from __future__ import annotations

import sys
import threading
from collections.abc import Generator

from typing import TextIO

from rich.console import Console
from rich.markdown import Markdown
from rich.style import Style
from rich.syntax import SyntaxTheme
from rich.text import Text

from asksh.client import (
    ChatStreamChunk,
    OllamaChatClient,
    ThinkOption,
    split_streaming_embedded_thinking,
    strip_embedded_thinking,
)
from asksh.history import ConversationHistory
from asksh.stream_render import AppendOnlyWriter, LiveRow, StreamAnimator

console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"
_THINKING_STYLE = "grey50 italic"


class _PlainSyntaxTheme(SyntaxTheme):
    """No-op theme: code blocks render as plain text, no highlighting (F-13)."""

    def get_style_for_token(self, token_type: object) -> Style:
        return Style.null()

    def get_background_style(self) -> Style:
        return Style.null()


plain_code_theme = _PlainSyntaxTheme()


def _drain(
    gen: Generator[ChatStreamChunk, None, object], file: TextIO | None = None
) -> None:
    """Write content chunks from a streaming generator to *file* (default stdout)."""
    out = file or sys.stdout
    try:
        while True:
            chunk = next(gen)
            if not chunk.is_thinking:
                out.write(chunk.text)
                out.flush()
    except StopIteration:
        pass


_THINKING_PREFIX = "Thinking...\n"


def _format_thinking(thinking: str) -> Text:
    return Text(f"{_THINKING_PREFIX}{thinking}", style=_THINKING_STYLE)


def _should_show_thinking(*, think: ThinkOption, show_thinking: bool) -> bool:
    return show_thinking or think is not False


def _print_thinking(thinking: str) -> None:
    if not thinking:
        return
    console.print(_format_thinking(thinking))


def _display_texts(content_text: str, thinking_text: str) -> tuple[str, str]:
    """Return ``(visible_content, thinking_to_show)`` for the current buffers.

    A dedicated ``thinking`` stream field always wins over embedded
    ``<think>`` tags (TUI-THINK-04); otherwise embedded tags are extracted
    from *content_text* as they close (and while a tag is still open, its
    partial contents are shown as thinking too, via
    ``split_streaming_embedded_thinking``).
    """
    if thinking_text:
        return strip_embedded_thinking(content_text), thinking_text
    visible, completed, pending = split_streaming_embedded_thinking(content_text)
    if completed and pending:
        return visible, f"{completed}\n\n{pending}"
    return visible, completed or pending


def _stream_reply_tty(
    gen: Generator[ChatStreamChunk, None, object],
    client: OllamaChatClient,
    abort: threading.Event,
    *,
    display_thinking: bool,
) -> None:
    live_row = LiveRow(console)
    content_writer = AppendOnlyWriter(
        console,
        live_row,
        markdown=True,
        code_theme=plain_code_theme,
        spinner_style=_SPINNER_STYLE,
    )
    thinking_writer = (
        AppendOnlyWriter(
            console,
            live_row,
            markdown=False,
            style=_THINKING_STYLE,
            preview_style=_THINKING_STYLE,
        )
        if display_thinking
        else None
    )

    animator = StreamAnimator()
    animator.start()
    animator.set(content_writer, "")

    thinking_text = ""
    content_text = ""
    thinking_started = False
    thinking_done = False

    try:
        try:
            while True:
                chunk = next(gen)
                if chunk.is_thinking:
                    if not display_thinking:
                        continue
                    thinking_text += chunk.text
                else:
                    content_text += chunk.text

                visible_content, thinking_display = _display_texts(
                    content_text, thinking_text
                )
                if thinking_display:
                    thinking_started = True

                if thinking_started and display_thinking and not thinking_done:
                    animator.set(
                        thinking_writer, f"{_THINKING_PREFIX}{thinking_display}"
                    )
                    if visible_content:
                        animator.finish(thinking_writer)
                        console.print()
                        thinking_done = True
                        animator.set(content_writer, visible_content)
                else:
                    animator.set(content_writer, visible_content)
        except StopIteration:
            pass
    except KeyboardInterrupt:
        # F-110: abort the reply cleanly -- no traceback, no history entry
        # for the partial reply. Whatever was already streamed to the
        # terminal stays (append-only: it cannot be un-printed).
        abort.set()
        client.abort_active_stream()
        return
    finally:
        animator.stop()
        if thinking_writer is not None and not thinking_done:
            thinking_writer.finish()
        content_writer.finish()


def print_assistant_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
    *,
    think: ThinkOption = False,
    show_thinking: bool = False,
) -> None:
    is_tty = sys.stdout.isatty()
    display_thinking = _should_show_thinking(think=think, show_thinking=show_thinking)

    if stream:
        abort = threading.Event()
        gen = client.stream_message(
            user_input,
            model=model,
            history=history,
            think=think,
            abort=abort,
        )
        if is_tty:
            _stream_reply_tty(gen, client, abort, display_thinking=display_thinking)
        else:
            _drain(gen)
            print()
    else:
        if is_tty:
            with console.status("", spinner="dots", spinner_style=_SPINNER_STYLE):
                reply, thinking = client.send_message(
                    user_input,
                    model=model,
                    history=history,
                    think=think,
                )
            if display_thinking and thinking:
                _print_thinking(thinking)
            if reply:
                if display_thinking and thinking:
                    console.print()
                console.print(Markdown(reply, code_theme=plain_code_theme))
        else:
            reply, _ = client.send_message(
                user_input,
                model=model,
                history=history,
                think=think,
            )
            print(reply)
