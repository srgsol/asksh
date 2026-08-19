# asksh — Terminal User Interface Specification

**Status:** Baseline document for the current implementation. The binding, technology-agnostic requirements live in [tui-feature-spec.md](tui-feature-spec.md); this document records the implementation-specific behavior (and its history).
**Source of truth:** [src/asksh/stream_render.py](../src/asksh/stream_render.py) (`AppendOnlyWriter`, `LiveRow`, `StreamAnimator`), [src/asksh/render.py](../src/asksh/render.py) (streaming orchestration, non-streaming and non-TTY output), [src/asksh/chat.py](../src/asksh/chat.py), [tests/test_stream_render.py](../tests/test_stream_render.py), [tests/test_render.py](../tests/test_render.py).
**Stack today:** Rich ≥ 15.0.0 (append-only streaming, Markdown, static printing), prompt_toolkit ≥ 3.0.43 (chat input), requests (Ollama HTTP). No Textual, no alternate screen, no signal handling.

The word **MUST** below describes behavior the current implementation exhibits and that a reimplementation is expected to reproduce, unless a section is explicitly marked *gap*. Sections marked *superseded* describe earlier implementations (Rich `Live` with cursor-relative repaints, and later a Textual alternate-screen overlay), kept for history; §4.1–§4.7 below replace them. Both were tried and abandoned: `Live`-based repaint desynchronized when content taller than the terminal caused the screen to scroll (`CUU` cannot move above row 0, so cursor-relative bookkeeping went stale); Textual's alternate screen fixed that but gave up native scrollback and mouse-wheel scrolling, which the project treats as non-negotiable (feature spec §16).

---

## 1. Scope

This document specifies the interactive terminal UI of `asksh`:

- Streaming reply display in a TTY (append-only commit of finished lines, one repaintable live row), including incremental Markdown rendering.
- Thinking / reasoning-trace display.
- Terminal resize handling and reflow.
- Scrolling behavior of streamed and final content.
- Chat-mode input (prompt_toolkit), prompts, key bindings, and input history.
- Intro panel, exit conditions, and session flow.
- Non-TTY fallback output (stdout redirected to a file or pipe).
- Styles, colors, and UX copy.

Out of scope: HTTP client behavior, system prompts, config file, update check internals (only their *terminal output* is specified), packaging.

---

## 2. Terminology

| Term | Meaning |
| --- | --- |
| **Committed line** | A rendered line that has been printed to the terminal and will never be redrawn, erased, or moved. Once committed it is ordinary scrollback, owned by the terminal. |
| **Live row** | The single row at the cursor that may still be repainted in place — a spinner while waiting, or a dim preview of the current, still-growing line. Repainted with carriage-return + erase-to-end-of-line only; never a cursor-up. |
| **Stable lines** | The prefix of a writer's rendered output that is confirmed to never change again, and is therefore safe to commit (§4.2). |
| **Thinking / reasoning trace** | Model-generated reasoning emitted as a separate stream field (`thinking`) or embedded in content as `<think>…</think>` tags. |

---

## 3. Output modes matrix

`print_assistant_reply` (the single reply-rendering entry point) behaves differently per mode:

| Mode | stdout TTY | Behavior |
| --- | --- | --- |
| stream = true | yes | Append-only streaming (`_stream_reply_tty`): committed Markdown/thinking lines plus one live row (this is the only mode the CLI currently wires up; `stream = True` is hard-coded in `cli.py`). |
| stream = true | no | Raw text drain (`_drain`): content deltas written verbatim, one trailing blank line. No ANSI, no Markdown. |
| stream = false | yes | Blocking request behind a spinner (`console.status`), then thinking + final Markdown, printed once each in the ordinary (non-append-only) way. |
| stream = false | no | Plain `print(reply)`. |

Notes for a reimplementation:

- Non-streaming paths exist and are specified here, but **no CLI flag currently selects them**; every path in `cli.py` uses streaming.
- A single module-level console (`Console(highlight=False)`) is used for all output; the flag disables syntax highlighting of fenced code blocks. A `_PlainSyntaxTheme` (null styles) is also passed as `code_theme` to `Markdown`/`Syntax` so fenced code carries no color even though `highlight=False` alone does not cover Pygments-based fence rendering.

---

## 4. Feature requirements

### 4.1 Streaming reply display (append-only)

