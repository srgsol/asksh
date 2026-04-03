#!/bin/bash
set -euo pipefail
ROOT="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
MODEL="qwen2.5-coder"

if [[ $# -eq 0 ]]; then
  exec uv run --project "$ROOT" python "$ROOT/app.py" \
    -c \
    --model "$MODEL" 
else
  exec uv run --project "$ROOT" python "$ROOT/app.py" --model "$MODEL" "$@"
fi
