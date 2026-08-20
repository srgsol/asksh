# asksh — Terminal User Interface Specification

**Status:** Baseline document for the current implementation. The binding, technology-agnostic requirements live in [tui-feature-spec.md](tui-feature-spec.md); this document records the implementation-specific behavior (and its history).
**Source of truth:** [src/asksh/stream_render.py](../src/asksh/stream_render.py) (`AppendOnlyWriter`, `PreviewWriter`, `LiveRow`, `StreamAnimator`), [src/asksh/render.py](../src/asksh/render.py) (render-style dispatch, streaming orchestration, non-streaming and non-TTY output), [src/asksh/config.py](../src/asksh/config.py) (`RenderStyle`, per-mode config keys), [src/asksh/chat.py](../src/asksh/chat.py), [tests/test_stream_render.py](../tests/test_stream_render.py), [tests/test_render.py](../tests/test_render.py).
**Stack today:** Rich ≥ 15.0.0 (append-only streaming, `Live`, Markdown, static printing), prompt_toolkit ≥ 3.0.43 (chat input), requests (Ollama HTTP). No Textual, no alternate screen, no signal handling.

The word **MUST** below describes behavior the current implementation exhibits and that a reimplementation is expected to reproduce, unless a section is explicitly marked *gap*. Sections marked *superseded* describe earlier implementations, kept for history. Three approaches were tried and abandoned or narrowed before landing here:

1. Rich `Live` with cursor-relative repaint of the *whole* reply — desynchronized when content taller than the terminal caused the screen to scroll (`CUU` cannot move above row 0, so cursor-relative bookkeeping went stale).
2. A Textual alternate-screen overlay — fixed resize/scroll desync by giving up native scrollback and mouse-wheel scrolling, which the project treats as non-negotiable (feature spec §16).
3. An append-only renderer that incrementally parsed and committed "stable" Markdown lines as they streamed (holding back tables/lists/fences until provably final) — correct, but the withholding logic added real complexity and always rendered Markdown live, with no way to get a plain, always-append-only stream. **This is also gone.**

