"""Rich console output and streaming/non-streaming reply rendering."""

from __future__ import annotations

import sys
import threading
from collections.abc import Generator
from typing import TextIO, cast

from rich._loop import loop_last
from rich.console import Console, Group
from rich.live import Live
from rich.live_render import LiveRender, VerticalOverflowMethod
from rich.markdown import Markdown
from rich.segment import Segment
from rich.spinner import Spinner
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
from asksh.config import RenderStyle
from asksh.history import ConversationHistory
from asksh.last_message import save_last_message
from asksh.stream_render import (
    AppendOnlyWriter,
    LiveRow,
    PreviewWriter,
    PrintWriter,
    StreamAnimator,
)

console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"
_THINKING_STYLE = "grey50 italic"
_WAIT_SPINNER = Spinner("dots", style=_SPINNER_STYLE)

# Only the ``live_markdown`` render style uses Rich ``Live``. Its cursor-up
# repaint can desync on resize or scroll for a preview taller than the
# terminal -- an accepted trade-off for that style, see docs/tui-spec.md.
_LIVE_VERTICAL_OVERFLOW = "crop_above"
_crop_above_patched = False


class _PlainSyntaxTheme(SyntaxTheme):
    """No-op theme: code blocks render as plain text, no highlighting (F-13)."""

    def get_style_for_token(self, token_type: object) -> Style:
        return Style.null()

    def get_background_style(self) -> Style:
        return Style.null()


plain_code_theme = _PlainSyntaxTheme()


def _patch_live_render_crop_above() -> None:
    """Keep the newest lines of a ``live_markdown`` preview visible when it
    grows taller than the terminal (Rich's default ``Live`` overflow keeps
    the *top* of the preview pinned and lets the newest lines scroll off the
    bottom, which is backwards for a streaming reply).

    Used only by the ``live_markdown`` render style. It is not part of the
    other styles' resize/scroll safety, which needs no patch: they never
    move the cursor up in the first place.
    """
    global _crop_above_patched
    if _crop_above_patched:
        return

    original_rich_console = LiveRender.__rich_console__

    def __rich_console__(self, console, options):
        if self.vertical_overflow != _LIVE_VERTICAL_OVERFLOW:
            yield from original_rich_console(self, console, options)
            return

        renderable = self.renderable
        style = console.get_style(self.style)
        lines = console.render_lines(renderable, options, style=style, pad=False)
        shape = Segment.get_shape(lines)

        _, height = shape
        if height > options.size.height:
            lines = lines[-options.size.height :]
            shape = Segment.get_shape(lines)
        self._shape = shape

        new_line = Segment.line()
        for last, line in loop_last(lines):
            yield from line
            if not last:
                yield new_line

    LiveRender.__rich_console__ = __rich_console__
    _crop_above_patched = True


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


def _live_markdown_display(
    content_text: str, thinking_text: str, *, display_thinking: bool
) -> Text | Markdown | Group | Spinner:
    visible_content, thinking_display = _display_texts(content_text, thinking_text)
    parts: list[Text | Markdown] = []
    if display_thinking and thinking_display:
        parts.append(_format_thinking(thinking_display))
    if visible_content:
        if parts:
            parts.append(Text(""))
        parts.append(Markdown(visible_content, code_theme=plain_code_theme))
    if not parts:
        return _WAIT_SPINNER
    if len(parts) == 1:
        return parts[0]
    return Group(*parts)


