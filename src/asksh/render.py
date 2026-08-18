"""Reply rendering: Textual streaming overlay (TTY) and plain output (non-TTY).

In a TTY, a streamed reply is shown by a transient Textual overlay app
(``asksh.stream``) that takes the alternate screen only while the stream is
in progress. When the stream ends, the overlay exits and the final reply is
printed here to the normal screen, so the terminal scrollback keeps exactly
one copy of the reply. Rich is used only for this static printing.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from typing import TextIO

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from asksh.client import ChatStreamChunk, OllamaChatClient, ThinkOption
from asksh.history import ConversationHistory
from asksh.stream import run_stream_overlay

console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"


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
    intro_markup: str | None = None,
) -> None:
    """Render one assistant reply.

    *intro_markup* (Textual markup for the intro panel) is passed in chat
    mode: the overlay shows it as the transcript header and the user message
    is echoed on the normal screen. In one-shot mode it is None.
    """
    is_tty = sys.stdout.isatty()
    display_thinking = _should_show_thinking(think=think, show_thinking=show_thinking)

    if stream:
        if is_tty:
            if intro_markup is not None:
                # Chat mode: echo the user message in the transcript (F-67).
                console.print(Text(">>> ", style="cyan") + Text(user_input))
            outcome = run_stream_overlay(
                client,
                history,
                model,
                user_input,
                think=think,
                show_thinking=show_thinking,
                intro_markup=intro_markup,
            )
            if outcome.error is not None:
                raise outcome.error
            if outcome.aborted:
                return  # F-110: the streaming display is removed; print nothing.
            if display_thinking and outcome.thinking:
                _print_thinking(outcome.thinking)
            if outcome.content:
                if display_thinking and outcome.thinking:
                    console.print()
                console.print(Markdown(outcome.content))
        else:
            _drain(
                client.stream_message(
                    user_input,
                    model=model,
                    history=history,
                    think=think,
                )
            )
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