**TUI-STREAM-01** — When stdout is a TTY, a streamed reply MUST be rendered append-only: finished lines are printed exactly once and become ordinary terminal scrollback; nothing already printed is ever redrawn, erased, or moved. `_stream_reply_tty` (`render.py`) owns this loop; `AppendOnlyWriter` (`stream_render.py`) computes what is safe to commit on each update.

**TUI-STREAM-02** — Updates MUST be driven by two sources: every chunk received from the model (`AppendOnlyWriter.update` called with the full accumulated text so far), and a background `StreamAnimator` thread ticking at 12 Hz (`interval = 1/12`) so the live row (TUI-STREAM-03) keeps animating between chunks. Both paths are serialized by the animator's lock so main-thread and ticker-thread writes never race.

**TUI-STREAM-03** — At most one row — the **live row** (`LiveRow`) — may be repainted in place, using carriage-return + erase-in-line only (`Control(CARRIAGE_RETURN, (ERASE_IN_LINE, 2))`), never a cursor-up. It shows a `dots` spinner (`bright_cyan`) before any text has arrived for the active writer, otherwise a dim, single-line, non-wrapping, ellipsis-truncated (`width - 1` cells) preview of the current, still-incomplete line. `LiveRow` is shared across writers (thinking and content) so whichever paints next always clears the previous owner's row first.

