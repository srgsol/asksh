"""Conversation history management for the chatbot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass
class Message:
    """A single message in a conversation."""

    role: str  # "user", "assistant", or "system"
    content: str


@dataclass
class ToolCallItem:
    """A tool (function) call from the assistant."""

    call_id: str
    name: str
    arguments: str


@dataclass
class ToolOutputItem:
    """The result of running a tool (function) handler."""

    call_id: str
    output: str


HistoryItem = Union[Message, ToolCallItem, ToolOutputItem]


class ConversationHistory:
    """Manages the conversation history for a chat session.

    Stores an ordered list of messages and tool call/output items.
    Provides helpers to manipulate them (add, clear, set system prompt, etc.).
    """

    def __init__(self, system_prompt: str = "") -> None:
        self._items: list[HistoryItem] = []
        self._system_prompt: str = system_prompt
        if system_prompt:
            self._items.append(Message(role="system", content=system_prompt))

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        """Return the current system prompt."""
        return self._system_prompt

    def set_system_prompt(self, prompt: str) -> None:
        """Set or update the system prompt.

        If a system prompt already exists it is replaced; otherwise a new
        system message is inserted at the beginning of the history.
        """
        self._system_prompt = prompt
        for i, item in enumerate(self._items):
            if isinstance(item, Message) and item.role == "system":
                self._items[i] = Message(role="system", content=prompt)
                return
        self._items.insert(0, Message(role="system", content=prompt))

    def add_message(self, role: str, content: str) -> None:
        """Append a message to the conversation history."""
        if role not in ("user", "assistant", "system"):
            raise ValueError(f"Invalid role: {role!r}. Must be 'user', 'assistant', or 'system'.")
        self._items.append(Message(role=role, content=content))

    def add_tool_calls(self, calls: list[tuple[str, str, str]]) -> None:
        """Append tool (function) call items. Each tuple is (call_id, name, arguments)."""
        for call_id, name, arguments in calls:
            self._items.append(ToolCallItem(call_id=call_id, name=name, arguments=arguments))

    def add_tool_outputs(self, outputs: list[dict]) -> None:
        """Append tool output items. Each dict must have 'call_id' and 'output' keys."""
        for out in outputs:
            self._items.append(
                ToolOutputItem(call_id=out["call_id"], output=out["output"])
            )

    def get_items(self) -> list[HistoryItem]:
        """Return a copy of all items in the history (messages + tool calls + tool outputs)."""
        return list(self._items)

    def get_messages(self) -> list[Message]:
        """Return a copy of all messages in the history (excludes tool call/output items)."""
        return [m for m in self._items if isinstance(m, Message)]

    def get_chat_messages(self) -> list[Message]:
        """Return messages excluding the system prompt (user + assistant only)."""
        return [m for m in self._items if isinstance(m, Message) and m.role != "system"]

    def clear(self) -> None:
        """Clear the conversation history.

        The system prompt is preserved if one was set.
        """
        self._items.clear()
        if self._system_prompt:
            self._items.append(Message(role="system", content=self._system_prompt))

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)
