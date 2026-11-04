#!/usr/bin/env bash
# Install the locked production dependencies into the repository .venv.
#
# Default: uv sync --frozen --no-dev.
# UV_INDEX_URL: export the locked requirements and install them through that
# index; required on hosts that cannot reach pypi.org directly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UV="${UV:-uv}"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"

if [[ ! -x "$PYTHON" ]]; then
    "$UV" venv "$PROJECT_ROOT/.venv"
fi

if [[ -n "${UV_INDEX_URL:-}" ]]; then
    requirements="$(mktemp)"
    trap 'rm -f "$requirements"' EXIT
    "$UV" export --frozen --no-dev --no-hashes --no-emit-project -o "$requirements"
    "$UV" pip install --python "$PYTHON" --index-url "$UV_INDEX_URL" -r "$requirements"
    "$UV" pip install --python "$PYTHON" --no-deps -e .
else
    "$UV" sync --frozen --no-dev
fi

"$PYTHON" -c "import simtradedata"
