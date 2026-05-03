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
from enum import Enum
import os

import argparse
from dataclasses import dataclass
import json
import re
import sys

from asksh.sysprompt import (LINUX_ASSISTANT_SYSTEM_PROMPT, LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT, LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN)
from asksh.client import DEFAULT_MODEL, OllamaChatClient, _history_to_ollama_messages
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


class AgentMode(Enum):
    COMMAND = 1
    EXPLAIN = 2
    DEBUG = 3
    CHAT = 4


@dataclass
class Response:
    user_query: str
    is_ambiguous: bool
    reasoning: str
    command: str
    is_destructive: bool
    # optional_command: str
    explanation: str


def extract_json_object(response: str) -> dict | None:
    """Parse the JSON object inside a ```json ... ``` fence, or None."""
    match = re.search(r"```json\s*\r?\n(.*?)```", response, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def extract_json_response(response: str) -> Response | None:
    obj = extract_json_object(response)
    if obj is None:
        return None
    try:
        return Response(**obj)
    except TypeError:
        return None


def print_assistant_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
    interaction_mode: AgentMode,
) -> None:
    is_tty = sys.stdout.isatty()
    prefix = f"{_PURPLE}>{_RST} " if is_tty else "> "
    print(prefix, end="", flush=True)

    if interaction_mode == AgentMode.COMMAND:
        _print_command_reply(client, history, model, stream, user_input)
    elif interaction_mode == AgentMode.DEBUG:
        _print_debug_reply(client, history, model, stream, user_input)
    else:
        _print_free_text_reply(client, history, model, stream, user_input)


def _collect_assistant_text(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
) -> str:
    if stream:
        gen = client.stream_message(user_input, model=model, history=history)
        response_content = ""
        try:
            while True:
                chunk = next(gen)
                response_content += chunk
        except StopIteration:
            pass
        return response_content
    reply, _ = client.send_message(user_input, model=model, history=history)
    return reply


def _print_debug_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
) -> None:
    text = _collect_assistant_text(client, history, model, stream, user_input)
    obj = extract_json_object(text)
    if obj is None:
        print(
            f"Error: failed to extract JSON from model output:\n{text}",
            file=sys.stderr,
        )
        return
    print(json.dumps(obj, indent=2))


def _print_free_text_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
) -> None:
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
        print(reply)


def _print_command_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
) -> None:
    text = _collect_assistant_text(client, history, model, stream, user_input)
    parsed = extract_json_response(text)
    if parsed is None:
        print(
            f"Error: failed to extract JSON command from model output:\n{text}",
            file=sys.stderr,
        )
        return
    # command = parsed.command or parsed.optional_command or parsed.explanation
    command = parsed.command or parsed.explanation
    print(command)


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
        print(
            f"{_GRAY}Chatting with model {_BLUE}{model}{_GRAY}."
            f" Type {_DIM}exit{_RST}{_GRAY} or {_DIM}Ctrl-C{_RST}{_GRAY}"
            f" to quit.{_RST}\n"
        )
    else:
        print(f"Chatting with model '{model}'. Type 'exit' or Ctrl-C to quit.\n")

    if initial_query:
        print_assistant_reply(
            client, history, model, stream, initial_query, interaction_mode=AgentMode.CHAT
        )

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

        print_assistant_reply(
            client, history, model, stream, user_input, interaction_mode=AgentMode.CHAT
        )


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
    return f"<stdin>{piped}</stdin>{query_text}"
    # return f'{{"context": "{piped}", "query": "{query_text}"}}'


def main() -> None:
    args = parse_args()
    piped = _read_piped_stdin()

    if args.chat:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT
    elif args.explain:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN
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
            if args.explain:
                one_shot_mode: AgentMode = AgentMode.EXPLAIN
            elif args.debug:
                one_shot_mode = AgentMode.DEBUG
            else:
                one_shot_mode = AgentMode.COMMAND
            print_assistant_reply(
                client,
                history,
                args.model,
                stream,
                query,
                interaction_mode=one_shot_mode,
            )
        # Write the history to a file
        history_path = os.path.expanduser("~/projects/s3/asksh/history.json")
        with open(history_path, "w") as f:
            history_dict = _history_to_ollama_messages(history)
            json.dump(history_dict, f, indent=2)
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
