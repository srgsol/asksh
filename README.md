# asksh

> AI in your terminal: turn plain English into shell commands.

`asksh` is an AI-powered command-line tool that lets you describe what you want in plain English and returns the exact POSIX shell command to run.

You don't need to leave your terminal to get things done. Don't you remember the exact command to compress a directory with `tar`? Just `asksh`.

```bash
$ asksh compress a directory with tar.gz excluding the .cache directory
> tar -czf archive.tar.gz --exclude=<dir>.cache <dir>
```

## Installation

### Ollama Server

`asksh` requires the **ollama** server running on your machine. [Install ollama here](https://ollama.com/download).

Pull the model used by `asksh` by running:

```bash
$ ollama pull qwen2.5-coder
```

### System requirements

- **Python 3.10** 
- Package manager: [**uv**](https://docs.astral.sh/uv/) 

### Download asksh

Clone this repository and install dependencies from the project root:

```bash
$ git clone git@github.com:srgsol/asksh.git
$ cd asksh
$ uv sync
```

### Add 'asksh' to your PATH

Locate the `asksh.sh` script in your local repository and create a symlink to the `asksh` script in your `PATH` (e.g. `~/.local/bin`). 

```bash
ln -sf "$(realpath path/to/asksh/asksh.sh)" ~/.local/bin/asksh
```

Be sure that `~/.local/bin` is in your `PATH`.

```bash
export PATH="$HOME/.local/bin:$PATH"
```

