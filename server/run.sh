#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port "${LUMINUS_PORT:-5111}"
