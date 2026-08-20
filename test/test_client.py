"""Unit tests for the Ollama chat client."""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
import requests

from asksh.client import (
    ChatStreamChunk,
    OllamaChatClient,
    parse_think_option,
    resolve_think_option,
    split_embedded_thinking,
    split_streaming_embedded_thinking,
    strip_embedded_thinking,
)
from asksh.history import ConversationHistory


def _think_block(body: str, suffix: str = "") -> str:
    tag = "think"
    return f"<{tag}>{body}</{tag}>{suffix}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("false", False),
        ("medium", "medium"),
        ("MAX", "max"),
        (False, False),
    ],
)
def test_parse_think_option(raw: str | bool, expected: bool | str) -> None:
    assert parse_think_option(raw) == expected


def test_parse_think_option_none() -> None:
    assert parse_think_option(None) is None


def test_parse_think_option_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="invalid think value"):
        parse_think_option("banana")


def test_resolve_think_option_disabled() -> None:
    assert (
        resolve_think_option(False, model="any", base_url="http://localhost:11434")
        is False
    )


def test_resolve_think_option_auto_enables_for_supported_model() -> None:
    with patch("asksh.client.model_supports_thinking", return_value=True):
        assert (
            resolve_think_option(None, model="deepseek-r1", base_url="http://x") is True
        )


def test_resolve_think_option_auto_disables_for_unsupported_model() -> None:
    with patch("asksh.client.model_supports_thinking", return_value=False):
        assert (
            resolve_think_option(None, model="qwen2.5-coder", base_url="http://x")
            is False
        )


def test_resolve_think_option_explicit_true_errors_when_unsupported() -> None:
    with (
        patch("asksh.client.model_supports_thinking", return_value=False),
        pytest.raises(ValueError, match="does not support --think"),
    ):
        resolve_think_option(True, model="qwen2.5-coder", base_url="http://x")


def test_resolve_think_option_explicit_level_passes_when_supported() -> None:
    with patch("asksh.client.model_supports_thinking", return_value=True):
        assert (
            resolve_think_option("medium", model="deepseek-r1", base_url="http://x")
            == "medium"
        )


def test_resolve_think_option_auto_disables_when_capability_probe_fails() -> None:
    with patch(
        "asksh.client.model_supports_thinking",
        side_effect=requests.exceptions.Timeout("timed out"),
    ):
        assert (
            resolve_think_option(None, model="deepseek-r1", base_url="http://x")
            is False
        )


def test_resolve_think_option_honors_explicit_think_when_capability_probe_fails() -> (
    None
):
    with patch(
        "asksh.client.model_supports_thinking",
        side_effect=requests.exceptions.HTTPError("503"),
    ):
        assert (
            resolve_think_option(True, model="deepseek-r1", base_url="http://x") is True
        )
        assert (
            resolve_think_option("medium", model="deepseek-r1", base_url="http://x")
            == "medium"
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ls -la", "ls -la"),
        (_think_block("reasoning\n", "ls -la"), "ls -la"),
        (_think_block("reasoning") + "ls -la", "ls -la"),
    ],
)
def test_strip_embedded_thinking(text: str, expected: str) -> None:
    assert strip_embedded_thinking(text) == expected


def test_split_embedded_thinking() -> None:
    content, thinking = split_embedded_thinking(
        _think_block("User wants cwd.") + "\npwd\n"
    )
    assert content == "pwd"
    assert thinking == "User wants cwd."


@pytest.mark.parametrize(
    ("text", "visible", "completed", "pending"),
    [
        ("ls -la", "ls -la", "", ""),
        ("Hello <thi", "Hello ", "", ""),
        ("Hello <think>reasoning so far", "Hello", "", "reasoning so far"),
        (_think_block("reasoning") + " world", "world", "reasoning", ""),
        (
            _think_block("first") + " mid <think>second",
            "mid",
            "first",
            "second",
        ),
    ],
)
def test_split_streaming_embedded_thinking(
    text: str, visible: str, completed: str, pending: str
) -> None:
    assert split_streaming_embedded_thinking(text) == (visible, completed, pending)


def test_send_message_returns_content_and_thinking_separately() -> None:
    client = OllamaChatClient()
    response = MagicMock()
    response.json.return_value = {
        "message": {
            "content": "ls -la",
            "thinking": "Need a directory listing.",
        }
    }
    response.raise_for_status.return_value = None

    with (
        patch("asksh.client.requests.post", return_value=response) as mock_post,
        patch.object(OllamaChatClient, "_model_supports_thinking", return_value=True),
    ):
        content, thinking = client.send_message("list files", think=True)

    assert content == "ls -la"
    assert thinking == "Need a directory listing."
    assert mock_post.call_args.kwargs["json"]["think"] is True


