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
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from asksh.client import OllamaChatClient, ThinkOption
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


def _intro_markup(model: str, stdin_is_tty: bool) -> str:
    """Intro panel body as Textual markup (the stream overlay re-prints it)."""
    lines = [
        f"Chatting with model [bright_cyan]{escape(model)}[/bright_cyan]",
        "- Multiline input: Alt+Enter.",
        "- Type 'exit' or Ctrl-C to quit.",
    ]
    if not stdin_is_tty:
        lines.append(
            "Without a TTY, use \\ at the end of a line, then Enter, "
            "to continue on the next line."
        )
    return "\n".join(lines)


def chat_loop(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    initial_query: str | None = None,
    *,
    think: ThinkOption = False,
    show_thinking: bool = False,
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
        intro_panel = Panel(
            intro,
            border_style="grey42",
            padding=(0, 1),
            expand=True,
        )
        console.print(intro_panel)
        intro_markup = _intro_markup(model, stdin_tty)
    else:
        msg = (
            f"Chatting with model '{model}'. "
            "Type 'exit' or Ctrl-C to quit.\n"
            "End a line with \\ then Enter to add more lines.\n"
        )
        print(msg)
        intro_markup = None

    if initial_query:
        print_assistant_reply(
            client,
            history,
            model,
            stream,
            initial_query,
            think=think,
            show_thinking=show_thinking,
            intro_markup=intro_markup,
        )

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

        print_assistant_reply(
            client,
            history,
            model,
            stream,
            user_input,
            think=think,
            show_thinking=show_thinking,
            intro_markup=intro_markup,
        )
