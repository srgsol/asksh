"""Ollama ``/api/chat`` HTTP client."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Generator

import requests

from .history import ConversationHistory

logger = logging.getLogger(__name__)
if not logger.handlers:
    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_stdout)
    logger.setLevel(logging.INFO)

DEFAULT_OLLAMA_MODEL: str = "qwen2.5-coder"
DEFAULT_OLLAMA_BASE_URL: str = "http://localhost:11434"


def is_ollama_server_running(base_url: str) -> bool:
    """Return True if the Ollama server is running."""
    try:
        resp = requests.get(base_url, timeout=3)
    except (OSError, requests.RequestException):
        return False
    return resp.ok


def is_ollama_model_available(base_url: str, model: str) -> bool:
    """Return True if the Ollama model is available."""
    try:
        payload = {"model": model, "prompt": "Hi!", "stream": False}
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=3)
        return resp.ok
    except (OSError, requests.RequestException):
        return False


def _history_to_ollama_messages(history: ConversationHistory) -> list[dict]:
    """Convert conversation history to the messages list Ollama /api/chat expects."""
    return [{"role": m.role, "content": m.content} for m in history.get_items()]


class OllamaChatClient:
    """Chat client that calls Ollama's /api/chat endpoint via HTTP."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

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
    ) -> tuple[str, str]:
        """Send a message (non-streaming) and return ``(assistant_text, "")``."""
        messages = self._build_messages(user_input, instructions, history)

        payload: dict = {"model": model, "messages": messages, "stream": False}
        resp = requests.post(f"{self._base_url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        assistant_text = message.get("content", "")

        if history is not None:
            history.add_message("assistant", assistant_text)
        return assistant_text, ""

    def stream_message(
        self,
        user_input: str,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        instructions: str | None = None,
        history: ConversationHistory | None = None,
    ) -> Generator[str, None, tuple[str, str]]:
        """Stream a response (NDJSON), yielding text deltas as they arrive."""
        messages = self._build_messages(user_input, instructions, history)

        full_text = ""
        payload: dict = {"model": model, "messages": messages, "stream": True}

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

        if history is not None:
            history.add_message("assistant", full_text)
        return full_text, ""
