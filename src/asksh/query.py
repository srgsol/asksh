"""Stdin piping and query assembly utilities."""

from __future__ import annotations

import sys


def read_piped_stdin() -> str | None:
    """Return piped stdin content, or None if stdin is a terminal."""
    if sys.stdin.isatty():
        return None
    content = sys.stdin.read()
    return content.strip() or None


def build_query(
    query_text: str, piped: str | None, context_file_name: str | None
) -> str:
    """Combine context file, piped stdin, and query text."""
    parts: list[str] = []

    if piped:
        parts.append(f"<stdin>{piped}</stdin>")

    if context_file_name:
        with open(context_file_name, "r", encoding="utf-8") as f:
            context = f.read()
        parts.append(
            f"<context>\nFile: {context_file_name}\nFile content:\n{context}</context>"
        )

    if query_text:
        parts.append(query_text)

    return "\n".join(parts)
