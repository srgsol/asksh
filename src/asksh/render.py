"""Rich console output and streaming/non-streaming reply rendering."""

from __future__ import annotations

import signal
import sys
import threading
from collections.abc import Callable, Generator

from typing import Any, cast, TextIO

from rich._loop import loop_last
from rich.console import Console, Group
from rich.control import Control
from rich.live import Live
from rich.live_render import LiveRender, VerticalOverflowMethod
from rich.markdown import Markdown
from rich.segment import ControlType, Segment
from rich.spinner import Spinner
from rich.style import Style
from rich.syntax import SyntaxTheme
from rich.text import Text

from asksh.client import (
    ChatStreamChunk,
    OllamaChatClient,
    ThinkOption,
    split_embedded_thinking,
)
from asksh.history import ConversationHistory

console = Console(highlight=False)
_SPINNER_STYLE = "bright_cyan"
_LIVE_VERTICAL_OVERFLOW = "crop_above"
_crop_above_patched = False


class _PlainSyntaxTheme(SyntaxTheme):
    """No-op theme: code blocks render as plain text, no highlighting (F-13)."""

    def get_style_for_token(self, token_type: Any) -> Style:
        return Style.null()

    def get_background_style(self) -> Style:
        return Style.null()


plain_code_theme = _PlainSyntaxTheme()


def _patch_live_render_crop_above() -> None:
    """Keep newest streamed lines visible when content exceeds terminal height."""
    global _crop_above_patched
    if _crop_above_patched:
        return

    original_rich_console = LiveRender.__rich_console__

    def __rich_console__(self, console, options):
        if self.vertical_overflow != _LIVE_VERTICAL_OVERFLOW:
            yield from original_rich_console(self, console, options)
            return

        renderable = self.renderable
        style = console.get_style(self.style)
        lines = console.render_lines(renderable, options, style=style, pad=False)
        shape = Segment.get_shape(lines)

        _, height = shape
        if height > options.size.height:
            lines = lines[-options.size.height :]
            shape = Segment.get_shape(lines)
        self._shape = shape

        new_line = Segment.line()
        for last, line in loop_last(lines):
            yield from line
            if not last:
                yield new_line

    LiveRender.__rich_console__ = __rich_console__
    _crop_above_patched = True


_patch_live_render_crop_above()

_resized = False


def _on_winch(_signum: int, _frame: object) -> None:
    """SIGWINCH handler: only flag the resize; the repair runs on the next refresh."""
    global _resized
    _resized = True


def _install_winch_handler() -> None:
    """Install the SIGWINCH handler (idempotent).

    prompt_toolkit replaces the handler with SIG_DFL after every prompt, so
    this must be re-called before each Live display.
    """
    sigwinch = getattr(signal, "SIGWINCH", None)
    if sigwinch is None:
        return
    if threading.current_thread() is not threading.main_thread():
        return  # signal.signal is only allowed on the main thread
    if signal.getsignal(sigwinch) is _on_winch:
        return
    signal.signal(sigwinch, _on_winch)


_install_winch_handler()


def _clear_resize_flag() -> None:
    """Discard a pending resize (a resize between turns only re-wrapped
    static text, which the terminal already displays correctly)."""
    global _resized
    _resized = False


