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
from asksh.client import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    OllamaChatClient,
    parse_think_option,
    resolve_think_option,
)
from asksh.config import default_config_path, load_user_config
from asksh.history import ConversationHistory
from asksh.last_message import default_state_dir, load_last_message
from asksh.ollama import verify_ollama_status
from asksh.query import build_query, read_piped_stdin
from asksh.render import print_assistant_reply, print_saved_markdown
from asksh.sysprompt import (
    build_system_prompt,
)
from asksh.update import check_for_update


def parse_args() -> argparse.Namespace:
    arg_defaults = load_user_config()
    parser = argparse.ArgumentParser(
        prog="asksh",
        description=(
            "Chat with an Ollama model.\nConfig path: "
            f"'{default_config_path()}'.\nState files path: "
            f"'{default_state_dir()}'."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-c",
        "--chat",
        action="store_true",
        help="Run interactive chat loop (also the default when QUERY is omitted).",
    )
    mode.add_argument(
        "-e",
        "--explain",
        action="store_true",
        help="Explain the answer (one-shot; cannot be combined with -c/--chat).",
    )
    mode.add_argument(
        "-m",
        "--markdown",
        action="store_true",
        help=(
            "Re-render the last assistant reply as Markdown, without querying "
            "Ollama (cannot be combined with -c/--chat or -e/--explain or a QUERY)."
        ),
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
        "--think",
        nargs="?",
        const=True,
        default=None,
        metavar="LEVEL",
        help=(
            "Enable model reasoning for thinking models: true, false, "
            "low, medium, high, or max (default: enabled when supported)."
        ),
    )
    parser.add_argument(
        "--show-thinking",
        action="store_true",
        help="Show the model reasoning trace (on by default when --think is enabled).",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_false",
        dest="update_check",
        default=True,
        help="Disable the startup check for new asksh versions on PyPI (default: enabled).",
    )
    parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="Prompt for one-shot mode; if omitted, interactive chat runs.",
    )
    # Render style per mode (config-only, no CLI flag): text, markdown,
    # post_markdown, or live_markdown. See config.example.toml.
    parser.set_defaults(
        oneshot_render="text",
        explain_render="text",
        chat_render="text",
    )
    parser.set_defaults(**arg_defaults)
    args = parser.parse_args()

    query_text = " ".join(args.query).strip()
    args.query_text = query_text
    if args.markdown and query_text:
        parser.error("argument -m/--markdown: not allowed with a QUERY")
    if not query_text and not args.explain and not args.markdown:
        args.chat = True

    if args.context:
        if not os.path.isfile(args.context):
            parser.error(f"Context file {args.context} does not exist")

    if args.think is not None:
        try:
            args.think = parse_think_option(args.think)
        except ValueError as exc:
            parser.error(str(exc))

    return args


def warn_if_update_available(args: argparse.Namespace) -> None:
    """Print a one-line stderr notice when a newer asksh release exists."""
    if not args.update_check:
        return
    latest = check_for_update(__version__)
    if latest:
        print(
            f"Info: new asksh version available: {latest} (installed: {__version__}). "
            "Upgrade with: 'pipx upgrade asksh' or 'uv tool upgrade asksh'\n",
            file=sys.stderr,
        )


def run(args: argparse.Namespace) -> None:
    if args.markdown:
        text = load_last_message()
        if not text:
            print("Error: no previous assistant message to render.", file=sys.stderr)
            sys.exit(1)
        print_saved_markdown(text)
        return

    warn_if_update_available(args)
    verify_ollama_status(required_model=args.model, base_url=args.base_url)

    try:
        args.think = resolve_think_option(
            args.think,
            model=args.model,
            base_url=args.base_url,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    piped = read_piped_stdin()

    mode = None
    if args.chat:
        mode = "chat"
    elif args.explain:
        mode = "explain"
    else:
        mode = "oneshot"
    system_prompt = build_system_prompt(mode)
    render_style = {
        "oneshot": args.oneshot_render,
        "explain": args.explain_render,
        "chat": args.chat_render,
    }[mode]

    stream = True
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
            think=args.think,
            show_thinking=args.show_thinking,
            render_style=render_style,
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
            think=args.think,
            show_thinking=args.show_thinking,
            render_style=render_style,
        )


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
