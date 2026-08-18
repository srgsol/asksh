"""Rich console output and streaming/non-streaming reply rendering."""

from __future__ import annotations

import sys
from collections.abc import Generator

from typing import cast, TextIO

from rich._loop import loop_last
from rich.console import Console, Group
from rich.live import Live
from rich.live_render import LiveRender, VerticalOverflowMethod
from rich.markdown import Markdown
from rich.segment import Segment
from rich.spinner import Spinner
from rich.text import Text

from asksh.client import (
    ChatStreamChunk,
    OllamaChatClient,
    ThinkOption,
    split_embedded_thinking,
)
from asksh.history import ConversationHistory

console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"
_LIVE_VERTICAL_OVERFLOW = "crop_above"
_crop_above_patched = False


def _patch_live_render_crop_above() -> None:
    """Keep newest streamed lines visible when content exceeds terminal height."""
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


_patch_live_render_crop_above()


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
    return Text(f"{_THINKING_PREFIX}{thinking}", style="grey50 italic")


def _should_show_thinking(*, think: ThinkOption, show_thinking: bool) -> bool:
    return show_thinking or think is not False


def _stream_display(
    thinking: str,
    content: str,
    *,
    think: ThinkOption,
    show_thinking: bool,
) -> Text | Markdown | Group | Spinner:
    display_thinking = _should_show_thinking(think=think, show_thinking=show_thinking)
    display_content, embedded = split_embedded_thinking(content)
    if embedded and not thinking:
        thinking = embedded

    parts: list[Text | Markdown] = []
    if display_thinking and thinking:
        parts.append(_format_thinking(thinking))
    if display_content:
        if parts:
            parts.append(Text(""))
        parts.append(Markdown(display_content))
    if not parts:
        return Spinner("dots", style=_SPINNER_STYLE)
    if len(parts) == 1:
        return parts[0]
    return Group(*parts)


def _print_thinking(thinking: str) -> None:
    if not thinking:
        return
    console.print(_format_thinking(thinking))


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
        gen = client.stream_message(
            user_input,
            model=model,
            history=history,
            think=think,
        )
        if is_tty:
            thinking_text = ""
            content_text = ""
            with Live(
                _stream_display("", "", think=think, show_thinking=show_thinking),
                console=console,
                refresh_per_second=12,
                transient=True,
                vertical_overflow=cast(VerticalOverflowMethod, _LIVE_VERTICAL_OVERFLOW),
            ) as live:
                try:
                    while True:
                        chunk = next(gen)
                        if chunk.is_thinking:
                            thinking_text += chunk.text
                        else:
                            content_text += chunk.text
                        live.update(
                            _stream_display(
                                thinking_text,
                                content_text,
                                think=think,
                                show_thinking=show_thinking,
                            )
                        )
                except StopIteration:
                    pass
            content_text, embedded_thinking = split_embedded_thinking(content_text)
            if embedded_thinking and not thinking_text:
                thinking_text = embedded_thinking
            if display_thinking and thinking_text:
                _print_thinking(thinking_text)
            if content_text:
                if display_thinking and thinking_text:
                    console.print()
                console.print(Markdown(content_text))
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
                console.print(Markdown(reply))
        else:
            reply, _ = client.send_message(
                user_input,
                model=model,
                history=history,
                think=think,
            )
            print(reply)