class _ResizeSafeLive(Live):
    """Live display that repairs the screen after a terminal resize.

    Rich repaints the live region with cursor-relative moves based on the
    height of the last render. A resize makes the terminal re-wrap the lines
    already on screen, so the on-screen height diverges from Rich's
    bookkeeping and the next repaint lands mid-region, duplicating text.
    When a resize is pending, refresh is replaced by a full screen repair:
    clear, re-print the content above the live region, forget the old
    geometry, and repaint.
    """

    def __init__(
        self,
        *args: Any,
        repaint_prefix: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._repaint_prefix = repaint_prefix

    def refresh(self) -> None:
        # Repair from the 12 Hz refresh thread too: covers a resize while
        # the main thread is blocked waiting for the next stream chunk.
        if _resized and self._started:
            self._repair()
            return
        super().refresh()

    def stop(self) -> None:
        # Repair first so the exit cleanup uses post-resize geometry.
        with self._lock:
            if _resized and self._started:
                self._repair()
        if not self.transient or self._alt_screen:
            super().stop()
            return
        # Transient on the main screen: erase the live region directly
        # instead of rich's final full-content render. That render paints
        # the complete reply with vertical_overflow="visible", scrolling a
        # duplicate copy into the scrollback before restore_cursor() erases
        # the visible screen; the caller prints the final content once, so
        # the reply must not be rendered twice. Mirrors rich 15.0.0
        # Live.stop() minus the final refresh and line().
        with self._lock:
            if not self._started:
                return
            self._started = False
            self.console.clear_live()
            if self._nested:
                return
            if self.auto_refresh and self._refresh_thread is not None:
                self._refresh_thread.stop()
                self._refresh_thread = None
            with self.console:
                try:
                    self._disable_redirect_io()
                    self.console.pop_render_hook()
                    if self.console.is_terminal:
                        self.console.control(self._erase_region_control())
                finally:
                    self.console.show_cursor(True)

    def repair(self) -> None:
        """Repair the screen if a resize is pending (called between chunks)."""
        with self._lock:
            if _resized:
                self._repair()

    def _erase_region_control(self) -> Control:
        """Control codes that erase the live region and land on its start row.

        Assumes the cursor is at the end of the region's last painted line.
        (Rich's ``restore_cursor()`` instead assumes the cursor is one row
        below the region, which its ``stop()`` guarantees via
        ``console.line()`` — skipping that line here for scrollback hygiene
        requires erasing the last row first.)
        """
        shape = self._live_render._shape
        if shape is None:
            return Control()
        _, height = shape
        return Control(
            ControlType.CARRIAGE_RETURN,
            (ControlType.ERASE_IN_LINE, 2),
            *(
                ((ControlType.CURSOR_UP, 1), (ControlType.ERASE_IN_LINE, 2))
                * (height - 1)
            ),
        )

    def _repair(self) -> None:
        """Clear and repaint the screen; caller must hold ``_lock``."""
        global _resized
        _resized = False
        self.console.clear()
        # Print the content above the live region with our render hook
        # temporarily popped, otherwise process_renderables() would prepend
        # position_cursor() and append the live render to every print.
        # While the hook is popped, concurrent refresh ticks emit zero bytes
        # and are serialized with us by _lock; nothing may print to
        # (redirected) stdout inside this window.
        if self.console._render_hooks and self.console._render_hooks[-1] is self:
            self.console.pop_render_hook()
        if self._repaint_prefix is not None:
            self._repaint_prefix()
        self._live_render._shape = None  # forget pre-resize geometry
        self.console.push_render_hook(self)
        super().refresh()


def _drain(
    gen: Generator[ChatStreamChunk, None, object], file: TextIO | None = None
) -> None:
    """Write content chunks from a streaming generator to *file* (default stdout)."""
    out = file or sys.stdout
    try:
        while True:
            chunk = next(gen)
            if not chunk.is_thinking:
                out.write(chunk.text)
                out.flush()
    except StopIteration:
        pass


_THINKING_PREFIX = "Thinking...\n"


def _format_thinking(thinking: str) -> Text:
    return Text(f"{_THINKING_PREFIX}{thinking}", style="grey50 italic")


def _should_show_thinking(*, think: ThinkOption, show_thinking: bool) -> bool:
    return show_thinking or think is not False


def _stream_display(
    thinking: str,
    content: str,
    *,
    think: ThinkOption,
    show_thinking: bool,
) -> Text | Markdown | Group | Spinner:
    display_thinking = _should_show_thinking(think=think, show_thinking=show_thinking)
    display_content, embedded = split_embedded_thinking(content)
    if embedded and not thinking:
        thinking = embedded

    parts: list[Text | Markdown] = []
    if display_thinking and thinking:
        parts.append(_format_thinking(thinking))
    if display_content:
        if parts:
            parts.append(Text(""))
        parts.append(Markdown(display_content, code_theme=plain_code_theme))
    if not parts:
        return Spinner("dots", style=_SPINNER_STYLE)
    if len(parts) == 1:
        return parts[0]
    return Group(*parts)


def _print_thinking(thinking: str) -> None:
    if not thinking:
        return
    console.print(_format_thinking(thinking))


def print_assistant_reply(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    stream: bool,
    user_input: str,
    *,
    think: ThinkOption = False,
    show_thinking: bool = False,
    repaint_prefix: Callable[[], None] | None = None,
) -> None:
    is_tty = sys.stdout.isatty()
    display_thinking = _should_show_thinking(think=think, show_thinking=show_thinking)

    if stream:
        abort = threading.Event()
        gen = client.stream_message(
            user_input,
            model=model,
            history=history,
            think=think,
            abort=abort,
        )
        if is_tty:
            thinking_text = ""
            content_text = ""
            _install_winch_handler()
            _clear_resize_flag()
            try:
                with _ResizeSafeLive(
                    _stream_display("", "", think=think, show_thinking=show_thinking),
                    console=console,
                    refresh_per_second=12,
                    transient=True,
                    vertical_overflow=cast(
                        VerticalOverflowMethod, _LIVE_VERTICAL_OVERFLOW
                    ),
                    repaint_prefix=repaint_prefix,
                ) as live:
                    try:
                        while True:
                            chunk = next(gen)
                            if chunk.is_thinking:
                                thinking_text += chunk.text
                            else:
                                content_text += chunk.text
                            live.update(
                                _stream_display(
                                    thinking_text,
                                    content_text,
                                    think=think,
                                    show_thinking=show_thinking,
                                )
                            )
                            if _resized:
                                live.repair()
                    except StopIteration:
                        pass
            except KeyboardInterrupt:
                # F-110: abort the reply cleanly — no traceback, no final
                # print, no history entry for the partial reply. The Live
                # context manager erases the streaming region on unwind.
                abort.set()
                client.abort_active_stream()
                return
            content_text, embedded_thinking = split_embedded_thinking(content_text)
            if embedded_thinking and not thinking_text:
                thinking_text = embedded_thinking
            if display_thinking and thinking_text:
                _print_thinking(thinking_text)
            if content_text:
                if display_thinking and thinking_text:
                    console.print()
                console.print(Markdown(content_text, code_theme=plain_code_theme))
        else:
            _drain(gen)
            print()
    else:
        if is_tty:
            with console.status("", spinner="dots", spinner_style=_SPINNER_STYLE):
                reply, thinking = client.send_message(
                    user_input,
                    model=model,
                    history=history,
                    think=think,
                )
            if display_thinking and thinking:
                _print_thinking(thinking)
            if reply:
                if display_thinking and thinking:
                    console.print()
                console.print(Markdown(reply, code_theme=plain_code_theme))
        else:
            reply, _ = client.send_message(
                user_input,
                model=model,
                history=history,
                think=think,
            )
            print(reply)
