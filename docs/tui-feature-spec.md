# asksh — TUI Feature Specification

**Purpose:** A technology-agnostic specification of the features and user-visible behavior the terminal interface must provide. It deliberately contains no implementation details (no libraries, no rendering mechanisms, no data formats); the implementing agent is free to choose how to satisfy each requirement.
**Input contract:** The UI consumes the assistant reply as an incremental stream of text deltas. Each delta is tagged as either **thinking** (reasoning trace) or **content** (the answer itself). Non-streamed answers may be treated as a single delta.

Requirements use **MUST** (mandatory behavior) and **SHOULD** (expected unless a documented deviation exists). A requirement marked *(improvement)* goes beyond today's behavior; it is a target for the new implementation.

---

## 1. Scope

In scope: everything the user sees and can do in the terminal when running `asksh` interactively (chat mode) or one-shot, including streaming display, Markdown rendering, resize behavior, scrolling, input editing, session flow, and output when the terminal is not interactive.

Out of scope: model communication, prompts sent to the model, configuration, packaging, install/update mechanics (only their terminal *messages* are covered).

---

## 2. Terminology (user-level concepts)

| Term | Meaning |
| --- | --- |
| **Streaming region** | The area of the screen where a reply is drawn while it is still being received. |
| **Transcript** | The visible record of the session: intro, user messages, and completed assistant replies. |
| **Thinking trace** | The model's reasoning, shown separately from the answer when enabled. |
| **Tail-follow (auto-follow)** | Default streaming behavior: the view stays pinned to the newest lines as they arrive. Suspended while the user is scrolled up reading earlier content. |
| **Render style** | One of four ways to present a reply's answer content, selected independently for each of the three modes (one-shot, explain, chat) — see §4. |

---

## 3. Streaming reply display

**F-00** — The answer content of a reply is presented using one of four **render styles** (`text`, `markdown`, `post_markdown`, `live_markdown`; see §4), chosen independently for each mode (one-shot, explain, chat). Everything else in this section (thinking trace, waiting indicator) is unaffected by the choice of render style.

**F-01** — In an interactive terminal, a streamed reply's answer content MUST appear incrementally for the `text` and `post_markdown` styles: text is displayed as it arrives, without waiting for the reply to complete. For `live_markdown` the *formatted* preview appears incrementally instead (§4). For `markdown`, the answer content is intentionally withheld until the reply is complete (a waiting indicator is shown in its place; see F-90); this is a deliberate exception, not a violation.

**F-02** — Once a piece of the reply has been displayed, it MUST NOT move, be redrawn, or be replaced: the display is append-only. This holds for everything committed under `text`, `post_markdown`, and `markdown`, and for the one final print every style produces (§6). It deliberately does not hold for `live_markdown`'s in-progress preview, which is redrawn in place by design (F-02-LIVE). Consequently the streaming display MAY scroll the terminal exactly as any other command's growing output would once it exceeds the visible height — that is normal, expected, and not something the program prevents or compensates for.

**F-02-LIVE** — `live_markdown` is the one documented exception to F-02: its in-progress preview is redrawn in place (not append-only) so it can show live-formatted Markdown while still streaming. This means a resize or a scroll mid-stream MAY visibly corrupt that in-progress preview (see §7); the final print (F-05) is unaffected and always clean. Choosing `live_markdown` is an explicit trade-off of that risk for a live-formatted view.

**F-03** — The display MUST feel live: updates MUST appear with imperceptible latency as tokens arrive (a useful bar: visible state changes reflect new content within ~100 ms), and any waiting animation MUST remain smoothly animated even when no new content is arriving.

**F-04** — For `text`, `post_markdown`, and `markdown`, at most one line — the one currently being received, or a waiting indicator — MAY be redrawn in place while it is still incomplete. Everything else on screen is final the moment it is printed. For `live_markdown`, the entire in-progress preview (not just one line) is redrawn in place on every update (F-02-LIVE); this is that style's counterpart to F-04.

**F-05** — For `text`, there is no separate "final, fully rendered reply" distinct from what streamed: the last piece of the reply is committed to the screen as soon as it is known to be complete, with no leftover fragments and no second copy. The other three styles each add exactly one final Markdown-formatted print once the reply is complete: for `markdown` it is the *only* copy (nothing streamed before it); for `post_markdown` it is a deliberate *second* copy printed immediately after the streamed plain-text copy; for `live_markdown` it *replaces* the erased in-progress preview. In every style there is exactly one lasting copy of each kind the style promises — never a stray, half-updated fragment left behind. See §6.

**F-06** — In a one-shot run (a query given as an argument), the reply is shown the same way as any other run and the program exits when the final reply has been printed; no intro and no prompt are displayed.

## 4. Render styles and Markdown rendering

