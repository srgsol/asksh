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

import argparse
from dataclasses import dataclass
import sys

from asksh.sysprompt import (LINUX_ASSISTANT_SYSTEM_PROMPT, LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT, LINUX_ASSISTANT_SYSTEM_PROMPT_DEBUG, LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN)
from asksh.client import DEFAULT_MODEL, OllamaChatClient
from asksh.history import ConversationHistory


_RST = "\033[0m"    # reset
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GRAY = "\033[38;5;243m"
_BLUE = "\033[38;5;75m"
_PURPLE = "\033[38;5;141m"


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
        help="Optional system prompt to set conversation context.",
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
    query_text = " ".join(args.query).strip()
    args.query_text = query_text
    return args

@dataclass
class Response:
    user_query: str
    is_ambiguous: bool
    reasoning: str
    command: str
    is_destructive: bool
    optional_command: str
    explanation: str


def print_assistant_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
) -> None:
    is_tty = sys.stdout.isatty()
    prefix = f"{_PURPLE}>{_RST} " if is_tty else "> "
    print(prefix, end="", flush=True)

    if stream:
        gen = client.stream_message(user_input, model=model, history=history)
        try:
            while True:
                chunk = next(gen)
                print(chunk, end="", flush=True)
        except StopIteration:
            pass
        print()
    else:
        reply, _ = client.send_message(user_input, model=model, history=history)
        response = Response(**json.loads(reply))
        print(response.command)


def chat_loop(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    initial_query: str | None = None,
) -> None:
    is_tty = sys.stdout.isatty()
    if is_tty:
        print(
            f"{_GRAY}Chatting with model {_BLUE}{model}{_GRAY}."
            f" Type {_DIM}exit{_RST}{_GRAY} or {_DIM}Ctrl-C{_RST}{_GRAY}"
            f" to quit.{_RST}\n"
        )
    else:
        print(f"Chatting with model '{model}'. Type 'exit' or Ctrl-C to quit.\n")

    if initial_query:
        print_assistant_reply(client, history, model, stream, initial_query)

    while True:
        try:
            if is_tty:
                sys.stdout.write(f"{_BOLD}{_BLUE}You:{_RST} ")
                sys.stdout.flush()
                user_input = input().strip()
            else:
                user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            goodbye = f"\n{_GRAY}Goodbye!{_RST}" if is_tty else "\nGoodbye!"
            print(goodbye)
            break

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            goodbye = f"{_GRAY}Goodbye!{_RST}" if is_tty else "Goodbye!"
            print(goodbye)
            break

        print_assistant_reply(client, history, model, stream, user_input)


def _read_piped_stdin() -> str | None:
    """Return piped stdin content, or None if stdin is a terminal."""
    if sys.stdin.isatty():
        return None
    content = sys.stdin.read()
    return content.strip() or None


def _build_query(query_text: str, piped: str | None) -> str:
    """Combine piped stdin context with the CLI query."""
    if not piped:
        return query_text
    if not query_text:
        return piped
    return f"<stdin>{piped}</stdin>Use <stdin> as context to generate the command.{query_text}"
    # return f'{{"context": "{piped}", "query": "{query_text}"}}'


def main() -> None:
    args = parse_args()
    piped = _read_piped_stdin()

    if args.chat:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT
    elif args.explain:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN
    elif args.debug:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_DEBUG
    else:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT
    history = ConversationHistory(system_prompt=args.system or system_prompt)
    client = OllamaChatClient(base_url=args.base_url)
    stream = not args.no_stream

    query = _build_query(args.query_text, piped)

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
