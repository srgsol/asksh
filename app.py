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
"""

from __future__ import annotations

import argparse
import sys

from src.asksh.sysprompt import LINUX_ASSISTANT_SYSTEM_PROMPT, LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT, LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN
from src.asksh.client import DEFAULT_MODEL, OllamaChatClient
from src.asksh.history import ConversationHistory


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
    # if args.chat:
    #     if args.query:
    #         parser.error("do not pass QUERY when using --chat")
    # elif not query_text:
    #     parser.error("QUERY is required unless --chat is used")
    args.query_text = query_text
    return args


def print_assistant_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
) -> None:
    print("> ", end="", flush=True)
    if stream:
        gen = client.stream_message(user_input, model=model, history=history)
        try:
            while True:
                chunk = next(gen)
                print(chunk, end="", flush=True)
        except StopIteration:
            pass
    else:
        reply, _ = client.send_message(user_input, model=model, history=history)
        print(reply, end="")
    print()


def chat_loop(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    initial_query: str | None = None,
) -> None:
    print(f"Chatting with model '{model}'. Type 'exit' or Ctrl-C to quit.\n")

    if initial_query:
        print_assistant_reply(client, history, model, stream, initial_query)

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        print_assistant_reply(client, history, model, stream, user_input)


def main() -> None:
    args = parse_args()
    if args.chat:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_CHAT
    elif args.explain:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT_EXPLAIN
    else:
        system_prompt = LINUX_ASSISTANT_SYSTEM_PROMPT
    history = ConversationHistory(system_prompt=args.system or system_prompt)
    client = OllamaChatClient(base_url=args.base_url)
    stream = not args.no_stream

    try:
        if args.chat:
            chat_loop(
                client=client,
                history=history,
                model=args.model,
                stream=stream,
                initial_query=args.query_text if args.query_text else None
            )
        else:
            print_assistant_reply(
                client,
                history,
                args.model,
                stream,
                args.query_text,
            )
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
