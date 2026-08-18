"""Textual overlay for streaming replies.

A short-lived Textual app runs in application mode (alternate screen) only
while a reply streams. It shows the transcript (intro panel + conversation
history) above the streaming region, which supports real mouse/keyboard
scrolling with tail-follow: new content stays visible by default, and
scrolling up pauses the follow until the user scrolls back to the newest
content.

When the stream ends (or Ctrl-C aborts it), the app exits and the caller
prints the final reply to the normal screen, so the terminal scrollback
keeps exactly one copy of the reply.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
import threading
from dataclasses import dataclass

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import LoadingIndicator, Markdown, Static
from textual.widgets._markdown import MarkdownFence

from asksh.client import (
    ChatStreamChunk,
    OllamaChatClient,
    ThinkOption,
    split_embedded_thinking,
)
from asksh.history import ConversationHistory

_REPAINT_RATE = 1 / 12  # 12 Hz coalesced repaint, the old Rich Live refresh rate


@dataclass
class StreamOutcome:
    """Result of a streamed reply rendered through the overlay."""

    aborted: bool
    error: Exception | None
    content: str
    thinking: str


class StreamChunkMessage(Message):
    """Worker → app: one streamed delta."""

    def __init__(self, chunk: ChatStreamChunk) -> None:
        super().__init__()
        self.chunk = chunk


class StreamDoneMessage(Message):
    """Worker → app: the stream finished (normally or aborted)."""

    def __init__(self, content: str, thinking: str, aborted: bool) -> None:
        super().__init__()
        self.content = content
        self.thinking = thinking
        self.aborted = aborted


class StreamErrorMessage(Message):
    """Worker → app: the stream failed mid-way."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error


class _PlainFence(MarkdownFence):
    """Fence variant that renders code without syntax highlighting (F-13)."""

    @classmethod
    def highlight(
        cls, code: str, language: str, ansi: bool = False, dark: bool = False
    ) -> Content:
        return Content(code)


class PlainMarkdown(Markdown):
    """Markdown widget with syntax highlighting disabled in code blocks."""

    # Swap only the fence class; every other block type is untouched.
    BLOCKS = {**Markdown.BLOCKS, "fence": _PlainFence, "code_block": _PlainFence}


class Transcript(VerticalScroll):
    """Intro panel + conversation history, above the streaming region."""

    def __init__(self, intro_markup: str, history: ConversationHistory) -> None:
        super().__init__()
        self._intro_markup = intro_markup
        self._history = history

    def compose(self) -> ComposeResult:
        yield Static(self._intro_markup, classes="intro")
        yield from self._message_widgets()

    def on_mount(self) -> None:
        # Long histories: start at the newest turns, next to the live region.
        self.scroll_end(animate=False)

    def refresh_from_history(self) -> None:
        """Rebuild the message widgets from history.

        The client adds the user message to history when streaming starts,
        so the first chunk triggers this to include the current turn.
        """
        for widget in self.query(".message"):
            widget.remove()
        self.mount(*self._message_widgets())

    def _message_widgets(self) -> list[Static | PlainMarkdown]:
        widgets: list[Static | PlainMarkdown] = []
        for message in self._history.get_messages():
            if message.role == "system":
                continue
            if message.role == "user":
                widgets.append(
                    Static(
                        f"[cyan]>>> [/cyan]{escape(message.content)}",
                        classes="message",
                    )
                )
            else:
                widgets.append(PlainMarkdown(message.content, classes="message"))
        return widgets


