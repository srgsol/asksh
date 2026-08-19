"""Append-only terminal renderer for streamed replies.

The renderer never moves the cursor up: finished lines are printed once and
are then the terminal's own scrollback, never touched again. Only the
current, still-growing line is ever repainted, with a carriage return plus
an erase-to-end-of-line -- never a cursor-up. This makes native mouse-wheel
scrolling and terminal resizes safe by construction: there is no region
geometry left for either to desynchronize.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from markdown_it import MarkdownIt
from rich.console import Console
from rich.control import Control
from rich.markdown import Markdown
from rich.segment import ControlType, Segment, Segments, SegmentLines
from rich.spinner import Spinner
from rich.text import Text

if TYPE_CHECKING:
    from rich.style import StyleType

_PARSER = MarkdownIt().enable("strikethrough").enable("table")

# Block types whose own rendered lines are guaranteed not to change once
# printed, no matter what arrives afterwards in the *same* block (bullet
# markers, heading style, fence/code lines, hr, blockquote prefix are all
# independent of anything that follows). Anything not listed here defaults
# to "hold the whole block back until a later block starts", notably:
#   - paragraphs: a bare line can retroactively become a setext heading
#     (`Hello\n===`) or a GFM table header (`| a | b |\n| - | - |`) once the
#     next line arrives, changing the style/shape of lines already shown;
#   - ordered lists: item indent width is `len(str(last_number)) + 2`
#     (rich.markdown.ListItem.render_number), so item 10 re-indents 1-9;
#   - tables: column widths depend on every row.
# Containers (blockquote, bullet list) are only checked at the top level;
# a table or ordered list nested *inside* one is not separately detected.
_PROGRESSIVE_BLOCK_TYPES = frozenset(
    {
        "heading_open",
        "fence",
        "code_block",
        "hr",
        "blockquote_open",
        "bullet_list_open",
    }
)


def _top_level_blocks(text: str) -> list[tuple[str, int]]:
    """Return ``(token_type, start_line)`` for each top-level Markdown block."""
    tokens = _PARSER.parse(text)
    return [
        (token.type, token.map[0])
        for token in tokens
        if token.level == 0 and token.nesting in (0, 1) and token.map is not None
    ]


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


class AppendOnlyWriter:
    """Streams Markdown or plain text, committing finished lines permanently.

    Call :meth:`update` with the full accumulated text on every delta, and
    :meth:`finish` once the stream ends. Multiple writers may share a
    console and a :class:`LiveRow` as long as only one is being updated at a
    time (e.g. a "thinking" writer finishes before a "content" writer
    starts).
    """

    def __init__(
        self,
        console: Console,
        live_row: LiveRow,
        *,
        markdown: bool,
        style: StyleType = "none",
        code_theme: object = None,
        preview_style: StyleType = "grey50",
        spinner_style: str = "bright_cyan",
    ) -> None:
        self._console = console
        self._live_row = live_row
        self._markdown = markdown
        self._style = style
        self._code_theme = code_theme
        self._preview_style = preview_style
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

    def _make_renderable(self, text: str):
        if self._markdown:
            return Markdown(text, code_theme=self._code_theme, style=self._style)
        return Text(text, style=self._style)

    def _render_lines(
        self, text: str, *, height: int | None = None
    ) -> list[list[Segment]]:
        width = max(self._console.size.width, 1)
        options = self._console.options.update(width=width, height=height)
        return self._console.render_lines(
            self._make_renderable(text), options, pad=False
        )

    def _candidate_source(self) -> str:
        """Return the source-text prefix that *should* be safe to commit.

        Two trims are applied to the source text (never to already-rendered
        lines, since e.g. a fenced code block's current code line is
        rewritten in place, not appended to, until its newline arrives --
        there is no stable rendered-line count to trust yet):

        1. The trailing, not-yet-newline-terminated physical line is always
           dropped -- it may still grow.
        2. If what remains still ends in a block type not in
           ``_PROGRESSIVE_BLOCK_TYPES`` (a paragraph, table, or ordered
           list -- see the module docstring above that set), the whole of
           that trailing block is dropped too: its rendering can still
           change non-locally (an ordered list's indent depends on the
           final item count; a bare line can retroactively become a
           setext heading or a GFM table header).
        """
        if not self._text.strip():
            return ""
        stable_text = "\n".join(self._text.split("\n")[:-1])
        if self._markdown and stable_text.strip():
            blocks = _top_level_blocks(stable_text)
            if blocks:
                last_type, last_start_line = blocks[-1]
                if last_type not in _PROGRESSIVE_BLOCK_TYPES:
                    stable_text = "\n".join(stable_text.split("\n")[:last_start_line])
        return stable_text if stable_text.strip() else ""

    def _stable_lines(self) -> list[list[Segment]]:
        """Commit only lines confirmed identical across two *distinct* source states.

        Even a "progressive" block type is not always safe line-by-line:
        Pygments/``rich.syntax.Syntax`` always shows at least one code line
        for a fence, even an empty one, so a fence with zero complete code
        lines and one with a single complete line both render as 3 lines
        (top pad, one code line, bottom pad) -- the *same slot* is filled
        in place, not appended to. Comparing the current candidate render
        against the render of the *previous distinct* candidate source
        (skipping ticks where the candidate source hasn't grown -- e.g.
        mid-word, before the next physical line completes, comparing it to
        itself would trivially "match" without proving anything) catches
        this without special-casing each block type, at the cost of
        waiting for one extra complete line before committing.
        """
        source = self._candidate_source()
        if source != self._pending_source:
            candidate = self._render_lines(source) if source else []
            limit = min(len(candidate), len(self._pending_lines))
            common = 0
            while common < limit and candidate[common] == self._pending_lines[common]:
                common += 1
            self._pending_source = source
            self._pending_lines = candidate
            self._committed = max(common, self._committed)
        return self._pending_lines[: self._committed] if self._pending_lines else []

    def _tick(self) -> None:
        previously_committed = self._committed
        stable_lines = self._stable_lines()
        if len(stable_lines) > previously_committed:
            new_lines = stable_lines[previously_committed:]
            self._live_row.clear()
            self._console.print(SegmentLines(new_lines, new_lines=True))
        self._paint_live()

    def _paint_live(self) -> None:
        if self._committed == 0 and not self._text.strip():
            preview = self._spinner
        else:
            preview_source = self._text.rsplit("\n", 1)[-1]
            preview = Text(
                preview_source,
                style=self._preview_style,
                no_wrap=True,
                overflow="ellipsis",
            )
        width = max(self._console.size.width - 1, 1)
        lines = self._render_lines_of(preview, width=width, height=1)
        line = lines[0] if lines else []
        self._live_row.paint(line)

    def _render_lines_of(
        self, renderable, *, width: int, height: int
    ) -> list[list[Segment]]:
        options = self._console.options.update(width=width, height=height)
        return self._console.render_lines(renderable, options, pad=False)


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
        self._writer: AppendOnlyWriter | None = None
        self._text = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def set(self, writer: AppendOnlyWriter, text: str) -> None:
        """Update *writer* with *text* now, and keep animating it."""
        with self._lock:
            self._writer = writer
            self._text = text
            writer.update(text)

    def finish(self, writer: AppendOnlyWriter) -> None:
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
