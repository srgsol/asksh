# asksh

[![PyPI](https://img.shields.io/pypi/v/asksh)](https://pypi.org/project/asksh/)
[![Python](https://img.shields.io/pypi/pyversions/asksh)](https://pypi.org/project/asksh/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> AI in your terminal for shell and coding help.

`asksh` is an AI-powered CLI that lets you describe what you want in plain English and get practical terminal guidance. In its default one-shot mode it replies with a concise Linux command. In explain or chat modes it can also provide short explanations or broader programming help.

You don't need to leave your terminal to get things done. Don't remember the exact `tar` flags? ask**sh**

```bash
$ asksh "compress a directory as tar.gz excluding the .cache directory"
tar -czf archive.tar.gz --exclude=.cache my_directory
```

## Why asksh

- **Stay in the terminal:** describe what you need in plain language and get a shell command (or a short explanation) without switching to a browser or another app.
- **Local model, low cost:** [Ollama](https://ollama.com/) with the default **`qwen2.5-coder`** is enough for most day-to-day terminal tasks—no API keys or token spend on large cloud providers.
- **No automatic access or execution:** `asksh` does not browse your filesystem, write files, delete anything, or run shell commands on its own. It only sends your prompt to the model, plus text you explicitly provide with **`-f`/`--context`** or by **piping stdin**. You review and run any command yourself.

## Features

- **One-shot command** (default): returns just the shell command, no commentary.
- **Explain mode** (`-e/--explain`): returns a command with a short explanation.
- **Interactive chat** (`-c/--chat`, or run with no query): streamed multi-turn chat for broader help.
- **Re-render last reply** (`-m/--markdown`): reprint the previous assistant reply as Markdown, offline (no Ollama call).
- **File context** (`-f/--context PATH`): attach a file (logs, configs, code) as context.
- **Stdin support**: pipe anything in (`cat error.log | asksh ...`), works in chat mode too.
- **Local & private**: runs against your own [Ollama](https://ollama.com/) server; no data leaves your machine.
- **Custom model / server**: override defaults per call (`--model`, `--base-url`) or via a TOML config.
- **Thinking models**: reasoning is enabled automatically for supported models; pass `--think false` to disable (see below).

## Quick Start

1. [Install Ollama](https://ollama.com/download) and make sure it's running (`ollama serve`, or launch the desktop app).
2. Pull the default model:

```bash
ollama pull qwen2.5-coder
```

3. Install and run `asksh`:

```bash
pipx install asksh
asksh "find files larger than 500MB in this directory"
```

## Safety

AI-generated shell commands can be destructive. Always review commands before running them, especially commands that remove files, rewrite git history, or modify system configuration.

## Installation

### Install the CLI

**From PyPI:**

```bash
pipx install asksh
# or: uv tool install asksh
```

**From a clone** (development):

```bash
git clone https://github.com/srgsol/asksh.git
cd asksh
uv sync
uv run asksh --help
```

Requires **Python 3.10+** and a reachable [Ollama](https://ollama.com/download) server (see Quick Start).

### Configuration (optional defaults)

CLI flags always win. To avoid retyping `--model`/`--base-url` on every run, drop a TOML file at:

- `$XDG_CONFIG_HOME/asksh/config.toml` (or `~/.config/asksh/config.toml` if `XDG_CONFIG_HOME` is unset).

Config keys are case-insensitive (`model` and `MODEL` both work). The same settings can be set via environment variables (`ASKSH_MODEL` / `asksh_model`, `ASKSH_BASE_URL` / `asksh_base_url`, and so on); env vars override the config file. See [`config.example.toml`](config.example.toml).

| Setting          | Default                  | Config key         | Environment variable   |
| ---------------- | ------------------------ | ------------------ | ---------------------- |
| `model`          | `qwen2.5-coder`          | `MODEL`            | `ASKSH_MODEL`          |
| `base_url`       | `http://localhost:11434` | `BASE_URL`         | `ASKSH_BASE_URL`       |
| `update_check`   | `true`                   | `UPDATE_CHECK`     | `ASKSH_UPDATE_CHECK`   |
| `oneshot_render` | `text`                   | `ONESHOT_RENDER`   | `ASKSH_ONESHOT_RENDER` |
| `explain_render` | `text`                   | `EXPLAIN_RENDER`   | `ASKSH_EXPLAIN_RENDER` |
| `chat_render`    | `text`                   | `CHAT_RENDER`      | `ASKSH_CHAT_RENDER`    |

### Render style

`ONESHOT_RENDER` / `EXPLAIN_RENDER` / `CHAT_RENDER` (config-only, no CLI flag) each pick one of:

- `text` — print tokens as they arrive as plain text (the only copy).
- `markdown` — show a spinner/preview while tokens arrive, then print the whole reply as Markdown once, when it completes.
- `post_markdown` — stream as plain text, then print a second Markdown copy of the same reply below it.
- `live_markdown` — live Markdown preview, redrawn as tokens arrive, then one final Markdown print. Resizing or scrolling the terminal mid-stream can garble the live preview (the final print is always clean); accept that trade-off only if you want live-formatted Markdown while it streams.

An invalid value is ignored with a warning; the mode's default (above) is used instead.

At startup, asksh checks PyPI for a new release (at most once per 24h, cached in `$XDG_CACHE_HOME/asksh/update_check`). If one exists it prints a one-line notice with the upgrade command; the check never fails startup and is skipped entirely when offline. Disable it with `UPDATE_CHECK = false` in the config or the `--no-update-check` flag.

## Usage

### Flags

| Flag                  | Description                                                  |
| --------------------- | ------------------------------------------------------------ |
| `-c, --chat`          | Start interactive chat (also the default when no query).     |
| `-e, --explain`       | Return a command with a short explanation.                   |
| `-m, --markdown`      | Re-render the last assistant reply as Markdown (no Ollama call). |
| `-f, --context PATH`  | Use a file as additional context.                            |
| `--model NAME`        | Ollama model (default `qwen2.5-coder`).                      |
| `--base-url URL`      | Ollama server (default `http://localhost:11434`).            |
| `--think LEVEL`       | Control reasoning for thinking models (`true`, `false`, `low`, `medium`, `high`, `max`; enabled when supported if omitted). |
| `--show-thinking`     | Show the model reasoning trace (requires `--think` other than `false`). |
| `--no-update-check`   | Skip the startup PyPI update check.                              |
| `-V, --version`       | Print version and exit.                                      |

Run `asksh --help` to see the full list.

### One-shot query

```bash
asksh "compress this folder as tar.gz excluding .cache"
```

### Interactive chat

`asksh` enters chat mode if no query is provided, or when `-c/--chat` is set.

```bash
asksh
# or:
asksh -c
```

By default (`CHAT_RENDER = "text"`), replies print as tokens arrive and become part of the terminal's normal scrollback, so mouse-wheel scrolling and window resizing behave exactly as with any other command's output — this guarantee holds for the `text`, `markdown`, and `post_markdown` render styles alike (only `live_markdown` redraws in place and can desync on resize/scroll; see [Render style](#render-style)). Press `Ctrl-C` to abort the stream; whatever was already printed stays on screen (it cannot be un-printed), but the partial reply is not added to the conversation history.

### Explain mode

Return a command with a short explanation:

```bash
asksh -e "show open tcp ports"
```

### Re-render the last reply as Markdown

`asksh` saves the most recent assistant reply (from one-shot, explain, or chat mode) to `$XDG_STATE_HOME/asksh/last_reply` (fallback `~/.local/state/asksh/last_reply`). Reprint it as formatted Markdown, offline, with no query and no Ollama call:

```bash
asksh -m
```

`-m/--markdown` cannot be combined with `-c/--chat`, `-e/--explain`, or a query.

### Thinking models

Models such as DeepSeek R1 or Qwen 3 can emit a separate reasoning trace. When `--think` is omitted, `asksh` enables reasoning only for models that support it. To disable reasoning:

```bash
asksh --think false "compress this folder as tar.gz"
```

Passing `--think true` (or a level such as `medium` or `high`) on a model that does not support thinking exits with an error. The reasoning trace is shown automatically when thinking is enabled; use `--show-thinking` to force it on.

### Context file

Pass a file as additional context:

```bash
asksh -f error.log "what is failing here?"
```

### Pipe stdin

Use piped input as context:

```bash
cat data.json | asksh "use jq to count items"
```

You can combine stdin with chat mode — stdin becomes the first message and the chat then continues interactively from your terminal:

```bash
cat error.log | asksh -c "what went wrong?"
```

## Troubleshooting

- **Cannot connect to Ollama:** ensure Ollama is running and reachable at `http://localhost:11434` (or pass `--base-url`).
- **Model not found:** run `ollama pull qwen2.5-coder` or pass another available model with `--model`.
- **Config not being used:** verify config path (`$XDG_CONFIG_HOME/asksh/config.toml` or `~/.config/asksh/config.toml`).
