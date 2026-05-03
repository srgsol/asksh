"""Command-line chat with Ollama using conversation history.

# interactive chat (streaming)
python app.py -c

# single query and exit
python app.py "What is 2+2?"

# pick a different model
python app.py --model gemma3 -c

# add a system prompt
python app.py --system "You are a concise assistant." -c

# non-streaming (full reply at once)
python app.py --no-stream "Explain TCP"

# custom Ollama server
python app.py --base-url http://192.168.1.10:11434 "Hello"

# pipe stdin as context
cat data.json | python app.py "use jq to count the items key"

# pipe + interactive chat (stdin context becomes the first message)
cat error.log | python app.py -c "what went wrong?"
"""

from __future__ import annotations
import os

import argparse
import json
import sys

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from asksh.sysprompt import (LINUX_ASSISTANT_SYSTEM_PROMPT, LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT, LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN)
from asksh.client import DEFAULT_MODEL, OllamaChatClient, _history_to_ollama_messages
from asksh.history import ConversationHistory


_console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with an Ollama model.")
    parser.add_argument(
        "-c",
        "--chat",
        action="store_true",
        help="Run interactive chat loop. If omitted, QUERY is required (one shot).",
    )
    parser.add_argument(
        "-e",
        "--explain",
        action="store_true",
        help="Explain the answer.",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Debug the answer.",
    )
    parser.add_argument(
        "-f",
        "--context",
        default=None,
        help="Path to the file to use as context (default: None)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:11434",
        help="Ollama server base URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--system",
        default=None,
        metavar="PROMPT",
        help="Optional system prompt.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming; print the full reply at once.",
    )
    parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="Prompt to send once and exit (required unless --chat).",
    )
    args = parser.parse_args()

    # Validate Query
    query_text = " ".join(args.query).strip()
    args.query_text = query_text

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


def _build_query(query_text: str, piped: str | None, context_file_name: str | None) -> str:
    """Combine piped stdin context with the CLI query."""
    if not piped and not context_file_name:
        return query_text
    if not query_text:
        return piped or context_file_name
    if piped:
        return f"<stdin>{piped}</stdin>{query_text}"
    if context_file_name:
        with open(context_file_name, "r") as f:
            context = f.read()
        return f"<context>File: {context_file_name}\nContext: {context}</context>{query_text}"
    return query_text


def main() -> None:
    args = parse_args()
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
    history = ConversationHistory(system_prompt=args.system or system_prompt)
    client = OllamaChatClient(base_url=args.base_url)

    # query = _build_query(args.query_text, piped, args.context)
    query = _build_query(args.query_text, piped, args.context)

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
