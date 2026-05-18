"""Interactive chat loop and user input (TTY and non-TTY)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.panel import Panel
from rich.text import Text

from asksh.client import OllamaChatClient
from asksh.history import ConversationHistory
from asksh.render import console, print_assistant_reply

_chat_prompt_session: PromptSession | None = None


def _chat_history_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    path = Path(base) / "asksh" / "chat_history"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_chat_user_input_prompt_toolkit() -> str:
    """TTY chat input: true multiline buffer, correct cursor/backspace, history."""
    global _chat_prompt_session

    if _chat_prompt_session is None:
        bindings = KeyBindings()

        @bindings.add("c-m")
        def _(event):
            """Pressing Enter submits the text immediately."""
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def _(event):
            """Pressing Alt+Enter inserts a clean explicit newline character."""
            event.current_buffer.insert_text("\n")

        style = Style.from_dict({"grey": "#808080"})

        _chat_prompt_session = PromptSession(
            history=FileHistory(str(_chat_history_path())),
            multiline=True,
            wrap_lines=True,
            enable_open_in_editor=False,
            key_bindings=bindings,
            style=style,
            prompt_continuation=lambda _pw, _ln, _wc: HTML("<grey>...</grey> "),
        )

    session = _chat_prompt_session
    assert session is not None
    text = session.prompt(HTML("\n<ansicyan>>>> </ansicyan>"))
    return text.strip()


def _read_chat_user_input_line_based() -> str:
    """Read one user message without prompt_toolkit (e.g. non-TTY stdin).

    A line ending with an odd number of ``\\`` (after stripping trailing
    spaces/tabs) continues on the next physical line. Pairs of trailing
    backslashes become one literal ``\\`` in the stored line; a lone trailing
    backslash is dropped and joins the next line (shell-like).

    Prompts must be passed to ``input()`` as plain text so line editing stays
    in sync with the terminal (no Rich/ANSI before ``input()``).
    """
    chunks: list[str] = []
    first = True
    while True:
        line = input("\n>>> " if first else "\n... ")

        tail = line.rstrip(" \t")
        n_backslashes = 0
        for i in range(len(tail) - 1, -1, -1):
            if tail[i] == "\\":
                n_backslashes += 1
            else:
                break

        if n_backslashes % 2 == 1:
            chunks.append(tail[:-1])
            first = False
            continue

        chunks.append(line)
        break

    return "\n".join(chunks).strip()


def _read_chat_user_input(stdin_is_tty: bool) -> str:
    if stdin_is_tty:
        return _read_chat_user_input_prompt_toolkit()
    return _read_chat_user_input_line_based()


def chat_loop(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    initial_query: str | None = None,
) -> None:
    stdout_tty = sys.stdout.isatty()
    stdin_tty = sys.stdin.isatty()
    if stdout_tty:
        intro = Text()
        intro.append("Chatting with model ", style="grey50")
        intro.append(model, style="bright_cyan")
        intro.append("\n- Multiline input: Alt+Enter.", style="grey50")
        intro.append("\n- Type 'exit' or Ctrl-C to quit.", style="grey50")
        if not stdin_tty:
            intro.append(
                "\nWithout a TTY, use \\ at the end of a line, then Enter, "
                "to continue on the next line.",
                style="grey50",
            )
        console.print(
            Panel(
                intro,
                border_style="grey42",
                padding=(0, 1),
                expand=True,
            )
        )
    else:
        msg = (
            f"Chatting with model '{model}'. "
            "Type 'exit' or Ctrl-C to quit.\n"
            "End a line with \\ then Enter to add more lines.\n"
        )
        print(msg)

    if initial_query:
        print_assistant_reply(client, history, model, stream, initial_query)

    while True:
        try:
            user_input = _read_chat_user_input(stdin_tty)
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye!", style="grey50")
            break

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            console.print("Goodbye!", style="grey50")
            break

        print_assistant_reply(client, history, model, stream, user_input)