**TUI-STREAM-04** — There is no transient region to tear down: `finish()` on a writer clears the live row, commits every remaining rendered line (the writer's `_text` re-rendered as a whole, since by then nothing further can arrive to change it), and flushes. The accumulated content **is** the final content; see §4.5.

**TUI-STREAM-05** — Streaming content MUST NOT be force-scrolled or artificially pinned by the program (no "scroll to end" / "tail-follow" logic exists). The terminal's own behavior governs whether the visible window follows new output; this is intentional (feature spec F-52).

### 4.2 Incremental Markdown rendering

**TUI-MD-01** — Each writer accumulates its own full text buffer (content or thinking) and calls `update(text)` with the complete buffer on every delta; `AppendOnlyWriter` re-renders from scratch each tick (GitHub-flavored Markdown for content: headings, lists, fenced and inline code, bold/italic, links, blockquotes, tables; a styled `Text` for thinking).

**TUI-MD-02** — A rendered line is committed only once it is a **stable line** — guaranteed not to change no matter what text arrives afterwards. This is decided in two steps (`AppendOnlyWriter._candidate_source` / `_stable_lines`):

1. **Block-type gate.** The trailing, not-yet-newline-terminated physical line is always excluded (it may still grow). Of what remains, top-level Markdown blocks are classified as *progressive* (`heading_open`, `fence`, `code_block`, `hr`, `blockquote_open`, `bullet_list_open` — types whose already-rendered lines cannot change regardless of what follows in the same block) or *hold-whole* (everything else, notably paragraphs — a bare line can retroactively become a setext heading or a GFM table header — ordered lists — item indent width depends on the final item count — and tables — column widths depend on every row). If the text's last block is hold-whole, that entire block is excluded from the candidate, not just its last line.
2. **One-tick-lagged diff safety net.** The candidate source surviving step 1 is rendered, but a line is only actually committed once its rendered form is identical across two *distinct* candidate-source states (comparing against the previous tick where the candidate source differed from the current one, not the immediately preceding tick, which may be identical mid-word). This catches renderer quirks that step 1's type-based rule doesn't know about — e.g. `rich.syntax.Syntax` always emits at least one code line (plus top/bottom padding) for a fence even with zero complete lines of code, so the first real code line *replaces* a slot rather than appending to one; comparing against the prior distinct candidate reveals that non-append and withholds it for one more tick.

**TUI-MD-03** — Syntax highlighting of code blocks MUST be disabled: `Console(highlight=False)` plus a `_PlainSyntaxTheme` (all styles null) passed as `code_theme`.

**TUI-MD-04** — Markdown wraps at `console.size.width`, which Rich re-queries from the terminal on every access — no resize signal handling is needed (§4.6). A line's wrapping is fixed at the width in effect when it is committed; a resize only affects lines rendered afterwards.

**TUI-MD-05** — Re-rendering the whole buffer per tick MUST keep up with token arrival and the 12 Hz animator tick without visible jank on typical local-model token rates and reply lengths.

### 4.3 Thinking / reasoning trace

**TUI-THINK-01** — The thinking trace is displayed iff `show_thinking` is set OR `think` was not explicitly `False` (i.e. when thinking is enabled — explicitly or auto-detected — the trace is shown by default).

**TUI-THINK-02** — Thinking text MUST be styled `grey50 italic` and prefixed with the literal line `Thinking...` followed by a newline.

**TUI-THINK-03** — Thinking and content each get their own `AppendOnlyWriter` (plain `Text`, markdown=False, for thinking; `Markdown` for content), sharing one `LiveRow`. Thinking deltas accumulate in their own buffer, separate from content deltas, and commit line-by-line exactly like content does (§4.2), just against `Text` instead of `Markdown`. The two writers are sequential, never simultaneously live: while thinking is still arriving and no content has started, the thinking writer owns the live row; the moment visible content appears, the thinking writer is finished (flushing whatever of it is still uncommitted), a blank line is printed, and the content writer takes over the live row for the rest of the stream.

**TUI-THINK-04** — Models that embed reasoning inside the content as `<think>…</think>` tags (case-insensitive, DOTALL regex) MUST have those tags stripped from the content and the extracted text shown in the thinking slot instead. `split_streaming_embedded_thinking` (`client.py`) handles the streaming case: while a `<think>` tag is open, its partial contents route to the thinking display and are held out of the content buffer entirely (so they can never be committed as content and later need retracting — impossible under append-only). If both a dedicated `thinking` stream field and embedded tags exist, the dedicated stream wins (`_display_texts` in `render.py`).

**TUI-THINK-05** — There is no separate "final thinking print": by the time the stream ends, the thinking trace has already been committed line-by-line during streaming, same as content (§4.2). If thinking never transitioned to content (e.g. the reply is thinking-only), `thinking_writer.finish()` in the `_stream_reply_tty` `finally` block flushes whatever of it remains uncommitted.

**TUI-THINK-06** — Thinking deltas received while thinking is not displayed (think disabled) MUST be dropped from the display entirely (they are still received and must not leak into the content).

**TUI-THINK-07** — Thinking is not stored in conversation history; history stores the thinking-stripped content. Thinking blocks of earlier turns are never reprinted for any reason (there is no repaint mechanism at all — §4.6); the originals simply remain wherever they were committed in scrollback.

### 4.4 Waiting states

**TUI-WAIT-01** — Before the first token arrives for the currently active writer (no thinking text and no content text yet), the live row MUST show a `dots` spinner in `bright_cyan`.

**TUI-WAIT-02** — The spinner MUST disappear as soon as the first thinking or content delta arrives (the live row switches to a preview of the growing line instead).

**TUI-WAIT-03** — In non-streaming TTY mode, the blocking request MUST be wrapped in a `console.status` spinner with the same `dots`/`bright_cyan` styling and an empty label.

### 4.5 Completion and scrollback hygiene

**TUI-FINAL-01** — There is no separate "final print": the reply is built up entirely out of commits made during streaming (§4.2, §4.3). When the stream ends, `finish()` is called on whichever writer is still active (content, and thinking if it never handed off — TUI-THINK-05); it clears the live row and commits every line of its buffer not yet committed, since by then nothing further can arrive to change any of it.

**TUI-FINAL-02** — The terminal scrollback MUST contain exactly **one copy** of the reply. This follows structurally from append-only commits (each rendered line is printed at most once, ever) rather than from any end-of-stream deduplication step.

**TUI-FINAL-03** — The reply MUST NOT be cropped. Every committed line stays in scrollback regardless of reply length; there is no "tail-follow" cropping of older lines (see §4.7).

**TUI-FINAL-04** — An empty reply (no content tokens) prints nothing: `finish()` on a writer whose buffer is blank (`not self._text.strip()`) commits zero lines. (Thinking may still have printed lines if present.)

**TUI-FINAL-05** — In streaming non-TTY mode, only content deltas are written to stdout (thinking deltas skipped), followed by exactly one trailing blank line.

### 4.6 Terminal resize handling

There is no resize-specific code at all — no signal handler, no flag, no repair pass. This is a direct consequence of the append-only rule (§2): every escape sequence emitted touches only the row the cursor is currently on (`LiveRow`'s carriage-return + erase-in-line), so there is no multi-row region geometry to keep in sync with the terminal in the first place.

**TUI-RESIZE-01** — `Console.size` re-queries the terminal's actual dimensions on every access (Rich's own implementation, not asksh code); `AppendOnlyWriter._render_lines` reads `self._console.size.width` fresh on every call, so a resize is picked up automatically by the very next render — no `SIGWINCH` handler is installed anywhere in asksh.

**TUI-RESIZE-02** — A resize MUST NOT trigger any redraw, reprint, or repair: lines already committed keep the wrapping they were given (they are the terminal's own scrollback now — see §4.1); only lines rendered after the resize use the new width.

**TUI-RESIZE-03** — A resize mid-stream MUST NOT lose or corrupt any content: the accumulated text buffers (`AppendOnlyWriter._text`) are independent of terminal geometry, so a resize between two chunks changes nothing about what has been received — only how the *next* rendered line is wrapped.

**TUI-RESIZE-04** — While prompt_toolkit owns the terminal (user input), resize/redraw handling is delegated to prompt_toolkit; asksh does nothing special there, exactly as before.

### 4.7 Scrolling behavior

**TUI-SCROLL-01** — Streaming content is ordinary terminal output: it prints downward and, once it exceeds the visible height, scrolls the terminal exactly as `cat`-ing a long file would. asksh has no scroll position of its own to manage and does not attempt to pin the view to the bottom (feature spec F-52).

**TUI-SCROLL-02** — The user MAY use the terminal's native scrollback (mouse wheel, PgUp/PgDn, terminal-emulator scrollbar) at any time — during streaming or after — with no interaction with asksh whatsoever, since asksh never draws above the current row (§2). This is what makes scroll and resize safe by construction, and is the core regression test of this architecture (`tests/test_stream_render.py::test_never_emits_cursor_up` and `tests/test_render.py::test_stream_tty_never_emits_cursor_up` assert no `CUU`/cursor-up escape sequence is ever emitted).

**TUI-SCROLL-03** — Completion (§4.5) leaves the reply in scrollback exactly once and is immediately followed by the next prompt or program exit, with no extra blank region: `finish()` prints only the lines not yet committed, nothing more.

**TUI-SCROLL-04** — The transcript above the currently streaming reply never moves: since nothing is ever redrawn (§2), earlier turns and earlier lines of the current reply are physically incapable of shifting once printed.

### 4.8 Chat input (prompt_toolkit)

**TUI-INPUT-01** — The chat prompt MUST be a true multiline buffer with correct cursor/backspace handling, displayed as `>>> ` in `ansicyan`, preceded by a blank line (the prompt string is `"\n>>> "`).

**TUI-INPUT-02** — Key bindings MUST be:

| Key | Action |
| --- | --- |
| `Enter` (`c-m`) | Submit the buffer immediately. |
| `Alt+Enter` (`escape`, `enter`) | Insert a literal newline into the buffer. |

**TUI-INPUT-03** — The continuation prompt for wrapped/continued lines MUST be `... ` in grey.

**TUI-INPUT-04** — Input history MUST persist across sessions in a file at `$XDG_STATE_HOME/asksh/chat_history` (fallback `~/.local/state/asksh/chat_history`), directory created on demand (prompt_toolkit `FileHistory`).

**TUI-INPUT-05** — `wrap_lines` MUST be enabled; the open-in-editor binding MUST be disabled.

**TUI-INPUT-06** — Submitted input is stripped of leading/trailing whitespace. Empty input MUST be ignored (re-prompt silently).

**TUI-INPUT-07** — Typing `exit` or `quit` (case-insensitive) MUST end the chat with `Goodbye!` in `grey50`.

**TUI-INPUT-08** — `Ctrl-C` or `Ctrl-D` (EOF) at the prompt MUST end the chat with a blank line + `Goodbye!` in `grey50`.

**TUI-INPUT-09** — Piped stdin + chat mode (`cat file | asksh -c "…"`): stdin content is consumed as the first message, then stdin MUST be reopened from `/dev/tty` so the interactive prompt works. The first assistant reply is rendered *before* the first prompt.

**TUI-INPUT-10** — Non-TTY chat input (stdin redirected, stdout TTY): line-based reader with shell-like backslash continuation — a line ending in an odd number of `\` (after stripping trailing spaces/tabs) continues on the next line; backslash pairs collapse to one literal `\`; a lone trailing `\` is dropped. Prompts MUST be plain text (`\n>>> ` / `\n... `) passed to `input()` with no Rich/ANSI markup (keeps line editing in sync).

### 4.9 Intro panel and session flow

**TUI-SESSION-01** — At chat start (stdout TTY), a panel MUST be printed (border `grey42`, padding `(0, 1)`, full width) containing:

```
Chatting with model <model>          ← model name in bright_cyan, rest grey50
- Multiline input: Alt+Enter.
- Type 'exit' or Ctrl-C to quit.
- Without a TTY, use \ at the end of a line, then Enter, to continue on the next line.   ← only when stdin is not a TTY
```

**TUI-SESSION-02** — With stdout not a TTY, the intro is plain text: `Chatting with model '<model>'. Type 'exit' or Ctrl-C to quit.\nEnd a line with \ then Enter to add more lines.\n`.

**TUI-SESSION-03** — User messages appear in the transcript as `>>> ` in `ansicyan` followed by the plain text; assistant messages as committed Markdown. There is no separate echo step for user input: prompt_toolkit's own prompt line, once submitted, already is that transcript entry, and it is never redrawn (§2).

**TUI-SESSION-04** — The conversation (system prompt + turns) is kept in memory per session; it is NOT persisted between sessions (only the *input* history is, per TUI-INPUT-04). Assistant turns are stored thinking-stripped.

### 4.10 Style constants

These are the current values; a reimplementation should reproduce them (or consciously rebrand — but any change is a product decision, not an accident):

| Element | Style |
| --- | --- |
| Prompt `>>> ` | `ansicyan` |
| Continuation `... ` | `grey` (`#808080`) |
| Thinking text | `grey50 italic` |
| Thinking prefix | literal `Thinking...\n` |
| Spinner | `dots`, `bright_cyan` |
| Intro text | `grey50` |
| Intro model name | `bright_cyan` |
| Intro panel border | `grey42` |
| User echo `>>> ` | `cyan` |
| Goodbye | `grey50` |
| Markdown code blocks | no syntax highlighting |

---

## 5. Edge-case behavior matrix

| Scenario | Required behavior |
| --- | --- |
| Resize while streaming | Nothing special happens: no handler fires, no redraw occurs. The next line rendered (whether the live row's preview or a newly committed line) uses `console.size` queried fresh, i.e. the new width. Lines already committed keep their old wrapping. |
| Resize while main thread blocked on HTTP | Irrelevant — there is no periodic repaint tied to geometry; the `StreamAnimator` tick just repaints the live row at whatever width `console.size` reports at that moment. |
| Resize at stream end | No special handling needed; `finish()` renders remaining lines at the width current at that moment. |
| Resize between turns | No-op; the terminal re-wraps already-printed static text on its own, same as any other program's past output. |
| Content taller than viewport mid-stream | No cropping: every committed line stays in scrollback; the terminal scrolls to keep the cursor (and the live row) visible, exactly like any growing command output. |
| Empty reply (no content tokens) | Spinner during wait; `finish()` on an empty buffer commits nothing. |
| Thinking stream + content stream interleaved | Separate buffers, separate writers sharing one live row; thinking commits line-by-line first, then (once content starts) a blank line, then content commits line-by-line. |
| Embedded `<think>` tags in content | Held out of the content buffer entirely while the tag is open (`split_streaming_embedded_thinking`) and routed to the thinking writer instead; not stored in history. |
| Thinking enabled but model yields none | No thinking lines committed; content unaffected. |
| Think disabled (`--think false`) | Thinking deltas dropped from display (no thinking writer is created); content unaffected. |
| `Ctrl-C` during an active stream | The stream aborts cleanly: `KeyboardInterrupt` sets the abort flag, force-closes the HTTP response (`OllamaChatClient.abort_active_stream`), and the `finally` block flushes whatever is committable from each writer's current buffer before returning to the chat prompt. Whatever was already committed (and whatever `finish()` commits on the way out) remains visible in scrollback — it cannot be un-printed — but the partial assistant reply is not added to history. No traceback (F-110). |
| stdout TTY but stdin redirected | prompt_toolkit input path; intro includes the `\` continuation hint (TUI-SESSION-01). |
| stdin piped + `-c` | Piped text becomes the first query; stdin reopened from `/dev/tty`; first reply rendered before first prompt. |
| Piped stdin in one-shot mode | Stdin content wrapped as context (`<stdin>…</stdin>`), one reply, exit. No TUI elements. |

---

## 6. Auxiliary terminal output (startup, non-TUI)

These precede or surround the TUI and affect the terminal experience:

- **Update notice** (`TUI-AUX-01`): at most once per 24 h (cached), a one-line *stderr* notice when a newer PyPI version exists: `Info: new asksh version available: <v> (installed: <v>). Upgrade with: 'pipx upgrade asksh' or 'uv tool upgrade asksh'`. Best-effort, 2 s timeout, never blocks or fails startup. Disable via `--no-update-check` or config.
- **Ollama health/model verification** (`TUI-AUX-02`): before any TUI starts, the server and model are verified; failures print an error to stderr with an emoji-prefixed tag (`❌ [OLLAMA_OFFLINE]`, `❌ [OLLAMA_MODEL_MISSING]`, …) plus troubleshooting hints, and exit with code 1.
- **Errors** (`TUI-AUX-03`): unexpected exceptions print `\nError: <msg>` to stderr and exit 1; known runtime errors print the message without the `Error:` prefix.

---

## 7. Known gaps / risks

1. ~~Ctrl-C during streaming terminates the process with a raw traceback.~~ **Fixed** (F-110): `_stream_reply_tty` catches `KeyboardInterrupt`, aborts the client stream, and returns cleanly through its `finally` block.
2. ~~Rich-internals coupling: monkey-patches of `rich.live_render.LiveRender.__rich_console__`, `Live` private attributes, and a SIGWINCH handler.~~ **Removed**: `_ResizeSafeLive`, `_patch_live_render_crop_above`, and all SIGWINCH machinery are gone. `AppendOnlyWriter`/`LiveRow` use only public `Console` methods (`render_lines`, `control`, `print`).
3. ~~A Textual alternate-screen overlay (`stream.py`) was tried as a second approach.~~ **Removed**: it fixed resize/scroll desync by giving up native scrollback and mouse-wheel scrolling, which conflicts with feature spec §16; the append-only model achieves the same safety without that trade-off.
4. **Full-buffer Markdown re-parse per tick** is O(buffer); accepted per TUI-MD-05. The 12 Hz animator tick bounds how often this happens when no new chunk has arrived; each chunk also triggers one re-parse.
5. **Progressive-block commits lag by one tick** (`_stable_lines`'s distinct-source diff, TUI-MD-02 step 2): a line that could have committed immediately instead waits for the next tick where the candidate source has changed. Invisible in practice at typical token rates; a documented trade-off for correctness over minimal latency.
6. **No non-streaming mode is reachable from the CLI** (`stream=True` hard-coded); the non-streaming paths are dead code unless a flag is added.
7. Thinking blocks of prior turns can never be restored (history is thinking-stripped, and there is no repaint mechanism of any kind). Accepted; the originals survive wherever they were committed in scrollback.

---

## 8. Current implementation map (traceability for reimplementers)

| Concern | Where today | Notes |
| --- | --- | --- |
| Append-only commit + live row | `AppendOnlyWriter`, `LiveRow` in `stream_render.py` | `LiveRow` is shared so ownership can hand off between writers; `AppendOnlyWriter` never emits a cursor-up, only carriage-return + erase-in-line for the live row and plain `print`/`Segments` for committed lines. |
| Stability analysis | `AppendOnlyWriter._candidate_source` / `_stable_lines` in `stream_render.py`; `_top_level_blocks` (markdown-it parse) and `_PROGRESSIVE_BLOCK_TYPES` | See TUI-MD-02 for the two-step algorithm (block-type gate + one-tick-lagged diff). |
| Streaming orchestration | `_stream_reply_tty` in `render.py` | Creates the two writers + `StreamAnimator`, feeds chunks, handles the thinking→content handoff, catches `KeyboardInterrupt`. |
| Background animation | `StreamAnimator` in `stream_render.py` | 12 Hz thread re-invokes `update` on whichever writer is "active"; a lock serializes it against main-thread updates. |
| Ctrl-C abort | `KeyboardInterrupt` handler in `_stream_reply_tty` + `OllamaChatClient.abort_active_stream` (closes the HTTP response) + `abort` `Event` checked in `stream_message` | On abort the partial assistant reply is not added to history; the `finally` block still calls `finish()` on the writers so whatever is bufferred lands in scrollback. |
| Resize handling | None — `Console.size` re-queries the terminal on every access | No signal handler, no flag, no repair pass exists anywhere in the codebase. |
| Thinking split | `split_embedded_thinking` / `split_streaming_embedded_thinking` in `client.py`; display precedence in `_display_texts` (`render.py`) | Embedded thinking is also stripped in the client before history is updated; the streaming variant holds an open `<think>` tag's contents out of the content buffer entirely. |
| Non-TTY drain | `_drain` in `render.py`: writes non-thinking deltas verbatim + trailing newline | — |
| Chat input | `_read_chat_user_input_prompt_toolkit` (TTY) / `_read_chat_user_input_line_based` (non-TTY) in `chat.py` | prompt_toolkit session is a module-level singleton; history path per XDG. User messages need no explicit "echo" step — prompt_toolkit's own input line is itself the terminal output, and it is never touched again. |

---

## 9. Verification

### Automated ([tests/test_stream_render.py](../tests/test_stream_render.py), [tests/test_render.py](../tests/test_render.py), [tests/test_client.py](../tests/test_client.py))

- `tests/term_helpers.py` provides `make_console` (an in-memory `Console` forced into non-dumb TTY mode, isolated from the environment) and `simulate_terminal` (replays raw ANSI output — CR, erase-in-line, newlines — into the resulting visible screen), used throughout both suites.
- `AppendOnlyWriter`/`LiveRow` (`test_stream_render.py`): **no cursor-up sequence is ever emitted**, the regression test for this whole class of bug; feeding a document one character at a time yields the same final visible text as feeding it in one chunk; ordered-list renumbering and tables stay withheld until complete, then appear correctly; a code fence streams progressively line-by-line before its closing fence arrives; the spinner shows before the first delta and is replaced by a preview after; `finish()` flushes whatever is still held back; `LiveRow` hands off cleanly between two writers.
- Render (`test_render.py`): TTY streaming produces the expected committed Markdown with no cursor-up sequences; thinking commits in grey italic under `Thinking...` when enabled and is absent when disabled; embedded `<think>` tags are handled correctly whether the tag closes before or after content starts; `Ctrl-C` mid-stream leaves the partial reply visible in scrollback (`test_stream_tty_abort_keeps_partial_reply_visible`) without adding it to history; non-TTY drains verbatim without thinking, followed by one blank line; non-streaming TTY/non-TTY paths are unaffected by this rewrite.
- Client (`test_client.py`): `split_streaming_embedded_thinking` covers an unterminated `<think>` tag, a tag split across chunk boundaries (including a partial opening tag like `...<thi`), and interaction with an already-completed `<think>...</think>` pair earlier in the same buffer.

### Manual checklist (for validating a new implementation)

1. Start chat; stream a reply longer than the terminal height → content scrolls the terminal naturally as it grows; mouse-wheel scroll up mid-stream never disturbs anything (asksh keeps printing below, unaware of the scroll position); on completion the full reply is present exactly once in scrollback.
2. Resize the window smaller and larger mid-stream → no duplicated/garbled text; content printed after the resize re-wraps; content printed before it is untouched; intro + prior turns are undisturbed (they were never going to move).
3. Resize while the model is producing no output (waiting state, spinner) → no corruption; the spinner keeps animating at the new width on its next tick.
4. Resize at the exact moment the stream ends → clean transition, no residue.
5. `Enter` submits; `Alt+Enter` inserts a newline; `↑` recalls previous inputs; input history survives a restart.
6. Ctrl-C / Ctrl-D at the prompt → `Goodbye!`; empty input re-prompts; `exit`/`QUIT` exits.
7. `cat file | asksh -c "…"` → file content sent as first message, prompt still interactive.
8. With `--think` on a thinking model: grey italic thinking commits first, then a blank line, then content, both live and in the end result (there is no separate "final" thinking print — see TUI-THINK-05). With `--think false`: no thinking visible anywhere.
9. `asksh "…" > out.txt` → plain text, no ANSI, one trailing newline.
10. One-shot + TTY → only the reply (no intro panel, no prompt).
11. Ctrl-C mid-stream → clean abort, back to the prompt, no traceback, partial reply stays visible but isn't added to history.

---

## 10. Non-goals

- A persistent full-screen chat UI, a custom pager, or an alternate screen: streamed and final content live entirely in the terminal's own scrollback, exactly once each.
- Syntax-highlighted code blocks (explicitly disabled).
- Persisting conversation history across sessions (only input history persists).
- Any mechanism that repaints, redraws, or moves previously-printed content (including for resize) — see §4.6.
