"""Chat client implementations: OpenAI Response API and Ollama /api/chat."""

from __future__ import annotations

import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Generator
from uuid import uuid4

import requests

from .sysprompt import LINUX_ASSISTANT_SYSTEM_PROMPT

from .history import ConversationHistory, Message, ToolCallItem, ToolOutputItem

logger = logging.getLogger(__name__)
if not logger.handlers:
    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_stdout)
    logger.setLevel(logging.INFO)

DEFAULT_MODEL: str = "qwen2.5-coder"

MAX_TOOL_ROUNDS = 5


def _history_to_ollama_messages(history: ConversationHistory) -> list[dict]:
    """Convert conversation history to the messages list Ollama /api/chat expects.

    Ollama follows the standard Chat Completions format:
    - Message -> {"role": ..., "content": ...}
    - ToolCallItem -> merged into the previous assistant message's "tool_calls" list
    - ToolOutputItem -> {"role": "tool", "content": ...}
    """
    out: list[dict] = []
    for item in history.get_items():
        if isinstance(item, Message):
            out.append({"role": item.role, "content": item.content})
        elif isinstance(item, ToolCallItem):
            try:
                arguments = json.loads(item.arguments) if item.arguments else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_call = {
                "function": {
                    "name": item.name,
                    "arguments": arguments,
                }
            }
            # Attach to the last assistant message, creating one if needed.
            if out and out[-1].get("role") == "assistant":
                out[-1].setdefault("tool_calls", []).append(tool_call)
            else:
                out.append({"role": "assistant", "content": "", "tool_calls": [tool_call]})
        elif isinstance(item, ToolOutputItem):
            out.append({"role": "tool", "content": item.output})
    return out


def _get_ollama_tool_calls(message: dict) -> list[tuple[str, str, str]]:
    """Extract (call_id, name, arguments_json) from an Ollama message dict."""
    out: list[tuple[str, str, str]] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        arguments = fn.get("arguments", {})
        arguments_str = json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
        call_id = f"ollama-{uuid4().hex[:8]}"
        out.append((call_id, name, arguments_str))
    return out


def _run_tool_handlers(
    tool_handlers: dict,
    function_calls: list[tuple[str, str, str]],
) -> list[dict]:
    """Run handlers for each function call and return list of function_call_output items."""
    results: list[dict] = []
    for call_id, name, arguments_str in function_calls:
        args = {}
        if arguments_str:
            try:
                parsed = json.loads(arguments_str)
                args = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
        logger.info("tool_call name=%s call_id=%s arguments=%s", name, call_id, args)

        handler = tool_handlers.get(name) if tool_handlers else None
        if not handler:
            output = f"Error: no handler for tool {name!r}"
        else:
            try:
                result = handler(**args)
                output = result if isinstance(result, str) else str(result)
            except Exception as e:
                output = f"Error: {e!s}"
        logger.info("tool_result name=%s call_id=%s output=%s", name, call_id, output)

        results.append(
            {"type": "function_call_output", "call_id": call_id, "output": output}
        )
    return results


class BaseChatClient(ABC):
    """Abstract base class shared by all chat backend implementations."""

    @abstractmethod
    def send_message(
        self,
        user_input: str,
        *,
        model: str = DEFAULT_MODEL,
        instructions: str | None = None,
        history: ConversationHistory | None = None,
        tools: list | None = None,
        tool_handlers: dict | None = None,
    ) -> tuple[str, str]:
        """Send a message and return ``(assistant_text, response_id)``."""

    @abstractmethod
    def stream_message(
        self,
        user_input: str,
        *,
        model: str = DEFAULT_MODEL,
        instructions: str | None = None,
        history: ConversationHistory | None = None,
        tools: list | None = None,
        tool_handlers: dict | None = None,
    ) -> Generator[str, None, tuple[str, str]]:
        """Stream a response, yielding text deltas.

        The generator's return value (``StopIteration.value``) is a
        ``(full_text, response_id)`` tuple.
        """