def _stream_reply_tty_live_markdown(
    gen: Generator[ChatStreamChunk, None, object],
    client: OllamaChatClient,
    abort: threading.Event,
    *,
    display_thinking: bool,
) -> None:
    """``live_markdown``: Rich ``Live`` re-renders the whole Markdown buffer
    on every chunk. Unlike the other styles this repaints with cursor-up and
    can desync on resize or scroll if the preview grows taller than the
    terminal; accepted for a live-formatted preview (docs/tui-spec.md).
    """
    _patch_live_render_crop_above()
    thinking_text = ""
    content_text = ""

    def _apply_chunk(chunk: ChatStreamChunk) -> None:
        nonlocal thinking_text, content_text
        if chunk.is_thinking:
            if display_thinking:
                thinking_text += chunk.text
        else:
            content_text += chunk.text

    def _has_displayable_text() -> bool:
        visible_content, thinking_display = _display_texts(content_text, thinking_text)
        return bool(visible_content or (display_thinking and thinking_display))

    live_row = LiveRow(console)
    wait_writer = PreviewWriter(console, live_row, spinner_style=_SPINNER_STYLE)
    animator = StreamAnimator()
    animator.start()
    animator.set(wait_writer, "")

    try:
        try:
            while True:
                chunk = next(gen)
                _apply_chunk(chunk)
                if _has_displayable_text():
                    break
        except StopIteration:
            pass
    except KeyboardInterrupt:
        abort.set()
        client.abort_active_stream()
        return
    finally:
        animator.stop()
        wait_writer.finish()

    if not _has_displayable_text():
        return

    try:
        with Live(
            _live_markdown_display(
                content_text, thinking_text, display_thinking=display_thinking
            ),
            console=console,
            refresh_per_second=12,
            transient=True,
            vertical_overflow=cast(VerticalOverflowMethod, _LIVE_VERTICAL_OVERFLOW),
        ) as live:
            try:
                while True:
                    chunk = next(gen)
                    _apply_chunk(chunk)
                    live.update(
                        _live_markdown_display(
                            content_text,
                            thinking_text,
                            display_thinking=display_thinking,
                        )
                    )
            except StopIteration:
                pass
    except KeyboardInterrupt:
        # F-110: abort cleanly. Live's transient region is erased on exit;
        # there is no final Markdown print for an aborted reply.
        abort.set()
        client.abort_active_stream()
        return

    visible_content, thinking_display = _display_texts(content_text, thinking_text)
    if display_thinking and thinking_display:
        _print_thinking(thinking_display)
    if visible_content:
        if display_thinking and thinking_display:
            console.print()
        console.print(Markdown(visible_content, code_theme=plain_code_theme))


def _stream_reply_tty(
    gen: Generator[ChatStreamChunk, None, object],
    client: OllamaChatClient,
    abort: threading.Event,
    *,
    display_thinking: bool,
    render_style: RenderStyle,
) -> None:
    if render_style == "live_markdown":
        _stream_reply_tty_live_markdown(
            gen, client, abort, display_thinking=display_thinking
        )
        return

    live_row = LiveRow(console)
    if render_style == "markdown":
        content_writer = PreviewWriter(console, live_row, spinner_style=_SPINNER_STYLE)
    elif render_style == "text":
        content_writer = PrintWriter(console, live_row, spinner_style=_SPINNER_STYLE)
    else:
        content_writer = AppendOnlyWriter(
            console, live_row, spinner_style=_SPINNER_STYLE
        )
    thinking_writer = (
        AppendOnlyWriter(
            console,
            live_row,
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
        # terminal stays (append-only: it cannot be un-printed). The
        # ``markdown`` style never printed a body, so nothing is left
        # behind there either.
        abort.set()
        client.abort_active_stream()
        return
    finally:
        animator.stop()
        if thinking_writer is not None and not thinking_done:
            thinking_writer.finish()
        content_writer.finish()

    visible_content, _ = _display_texts(content_text, thinking_text)
    if render_style in ("markdown", "post_markdown") and visible_content:
        if render_style == "post_markdown":
            console.print()
        console.print(Markdown(visible_content, code_theme=plain_code_theme))


def _save_last_assistant_reply(history: ConversationHistory) -> None:
    """Persist the most recent assistant turn so ``asksh -m`` can re-render it.

    A no-op when the last chat message isn't from the assistant (e.g. the
    reply was aborted, so no assistant turn was added to *history*), or when
    that turn has no visible content.
    """
    chat_messages = history.get_chat_messages()
    if chat_messages and chat_messages[-1].role == "assistant":
        content = chat_messages[-1].content
        if content.strip():
            save_last_message(content)


def print_saved_markdown(content: str) -> None:
    """Render a previously saved assistant reply (``asksh -m``) as Markdown."""
    if sys.stdout.isatty():
        console.print(Markdown(content, code_theme=plain_code_theme))
    else:
        print(content)


def print_assistant_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
    *,
    think: ThinkOption = False,
    show_thinking: bool = False,
    render_style: RenderStyle = "text",
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
            _stream_reply_tty(
                gen,
                client,
                abort,
                display_thinking=display_thinking,
                render_style=render_style,
            )
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
                if render_style == "text":
                    console.print(Text(reply))
                else:
                    console.print(Markdown(reply, code_theme=plain_code_theme))
        else:
            reply, _ = client.send_message(
                user_input,
                model=model,
                history=history,
                think=think,
            )
            print(reply)

    _save_last_assistant_reply(history)
