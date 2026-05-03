"""Conversation history management for the chatbot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Message:
    role: str  # "user", "assistant", or "system"
    content: str


class ConversationHistory:
    """Manages the conversation history for a chat session."""

    def __init__(self, system_prompt: str = "") -> None:
        self._items: list[Message] = []
        self._system_prompt: str = system_prompt
        if system_prompt:
            self._items.append(Message(role="system", content=system_prompt))

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def set_system_prompt(self, prompt: str) -> None:
        """Set or update the system prompt."""
        self._system_prompt = prompt
        for i, item in enumerate(self._items):
            if item.role == "system":
                self._items[i] = Message(role="system", content=prompt)
                return
        self._items.insert(0, Message(role="system", content=prompt))

    def add_message(self, role: str, content: str) -> None:
        if role not in ("user", "assistant", "system"):
            raise ValueError(f"Invalid role: {role!r}. Must be 'user', 'assistant', or 'system'.")
        self._items.append(Message(role=role, content=content))

    def get_items(self) -> list[Message]:
        return list(self._items)

    def get_messages(self) -> list[Message]:
        return list(self._items)

    def get_chat_messages(self) -> list[Message]:
        return [m for m in self._items if m.role != "system"]

    def clear(self) -> None:
        self._items.clear()
        if self._system_prompt:
            self._items.append(Message(role="system", content=self._system_prompt))

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)
