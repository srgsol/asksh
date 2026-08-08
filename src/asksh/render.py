"""Rich console output and streaming/non-streaming reply rendering."""

from __future__ import annotations

import sys
from collections.abc import Generator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from asksh.client import OllamaChatClient
from asksh.history import ConversationHistory

console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"


def _drain(gen: Generator[str, None, object], file: object = None) -> None:
    """Write all remaining chunks from a streaming generator to *file* (default stdout)."""
    out = file or sys.stdout
    try:
        while True:
            chunk = next(gen)
            out.write(chunk)
            out.flush()
    except StopIteration:
        pass


def print_assistant_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
) -> None:
    is_tty = sys.stdout.isatty()

    if stream:
        gen = client.stream_message(
            user_input,
            model=model,
            history=history,
        )
        if is_tty:
            text = ""
            with Live(
                Markdown(""),
                console=console,
                refresh_per_second=12,
                vertical_overflow="visible",
            ) as live:
                try:
                    while True:
                        chunk = next(gen)
                        text += chunk
                        live.update(Markdown(text))
                except StopIteration:
                    pass
            print()
        else:
            _drain(gen)
            print()
    else:
        if is_tty:
            with console.status("", spinner="dots", spinner_style=_SPINNER_STYLE):
                reply, _ = client.send_message(
                    user_input,
                    model=model,
                    history=history,
                )
            console.print(Markdown(reply))
        else:
            reply, _ = client.send_message(
                user_input,
                model=model,
                history=history,
            )
            print(reply)
