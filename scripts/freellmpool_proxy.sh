#!/usr/bin/env bash
# Sobe o proxy OpenAI-compatible do freellmpool para o Luminus usar como pool
# de modelos na criação profunda de NPCs.
#
#   bash scripts/freellmpool_proxy.sh
#   # em outro terminal / no server/.env:
#   LUMINUS_NPC_POOL_URL=http://127.0.0.1:8080/v1
#   LUMINUS_NPC_POOL_MODEL=quality
#
# O freellmpool não vem instalado; escolha um:
#   pipx install freellmpool
#   # ou: uvx freellmpool ...
set -euo pipefail

PORT="${FREELLMPOOL_PORT:-8080}"
HOST="${FREELLMPOOL_HOST:-127.0.0.1}"

if command -v freellmpool >/dev/null 2>&1; then
  exec freellmpool proxy --host "$HOST" --port "$PORT"
fi

if command -v uvx >/dev/null 2>&1; then
  exec uvx freellmpool proxy --host "$HOST" --port "$PORT"
fi

cat >&2 <<'EOF'
freellmpool não encontrado.
Instale com:
  pipx install freellmpool
ou tenha o uv instalado para usar:
  uvx freellmpool proxy --port 8080
Depois configure no server/.env:
  LUMINUS_NPC_POOL_URL=http://127.0.0.1:8080/v1
  LUMINUS_NPC_POOL_MODEL=quality
EOF
exit 1