**F-10** — Markdown formatting of assistant content is **opt-in**, chosen per mode via the render style (F-00). The four styles:

| Style | While streaming | When the reply completes |
| --- | --- | --- |
| `text` | Answer content streams as plain text (append-only). | Nothing further — the streamed text is the only copy. |
| `markdown` | Nothing of the answer is shown (a waiting indicator only, F-90). | The complete answer is printed once, rendered as Markdown. |
| `post_markdown` | Same as `text`. | A second copy of the complete answer is printed immediately after, rendered as Markdown. |
| `live_markdown` | A live, reformatted-on-every-update Markdown preview of the answer so far (F-02-LIVE). | The preview is erased and the complete answer is printed once, rendered as Markdown. |

Whichever style is active, Markdown rendering (wherever it occurs) MUST support at minimum: headings, paragraphs, lists (ordered and unordered), fenced code blocks, inline code, bold, italic, links, and blockquotes. Tables SHOULD be supported.

**F-11** — Rendering is **not** required to be incremental at the level of individual Markdown constructs (a heading, list, or table styling up the instant its closing token arrives mid-stream is not a requirement of any style): `markdown` and `post_markdown` apply Markdown formatting exactly once, to the complete, final text. `live_markdown` re-renders its *entire* buffer as Markdown on every update, so a construct may render provisionally (e.g. a list still growing) until the buffer settles — full-buffer re-render, not per-construct incremental commit.

**F-12** — Content MUST wrap and reflow to the current terminal width at the time each part of it is displayed. Because already-displayed lines are never redrawn (F-02), a resize only affects lines printed *after* it; lines printed before keep the wrapping they were given, exactly like any other command's past output. (For `live_markdown`'s in-progress preview specifically, see F-02-LIVE and §7.)

**F-13** — Code blocks MUST be shown without syntax highlighting.

