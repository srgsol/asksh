# asksh

> AI in your terminal: turn plain English into shell commands.

`asksh` is an AI-powered command-line tool that lets you describe what you want in plain English and returns the exact POSIX shell command to run.

You don't need to leave your terminal to get things done. Don't you remember the exact command to compress a directory with `tar`? Just `asksh`.

```bash
$ asksh compress a directory with tar.gz excluding the .cache directory
> tar -czf archive.tar.gz --exclude=<dir>.cache <dir>
```

## Installation

### Ollama server

`asksh` expects an **Ollama** server. [Install Ollama](https://ollama.com/download), then pull a model (defaults match `config.example.toml`):

```bash
ollama pull qwen2.5-coder
```

### Install the CLI

**From PyPI:**

```bash
pipx install asksh
# or: uv tool install asksh
```

**From a clone** (development):

```bash
git clone git@github.com:srgsol/asksh.git
cd asksh
uv sync
uv run asksh --help
```

Requires **Python 3.10+**.

### Configuration (optional defaults)

Defaults can live in a TOML file so you do not repeat `--model` / `--base-url` on every run. CLI flags always override the file.

- **Default path:** `$XDG_CONFIG_HOME/asksh/config.toml`, or `~/.config/asksh/config.toml` if `XDG_CONFIG_HOME` is unset.
- **Override path:** set `ASKSH_CONFIG` to another file.

See `config.example.toml` in this repository. The config file currently applies **`model`** and **`base_url`** only (CLI flags still override them). Use **`--context`** on the command line for a context file path; there is no `context` key in the TOML.

## Publishing to PyPI (maintainers)

This project uses [flit](https://flit.pypa.io/) as the build backend. With `flit` installed and PyPI credentials configured:

```bash
uv build
# inspect dist/ then:
flit publish
```

Use [trusted publishing](https://docs.pypi.org/trusted-publishers/) (e.g. GitHub Actions) for releases when possible.