class LiveRegion(VerticalScroll):
    """The streaming region: thinking (optional) + content, tail-following.

    Occupies exactly the space its content needs (auto height) and scrolls
    internally once the content is taller than the remaining screen.
    """

    def __init__(self, *, think: ThinkOption, show_thinking: bool) -> None:
        super().__init__()
        self._think = think
        self._show_thinking = show_thinking
        self._spinner = LoadingIndicator(id="spinner")
        self._thinking = Static("", markup=False, id="thinking", classes="thinking")
        self._separator = Static("", id="separator")
        self._content = PlainMarkdown("", id="content")

    def compose(self) -> ComposeResult:
        yield self._spinner
        yield self._thinking
        yield self._separator
        yield self._content

    def update_content(self, thinking: str, content: str) -> None:
        """Repaint from the accumulated buffers, preserving tail-follow.

        If the view is at the end before the update, it stays pinned to the
        newest lines afterwards; if the user has scrolled up, the view stays
        where it is (F-50/F-52/F-53).
        """
        display_thinking = self._show_thinking or self._think is not False
        display_content, embedded = split_embedded_thinking(content)
        if embedded and not thinking:
            thinking = embedded

        thinking_shown = display_thinking and bool(thinking)
        content_shown = bool(display_content)
        follow = self.scroll_offset.y >= self.max_scroll_y

        self._spinner.display = not thinking_shown and not content_shown
        self._thinking.display = thinking_shown
        self._separator.display = thinking_shown and content_shown
        self._content.display = content_shown
        if thinking_shown:
            self._thinking.update(thinking)
        update_task = None
        if content_shown:
            update_task = self._content.update(display_content)

        if follow:
            # Snap after the next refresh, once the pending Markdown remount
            # (an async task) has settled; scrolling earlier would land short
            # of the true end.
            self.call_after_refresh(self._snap_to_end, update_task)

    async def _snap_to_end(self, update_task: object | None) -> None:
        if update_task is not None:
            await update_task
        self.scroll_end(animate=False, force=True)


