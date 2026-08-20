"""Persist the last assistant reply so it can be re-rendered as Markdown.

The conversation itself lives in memory for the session only (see
``ConversationHistory``); this module is a narrow, deliberate exception that
saves just the most recent assistant reply to disk so ``asksh -m`` can
re-render it after the process exits.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_state_dir() -> Path:
    """Return ``$XDG_STATE_HOME/asksh`` (fallback ``~/.local/state/asksh``)."""
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    if not base:
        base = str(Path.home() / ".local" / "state")
    return Path(base) / "asksh"


def last_message_path() -> Path:
    """Return ``$XDG_STATE_HOME/asksh/last_reply`` (fallback ``~/.local/state/...``)."""
    return default_state_dir() / "last_reply"


def save_last_message(content: str) -> None:
    """Persist *content* as the last assistant reply, overwriting any previous one."""
    path = last_message_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_last_message() -> str | None:
    """Return the previously saved assistant reply, or ``None`` if none exists."""
    path = last_message_path()
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")
