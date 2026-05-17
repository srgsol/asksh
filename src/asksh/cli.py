"""Command-line chat with Ollama using conversation history.

# interactive chat (streaming): no args, or -c
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
    is_ollama_model_available,
    is_ollama_server_running,
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
    is_tty = sys.stdout.isatty()
    if is_tty:
        intro = Text()
        intro.append("Chatting with model ", style="grey50")
        intro.append(model, style="bright_cyan")
        intro.append(". Type exit or Ctrl-C to quit.", style="grey50")
        _console.print(
            Panel(
                intro,
                border_style="grey42",
                padding=(0, 1),
                expand=False,
            )
        )
    else:
        print(f"Chatting with model '{model}'. Type 'exit' or Ctrl-C to quit.\n")

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
            if is_tty:
                _console.print(Text("\n>>> ", style="bright_cyan"), end="")
                user_input = input().strip()
            else:
                user_input = input("\n>>> ").strip()
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
    if not is_ollama_server_running(args.base_url):
        base = args.base_url.rstrip("/")
        lines = [
            f"\nError: cannot reach Ollama at {base}.",
            "",
            "Ollama is required to run this the AI model used by asksh.",
            "",
            "Ollama installation:",
            "",
            "\t- Install Ollama: https://ollama.com/download",
            "\t- Pull the Ollama model: `ollama pull qwen2.5-coder`",
            "\t- Check Ollama is running fine: `ollama run qwen2.5-coder`",
            "",
            "Ollama configuration:",
            "",
            "\t- Set your Ollama server URL in your ~/.config/asksh/config.toml file:",
            '\t  base_url = "http://HOST:PORT"',
            "",
            '\t  Where HOST is the host of your Ollama server and PORT is the port it is listening on. Example: base_url = "http://localhost:11434"',
        ]
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)
    if not is_ollama_model_available(base_url=args.base_url, model=args.model):
        lines = [
            f"\nError: Missing Ollama model {args.model}.",
            "",
            "Ollama model installation:",
            "",
            "\t- Pull the Ollama model: `ollama pull qwen2.5-coder`",
            "",
        ]
        print("\n".join(lines), file=sys.stderr)
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
