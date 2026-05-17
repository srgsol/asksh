"""Command-line chat with Ollama using conversation history.

# interactive chat (streaming): no args, or -c
# In a TTY, chat uses a multiline editor (Enter = newline, Alt+Enter to send).
# Without a TTY, end a line with \\ then Enter to continue on the next line.
asksh

asksh -c

# single query and exit
asksh "What is 2+2?"

# pick a different model
asksh --model gemma3 -c

# custom Ollama server
asksh --base-url http://192.168.1.10:11434 "Hello"

# pipe stdin as context
cat data.json | asksh "use jq to count the items key"

# pipe + interactive chat (stdin context becomes the first message)
cat error.log | asksh -c "what went wrong?"

Equivalent: ``python -m asksh`` (same flags as the ``asksh`` console script).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from asksh.ollama import verify_ollama_status

if TYPE_CHECKING:
    from prompt_toolkit import PromptSession

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from asksh import __version__
from asksh.client import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    OllamaChatClient,
)
from asksh.config import default_config_path, load_user_config
from asksh.history import ConversationHistory
from asksh.sysprompt import (
    LINUX_ASSISTANT_SYSTEM_PROMPT,
    LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT,
    LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN,
)

_console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"

# Lazily created so one-shot ``asksh "query"`` does not import prompt_toolkit.
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
        # Define custom key bindings to force immediate send via Enter
        bindings = KeyBindings()

        @bindings.add("c-m")  # 'c-m' maps directly to the standard Enter key
        def _(event):
            """Pressing Enter submits the text immediately."""
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")  # Maps to Alt+Enter / Esc then Enter
        def _(event):
            """Pressing Alt+Enter inserts a clean explicit newline character."""
            event.current_buffer.insert_text("\n")

        # Connect text formatting colors for your toolbar tags
        style = Style.from_dict(
            {
                "grey": "#808080",  # Explicitly matches a dark grey color token
            }
        )

        _chat_prompt_session = PromptSession(
            history=FileHistory(str(_chat_history_path())),
            multiline=True,
            wrap_lines=True,
            enable_open_in_editor=False,
            key_bindings=bindings,
            style=style,
            prompt_continuation=lambda _pw, _ln, _wc: HTML("<grey>...</grey> "),
            # bottom_toolbar=lambda: HTML(
            #     "<grey>Enter to send  Alt+Enter for new line  Ctrl+C cancel </grey>"
            # ),
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


def parse_args() -> argparse.Namespace:
    arg_defaults, _ = load_user_config()
    parser = argparse.ArgumentParser(
        prog="asksh",
        description=(
            "Chat with an Ollama model. Optional defaults are read from "
            f"{default_config_path()} (set ASKSH_CONFIG to use another file)."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-c",
        "--chat",
        action="store_true",
        help="Run interactive chat loop (also the default when QUERY is omitted).",
    )
    parser.add_argument(
        "-e",
        "--explain",
        action="store_true",
        help="Explain the answer.",
    )
    parser.add_argument(
        "-f",
        "--context",
        default=None,
        help="Path to the file to use as context (default: None)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_OLLAMA_MODEL,
        help=f"Ollama model to use (default: {DEFAULT_OLLAMA_MODEL}; config file may override).",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_OLLAMA_BASE_URL,
        help=f"Ollama server base URL (default: {DEFAULT_OLLAMA_BASE_URL}; config file may override).",
    )
    parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="Prompt for one-shot mode; if omitted, interactive chat runs.",
    )
    parser.set_defaults(**arg_defaults)
    args = parser.parse_args()

    # Validate Query
    query_text = " ".join(args.query).strip()
    args.query_text = query_text
    if not query_text:
        args.chat = True

    # Validate Context File
    if args.context:
        if not os.path.isfile(args.context):
            parser.error(f"Context file {args.context} does not exist")

    return args


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
            with Live(
                Spinner("dots", style=_SPINNER_STYLE),
                console=_console,
                refresh_per_second=12,
                transient=True,
            ):
                try:
                    first_chunk = next(gen)
                except StopIteration:
                    first_chunk = None
            if first_chunk is None:
                print()
                return
            sys.stdout.write(first_chunk)
            sys.stdout.flush()
            try:
                while True:
                    chunk = next(gen)
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            except StopIteration:
                pass
            print()
        else:
            try:
                while True:
                    chunk = next(gen)
                    print(chunk, end="", flush=True)
            except StopIteration:
                pass
            print()
    else:
        if is_tty:
            with _console.status("", spinner="dots", spinner_style=_SPINNER_STYLE):
                reply, _ = client.send_message(
                    user_input,
                    model=model,
                    history=history,
                )
            _console.print(reply, markup=False)
        else:
            reply, _ = client.send_message(
                user_input,
                model=model,
                history=history,
            )
            print(reply)


def chat_loop(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    initial_query: str | None = None,
    context: str | None = None,
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
        _console.print(
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
        print_assistant_reply(
            client,
            history,
            model,
            stream,
            initial_query,
        )

    while True:
        try:
            user_input = _read_chat_user_input(stdin_tty)
        except (EOFError, KeyboardInterrupt):
            _console.print("\nGoodbye!", style="grey50")
            break

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            _console.print("Goodbye!", style="grey50")
            break

        print_assistant_reply(
            client,
            history,
            model,
            stream,
            user_input,
        )


def _read_piped_stdin() -> str | None:
    """Return piped stdin content, or None if stdin is a terminal."""
    if sys.stdin.isatty():
        return None
    content = sys.stdin.read()
    return content.strip() or None


def _build_query(
    query_text: str, piped: str | None, context_file_name: str | None
) -> str:
    """Combine context file, piped stdin, and query text."""
    parts: list[str] = []

    if piped:
        parts.append(f"<stdin>{piped}</stdin>")

    if context_file_name:
        with open(context_file_name, "r", encoding="utf-8") as f:
            context = f.read()
        parts.append(
            f"<context>\nFile: {context_file_name}\nFile content:\n{context}</context>"
        )

    if query_text:
        parts.append(query_text)

    return "\n".join(parts)


def main() -> None:
    args = parse_args()

    try:
        verify_ollama_status(required_model=args.model, base_url=args.base_url)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    piped = _read_piped_stdin()

    if args.chat:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT
        stream = True
    elif args.explain:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN
        stream = True
    else:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT
        stream = False
    history = ConversationHistory(system_prompt=system_prompt)
    client = OllamaChatClient(base_url=args.base_url)

    query = _build_query(args.query_text, piped, args.context)
    # print(query)
    try:
        if args.chat:
            if piped:
                sys.stdin.close()
                sys.stdin = open("/dev/tty")  # noqa: SIM115
            chat_loop(
                client=client,
                history=history,
                model=args.model,
                stream=stream,
                initial_query=query if query else None,
            )
        else:
            if not query:
                print("Error: provide a query or pipe input.", file=sys.stderr)
                sys.exit(1)
            print_assistant_reply(
                client,
                history,
                args.model,
                stream,
                query,
            )
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
