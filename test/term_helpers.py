"""Shared helpers for testing terminal renderers (append-only and Live-based).

Not a test module itself (no ``test_`` prefix): pytest won't collect it.
"""

from __future__ import annotations

import re
from io import StringIO

from rich.console import Console

_CURSOR_UP_RE = re.compile(r"\x1b\[(\d*)A")
_ERASE_IN_LINE_RE = re.compile(r"\x1b\[(\d*)K")
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def make_console(width: int = 60) -> tuple[Console, StringIO]:
    """A force-terminal console writing to an in-memory buffer.

    ``_environ={}`` makes this independent of the real ``TERM`` env var
    (some CI/sandbox shells set ``TERM=dumb``, which would otherwise
    suppress every control code Rich writes, real terminal or not).
    """
    buf = StringIO()
    console = Console(
        file=buf,
        width=width,
        force_terminal=True,
        no_color=True,
        _environ={},
    )
    return console, buf


def has_cursor_up(raw: str) -> bool:
    """Whether *raw* contains a CURSOR_UP escape sequence."""
    return bool(_CURSOR_UP_RE.search(raw))


def simulate_terminal(raw: str) -> list[str]:
    """Replay *raw* through a minimal terminal model.

    Understands carriage return, erase-in-line, ``\\n``, and cursor-up (so
    ``live_markdown``'s Rich ``Live`` erasure -- which legitimately moves the
    cursor up to clear its region -- replays correctly, unlike the other
    render styles which never emit it). Any other escape sequence (SGR
    color/style codes, cursor show/hide, ...) is consumed and ignored so it
    never leaks into the visible text.

    Returns the resulting screen as a list of line strings (trailing
    whitespace stripped, since ``pad=False`` renders can still leave a few
    trailing spaces from wide-character measurement).
    """
    lines: list[str] = [""]
    row = 0
    column = 0
    i = 0
    while i < len(raw):
        erase_match = _ERASE_IN_LINE_RE.match(raw, i)
        if erase_match:
            if (erase_match.group(1) or "0") == "2":
                lines[row] = ""
                column = 0
            i = erase_match.end()
            continue
        cursor_up_match = _CURSOR_UP_RE.match(raw, i)
        if cursor_up_match:
            n = int(cursor_up_match.group(1) or "1")
            row = max(0, row - n)
            i = cursor_up_match.end()
            continue
        char = raw[i]
        if char == "\r":
            column = 0
            i += 1
            continue
        if char == "\n":
            row += 1
            if row == len(lines):
                lines.append("")
            column = 0
            i += 1
            continue
        other_csi = _CSI_RE.match(raw, i)
        if other_csi:
            i = other_csi.end()
            continue
        current = lines[row]
        lines[row] = (
            current[:column] + char + current[column + 1 :]
            if column < len(current)
            else current + char
        )
        column += 1
        i += 1
    return [line.rstrip() for line in lines]