class OllamaChatClient(BaseChatClient):
    """Chat client that calls Ollama's /api/chat endpoint via HTTP.

    Supports both streaming (NDJSON) and non-streaming modes, optional
    ``ConversationHistory``, and the same tool-calling loop as
    ``OpenAIChatClient``.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def _build_messages(
        self,
        user_input: str,
        instructions: str | None,
        history: ConversationHistory | None,
    ) -> list[dict]:
        """Build the messages list for a new turn."""
        if history is not None:
            history.add_message("user", user_input)
            return _history_to_ollama_messages(history)

        messages: list[dict] = []
        if instructions:
            messages.append({"role": "system", "content": instructions})
        messages.append({"role": "user", "content": user_input})
        return messages

    def _rebuild_messages(
        self,
        instructions: str | None,
        history: ConversationHistory | None,
        messages: list[dict],
    ) -> list[dict]:
        """Rebuild messages after a tool round."""
        if history is not None:
            return _history_to_ollama_messages(history)
        return messages

    def send_message(
        self,
        user_input: str,
        *,
        model: str = DEFAULT_MODEL,
        instructions: str | None = None,
        history: ConversationHistory | None = None,
        tools: list | None = None,
        tool_handlers: dict | None = None,
    ) -> tuple[str, str]:
        """Send a message (non-streaming) and return ``(assistant_text, "")``.

        Loops for tool calls up to MAX_TOOL_ROUNDS exactly like
        ``OpenAIChatClient.send_message``.
        """
        messages = self._build_messages(user_input, instructions, history)

        with open("messages.json", "w") as f:
            json.dump(messages, f, indent=2)
            print("messages written to messages.json")
   

        rounds = 0
        assistant_text = ""
        while rounds <= MAX_TOOL_ROUNDS:
            payload: dict = {"model": model, "messages": messages, "stream": False}
            if tools:
                payload["tools"] = tools

            resp = requests.post(f"{self._base_url}/api/chat", json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            message = data.get("message", {})
            assistant_text = message.get("content", "")

            function_calls = _get_ollama_tool_calls(message)
            if not function_calls:
                break

            tool_outputs = _run_tool_handlers(tool_handlers or {}, function_calls)
            if history is not None:
                history.add_tool_calls(function_calls)
                history.add_tool_outputs(tool_outputs)
                messages = _history_to_ollama_messages(history)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": n, "arguments": json.loads(a)}}
                    for _, n, a in function_calls
                ]})
                for out in tool_outputs:
                    messages.append({"role": "tool", "content": out["output"]})

            rounds += 1

        if history is not None:
            history.add_message("assistant", assistant_text)
        return assistant_text, ""

    def stream_message(
        self,
        user_input: str,
        *,
        model: str = DEFAULT_MODEL,
        instructions: str | None = None,
        history: ConversationHistory | None = None,
        tools: list | None = None,
        tool_handlers: dict | None = None,
    ) -> Generator[str, None, tuple[str, str]]:
        """Stream a response (NDJSON), yielding text deltas as they arrive.

        Ollama streams one JSON object per line. Each intermediate chunk has
        ``"done": false`` and carries a ``message.content`` delta. The final
        chunk has ``"done": true`` and may carry tool_calls.

        The generator's return value (``StopIteration.value``) is a
        ``(full_text, "")`` tuple.

        Yields:
            Incremental text chunks from the assistant.
        """
        messages = self._build_messages(user_input, instructions, history)
        
        messages_path = os.path.expanduser("~/projects/s3/asksh/messages.json")
        with open(messages_path, "w") as f:
            json.dump(messages, f, indent=2)
            # print(f"messages written to {messages_path}")

       
        full_text = ""
        rounds = 0

        while rounds <= MAX_TOOL_ROUNDS:
            payload: dict = {"model": model, "messages": messages, "stream": True}
            if tools:
                payload["tools"] = tools

            full_text = ""
            final_message: dict = {}

            with requests.post(
                f"{self._base_url}/api/chat",
                json=payload,
                stream=True,
                timeout=120,
            ) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("message", {}).get("content", "")
                    if delta:
                        full_text += delta
                        yield delta

                    if chunk.get("done"):
                        final_message = chunk.get("message", {})

            function_calls = _get_ollama_tool_calls(final_message)
            if not function_calls:
                break

            tool_outputs = _run_tool_handlers(tool_handlers or {}, function_calls)
            if history is not None:
                history.add_tool_calls(function_calls)
                history.add_tool_outputs(tool_outputs)
                messages = _history_to_ollama_messages(history)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": n, "arguments": json.loads(a)}}
                    for _, n, a in function_calls
                ]})
                for out in tool_outputs:
                    messages.append({"role": "tool", "content": out["output"]})

            rounds += 1

        if history is not None:
            history.add_message("assistant", full_text)
        return full_text, ""