class StreamApp(App):
    """Transient full-screen view of one streaming reply (alt screen)."""

    BINDINGS = [("ctrl+c", "abort", "Abort reply")]

    CSS = """
    Screen {
        layout: vertical;
    }

    Transcript {
        height: 1fr;
        overflow-y: auto;
    }

    Transcript .intro {
        border: round #6b6b6b;  /* grey42 */
        width: 100%;
        padding: 0 1;
        color: #808080;  /* grey50 */
    }

    Transcript .message {
        width: 100%;
    }

    LiveRegion {
        height: auto;
        max-height: 100%;
        overflow-y: auto;
    }

    LiveRegion > Static,
    LiveRegion > Markdown {
        width: 100%;
    }

    LiveRegion .thinking {
        color: #808080;  /* grey50 */
        text-style: italic;
    }

    LiveRegion .separator {
        height: 1;
    }

    LiveRegion LoadingIndicator {
        height: 1;
        color: #00ffff;  /* bright cyan */
    }
    """

    def __init__(
        self,
        client: OllamaChatClient,
        history: ConversationHistory,
        model: str,
        user_input: str,
        *,
        think: ThinkOption,
        show_thinking: bool,
        intro_markup: str | None,
        abort_event: threading.Event,
        ready_event: threading.Event,
    ) -> None:
        super().__init__()
        self._client = client
        self._history = history
        self._model = model
        self._user_input = user_input
        self._think = think
        self._show_thinking = show_thinking
        self._intro_markup = intro_markup
        self._abort_event = abort_event
        self._ready_event = ready_event

        self._thinking_text = ""
        self._content_text = ""
        self._first_chunk = True
        self._dirty = False

    def compose(self) -> ComposeResult:
        if self._intro_markup is not None:
            self._transcript = Transcript(self._intro_markup, self._history)
            yield self._transcript
        else:
            self._transcript = None
        self._live_region = LiveRegion(
            think=self._think, show_thinking=self._show_thinking
        )
        yield self._live_region

    def on_mount(self) -> None:
        self._ready_event.set()  # the stream worker waits for this
        self.set_interval(_REPAINT_RATE, self._maybe_repaint)
        self._install_sigint_abort()

    def on_unmount(self) -> None:
        """Restore the signal state installed by ``_install_sigint_abort``."""
        if not hasattr(self, "_signal_read"):
            return
        sigint = getattr(signal, "SIGINT", None)
        if sigint is None:
            return
        asyncio.get_running_loop().remove_reader(self._signal_read.fileno())
        self._signal_read.close()
        self._signal_write.close()
        signal.set_wakeup_fd(self._previous_wakeup_fd)
        signal.signal(sigint, self._previous_sigint_handler)

    def _install_sigint_abort(self) -> None:
        """Route SIGINT (a real signal, e.g. ``kill -INT``) to the abort action.

        The ctrl+c *key* arrives as a key event and is handled by the
        ``abort`` binding; a real SIGINT would otherwise raise
        KeyboardInterrupt inside Textual's message loop, which can interrupt
        the shutdown sequence, leave the driver's threads running, and hang
        the process at interpreter exit. A no-op handler plus a wakeup fd
        turns the signal into a normal loop wakeup that performs the same
        abort action, so the app exits through its regular shutdown path.
        """
        sigint = getattr(signal, "SIGINT", None)
        if sigint is None:
            return
        self._signal_read, self._signal_write = socket.socketpair()
        try:
            self._signal_read.setblocking(False)
            self._signal_write.setblocking(False)  # required by set_wakeup_fd
            self._previous_wakeup_fd = signal.set_wakeup_fd(self._signal_write.fileno())
            self._previous_sigint_handler = signal.signal(sigint, lambda *_: None)
            asyncio.get_running_loop().add_reader(
                self._signal_read.fileno(), self._on_sigint_wakeup
            )
        except (OSError, ValueError, RuntimeError):
            # Degrade gracefully: the ctrl+c key still works without this;
            # a real SIGINT then falls back to the caller's KeyboardInterrupt
            # handling. Roll back whatever was installed.
            if hasattr(self, "_previous_sigint_handler"):
                signal.signal(sigint, self._previous_sigint_handler)
            if hasattr(self, "_previous_wakeup_fd"):
                signal.set_wakeup_fd(self._previous_wakeup_fd)
            self._signal_read.close()
            self._signal_write.close()
            del self._signal_read
            del self._signal_write

    def _on_sigint_wakeup(self) -> None:
        try:
            os.read(self._signal_read.fileno(), 4096)
        except (BlockingIOError, InterruptedError):
            pass
        self.action_abort()

    # -- Worker-thread interface (fed by messages via post_message) --

    def post_chunk(self, chunk: ChatStreamChunk) -> None:
        """Record one streamed delta."""
        if chunk.is_thinking:
            self._thinking_text += chunk.text
        else:
            self._content_text += chunk.text
        self._dirty = True
        if self._first_chunk:
            self._first_chunk = False
            # The client adds the user message to history when streaming
            # starts; the transcript needs a rebuild to include it.
            if self._transcript is not None:
                self._transcript.refresh_from_history()

    def post_done(self, content: str, thinking: str, aborted: bool) -> None:
        """The stream finished (normally or aborted)."""
        self.exit(
            StreamOutcome(
                aborted=aborted, error=None, content=content, thinking=thinking
            )
        )

    def post_error(self, error: Exception) -> None:
        """The stream failed mid-way (F-111)."""
        self.exit(StreamOutcome(aborted=False, error=error, content="", thinking=""))

    def on_stream_chunk_message(self, message: StreamChunkMessage) -> None:
        self.post_chunk(message.chunk)

    def on_stream_done_message(self, message: StreamDoneMessage) -> None:
        self.post_done(message.content, message.thinking, message.aborted)

    def on_stream_error_message(self, message: StreamErrorMessage) -> None:
        self.post_error(message.error)

    # -- UI --

    def action_abort(self) -> None:
        """Abort the stream (Ctrl-C): unblock the worker and exit cleanly."""
        self._abort_event.set()
        self._client.abort_active_stream()
        self.exit(
            StreamOutcome(
                aborted=True,
                error=None,
                content=self._content_text,
                thinking=self._thinking_text,
            )
        )

    def _maybe_repaint(self) -> None:
        """Coalesced repaint: apply pending deltas at most ~12 times/second."""
        if self._dirty:
            self._dirty = False
            self._live_region.update_content(self._thinking_text, self._content_text)


