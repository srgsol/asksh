"""Unit tests for Ollama server helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from asksh import ollama


def test_fetch_model_capabilities() -> None:
    response = MagicMock()
    response.json.return_value = {"capabilities": ["completion", "thinking", "tools"]}
    response.raise_for_status.return_value = None

    with patch("asksh.ollama.requests.post", return_value=response) as mock_post:
        caps = ollama.fetch_model_capabilities("deepseek-r1")

    assert caps == ["completion", "thinking", "tools"]
    mock_post.assert_called_once_with(
        "http://localhost:11434/api/show",
        json={"model": "deepseek-r1"},
        timeout=4.0,
    )


def test_model_supports_thinking() -> None:
    with patch(
        "asksh.ollama.fetch_model_capabilities",
        return_value=["completion", "tools"],
    ):
        assert ollama.model_supports_thinking("qwen2.5-coder") is False

    with patch(
        "asksh.ollama.fetch_model_capabilities",
        return_value=["completion", "thinking"],
    ):
        assert ollama.model_supports_thinking("deepseek-r1") is True


def test_model_supports_thinking_returns_false_when_probe_fails() -> None:
    with patch(
        "asksh.ollama.fetch_model_capabilities",
        side_effect=requests.exceptions.ConnectionError("offline"),
    ):
        assert ollama.model_supports_thinking("deepseek-r1") is False
