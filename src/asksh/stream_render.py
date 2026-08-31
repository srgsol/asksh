"""Append-only terminal renderer for streamed replies.

The renderer never moves the cursor up: finished lines are printed once and
are then the terminal's own scrollback, never touched again. Only the
current, still-growing line is ever repainted, with a carriage return plus
an erase-to-end-of-line -- never a cursor-up. This makes native mouse-wheel
scrolling and terminal resizes safe by construction: there is no region
geometry left for either to desynchronize.

``PrintWriter`` (the ``text`` render style) skips that last-line preview
and writes tokens as they arrive; a wait spinner occupies the live row
only until the first text.

Everything here renders plain text only. Markdown formatting (when a render
style asks for it) is applied once, after a stream completes, in
``render.py`` -- never incrementally against a growing buffer.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from rich.console import Console
from rich.control import Control
from rich.segment import ControlType, Segment, Segments, SegmentLines
from rich.spinner import Spinner
from rich.text import Text

if TYPE_CHECKING:
    from rich.style import StyleType


class LiveRow:
    """The single repaintable row at the cursor, shared by every writer.

    Ownership of the row can move between writers (e.g. a "thinking" writer
    hands it to a "content" writer once the trace finishes). Tracking
    "is a live row currently painted" here rather than per-writer ensures
    whoever paints next always erases the previous owner's row first.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._painted = False

    def clear(self) -> None:
        if self._painted:
            self._console.control(
                Control(ControlType.CARRIAGE_RETURN, (ControlType.ERASE_IN_LINE, 2))
            )
            self._painted = False

    def paint(self, segments: list[Segment]) -> None:
        self.clear()
        self._console.print(Segments(segments))
        self._painted = True
        self._console.file.flush()


def _preview_line(
    console: Console,
    text: str,
    *,
    spinner: Spinner,
    style: StyleType = "grey50",
) -> list[Segment]:
    """Render the live row: a spinner before any text has arrived, else a
    dim, single-line, non-wrapping preview of *text*'s last physical line."""
    if not text.strip():
        renderable = spinner
    else:
        preview_source = text.rsplit("\n", 1)[-1]
        renderable = Text(
            preview_source, style=style, no_wrap=True, overflow="ellipsis"
        )
    width = max(console.size.width - 1, 1)
    options = console.options.update(width=width, height=1)
    lines = console.render_lines(renderable, options, pad=False)
    return lines[0] if lines else []


class AppendOnlyWriter:
    """Streams plain text, committing finished physical lines permanently.

    Call :meth:`update` with the full accumulated text on every delta, and
    :meth:`finish` once the stream ends. Multiple writers may share a
    console and a :class:`LiveRow` as long as only one is being updated at a
    time (e.g. a "thinking" writer finishes before a "content" writer
    starts).

    A physical source line (terminated by ``\\n``) renders to the same
    wrapped lines no matter what text arrives afterwards -- plain ``Text``
    wraps each line independently -- so a line is safe to commit the moment
    it is complete; only the trailing, not-yet-terminated line is held back
    on the live row.
    """

    def __init__(
        self,
        console: Console,
        live_row: LiveRow,
        *,
        style: StyleType = "none",
        preview_style: StyleType | None = None,
        spinner_style: str = "bright_cyan",
    ) -> None:
        self._console = console
        self._live_row = live_row
        self._style = style
        self._preview_style = style if preview_style is None else preview_style
        self._text = ""
        self._committed = 0
        self._pending_source: str | None = None
        self._pending_lines: list[list[Segment]] = []
        self._spinner = Spinner("dots", style=spinner_style)

    def update(self, text: str) -> None:
        self._text = text
        self._tick()

    def finish(self) -> None:
        full_lines = self._render_lines(self._text) if self._text.strip() else []
        self._live_row.clear()
        remaining = full_lines[self._committed :]
        if remaining:
            self._console.print(SegmentLines(remaining, new_lines=True))
        self._committed = len(full_lines)
        self._console.file.flush()

    def _render_lines(self, text: str) -> list[list[Segment]]:
        width = max(self._console.size.width, 1)
        options = self._console.options.update(width=width)
        return self._console.render_lines(
            Text(text, style=self._style), options, pad=False
        )

    def _candidate_source(self) -> str:
        """The prefix of ``self._text`` guaranteed not to change: everything
        up to, but not including, the trailing physical line, which has not
        yet been terminated by a newline and may still grow."""
        if not self._text.strip():
            return ""
        stable_text = "\n".join(self._text.split("\n")[:-1])
        return stable_text if stable_text.strip() else ""

    def _stable_lines(self) -> list[list[Segment]]:
        source = self._candidate_source()
        if source != self._pending_source:
            self._pending_lines = self._render_lines(source) if source else []
            self._pending_source = source
        return self._pending_lines

    def _tick(self) -> None:
        stable_lines = self._stable_lines()
        if len(stable_lines) > self._committed:
            new_lines = stable_lines[self._committed :]
            self._live_row.clear()
            self._console.print(SegmentLines(new_lines, new_lines=True))
            self._committed = len(stable_lines)
        self._paint_live()

    def _paint_live(self) -> None:
        line = _preview_line(
            self._console,
            self._text,
            spinner=self._spinner,
            style=self._preview_style,
        )
        self._live_row.paint(line)


