"""Ollama ``/api/chat`` HTTP client."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from collections.abc import Generator
from typing import Literal, NamedTuple

import requests

from .history import ConversationHistory
from .ollama import model_supports_thinking

logger = logging.getLogger(__name__)
if not logger.handlers:
    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_stdout)
    logger.setLevel(logging.INFO)

DEFAULT_OLLAMA_MODEL: str = "qwen2.5-coder"
DEFAULT_OLLAMA_BASE_URL: str = "http://localhost:11434"

ThinkOption = bool | Literal["low", "medium", "high", "max"]
_THINK_LEVELS = frozenset({"low", "medium", "high", "max"})
_THINK_TAG = "think"
_EMBEDDED_THINKING_RE = re.compile(
    rf"<{_THINK_TAG}>(.*?)</{_THINK_TAG}>",
    re.DOTALL | re.IGNORECASE,
)


class ChatStreamChunk(NamedTuple):
    """One streamed delta from ``/api/chat``."""

    text: str
    is_thinking: bool = False


def parse_think_option(value: str | bool | None) -> ThinkOption | None:
    """Parse CLI ``think`` values into Ollama's ``think`` parameter."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    if normalized in _THINK_LEVELS:
        return normalized  # type: ignore[return-value]
    raise ValueError(
        f"invalid think value {value!r}; use true, false, low, medium, high, or max"
    )


def resolve_think_option(
    think: ThinkOption | None,
    *,
    model: str,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
) -> ThinkOption:
    """Resolve an optional ``--think`` value against model capabilities."""
    if think is False:
        return False
    supported = model_supports_thinking(model, base_url)
    if think is None:
        return True if supported else False
    if not supported:
        raise ValueError(f"model {model!r} does not support --think")
    return think


def split_embedded_thinking(text: str) -> tuple[str, str]:
    """Return ``(content, thinking)`` split from embedded `` blocks."""
    thinking_parts = [
        match.group(1).strip()
        for match in _EMBEDDED_THINKING_RE.finditer(text)
        if match.group(1).strip()
    ]
    content = _EMBEDDED_THINKING_RE.sub("", text).strip()
    thinking = "\n\n".join(thinking_parts)
    return content, thinking


def strip_embedded_thinking(text: str) -> str:
    """Remove `` blocks embedded in model content."""
    return split_embedded_thinking(text)[0]


def _history_to_ollama_messages(history: ConversationHistory) -> list[dict]:
    """Convert conversation history to the messages list Ollama /api/chat expects."""
    return [{"role": m.role, "content": m.content} for m in history.get_items()]


class OllamaChatClient:
    """Chat client that calls Ollama's /api/chat endpoint via HTTP."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._thinking_support: dict[str, bool] = {}
        self._current_response: requests.Response | None = None

    def abort_active_stream(self) -> None:
        """Force-close the HTTP response of an in-flight stream.

        Closing the connection from the UI thread unblocks the worker
        thread's blocked socket read so the stream can abort promptly.
        """
        response = self._current_response
        if response is not None:
            response.close()

    def _model_supports_thinking(self, model: str) -> bool:
        if model not in self._thinking_support:
            self._thinking_support[model] = model_supports_thinking(
                model, self._base_url
            )
        return self._thinking_support[model]

    def _apply_think(self, payload: dict, model: str, think: ThinkOption) -> None:
        if think is False:
            return
        if self._model_supports_thinking(model):
            payload["think"] = think

    def _build_messages(
        self,
        user_input: str,
        instructions: str | None,
        history: ConversationHistory | None,
    ) -> list[dict]:
        if history is not None:
            history.add_message("user", user_input)
            return _history_to_ollama_messages(history)

        messages: list[dict] = []
        if instructions:
            messages.append({"role": "system", "content": instructions})
        messages.append({"role": "user", "content": user_input})
        return messages

    def send_message(
        self,
        user_input: str,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        instructions: str | None = None,
        history: ConversationHistory | None = None,
        think: ThinkOption = False,
    ) -> tuple[str, str]:
        """Send a message (non-streaming) and return ``(content, thinking)``."""
        messages = self._build_messages(user_input, instructions, history)

        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        self._apply_think(payload, model, think)
        resp = requests.post(f"{self._base_url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        thinking = message.get("thinking") or ""
        content_raw = message.get("content", "")
        if thinking:
            content = strip_embedded_thinking(content_raw)
        else:
            content, embedded = split_embedded_thinking(content_raw)
            if embedded:
                thinking = embedded

        if history is not None:
            history.add_message("assistant", content)
        return content, thinking

    def stream_message(
        self,
        user_input: str,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        instructions: str | None = None,
        history: ConversationHistory | None = None,
        think: ThinkOption = False,
        abort: threading.Event | None = None,
    ) -> Generator[ChatStreamChunk, None, tuple[str, str]]:
        """Stream a response (NDJSON), yielding text deltas as they arrive.

        When *abort* is set (checked between lines, and combined with
        ``abort_active_stream`` to unblock a stalled read), the stream stops
        and the partial assistant reply is not added to history.
        """
        messages = self._build_messages(user_input, instructions, history)

        thinking_text = ""
        content_text = ""
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        self._apply_think(payload, model, think)

        with requests.post(
            f"{self._base_url}/api/chat",
            json=payload,
            stream=True,
            timeout=120,
        ) as resp:
            self._current_response = resp
            try:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if abort is not None and abort.is_set():
                        break
                    if not raw_line:
                        continue
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    msg = chunk.get("message", {})
                    thinking_delta = msg.get("thinking") or ""
                    content_delta = msg.get("content") or ""

                    if thinking_delta:
                        thinking_text += thinking_delta
                        yield ChatStreamChunk(thinking_delta, is_thinking=True)
                    if content_delta:
                        content_text += content_delta
                        yield ChatStreamChunk(content_delta, is_thinking=False)
            finally:
                self._current_response = None

        content, embedded = split_embedded_thinking(content_text)
        if embedded and not thinking_text:
            thinking_text = embedded
        if history is not None and not (abort is not None and abort.is_set()):
            history.add_message("assistant", content)
        return content, thinking_text
