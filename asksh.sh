#!/bin/bash
set -euo pipefail
ROOT="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
MODEL="qwen2.5-coder"
# MODEL="gemma4:latest"
BASE_URL="http://localhost:11434"

if [[ $# -eq 0 ]]; then
  # No arguments, start interactive chat
  exec uv run --project "$ROOT" python "$ROOT/app.py" \
    -c \
    --model "$MODEL" \
    --base-url "$BASE_URL"
else
  # Arguments, run as a command
  exec uv run --project "$ROOT" python "$ROOT/app.py" \
    --model "$MODEL" \
    --base-url "$BASE_URL" \
    --no-stream \
    "$@"
fi