Today: Markdown formatting is **opt-in and applied at most twice per reply, never incrementally against a growing buffer** — once as a single final print (styles `markdown`, `post_markdown`), or continuously against the *whole* buffer as a `Live` preview with a final print (`live_markdown`, which knowingly reintroduces approach 1's desync risk as an opt-in trade-off). The default, append-only, plain-text streamer (`AppendOnlyWriter`) never parses Markdown at all.

---

## 1. Scope

This document specifies the interactive terminal UI of `asksh`:

- The four render styles (`text`, `markdown`, `post_markdown`, `live_markdown`) and how each mode (one-shot, explain, chat) selects one independently via config.
- Streaming reply display in a TTY: append-only commit of finished lines for the streamed styles, plus the one `Live`-based style.
- Thinking / reasoning-trace display.
- Terminal resize handling and reflow.
- Scrolling behavior of streamed and final content.
- Chat-mode input (prompt_toolkit), prompts, key bindings, and input history.
- Intro panel, exit conditions, and session flow.
- Non-TTY fallback output (stdout redirected to a file or pipe).
- Styles, colors, and UX copy.

Out of scope: HTTP client behavior, system prompts, update check internals (only their *terminal output* is specified), packaging.

---

## 2. Terminology

| Term | Meaning |
| --- | --- |
| **Render style** | One of `text`, `markdown`, `post_markdown`, `live_markdown` (§3). Selected per mode via `ONESHOT_RENDER` / `EXPLAIN_RENDER` / `CHAT_RENDER` in `config.toml` (`src/asksh/config.py`). |
| **Committed line** | A rendered line that has been printed to the terminal and will never be redrawn, erased, or moved. Once committed it is ordinary scrollback, owned by the terminal. |
| **Live row** | The single row at the cursor that may still be repainted in place — a spinner while waiting, or a dim preview of the current, still-growing line. Repainted with carriage-return + erase-to-end-of-line only; never a cursor-up. Used by `text`, `post_markdown`, and `markdown`. |
| **Live preview** | `live_markdown`'s cursor-relative, full-buffer Markdown region, repainted via Rich `Live` on every update. Unlike the live row, this can span many lines and moves the cursor up to repaint. |
| **Thinking / reasoning trace** | Model-generated reasoning emitted as a separate stream field (`thinking`) or embedded in content as `<think>…</think>` tags. |

---

## 3. Render styles

`print_assistant_reply` (the single reply-rendering entry point, `render.py`) takes a `render_style: RenderStyle` keyword (default `"text"`); `cli.py` resolves it per mode from config before calling in. `RenderStyle = Literal["text", "markdown", "post_markdown", "live_markdown"]` (`config.py`).

| Style | Streaming (TTY) | On completion (TTY) | Non-streaming (TTY) |
| --- | --- | --- | --- |
| `text` | Append-only plain text (`AppendOnlyWriter`, no Markdown parsing ever). | Nothing further. | `console.print(Text(reply))`. |
| `markdown` | Nothing of the answer shown — only a wait indicator (`PreviewWriter`: spinner, then a dim last-line preview; never commits). | One `console.print(Markdown(...))`. | One `console.print(Markdown(...))`. |
| `post_markdown` | Same as `text`. | A second `console.print(Markdown(...))`, after a blank line. | One `console.print(Markdown(...))`. |
| `live_markdown` | Rich `Live` repaints the whole buffer as `Markdown` on every chunk (§3.2). | Live region erased, then one `console.print(Markdown(...))`. | One `console.print(Markdown(...))`. |

Notes:

- Config keys are config-file-only; there is no CLI flag. Defaults: `ONESHOT_RENDER = "markdown"`, `EXPLAIN_RENDER = "text"`, `CHAT_RENDER = "text"` (`cli.py::parse_args`, before config overrides are applied). An invalid value is ignored with a warning (`config.py::load_user_config`) and the mode's default is used.
- Thinking display (§4.3) is identical across `text`, `markdown`, and `post_markdown`: always append-only, committed progressively, independent of what the content is doing. `live_markdown` folds thinking into the same `Live` region instead (§3.2).
- Non-streaming TTY output (`stream=False`) collapses to two cases regardless of style: `text` prints `Text(reply)`; the other three print `Markdown(reply)` once. There is no "post" copy without a stream to follow.
- Non-TTY output (`_drain`) is completely unaffected by `render_style`: raw content deltas verbatim, then one trailing blank line, no Markdown, ever (F-80).

### 3.1 The append-only styles (`text`, `post_markdown`, and thinking for all four)

**TUI-STREAM-01** — When stdout is a TTY, `text`/`post_markdown` content and thinking (in every style but `live_markdown`) render append-only: finished physical lines are printed exactly once and become ordinary terminal scrollback; nothing already printed is ever redrawn, erased, or moved. `_stream_reply_tty` (`render.py`) owns this loop; `AppendOnlyWriter` (`stream_render.py`) decides what is safe to commit on each update.

**TUI-STREAM-02** — A physical source line (terminated by `\n`) renders to the same wrapped output lines no matter what arrives afterwards — plain `Text` wraps each line independently, unlike Markdown constructs whose shape can depend on later text. So a line is committed the moment it is complete (`AppendOnlyWriter._candidate_source`: everything up to, but excluding, the trailing not-yet-newline-terminated line); only that trailing line is ever held back on the live row.

**TUI-STREAM-03** — Updates are driven by two sources: every chunk received from the model (`AppendOnlyWriter.update` / `PreviewWriter.update`, called with the full accumulated text so far) and a background `StreamAnimator` thread ticking at 12 Hz (`interval = 1/12`) so the live row (TUI-STREAM-04) keeps animating between chunks. Both paths are serialized by the animator's lock so main-thread and ticker-thread writes never race. `StreamAnimator` works with either writer type (duck-typed `update`/`finish`).

**TUI-STREAM-04** — At most one row — the **live row** (`LiveRow`) — may be repainted in place, using carriage-return + erase-in-line only (`Control(CARRIAGE_RETURN, (ERASE_IN_LINE, 2))`), never a cursor-up. It shows a `dots` spinner (`bright_cyan`) before any text has arrived for the active writer, otherwise a dim, single-line, non-wrapping, ellipsis-truncated (`width - 1` cells) preview of the current, still-incomplete line (`stream_render._preview_line`, shared by both writer types). `LiveRow` is shared across writers (thinking and content) so whichever paints next always clears the previous owner's row first.

**TUI-STREAM-05** — `finish()` semantics differ by writer:

- `AppendOnlyWriter.finish()` (content in `text`/`post_markdown`; thinking in every style but `live_markdown`) clears the live row and commits every remaining rendered line of its buffer — the accumulated content *is* the final content, nothing more happens for `text`.
- `PreviewWriter.finish()` (content in `markdown`) only clears the live row; it never commits anything. `_stream_reply_tty` then prints the one Markdown copy separately (§3.3).

**TUI-STREAM-06** — Streaming content MUST NOT be force-scrolled or artificially pinned by the program (no "scroll to end" / "tail-follow" logic exists) for any style. The terminal's own behavior governs whether the visible window follows new output; this is intentional (feature spec F-52). `live_markdown`'s `Live` uses `transient=True` and never forces a scroll position either, though its cursor-relative repaint is itself the one thing in this codebase that touches rows above the current line (§3.2).

### 3.2 `live_markdown`

**TUI-LIVE-01** — `_stream_reply_tty_live_markdown` (`render.py`) opens a Rich `Live(transient=True, refresh_per_second=12, vertical_overflow="crop_above")` around the whole stream. On every chunk it calls `live.update(...)` with a renderable built by `_live_markdown_display`: the thinking trace (if shown) rendered as styled `Text`, a blank-line spacer, and `Markdown(visible_content)` — grouped with `rich.console.Group` — or just a `Spinner` before anything has arrived.

**TUI-LIVE-02** — Rich's `Live` has no built-in "keep the newest lines visible when taller than the terminal" overflow mode (`crop`/`ellipsis`/`visible` only, and `crop`/`ellipsis` keep the *top*). `_patch_live_render_crop_above` monkey-patches `rich.live_render.LiveRender.__rich_console__` to add a `"crop_above"` mode: when the rendered preview is taller than the terminal, it keeps the last `options.size.height` lines instead of the first. This patch is installed once, lazily, only when `live_markdown` is used; it is not used by, and has no effect on, the other three styles.

**TUI-LIVE-03** — This is a deliberate, documented exception to append-only rendering (F-02-LIVE): `Live`'s repaint is cursor-relative (`position_cursor()` / `restore_cursor()` move the cursor up and erase). If the terminal is resized or scrolled while the preview is on screen, that cursor-relative bookkeeping can desync from reality, visibly corrupting the *in-progress* preview — the same failure mode approach 1 in §0 hit for the whole reply. `live_markdown` accepts this for the in-progress view only; see TUI-LIVE-04 for why the final result is still clean.

**TUI-LIVE-04** — On a clean stream end, exiting the `with Live(...)` block calls `Live.stop()`, which performs one last full (non-cropped) refresh and then emits the cursor-up/erase-in-line sequence to remove the whole preview region, leaving no residue. `_stream_reply_tty_live_markdown` then prints the thinking trace (if any, via `_print_thinking`) and exactly one `Markdown(visible_content)` — an ordinary, non-`Live`, append-only print, so *this* copy carries none of `Live`'s desync risk.

**TUI-LIVE-05** — On `KeyboardInterrupt` (Ctrl-C) raised while iterating the generator inside the `with Live(...)` block, the exception propagates out of the block, so `Live.__exit__` → `Live.stop()` still runs and erases the preview (transient) before the `except KeyboardInterrupt` clause sets `abort` and calls `client.abort_active_stream()`. No final Markdown print happens for an aborted `live_markdown` reply — nothing of the answer is left visible, matching `markdown`'s abort behavior (§3.3) rather than the append-only styles', which do leave partial text.

### 3.3 `markdown`

**TUI-MDONLY-01** — Content uses `PreviewWriter`, not `AppendOnlyWriter`: `update(text)` only repaints the live row (spinner, then a dim last-physical-line preview via the same `_preview_line` helper as TUI-STREAM-04) and never commits anything to scrollback. Thinking (if shown) still uses a regular `AppendOnlyWriter` and commits progressively, exactly as in `text`/`post_markdown` — only the *content* is deferred.

**TUI-MDONLY-02** — On a clean finish, `content_writer.finish()` (a `PreviewWriter`) just clears the live row. `_stream_reply_tty` then prints `console.print(Markdown(visible_content, code_theme=plain_code_theme))` once — no blank-line separator beyond whatever the thinking→content transition already printed (there is only one copy here, unlike `post_markdown`'s deliberate second copy).

**TUI-MDONLY-03** — On `KeyboardInterrupt`, nothing has ever been committed for the content (`PreviewWriter` never commits), so aborting leaves no trace of the answer at all; the `finally` block's `content_writer.finish()` still runs (clearing the live row) but the function returns before the post-loop Markdown print, so that print never happens.

### 3.4 `post_markdown`

**TUI-POSTMD-01** — Content uses a plain `AppendOnlyWriter`, identical in every respect to `text` while streaming (§3.1). The only difference is what happens after `finish()`: `_stream_reply_tty` prints a blank line then `console.print(Markdown(visible_content, code_theme=plain_code_theme))` — a second, separately-formatted copy of the same reply, directly below the first.

**TUI-POSTMD-02** — On `KeyboardInterrupt`, whatever plain text had already streamed remains (append-only, same as `text`'s abort behavior), and the function returns before the post-loop block, so the Markdown copy is never printed for an aborted reply.

---

## 4. Feature requirements

### 4.1 Syntax highlighting and Markdown formatting details

**TUI-MD-01** — Wherever Markdown is rendered (`markdown`/`post_markdown`'s final print, `live_markdown`'s preview and final print), it uses GitHub-flavored Markdown: headings, lists, fenced and inline code, bold/italic, links, blockquotes, tables. It is always rendered from a single, complete (or, for `live_markdown`'s in-progress preview, currently-accumulated) text buffer via `rich.markdown.Markdown(text, code_theme=plain_code_theme)` — a single, from-scratch parse each time it is called, never an incremental line-by-line commit.

**TUI-MD-02** — Syntax highlighting of code blocks MUST be disabled: `Console(highlight=False)` plus a `_PlainSyntaxTheme` (all styles null) passed as `code_theme` to every `Markdown(...)` call.

**TUI-MD-03** — Markdown (and plain-text) wraps at `console.size.width`, which Rich re-queries from the terminal on every access — no resize signal handling is needed (§4.6). A line's wrapping is fixed at the width in effect when it is committed (or, for `live_markdown`'s live preview, at the width in effect on the tick that rendered it — it can and does re-wrap on later ticks, since it's a full re-render each time); a resize only affects lines rendered afterwards for the append-only styles.

**TUI-MD-04** — Re-rendering the whole buffer per tick (`live_markdown` only) MUST keep up with token arrival and the 12 Hz `Live` refresh rate without visible jank on typical local-model token rates and reply lengths.

### 4.2 Thinking / reasoning trace

**TUI-THINK-01** — The thinking trace is displayed iff `show_thinking` is set OR `think` was not explicitly `False` (i.e. when thinking is enabled — explicitly or auto-detected — the trace is shown by default). This is independent of render style.

**TUI-THINK-02** — Thinking text MUST be styled `grey50 italic` and prefixed with the literal line `Thinking...` followed by a newline (`_format_thinking`, shared by every style, including as part of `live_markdown`'s `Live` group).

**TUI-THINK-03** — For `text`, `post_markdown`, and `markdown`: thinking and content each get their own writer (`AppendOnlyWriter` for thinking always; `AppendOnlyWriter` or `PreviewWriter` for content depending on style), sharing one `LiveRow`. Thinking deltas accumulate in their own buffer, separate from content deltas, and commit line-by-line exactly like `text` content does (§3.1), just against styled `Text` instead of (deferred) Markdown. The two writers are sequential, never simultaneously live: while thinking is still arriving and no content has started, the thinking writer owns the live row; the moment visible content appears, the thinking writer is finished (flushing whatever of it is still uncommitted), a blank line is printed, and the content writer takes over the live row for the rest of the stream. For `live_markdown`, there is no separate writer at all: `_live_markdown_display` folds the thinking trace and the content into one `Live`-managed `Group`.

**TUI-THINK-04** — Models that embed reasoning inside the content as `<think>…</think>` tags (case-insensitive, DOTALL regex) MUST have those tags stripped from the content and the extracted text shown in the thinking slot instead. `split_streaming_embedded_thinking` (`client.py`) handles the streaming case: while a `<think>` tag is open, its partial contents route to the thinking display and are held out of the content buffer entirely (so they can never be committed as content and later need retracting). If both a dedicated `thinking` stream field and embedded tags exist, the dedicated stream wins (`_display_texts` in `render.py`, shared by every style).

**TUI-THINK-05** — For the append-only styles, there is no separate "final thinking print": by the time the stream ends, the thinking trace has already been committed line-by-line during streaming (§3.1), same as `text` content. If thinking never transitioned to content (e.g. the reply is thinking-only), `thinking_writer.finish()` in the `_stream_reply_tty` `finally` block flushes whatever of it remains uncommitted. For `live_markdown`, the trace is part of the one `Live` region and is printed once, alongside the content, at the end (TUI-LIVE-04).

**TUI-THINK-06** — Thinking deltas received while thinking is not displayed (think disabled) MUST be dropped from the display entirely (they are still received and must not leak into the content), in every style.

**TUI-THINK-07** — Thinking is not stored in conversation history; history stores the thinking-stripped content. Thinking blocks of earlier turns are never reprinted for any reason; the originals simply remain wherever they were committed in scrollback (or, having never been committed for an aborted `live_markdown`/`markdown` reply, nowhere at all).

### 4.3 Waiting states

**TUI-WAIT-01** — Before the first token arrives for the currently active writer (no thinking text and no content text yet), the live row (or, for `live_markdown`, the `Live` region) MUST show a `dots` spinner in `bright_cyan`.

**TUI-WAIT-02** — The spinner MUST disappear as soon as the first thinking or content delta arrives (the live row / `Live` region switches to a preview of the growing line, or — for `markdown` specifically — stays a preview-only row for content until the stream ends).

**TUI-WAIT-03** — In non-streaming TTY mode (any render style), the blocking request MUST be wrapped in a `console.status` spinner with the same `dots`/`bright_cyan` styling and an empty label.

### 4.4 Completion and scrollback hygiene

**TUI-FINAL-01** — For `text`: there is no separate "final print" beyond what streaming already committed; `finish()` on the content writer (and the thinking writer, if it never handed off) clears the live row and commits every line of its buffer not yet committed, since by then nothing further can arrive to change any of it.

**TUI-FINAL-02** — For `markdown`, `post_markdown`, and `live_markdown`: exactly one Markdown-formatted print happens after streaming (or, for `post_markdown`, in addition to the streamed plain-text copy) — never more than one such print per style, and never a leftover half-updated fragment from the streaming phase.

**TUI-FINAL-03** — The terminal scrollback MUST contain exactly the number of copies the active style promises (§3): one for `text`/`markdown`/`live_markdown`, two for `post_markdown`. This follows structurally from append-only commits (each rendered line is printed at most once, ever) plus each style printing its one designated Markdown copy at most once.

**TUI-FINAL-04** — The reply MUST NOT be cropped. Every committed line (and each Markdown print, once made) stays in scrollback regardless of reply length; there is no "tail-follow" cropping of older lines (see §4.7). `live_markdown`'s *in-progress* preview alone may be height-limited on screen while streaming (`crop_above`, TUI-LIVE-02) — that limit disappears the moment the final, uncropped print replaces it.

**TUI-FINAL-05** — An empty reply (no content tokens) prints nothing of the answer, in every style: an empty `AppendOnlyWriter`/`PreviewWriter` buffer commits/prints zero lines, and the post-loop Markdown print (for `markdown`/`post_markdown`) and the `live_markdown` final print are both skipped when `visible_content` is empty. (Thinking may still have printed lines if present.)

**TUI-FINAL-06** — In streaming non-TTY mode, only content deltas are written to stdout (thinking deltas skipped), followed by exactly one trailing blank line, regardless of `render_style` (F-80).

### 4.5 Terminal resize handling

There is no resize-specific code for the append-only styles — no signal handler, no flag, no repair pass. This is a direct consequence of the append-only rule (§2): every escape sequence emitted touches only the row the cursor is currently on (`LiveRow`'s carriage-return + erase-in-line), so there is no multi-row region geometry to keep in sync with the terminal in the first place. `live_markdown` is the one style where a resize can visibly affect the *in-progress* preview (TUI-LIVE-03); its final print is unaffected, going through the same resize-agnostic path as the other styles' prints.

**TUI-RESIZE-01** — `Console.size` re-queries the terminal's actual dimensions on every access (Rich's own implementation, not asksh code); `AppendOnlyWriter._render_lines` / `_preview_line` read `self._console.size.width` (or `console.size.width`) fresh on every call, so a resize is picked up automatically by the very next render — no `SIGWINCH` handler is installed anywhere in asksh.

**TUI-RESIZE-02** — For the append-only styles, a resize MUST NOT trigger any redraw, reprint, or repair: lines already committed keep the wrapping they were given (they are the terminal's own scrollback now — see §3.1); only lines rendered after the resize use the new width. For `live_markdown`, each `Live` refresh already re-renders the whole buffer, so a resize simply changes the width used by the *next* refresh; what's already been erased/redrawn by `Live` itself is Rich's concern, not asksh's, and is where TUI-LIVE-03's desync risk lives.

**TUI-RESIZE-03** — A resize mid-stream MUST NOT lose or corrupt any content: the accumulated text buffers (`AppendOnlyWriter._text` / the content/thinking strings in `_stream_reply_tty(_live_markdown)`) are independent of terminal geometry, so a resize between two chunks changes nothing about what has been received — only how the *next* rendered line (or, for `live_markdown`, the next full-buffer repaint) is wrapped.

**TUI-RESIZE-04** — While prompt_toolkit owns the terminal (user input), resize/redraw handling is delegated to prompt_toolkit; asksh does nothing special there, exactly as before.

### 4.6 Scrolling behavior

**TUI-SCROLL-01** — For `text`, `post_markdown`, and `markdown`: streaming content is ordinary terminal output — it prints downward and, once it exceeds the visible height, scrolls the terminal exactly as `cat`-ing a long file would. asksh has no scroll position of its own to manage and does not attempt to pin the view to the bottom (feature spec F-52). `live_markdown`'s `Live` region is the one part of the UI that repaints in place; once it finishes and is erased, the final print behaves like the other styles.

**TUI-SCROLL-02** — The user MAY use the terminal's native scrollback (mouse wheel, PgUp/PgDn, terminal-emulator scrollbar) at any time — during streaming or after — with no interaction with asksh whatsoever, for `text`/`post_markdown`/`markdown`, since asksh never draws above the current row for those styles (§2). This is what makes scroll and resize safe by construction for those three styles, and is the core regression test of this architecture (`tests/test_stream_render.py` and `tests/test_render.py` assert no `CUU`/cursor-up escape sequence is ever emitted for `text`, `markdown`, or `post_markdown`). `live_markdown` is exempted from this guarantee by design (TUI-LIVE-03); its tests instead assert exactly one Markdown copy survives in the replayed screen once the `Live` region is torn down.

**TUI-SCROLL-03** — Completion (§4.4) leaves the reply in scrollback with exactly the number of copies its style promises, immediately followed by the next prompt or program exit, with no extra blank region.

**TUI-SCROLL-04** — For the append-only styles, the transcript above the currently streaming reply never moves: since nothing is ever redrawn (§2), earlier turns and earlier lines of the current reply are physically incapable of shifting once printed.

### 4.7 Chat input (prompt_toolkit)

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

**TUI-INPUT-09** — Piped stdin + chat mode (`cat file | asksh -c "…"`): stdin content is consumed as the first message, then stdin MUST be reopened from `/dev/tty` so the interactive prompt works. The first assistant reply is rendered (per `CHAT_RENDER`) *before* the first prompt.

**TUI-INPUT-10** — Non-TTY chat input (stdin redirected, stdout TTY): line-based reader with shell-like backslash continuation — a line ending in an odd number of `\` (after stripping trailing spaces/tabs) continues on the next line; backslash pairs collapse to one literal `\`; a lone trailing `\` is dropped. Prompts MUST be plain text (`\n>>> ` / `\n... `) passed to `input()` with no Rich/ANSI markup (keeps line editing in sync).

### 4.8 Intro panel and session flow

**TUI-SESSION-01** — At chat start (stdout TTY), a panel MUST be printed (border `grey42`, padding `(0, 1)`, full width) containing:

```
Chatting with model <model>          ← model name in bright_cyan, rest grey50
- Multiline input: Alt+Enter.
- Type 'exit' or Ctrl-C to quit.
- Without a TTY, use \ at the end of a line, then Enter, to continue on the next line.   ← only when stdin is not a TTY
```

**TUI-SESSION-02** — With stdout not a TTY, the intro is plain text: `Chatting with model '<model>'. Type 'exit' or Ctrl-C to quit.\nEnd a line with \ then Enter to add more lines.\n`.

**TUI-SESSION-03** — User messages appear in the transcript as `>>> ` in `ansicyan` followed by the plain text; assistant messages per the active `CHAT_RENDER` style. There is no separate echo step for user input: prompt_toolkit's own prompt line, once submitted, already is that transcript entry, and it is never redrawn (§2).

**TUI-SESSION-04** — The conversation (system prompt + turns) is kept in memory per session; it is NOT persisted between sessions (only the *input* history is, per TUI-INPUT-04). Assistant turns are stored thinking-stripped.

### 4.9 Style constants

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
| Resize while streaming (`text`/`post_markdown`/`markdown`) | Nothing special happens: no handler fires, no redraw occurs. The next line rendered (whether the live row's preview or a newly committed line) uses `console.size` queried fresh, i.e. the new width. Lines already committed keep their old wrapping. |
| Resize while streaming (`live_markdown`) | The next `Live` refresh re-renders at the new width; a resize or scroll at this moment MAY visibly disturb the in-progress preview (TUI-LIVE-03) — accepted trade-off. |
| Resize while main thread blocked on HTTP | Irrelevant for the append-only styles — there is no periodic repaint tied to geometry; the `StreamAnimator` tick just repaints the live row at whatever width `console.size` reports at that moment. For `live_markdown`, the `Live` auto-refresh thread ticks independently and repaints at the current width. |
| Resize at stream end | No special handling needed for any style; the final print (or `finish()`) renders at the width current at that moment. |
| Resize between turns | No-op; the terminal re-wraps already-printed static text on its own, same as any other program's past output. |
| Content taller than viewport mid-stream (`text`/`post_markdown`/`markdown`) | No cropping: every committed line stays in scrollback; the terminal scrolls to keep the cursor (and the live row) visible, exactly like any growing command output. |
| In-progress preview taller than the terminal (`live_markdown`) | `crop_above` keeps the newest lines visible instead of the oldest (TUI-LIVE-02); this cropping is purely cosmetic for the in-progress view and has no effect on the final, uncropped print. |
| Empty reply (no content tokens) | Spinner during wait, for every style; nothing of the answer is printed once the stream ends, in any style (thinking, if any, is unaffected). |
| Thinking stream + content stream interleaved (`text`/`post_markdown`/`markdown`) | Separate buffers, separate writers sharing one live row; thinking commits line-by-line first, then (once content starts) a blank line, then content commits line-by-line (or, for `markdown`, content stays preview-only until the end). |
| Thinking stream + content stream interleaved (`live_markdown`) | One `Live` region shows both, grouped, with a blank-line spacer once content starts; both are captured in the one final print. |
| Embedded `<think>` tags in content | Held out of the content buffer entirely while the tag is open (`split_streaming_embedded_thinking`) and routed to the thinking display instead; not stored in history. Applies identically regardless of render style. |
| Thinking enabled but model yields none | No thinking lines committed; content unaffected, in every style. |
| Think disabled (`--think false`) | Thinking deltas dropped from display (no thinking writer/slot used); content unaffected, in every style. |
| `Ctrl-C` during an active stream (`text`/`post_markdown`) | Whatever plain text had already streamed remains visible; the pending Markdown copy (for `post_markdown`) is skipped. Not added to history. No traceback. |
| `Ctrl-C` during an active stream (`markdown`) | Nothing of the answer was ever committed (`PreviewWriter` never commits); nothing is left visible, and the pending Markdown print is skipped. Not added to history. No traceback. |
| `Ctrl-C` during an active stream (`live_markdown`) | `Live.stop()` (transient) erases the in-progress preview as the exception propagates out of the `with` block; no final Markdown print happens. Not added to history. No traceback. |
| stdout TTY but stdin redirected | prompt_toolkit input path; intro includes the `\` continuation hint (TUI-SESSION-01). |
| stdin piped + `-c` | Piped text becomes the first query; stdin reopened from `/dev/tty`; first reply rendered (per `CHAT_RENDER`) before first prompt. |
| Piped stdin in one-shot mode | Stdin content wrapped as context (`<stdin>…</stdin>`), one reply (per `ONESHOT_RENDER`), exit. No other TUI elements. |
| Invalid `*_RENDER` value in config | Warning to stderr; that mode falls back to its own default (`markdown` for one-shot, `text` for explain/chat) rather than failing startup. |

---

## 6. Auxiliary terminal output (startup, non-TUI)

These precede or surround the TUI and affect the terminal experience:

- **Update notice** (`TUI-AUX-01`): at most once per 24 h (cached), a one-line *stderr* notice when a newer PyPI version exists: `Info: new asksh version available: <v> (installed: <v>). Upgrade with: 'pipx upgrade asksh' or 'uv tool upgrade asksh'`. Best-effort, 2 s timeout, never blocks or fails startup. Disable via `--no-update-check` or config.
- **Ollama health/model verification** (`TUI-AUX-02`): before any TUI starts, the server and model are verified; failures print an error to stderr with an emoji-prefixed tag (`❌ [OLLAMA_OFFLINE]`, `❌ [OLLAMA_MODEL_MISSING]`, …) plus troubleshooting hints, and exit with code 1.
- **Errors** (`TUI-AUX-03`): unexpected exceptions print `\nError: <msg>` to stderr and exit 1; known runtime errors print the message without the `Error:` prefix.

---

## 7. Known gaps / risks

1. ~~Ctrl-C during streaming terminates the process with a raw traceback.~~ **Fixed** (F-110): every style catches `KeyboardInterrupt`, aborts the client stream, and returns cleanly.
2. `live_markdown` reintroduces Rich-internals coupling as an opt-in: a monkey-patch of `rich.live_render.LiveRender.__rich_console__` (`_patch_live_render_crop_above`) and use of `Live` private/cursor-relative repaint behavior. This is scoped to that one style only; `text`, `post_markdown`, and `markdown` use only public `Console` methods (`render_lines`, `control`, `print`) and never move the cursor up.
3. A Textual alternate-screen overlay (`stream.py`) was tried as a second approach, and later an incremental "commit stable Markdown lines while streaming" renderer as a third. Both are gone: the alternate screen gave up native scrollback/mouse-wheel scrolling (feature spec §16); the stable-line committer always rendered Markdown live with no plain-text option and added real complexity for a rarely-noticeable latency win. The current four-style split achieves a safe default (`text`) plus explicit, separately-reasoned-about opt-ins.
4. **`live_markdown` full-buffer Markdown re-parse per tick** is O(buffer); the 12 Hz `Live` refresh rate bounds how often this happens when no new chunk has arrived; each chunk also triggers one re-parse. Accepted per TUI-MD-04.
5. Thinking blocks of prior turns can never be restored (history is thinking-stripped, and there is no repaint mechanism of any kind). Accepted; the originals survive wherever they were committed in scrollback (or nowhere, if the reply was aborted before commit).

---

## 8. Current implementation map (traceability for reimplementers)

| Concern | Where today | Notes |
| --- | --- | --- |
| Render-style config | `RenderStyle`, `load_user_config` in `config.py`; defaults set in `cli.py::parse_args` | Config-only keys (`ONESHOT_RENDER`/`EXPLAIN_RENDER`/`CHAT_RENDER`), no CLI flag; invalid values warn and fall back to the mode's default. |
| Render-style dispatch | `print_assistant_reply`, `_stream_reply_tty` in `render.py` | `_stream_reply_tty` delegates to `_stream_reply_tty_live_markdown` for that one style; otherwise picks `AppendOnlyWriter` (`text`/`post_markdown`) or `PreviewWriter` (`markdown`) for the content writer and prints the style's post-loop Markdown copy if any. |
| Append-only commit + live row | `AppendOnlyWriter`, `PreviewWriter`, `LiveRow`, `_preview_line` in `stream_render.py` | `LiveRow` is shared so ownership can hand off between writers; neither writer type ever emits a cursor-up — only carriage-return + erase-in-line for the live row, and plain `print`/`Segments` for `AppendOnlyWriter`'s committed lines. |
| Stability analysis (append-only) | `AppendOnlyWriter._candidate_source` / `_stable_lines` in `stream_render.py` | Plain-text only: everything up to the trailing, not-yet-terminated physical line is stable and commits immediately (§3.1, TUI-STREAM-02) — no Markdown-block-type gating, no lagged-diff safety net (both existed only for the old incremental Markdown committer and are gone). |
| `live_markdown` orchestration | `_stream_reply_tty_live_markdown`, `_live_markdown_display`, `_patch_live_render_crop_above` in `render.py` | Creates the `Live` context, feeds it a `Group`/`Markdown`/`Text`/`Spinner` renderable per tick, patches in `crop_above` overflow, and prints the final Markdown copy after the `Live` block exits cleanly. |
| Background animation | `StreamAnimator` in `stream_render.py` | 12 Hz thread re-invokes `update` on whichever writer is "active" (`AppendOnlyWriter` or `PreviewWriter`); a lock serializes it against main-thread updates. Not used by `live_markdown`, which relies on `Live`'s own auto-refresh thread instead. |
| Ctrl-C abort | `KeyboardInterrupt` handlers in `_stream_reply_tty` and `_stream_reply_tty_live_markdown` + `OllamaChatClient.abort_active_stream` (closes the HTTP response) + `abort` `Event` checked in `stream_message` | On abort the partial assistant reply is not added to history; no style prints its post-stream Markdown copy for an aborted reply. |
| Resize handling | None for the append-only styles — `Console.size` re-queries the terminal on every access; `live_markdown` relies on `Live`'s own (cursor-relative) repaint | No signal handler, no flag, no repair pass exists anywhere in asksh. |
| Thinking split | `split_embedded_thinking` / `split_streaming_embedded_thinking` in `client.py`; display precedence in `_display_texts` (`render.py`) | Shared by every render style. Embedded thinking is also stripped in the client before history is updated; the streaming variant holds an open `<think>` tag's contents out of the content buffer entirely. |
| Non-TTY drain | `_drain` in `render.py`: writes non-thinking deltas verbatim + trailing newline | Ignores `render_style` entirely. |
| Chat input | `_read_chat_user_input_prompt_toolkit` (TTY) / `_read_chat_user_input_line_based` (non-TTY) in `chat.py` | prompt_toolkit session is a module-level singleton; history path per XDG. User messages need no explicit "echo" step — prompt_toolkit's own input line is itself the terminal output, and it is never touched again. |

---

## 9. Verification

### Automated ([tests/test_stream_render.py](../tests/test_stream_render.py), [tests/test_render.py](../tests/test_render.py), [tests/test_client.py](../tests/test_client.py), [tests/test_config.py](../tests/test_config.py), [tests/test_cli_argv.py](../tests/test_cli_argv.py))

- `tests/term_helpers.py` provides `make_console` (an in-memory `Console` forced into non-dumb TTY mode, isolated from the environment) and `simulate_terminal` (replays raw ANSI output — CR, erase-in-line, newlines, *and* cursor-up — into the resulting visible screen; needed because `live_markdown` is the one style that legitimately emits cursor-up), used throughout both suites. `has_cursor_up` flags any cursor-up escape, used as the core regression check for the three append-only styles.
- `AppendOnlyWriter`/`PreviewWriter`/`LiveRow` (`test_stream_render.py`): no cursor-up sequence is ever emitted; feeding text one character at a time yields the same final visible text as feeding it in one chunk; a physical line commits as soon as its newline arrives (no Markdown-holdback logic remains to test); the spinner shows before the first delta and is replaced by a preview after; `AppendOnlyWriter.finish()` flushes whatever is still held back, while `PreviewWriter` never commits anything, ever; `LiveRow` hands off cleanly between two writers.
- Render (`test_render.py`): each of the four styles is covered independently — `text`'s literal, unparsed stream; `markdown`'s deferred body and single final Markdown print; `post_markdown`'s streamed copy plus a second Markdown copy; `live_markdown`'s one final Markdown copy (cursor-up is expected and tolerated for this style only). Thinking commits in grey italic under `Thinking...` when enabled and is absent when disabled, independent of render style. Embedded `<think>` tags are handled correctly whether the tag closes before or after content starts. `Ctrl-C` mid-stream is covered per style: append-only styles leave partial plain text visible without adding it to history; `markdown`/`live_markdown` leave nothing of the answer visible. Non-TTY drains verbatim without thinking, followed by one blank line, unaffected by `render_style`. Non-streaming TTY prints `Text` for `text` and `Markdown` for the other three; non-streaming non-TTY prints plain text regardless of style.
- Config (`test_config.py`): render-style keys round-trip through `load_user_config`, invalid values warn and are dropped, wrong-typed values warn and are dropped.
- CLI (`test_cli_argv.py`): default render styles per mode, and config-file overrides, resolve onto `argparse.Namespace` correctly.
- Client (`test_client.py`): `split_streaming_embedded_thinking` covers an unterminated `<think>` tag, a tag split across chunk boundaries (including a partial opening tag like `...<thi`), and interaction with an already-completed `<think>...</think>` pair earlier in the same buffer.

### Manual checklist (for validating a new implementation)

1. For `text`/`post_markdown`: start chat, stream a reply longer than the terminal height → content scrolls the terminal naturally as it grows; mouse-wheel scroll up mid-stream never disturbs anything; on completion the full reply is present exactly once (`text`) or twice (`post_markdown`, plain then Markdown) in scrollback.
2. For `markdown`: start chat, stream a reply → only a spinner/last-line preview is visible while streaming; on completion, the whole reply appears once, formatted as Markdown, with no earlier fragment left behind.
3. For `live_markdown`: start chat, stream a reply longer than the terminal → a live-updating Markdown preview is shown, newest lines visible (`crop_above`) if it exceeds the terminal height; on completion the preview is replaced cleanly by one final Markdown print.
4. Resize the window smaller and larger mid-stream, for each style → for `text`/`post_markdown`/`markdown`: no duplicated/garbled text; content printed after the resize re-wraps; content printed before it is untouched. For `live_markdown`: the in-progress preview may visibly glitch (accepted); the eventual final print is still clean.
5. Resize while the model is producing no output (waiting state, spinner) → no corruption for any style; the spinner keeps animating at the new width on its next tick.
6. Resize at the exact moment the stream ends → clean transition, no residue, for every style.
7. `Enter` submits; `Alt+Enter` inserts a newline; `↑` recalls previous inputs; input history survives a restart.
8. Ctrl-C / Ctrl-D at the prompt → `Goodbye!`; empty input re-prompts; `exit`/`QUIT` exits.
9. `cat file | asksh -c "…"` → file content sent as first message, prompt still interactive.
10. With `--think` on a thinking model: grey italic thinking commits first, then a blank line, then content, both live and in the end result, for `text`/`post_markdown`/`markdown`. For `live_markdown`, thinking and content are part of the same live-updating group. With `--think false`: no trace anywhere, in any style.
11. `asksh "…" > out.txt` → plain text, no ANSI, one trailing newline, regardless of render style.
12. One-shot + TTY → only the reply (no intro panel, no prompt), rendered per `ONESHOT_RENDER` (default `markdown`).
13. Ctrl-C mid-stream, for each style → clean abort, back to the prompt, no traceback, no post-stream Markdown print; partial plain text (if any) stays visible but isn't added to history.
14. Set distinct render styles per mode in `config.toml` and confirm one-shot, `-e`, and chat each honor their own configured style.

---

## 10. Non-goals

- A persistent full-screen chat UI, a custom pager, or an alternate screen: streamed and final content live entirely in the terminal's own scrollback, exactly as many times as each style promises.
- Syntax-highlighted code blocks (explicitly disabled).
- Persisting conversation history across sessions (only input history persists).
- Incremental, per-construct Markdown commit while streaming (removed; see §0). `live_markdown`'s full-buffer live preview and the other styles' single final print are the only ways Markdown formatting appears.
- Any mechanism that repairs `live_markdown`'s in-progress preview against resize/scroll desync (e.g. a `SIGWINCH` handler or a `Live` subclass): that risk is accepted, not engineered around, for that one opt-in style.
