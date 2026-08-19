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

---

## 3. Streaming reply display

**F-01** — In an interactive terminal, a streamed reply MUST appear incrementally: text is displayed as it arrives, without waiting for the reply to complete.

**F-02** — Once a piece of the reply has been displayed, it MUST NOT move, be redrawn, or be replaced: the display is append-only. Consequently the streaming display MAY scroll the terminal exactly as any other command's growing output would once it exceeds the visible height — that is normal, expected, and not something the program prevents or compensates for.

**F-03** — The display MUST feel live: updates MUST appear with imperceptible latency as tokens arrive (a useful bar: visible state changes reflect new content within ~100 ms), and any waiting animation MUST remain smoothly animated even when no new content is arriving.

**F-04** — At most one line — the one currently being received — MAY be redrawn in place while it is still incomplete (e.g. an animated waiting indicator, or a preview of the in-progress line). Everything else on screen is final the moment it is printed.

**F-05** — There is no separate "final, fully rendered reply" distinct from what streamed: the last piece of the reply is committed to the screen as soon as it is known to be complete, with no leftover fragments and no second copy. See §6.

**F-06** — In a one-shot run (a query given as an argument), the streamed reply is shown the same way and the program exits when the final reply has been printed; no intro and no prompt are displayed.

## 4. Markdown rendering

**F-10** — Assistant content MUST be rendered as Markdown. Supported elements MUST include at minimum: headings, paragraphs, lists (ordered and unordered), fenced code blocks, inline code, bold, italic, links, and blockquotes. Tables SHOULD be supported.

**F-11** — Rendering MUST be incremental: a Markdown construct MUST be displayed in its styled form as soon as it is known that later text cannot change how it is shown (e.g. a code fence's lines render as a code block as they arrive, one full line at a time; a construct whose meaning or layout could still change — a numbered list whose indent width depends on its final item count, a table whose column widths depend on every row, a line that could still turn into a heading — is held back until it is unambiguously finished).

**F-12** — Content MUST wrap and reflow to the current terminal width at the time each part of it is displayed. Because already-displayed lines are never redrawn (F-02), a resize only affects lines printed *after* it; lines printed before keep the wrapping they were given, exactly like any other command's past output.

**F-13** — Code blocks MUST be shown without syntax highlighting.

**F-14** — The reply the user ends up with in scrollback MUST be equivalent to rendering the complete accumulated text once: streaming it out incrementally (F-11) must never produce a different visual result than rendering the whole thing at once would, just spread out over time.

## 5. Thinking trace

**F-20** — The thinking trace MUST be displayed whenever thinking is enabled for the model or explicitly forced by the user; it MUST NOT be displayed when thinking is disabled. (If the user explicitly asks to show the trace, that wins over a disabled state only if a trace actually exists.)

**F-21** — Thinking text MUST be visually distinct from the answer: dim and italic, preceded by the heading `Thinking...`.

**F-22** — During streaming, the thinking trace and the answer content MUST be shown together: trace first, then a blank separator line, then the content. The trace grows incrementally alongside the content.

**F-23** — Models that put reasoning inside the answer as delimited blocks (e.g. `<think>…</think>`) MUST have those blocks extracted: the answer shows without them, and the extracted text is displayed as the thinking trace, both while streaming and in the final output. A trace delivered separately takes precedence over embedded blocks.

**F-24** — In the final output, the complete trace MUST be printed once (dim/italic, with the `Thinking...` heading), followed by a blank line, then the final rendered answer.

**F-25** — The thinking trace MUST NOT be included in the conversation history or in the transcript echo of past replies.

## 6. Final reply and terminal scrollback

**F-30** — By the time streaming ends, the reply MUST be presented in full in scrollback: every part of it committed, wrapped at the terminal width in effect when it was printed, never cropped regardless of length.

**F-31** — Scrolling back MUST show exactly one copy of the reply. Because content is committed once and never redrawn (F-02), there is no intermediate state left behind to appear as a second copy.

**F-32** — If the reply contains no answer content, nothing from the reply MUST be printed (a thinking trace, if any, is still printed per F-24).

**F-33** — The final reply MUST be followed immediately by whatever comes next (the next prompt in chat mode, or program exit in one-shot mode), with no stray blank region between them.

## 7. Terminal resize

**F-40** — The user MUST be able to resize the terminal at any point while a reply is streaming — including while scrolled up into older content — without corrupting the display. Because already-printed lines are never redrawn or moved (F-02), a resize has nothing to corrupt: there MUST be no duplicated lines, no garbled or half-erased text, no misaligned cursor.

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

**F-110** — `Ctrl-C` while a reply is streaming MUST abort the current reply cleanly, and the program either returns to the chat prompt or exits gracefully. A traceback MUST NOT be shown. Whatever part of the reply was already committed to the screen (F-02) MUST remain visible — it cannot be un-printed — but the interrupted reply MUST NOT be added to the conversation history.

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

1. Stream a reply longer than the terminal: content scrolls the terminal naturally as it grows, exactly like any other command's output; on completion the full reply is present exactly once in scrollback.
2. Scroll up while a long reply is streaming: earlier lines of the reply are visible; the program never fights the terminal's own scroll position, and new content keeps arriving below regardless of where the view is scrolled.
3. Resize smaller and larger mid-stream (including while scrolled up into older content): no duplicated/garbled text; content printed after the resize wraps at the new width; content printed before it is untouched.
4. Resize while waiting for the first token: no corruption; the waiting indicator keeps animating at the new width.
5. Resize at the exact moment streaming ends: clean transition, no residue.
6. Markdown constructs (code fence, list, heading) style up as soon as their closing tokens arrive mid-stream.
7. Thinking model with trace enabled: dim italic trace above a blank line above the content, live and final; `--think false`: no trace anywhere.
8. `Enter` submits, `Alt+Enter` inserts a newline, input history survives a restart, empty input re-prompts, `exit`/`QUIT`/`Ctrl-C`/`Ctrl-D` all say `Goodbye!`.
9. `cat file | asksh -c "…"`: file content sent as first message; prompt still interactive afterwards.
10. `asksh "…" > out.txt`: plain text only, no ANSI, one trailing newline.
11. `Ctrl-C` mid-stream: stream aborts cleanly, no traceback (F-110).