def test_send_message_stores_only_content_in_history() -> None:
    client = OllamaChatClient()
    history = ConversationHistory(system_prompt="test")
    response = MagicMock()
    response.json.return_value = {
        "message": {
            "content": "pwd",
            "thinking": "User wants the working directory.",
        }
    }
    response.raise_for_status.return_value = None

    with (
        patch("asksh.client.requests.post", return_value=response),
        patch.object(OllamaChatClient, "_model_supports_thinking", return_value=True),
    ):
        client.send_message("where am i", history=history, think=True)

    items = history.get_items()
    assert items[-1].role == "assistant"
    assert items[-1].content == "pwd"


def test_send_message_omits_think_when_disabled() -> None:
    client = OllamaChatClient()
    response = MagicMock()
    response.json.return_value = {"message": {"content": "ls -la"}}
    response.raise_for_status.return_value = None

    with patch("asksh.client.requests.post", return_value=response) as mock_post:
        client.send_message("list files", think=False)

    payload = mock_post.call_args.kwargs["json"]
    assert "think" not in payload


def test_stream_message_omits_think_when_disabled() -> None:
    client = OllamaChatClient()
    lines = [json.dumps({"message": {"content": "ls"}}).encode()]
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = lines

    with patch("asksh.client.requests.post", return_value=response) as mock_post:
        gen = client.stream_message("list files", think=False)
        try:
            while True:
                next(gen)
        except StopIteration:
            pass

    payload = mock_post.call_args.kwargs["json"]
    assert "think" not in payload


def test_send_message_omits_think_when_unsupported() -> None:
    client = OllamaChatClient()
    response = MagicMock()
    response.json.return_value = {"message": {"content": "ls -la"}}
    response.raise_for_status.return_value = None

    with (
        patch("asksh.client.requests.post", return_value=response) as mock_post,
        patch.object(OllamaChatClient, "_model_supports_thinking", return_value=False),
    ):
        client.send_message("list files", think=True, model="qwen2.5-coder")

    payload = mock_post.call_args.kwargs["json"]
    assert "think" not in payload


def test_stream_message_yields_thinking_and_content_chunks() -> None:
    client = OllamaChatClient()
    lines = [
        json.dumps({"message": {"thinking": "Hmm"}}).encode(),
        json.dumps({"message": {"content": "ls"}}).encode(),
    ]
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = lines

    with (
        patch("asksh.client.requests.post", return_value=response),
        patch.object(OllamaChatClient, "_model_supports_thinking", return_value=True),
    ):
        gen = client.stream_message("list files", think=True)
        chunks = []
        try:
            while True:
                chunks.append(next(gen))
        except StopIteration as stop:
            content, thinking = stop.value

    assert chunks == [
        ChatStreamChunk("Hmm", is_thinking=True),
        ChatStreamChunk("ls", is_thinking=False),
    ]
    assert content == "ls"
    assert thinking == "Hmm"


def test_stream_message_adds_assistant_to_history_and_clears_response() -> None:
    client = OllamaChatClient()
    history = ConversationHistory(system_prompt="test")
    lines = [json.dumps({"message": {"content": "ls"}}).encode()]
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = lines

    with patch("asksh.client.requests.post", return_value=response):
        gen = client.stream_message("list files", history=history)
        try:
            while True:
                next(gen)
        except StopIteration:
            pass

    assert client._current_response is None
    roles = [m.role for m in history.get_messages()]
    assert roles == ["system", "user", "assistant"]
    assert history.get_messages()[-1].content == "ls"


def test_stream_message_abort_stops_yielding_and_skips_history() -> None:
    client = OllamaChatClient()
    history = ConversationHistory(system_prompt="test")
    lines = [
        json.dumps({"message": {"content": "ls"}}).encode(),
        json.dumps({"message": {"content": " -la"}}).encode(),
    ]
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = lines
    abort = threading.Event()

    with patch("asksh.client.requests.post", return_value=response):
        gen = client.stream_message("list files", history=history, abort=abort)
        assert next(gen) == ChatStreamChunk("ls", is_thinking=False)
        abort.set()
        with pytest.raises(StopIteration) as stop:
            next(gen)

    content, thinking = stop.value.value
    assert content == "ls"
    assert thinking == ""
    # The aborted partial reply must not corrupt the conversation context.
    roles = [m.role for m in history.get_messages()]
    assert roles == ["system", "user"]


def test_abort_active_stream_closes_current_response() -> None:
    client = OllamaChatClient()
    client._current_response = response = MagicMock()

    client.abort_active_stream()

    response.close.assert_called_once()


def test_abort_active_stream_without_stream_is_noop() -> None:
    client = OllamaChatClient()
    client.abort_active_stream()