def _ensure_driver_input() -> None:
    """Point Textual's keyboard input at a terminal.

    Textual's Linux driver reads input from ``sys.__stdin__`` (not
    ``sys.stdin``). When stdin is redirected, that is a pipe: the overlay
    would receive no key events and Ctrl-C could not abort the stream
    (F-110). Chat mode has already reopened ``sys.stdin`` from ``/dev/tty``
    (cli.py); use it when possible, otherwise open the controlling terminal.
    """
    try:
        if sys.__stdin__.isatty():
            return
    except ValueError:
        pass  # the original stdin was closed (chat mode after piped input)
    if sys.stdin is not sys.__stdin__:
        try:
            stdin_is_tty = sys.stdin.isatty()
        except ValueError:
            stdin_is_tty = False
        if stdin_is_tty:
            sys.__stdin__ = sys.stdin
            return
    sys.__stdin__ = open("/dev/tty")  # noqa: SIM115


def run_stream_overlay(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    user_input: str,
    *,
    think: ThinkOption,
    show_thinking: bool,
    intro_markup: str | None,
) -> StreamOutcome:
    """Run the streaming overlay to completion and return the outcome.

    Must be called from the main thread: `App.run()` drives the terminal
    event loop; the HTTP stream runs on a worker thread and posts deltas
    into the app via (thread-safe, non-blocking) ``post_message``.
    """
    _ensure_driver_input()
    abort_event = threading.Event()
    ready_event = threading.Event()
    app = StreamApp(
        client,
        history,
        model,
        user_input,
        think=think,
        show_thinking=show_thinking,
        intro_markup=intro_markup,
        abort_event=abort_event,
        ready_event=ready_event,
    )
    worker = threading.Thread(
        target=_stream_worker,
        args=(
            client,
            history,
            model,
            user_input,
            think,
            app,
            abort_event,
            ready_event,
        ),
        daemon=True,
    )
    worker.start()
    try:
        app.run()
    except KeyboardInterrupt:
        # Fallback for a SIGINT in the window before the app's on_mount
        # installed the signal handler: F-110 forbids a traceback, and the
        # return-value fallback below turns this into an abort.
        pass
    finally:
        # Stop the worker promptly on every exit path — normal completion,
        # abort, or an unexpected app exit (e.g. Textual's ctrl+q quit).
        abort_event.set()
        client.abort_active_stream()
    worker.join(timeout=5)

    outcome = app.return_value
    if isinstance(outcome, StreamOutcome):
        return outcome
    # The app quit without an outcome — treat as abort.
    return StreamOutcome(aborted=True, error=None, content="", thinking="")


def _stream_worker(
    client: OllamaChatClient,
    history: ConversationHistory,
    model: str,
    user_input: str,
    think: ThinkOption,
    app: StreamApp,
    abort_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    """Consume the client stream on a worker thread and post deltas to the app."""
    ready_event.wait()
    if abort_event.is_set():
        app.post_message(StreamDoneMessage("", "", True))
        return

    content = ""
    thinking = ""
    gen = client.stream_message(
        user_input,
        model=model,
        history=history,
        think=think,
        abort=abort_event,
    )
    try:
        while True:
            try:
                chunk = next(gen)
            except StopIteration as stop:
                content, thinking = stop.value
                break
            if abort_event.is_set():
                break
            app.post_message(StreamChunkMessage(chunk))
    except Exception as error:
        if abort_event.is_set():
            app.post_message(StreamDoneMessage("", "", True))
        else:
            app.post_message(StreamErrorMessage(error))
        return

    if abort_event.is_set():
        gen.close()  # release the response reference held by the generator
        app.post_message(StreamDoneMessage("", "", True))
    else:
        app.post_message(StreamDoneMessage(content, thinking, False))
