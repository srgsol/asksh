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

from asksh import __version__
from asksh.client import DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL, OllamaChatClient
from asksh.config import default_config_path, load_user_config
from asksh.history import ConversationHistory
from asksh.ollama import verify_ollama_status
from asksh.query import build_query, read_piped_stdin
from asksh.render import print_assistant_reply
from asksh.sysprompt import (
    LINUX_ASSISTANT_SYSTEM_PROMPT,
    LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT,
    LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN,
)


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

    query_text = " ".join(args.query).strip()
    args.query_text = query_text
    if not query_text:
        args.chat = True

    if args.context:
        if not os.path.isfile(args.context):
            parser.error(f"Context file {args.context} does not exist")

    return args


def run(args: argparse.Namespace) -> None:
    verify_ollama_status(required_model=args.model, base_url=args.base_url)

    piped = read_piped_stdin()

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
    query = build_query(args.query_text, piped, args.context)

    if args.chat:
        if piped:
            sys.stdin.close()
            sys.stdin = open("/dev/tty")  # noqa: SIM115
        from asksh.chat import (
            chat_loop,  # lazy: keeps prompt_toolkit out of one-shot path
        )

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
        print_assistant_reply(client, history, args.model, stream, query)


def main() -> None:
    args = parse_args()
    try:
        run(args)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
