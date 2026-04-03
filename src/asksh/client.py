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

# Models available for selection in the UI.
AVAILABLE_MODELS: list[str] = [
    "llama3.1:8b-instruct-q8_0",
]

DEFAULT_MODEL: str = "llama3.1:8b-instruct-q8_0"

MAX_TOOL_ROUNDS = 5


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _history_to_api_input(history: ConversationHistory) -> list[dict]:
    """Convert conversation history to the list of input items the OpenAI Responses API expects."""

    out: list[dict] = []
    for item in history.get_items():
        if isinstance(item, Message):
            out.append({"role": item.role, "content": item.content})
        elif isinstance(item, ToolCallItem):
            out.append(
                {
                    "type": "function_call",
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments,
                }
            )
        elif isinstance(item, ToolOutputItem):
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": item.output,
                }
            )
    return out


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


def _get_function_calls(response) -> list[tuple[str, str, str]]:
    """Extract (call_id, name, arguments) from response.output for function_call items."""
    out: list[tuple[str, str, str]] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) == "function_call":
            out.append(
                (
                    getattr(item, "call_id", ""),
                    getattr(item, "name", ""),
                    getattr(item, "arguments", "{}"),
                )
            )
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


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# OpenAI implementation
# ---------------------------------------------------------------------------


class OpenAIChatClient(BaseChatClient):
    """Thin wrapper around the OpenAI Response API.

    Handles streaming responses and multi-turn state via optional
    ``ConversationHistory``. When history is provided, every request is built
    from the full history (including tool calls and outputs). Supports
    optional tools and tool_handlers for function calling.
    """

    def __init__(self, api_key: str | None = None) -> None:
        from openai import OpenAI  # imported lazily so the package is optional

        self._client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

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
        """Send a message and return the full assistant reply and response id.

        If history is provided, the full conversation (including tool calls and
        outputs) is sent as input; no previous_response_id is used. If tools
        are provided and the model returns function calls, handlers are run,
        tool calls and outputs are appended to history, and the API is called
        again with the full history until the model returns only text (up to
        MAX_TOOL_ROUNDS).

        Returns:
            A ``(assistant_text, response_id)`` tuple.
        """
        if history is not None:
            history.add_message("user", user_input)
            api_input = _history_to_api_input(history)
            kwargs = {"model": model, "input": api_input}
            if instructions:
                kwargs["instructions"] = instructions
            if tools:
                kwargs["tools"] = tools
        else:
            kwargs = {"model": model, "input": user_input}
            if instructions:
                kwargs["instructions"] = instructions
            if tools:
                kwargs["tools"] = tools

        response = self._client.responses.create(**kwargs)
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            function_calls = _get_function_calls(response)
            if not function_calls:
                break
            tool_outputs = _run_tool_handlers(tool_handlers or {}, function_calls)
            if history is not None:
                history.add_tool_calls(function_calls)
                history.add_tool_outputs(tool_outputs)
                kwargs = {
                    "model": model,
                    "input": _history_to_api_input(history),
                    "tools": tools,
                }
                if instructions:
                    kwargs["instructions"] = instructions
            else:
                kwargs = {
                    "model": model,
                    "input": tool_outputs,
                    "previous_response_id": response.id,
                    "tools": tools,
                }
                if instructions:
                    kwargs["instructions"] = instructions
            response = self._client.responses.create(**kwargs)
            rounds += 1

        if history is not None:
            history.add_message("assistant", response.output_text)
        return response.output_text, response.id

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
        """Stream a response, yielding text deltas as they arrive.

        If history is provided, the full conversation is sent as input. If
        tools are provided and the model returns function calls, handlers are
        run, tool calls and outputs are appended to history, and a follow-up
        request is streamed until the model returns only text (up to
        MAX_TOOL_ROUNDS).

        The generator's return value (accessible via ``StopIteration.value``)
        is a ``(full_text, response_id)`` tuple that callers can capture
        after exhausting the generator.

        Yields:
            Incremental text chunks from the assistant.
        """
        full_text = ""
        response_id = ""
        if history is not None:
            history.add_message("user", user_input)
            api_input = _history_to_api_input(history)
            kwargs = {"model": model, "input": api_input, "stream": True}
            if instructions:
                kwargs["instructions"] = instructions
            if tools:
                kwargs["tools"] = tools
        else:
            kwargs = {
                "model": model,
                "input": user_input,
                "stream": True,
            }
            if instructions:
                kwargs["instructions"] = instructions
            if tools:
                kwargs["tools"] = tools

        response_for_output = None
        rounds = 0

        while rounds < MAX_TOOL_ROUNDS:
            stream = self._client.responses.create(**kwargs)
            full_text = ""
            response_for_output = None

            for event in stream:
                if event.type == "response.output_text.delta":
                    full_text += event.delta
                    yield event.delta
                elif event.type == "response.completed":
                    response_for_output = event.response
                    response_id = getattr(response_for_output, "id", "") or ""

            if response_for_output is None:
                break

            function_calls = _get_function_calls(response_for_output)
            if not function_calls:
                break

            tool_outputs = _run_tool_handlers(tool_handlers or {}, function_calls)
            if history is not None:
                history.add_tool_calls(function_calls)
                history.add_tool_outputs(tool_outputs)
                kwargs = {
                    "model": model,
                    "input": _history_to_api_input(history),
                    "stream": True,
                    "tools": tools,
                }
                if instructions:
                    kwargs["instructions"] = instructions
            else:
                kwargs = {
                    "model": model,
                    "input": tool_outputs,
                    "previous_response_id": response_for_output.id,
                    "stream": True,
                    "tools": tools,
                }
                if instructions:
                    kwargs["instructions"] = instructions
            rounds += 1

        if history is not None:
            history.add_message("assistant", full_text)
        return full_text, response_id


# Backward-compatible alias.
ChatClient = OpenAIChatClient


# ---------------------------------------------------------------------------
# Ollama implementation
# ---------------------------------------------------------------------------


class OllamaChatClient(BaseChatClient):
    """Chat client that calls Ollama's /api/chat endpoint via HTTP.

    Supports both streaming (NDJSON) and non-streaming modes, optional
    ``ConversationHistory``, and the same tool-calling loop as
    ``OpenAIChatClient``.
    """

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