**F-14** — Wherever a style produces a Markdown-formatted copy (`markdown`, `post_markdown`, `live_markdown`'s final print), it MUST be equivalent to rendering the complete accumulated text once — trivially true since it is in fact rendered exactly once, from the complete text, never incrementally. For `text`, the analogous guarantee is that the streamed plain text, taken as a whole, is exactly the complete accumulated text with no reformatting at all.

## 5. Thinking trace

**F-20** — The thinking trace MUST be displayed whenever thinking is enabled for the model or explicitly forced by the user; it MUST NOT be displayed when thinking is disabled. (If the user explicitly asks to show the trace, that wins over a disabled state only if a trace actually exists.)

**F-21** — Thinking text MUST be visually distinct from the answer: dim and italic, preceded by the heading `Thinking...`.

**F-22** — During streaming, the thinking trace and the answer content MUST be shown together: trace first, then a blank separator line, then the content. The trace grows incrementally alongside the content.

**F-23** — Models that put reasoning inside the answer as delimited blocks (e.g. `<think>…</think>`) MUST have those blocks extracted: the answer shows without them, and the extracted text is displayed as the thinking trace, both while streaming and in the final output. A trace delivered separately takes precedence over embedded blocks.

**F-24** — In the final output, the complete trace MUST be printed once (dim/italic, with the `Thinking...` heading), followed by a blank line, then the final rendered answer.

**F-25** — The thinking trace MUST NOT be included in the conversation history or in the transcript echo of past replies.

## 6. Final reply and terminal scrollback

**F-30** — By the time streaming ends, the reply MUST be presented in full in scrollback: every part of it committed, wrapped at the terminal width in effect when it was printed, never cropped regardless of length.

**F-31** — Scrolling back MUST show exactly one copy of the reply per copy the active render style promises (F-05, F-10): one for `text`, `markdown`, and `live_markdown`; deliberately two (plain text, then Markdown) for `post_markdown`. No style ever leaves behind an *extra*, unintended copy or a leftover in-progress fragment: content is committed once and never redrawn (F-02), and `live_markdown`'s in-progress preview is fully erased before its one final print.

**F-32** — If the reply contains no answer content, nothing from the reply MUST be printed, regardless of render style (a thinking trace, if any, is still printed per F-24).

**F-33** — The final reply MUST be followed immediately by whatever comes next (the next prompt in chat mode, or program exit in one-shot mode), with no stray blank region between them.

## 7. Terminal resize

**F-40** — For `text`, `post_markdown`, and `markdown`, the user MUST be able to resize the terminal at any point while a reply is streaming — including while scrolled up into older content — without corrupting the display. Because already-printed lines are never redrawn or moved (F-02), a resize has nothing to corrupt: there MUST be no duplicated lines, no garbled or half-erased text, no misaligned cursor. `live_markdown` is the documented exception (F-02-LIVE): a resize or scroll while its in-progress preview is on screen MAY corrupt that preview, because it is a cursor-relative repaint, not append-only; this is an accepted trade-off of that style, and the final print after the preview is erased is unaffected.

**F-41** — After a resize, content printed from that point on MUST wrap at the new width. Content printed before the resize keeps the wrapping it was given, exactly as with any other command's past terminal output.

**F-42** — Everything printed before a resize — the intro, the transcript of previous turns, and any part of the current reply already committed — MUST remain exactly as it was: a resize is not a trigger for redrawing or reprinting anything.

**F-43** — A resize while the reply is still streaming MUST NOT lose any content: the accumulated reply text is preserved and continues streaming normally, wrapping at whatever width is current at the moment each new part is printed.

**F-44** — Resizing while the input prompt is active MUST NOT corrupt the prompt or the edited text.

**F-45** — Resizing after a reply has completed (between turns) MUST NOT cause any redraw or duplication of the transcript; the already-printed text simply re-wraps as the terminal itself does, the same as it would for any other past output.

## 8. Scrolling behavior

**F-50** — Content MUST print in the normal terminal scrollback, growing downward, exactly like the output of any other command. There is no separate "streaming region" with its own scrolling behavior distinct from the rest of the transcript.

**F-51** — The user MUST be able to scroll to view earlier lines of the reply at any time, during streaming or after — this is the terminal's own scrollback and is never restricted or intercepted by the program.

**F-52** — The program MUST NOT attempt to force the view to any particular position (e.g. snap to the bottom) while streaming: whether the visible window follows new output as it is printed, or stays where the user scrolled it, is the terminal emulator's own behavior, outside the program's control, and that is by design — the same as it is for any other command whose output exceeds the screen.

**F-53** — Streaming MUST NOT disturb the transcript above the newly printed content: previous turns and earlier parts of the current reply stay exactly where they were printed.

## 9. Chat input and session flow

**F-60** — The chat prompt MUST be `>>> ` in cyan, preceded by a blank line. Wrapped or continued lines MUST show `... ` in grey as the continuation prompt.

**F-61** — The prompt MUST be a multiline editor with correct cursor movement and backspacing, supporting text that wraps across lines.

**F-62** — Key bindings:

| Key | Action |
| --- | --- |
| `Enter` | Submit the input immediately. |
| `Alt+Enter` | Insert a newline into the input. |

**F-63** — Input history MUST persist across program restarts.

**F-64** — Submitted input is stripped of leading/trailing whitespace; empty input MUST be ignored (prompt again, nothing sent).

**F-65** — Typing `exit` or `quit` (case-insensitive) MUST end the session with `Goodbye!` in grey. `Ctrl-C` and `Ctrl-D` at the prompt MUST do the same (preceded by a blank line).

**F-66** — The intro, shown once at chat start (not in one-shot mode), MUST be a bordered, full-width panel containing:

```
Chatting with model <name>          (model name highlighted in bright cyan)
- Multiline input: Alt+Enter.
- Type 'exit' or Ctrl-C to quit.
- Without a TTY, use \ at the end of a line, then Enter, to continue on the next line.   (only when input is redirected)
```

**F-67** — In the transcript, user messages MUST be echoed as `>>> ` in cyan followed by the plain text; assistant replies as rendered Markdown.

**F-68** — When stdin is piped and chat mode is used (`cat file | asksh -c "…"`), the piped text MUST be sent as the first message, its reply MUST be displayed, and the interactive prompt MUST then work normally (the piped stdin must not break interactive input).

**F-69** — When input is redirected but the output is a terminal, chat input MUST still work line-by-line: `\` at the end of a line (an odd number of backslashes after trailing whitespace is stripped) continues on the next line; a doubled `\\` ends the message with one literal backslash. Prompts for this mode MUST be plain text (`>>> ` / `... `) with no styling codes.

**F-70** — The conversation (system prompt + turns) lives for the session only; only input history persists (F-63).

## 10. Non-interactive output (stdout redirected)

**F-80** — When stdout is not a terminal, output MUST be plain text with no escape sequences and no styling: the answer content verbatim, followed by exactly one trailing newline. Thinking traces MUST NOT be included.

**F-81** — Chat mode with redirected stdout MUST print a plain-text notice (model name, how to quit, backslash continuation hint) instead of the panel of F-66.

## 11. Waiting states

**F-90** — While a reply is being awaited (or streaming has produced nothing yet), a small animated indicator MUST be shown in bright cyan in place of the reply. It MUST disappear the moment the first text (thinking or content) arrives.

## 12. Appearance

These values are part of the product; a new implementation MUST reproduce them (or deliberately rebrand them — a documented decision):

| Element | Style |
| --- | --- |
| Prompt `>>> ` | cyan |
| Continuation prompt `... ` | grey |
| Thinking trace | grey, italic |
| Waiting indicator | bright cyan |
| Intro text | grey |
| Intro model name | bright cyan |
| Intro panel border | grey |
| User echo `>>> ` | cyan |
| Goodbye message | grey |
| Code blocks | no syntax highlighting |

## 13. Auxiliary terminal messages

**F-100** — At startup (before any TUI element), a one-line notice MUST be printed to stderr when a newer version is available, including both versions and the upgrade command. The check MUST be best-effort: at most once per day, never blocking startup more than ~2 s, and never failing the run. Users MUST be able to disable it.

**F-101** — Server/model health problems MUST be reported to stderr with a clear, identifiable error tag and actionable troubleshooting hints, and the program MUST exit with a non-zero status before the UI starts.

**F-102** — Unexpected runtime errors MUST print `Error: <message>` to stderr and exit with a non-zero status.

## 14. Interrupts and error handling

**F-110** — `Ctrl-C` while a reply is streaming MUST abort the current reply cleanly, and the program either returns to the chat prompt or exits gracefully. A traceback MUST NOT be shown. Whatever part of the reply was already committed to the screen MUST remain visible — it cannot be un-printed — but the interrupted reply MUST NOT be added to the conversation history. There is no final Markdown print for an aborted reply: `text` and `post_markdown` leave whatever plain text had already streamed; `markdown` and `live_markdown` leave nothing of the answer visible at all (nothing had been committed yet, and `live_markdown`'s in-progress preview is erased on abort like any other exit).

**F-111** — If the reply stream fails mid-way (network error, server error), the UI MUST leave the terminal in a clean state: no garbled screen, whatever was already committed stays as-is, and an error message on stderr per F-102.

## 15. Performance and quality bar

**F-120** — Streaming display MUST keep up with typical local-model token rates without visible lag or flicker. As a reference: updates reflecting newly arrived tokens within ~100 ms, smooth waiting animation, and no perceptible delay between the last token and the final reply print.

**F-121** — Rendering a reply of typical length (up to several screens of Markdown) MUST NOT noticeably slow down token display, even when the reply is taller than the terminal.

## 16. Non-goals

The following are explicitly **not** required (changing them is a product decision, not part of this spec):

- A custom in-app pager, scroll bars, or alternate-screen UI: the terminal's native scrolling must suffice for reviewing content (F-51–F-53).
- Syntax-highlighted code blocks (F-13).
- Mouse support, alternate-screen (full-screen) layouts, or anything that breaks the terminal's native scrollback.
- Persisting the conversation itself across sessions (F-70).

## 17. Acceptance checklist (manual, observable)

1. `text`/`post_markdown`: stream a reply longer than the terminal: content scrolls the terminal naturally as it grows, exactly like any other command's output; on completion the full reply is present exactly once (`text`) or exactly twice, plain then Markdown (`post_markdown`) in scrollback.
2. `text`/`post_markdown`/`markdown`: scroll up while a long reply is streaming (or waiting, for `markdown`): earlier lines are visible; the program never fights the terminal's own scroll position, and new content keeps arriving below regardless of where the view is scrolled.
3. `text`/`post_markdown`/`markdown`: resize smaller and larger mid-stream (including while scrolled up into older content): no duplicated/garbled text; content printed after the resize wraps at the new width; content printed before it is untouched.
4. Resize while waiting for the first token (any style): no corruption; the waiting indicator keeps animating at the new width.
5. Resize at the exact moment streaming ends (any style): clean transition, no residue.
6. `markdown`/`post_markdown`/`live_markdown`: the final printed copy renders Markdown constructs (code fence, list, heading, bold, italic, links, blockquote) correctly; for `live_markdown`, the in-progress preview also shows Markdown formatting, live, as the buffer grows (may render provisionally until a construct settles — F-11).
6a. `live_markdown`: resize or scroll while the in-progress preview is on screen MAY visibly disturb that preview (F-02-LIVE) — verify only that the final printed copy afterward is clean, not that the in-progress preview survives.
7. Thinking model with trace enabled: dim italic trace above a blank line above the content, live and final; `--think false`: no trace anywhere. This is independent of render style.
8. `Enter` submits, `Alt+Enter` inserts a newline, input history survives a restart, empty input re-prompts, `exit`/`QUIT`/`Ctrl-C`/`Ctrl-D` all say `Goodbye!`.
9. `cat file | asksh -c "…"`: file content sent as first message; prompt still interactive afterwards.
10. `asksh "…" > out.txt`: plain text only, no ANSI, one trailing newline, regardless of render style (F-80).
11. `Ctrl-C` mid-stream (each style): stream aborts cleanly, no traceback, no final Markdown print (F-110).
12. Set a different render style per mode in `config.toml` (e.g. `ONESHOT_RENDER = "live_markdown"`, `CHAT_RENDER = "post_markdown"`) and confirm each mode behaves per its own configured style, independently of the others.