class PrintWriter:
    """Writes new text to the console as it arrives.

    Used by the ``text`` render style: no last-line preview and no
    holdback. A wait spinner occupies the live row until the first
    non-empty text; after that, tokens are printed and never rewritten.
    """

    def __init__(
        self,
        console: Console,
        live_row: LiveRow,
        *,
        spinner_style: str = "bright_cyan",
    ) -> None:
        self._console = console
        self._live_row = live_row
        self._spinner = Spinner("dots", style=spinner_style)
        self._text = ""
        self._printed = 0
        self._started = False

    def update(self, text: str) -> None:
        self._text = text
        if not text.strip() and not self._started:
            line = _preview_line(
                self._console, "", spinner=self._spinner, style="grey50"
            )
            self._live_row.paint(line)
            return
        if not self._started:
            self._live_row.clear()
            self._started = True
        if len(text) > self._printed:
            self._console.file.write(text[self._printed :])
            self._console.file.flush()
            self._printed = len(text)

    def finish(self) -> None:
        if not self._started:
            self._live_row.clear()
        elif self._text and not self._text.endswith("\n"):
            self._console.file.write("\n")
        self._console.file.flush()


class PreviewWriter:
    """Live-row-only preview: spinner, then a dim last-line preview.

    Never commits any text to the scrollback. Used by the ``markdown``
    render style, which shows no streamed body at all -- only a wait
    indicator -- and prints the complete reply as Markdown exactly once,
    when the stream ends.
    """

    def __init__(
        self,
        console: Console,
        live_row: LiveRow,
        *,
        style: StyleType = "grey50",
        spinner_style: str = "bright_cyan",
    ) -> None:
        self._console = console
        self._live_row = live_row
        self._style = style
        self._spinner = Spinner("dots", style=spinner_style)

    def update(self, text: str) -> None:
        line = _preview_line(
            self._console, text, spinner=self._spinner, style=self._style
        )
        self._live_row.paint(line)

    def finish(self) -> None:
        self._live_row.clear()
        self._console.file.flush()


class _StreamWriter(Protocol):
    """Duck-typed interface shared by the stream writers."""

    def update(self, text: str) -> None: ...

    def finish(self) -> None: ...


class StreamAnimator:
    """Keeps a writer's live row animating (spinner) between chunk arrivals.

    A single background thread re-invokes ``update`` on whichever writer is
    currently "active" at a fixed interval, so the wait spinner keeps
    spinning while blocked on the next network chunk. All calls to a
    writer's ``update``/``finish`` while the animator is running must go
    through this class so main-thread and ticker-thread writes never race.
    """

    def __init__(self, interval: float = 1 / 12) -> None:
        self._interval = interval
        self._lock = threading.Lock()
        self._writer: _StreamWriter | None = None
        self._text = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def set(self, writer: _StreamWriter, text: str) -> None:
        """Update *writer* with *text* now, and keep animating it."""
        with self._lock:
            self._writer = writer
            self._text = text
            writer.update(text)

    def finish(self, writer: _StreamWriter) -> None:
        """Stop animating *writer* and commit its final state."""
        with self._lock:
            if self._writer is writer:
                self._writer = None
            writer.finish()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._writer is not None:
                    self._writer.update(self._text)
