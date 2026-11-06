#!/usr/bin/env bash
# Liga/desliga o narrador no llama.cpp (MTP) sem mexer no codigo do app:
# o OLLAMA_URL do server/.env passa a apontar para a ponte (llamacpp_shim.py).
#
#   bash scripts/llamacpp.sh on      # usar llama.cpp + MTP
#   bash scripts/llamacpp.sh off     # voltar ao Ollama
#   bash scripts/llamacpp.sh status
#   bash scripts/llamacpp.sh warm    # pre-carrega o modelo agora
#   bash scripts/llamacpp.sh log     # ultimas linhas do llama-server
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$DIR/server/.env"
HOME_DIR="${LUMINUS_LLAMACPP_HOME:-$HOME/.local/share/luminus/llamacpp}"
CONFIG="$HOME_DIR/config.env"
PY="${PYTHON:-python3}"

cfg() {
  local key="$1" def="${2:-}"
  if [ -f "$CONFIG" ]; then
    local v
    v="$(grep -E "^${key}=" "$CONFIG" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    [ -n "$v" ] && { printf '%s' "$v"; return; }
  fi
  printf '%s' "$def"
}

SHIM_PORT="$(cfg SHIM_PORT 11436)"
OLLAMA_REAL="$(cfg OLLAMA http://127.0.0.1:11434)"
MODEL="$(cfg NARRATORS luminus-27b)"
SHIM_URL="http://127.0.0.1:${SHIM_PORT}"

UNIT="luminus-llamacpp.service"
UNIT_PATH="$HOME/.config/systemd/user/$UNIT"

shim_up() { curl -s -m 2 "${SHIM_URL}/luminus/status" >/dev/null 2>&1; }
have_systemd() { systemctl --user show-environment >/dev/null 2>&1; }

write_unit() {
  mkdir -p "$(dirname "$UNIT_PATH")"
  cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Luminus llama.cpp shim (narrador com MTP)
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR/scripts
Environment=PYTHONUNBUFFERED=1
Environment=LUMINUS_LLAMACPP_CONFIG=$CONFIG
ExecStart=$PY $DIR/scripts/llamacpp_shim.py
KillMode=control-group
TimeoutStopSec=30
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
}

stop_shim() {
  if have_systemd && [ -f "$UNIT_PATH" ]; then
    systemctl --user disable --now "$UNIT" >/dev/null 2>&1 || true
  fi
  if [ -f "$HOME_DIR/shim.pid" ]; then
    kill "$(cat "$HOME_DIR/shim.pid")" 2>/dev/null || true
    rm -f "$HOME_DIR/shim.pid"
  fi
  fuser -k "${SHIM_PORT}/tcp" 2>/dev/null || true
  sleep 1
}

start_shim() {
  stop_shim
  mkdir -p "$HOME_DIR"
  if have_systemd; then
    write_unit
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT" >/dev/null 2>&1 || true
    systemctl --user restart "$UNIT"
  else
    setsid --fork bash -c "exec '$PY' '$DIR/scripts/llamacpp_shim.py' >> '$HOME_DIR/shim.log' 2>&1" \
      </dev/null >/dev/null 2>&1
  fi
  for _ in $(seq 1 60); do
    shim_up && return 0
    sleep 0.5
  done
  echo "ERRO: a ponte nao subiu; veja $HOME_DIR/shim.log" >&2
  tail -20 "$HOME_DIR/shim.log" >&2 || true
  return 1
}

env_set() {
  local value="$1"
  touch "$ENV_FILE"
  if grep -qE '^#?[[:space:]]*OLLAMA_URL=' "$ENV_FILE"; then
    sed -i -E "s|^#?[[:space:]]*OLLAMA_URL=.*|OLLAMA_URL=${value}|" "$ENV_FILE"
  else
    printf '\n# Narrador via llama.cpp (MTP). Gerenciado por scripts/llamacpp.sh\nOLLAMA_URL=%s\n' "$value" >> "$ENV_FILE"
  fi
}

env_unset() {
  touch "$ENV_FILE"
  if grep -qE '^[[:space:]]*OLLAMA_URL=' "$ENV_FILE"; then
    sed -i -E "s|^[[:space:]]*OLLAMA_URL=.*|# OLLAMA_URL desativado por scripts/llamacpp.sh off|" "$ENV_FILE"
  fi
}

restart_api() {
  if systemctl --user is-active --quiet luminus-api 2>/dev/null; then
    systemctl --user restart luminus-api
    echo "API reiniciada (luminus-api)."
  elif pgrep -f "uvicorn main:app" >/dev/null 2>&1; then
    echo "AVISO: a API roda fora do systemd; reinicie-a (bash start.sh) para aplicar." >&2
  else
    echo "AVISO: API nao esta rodando; suba com bash start.sh." >&2
  fi
}

unload_ollama() {
  curl -s -m 30 -X POST "${OLLAMA_REAL}/api/generate" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${MODEL}\",\"keep_alive\":0}" >/dev/null 2>&1 || true
}

do_on() {
  [ -f "$CONFIG" ] || { echo "Instale primeiro: bash scripts/install_llamacpp.sh" >&2; exit 1; }
  echo "Liberando VRAM do Ollama ..."
  unload_ollama
  env_set "$SHIM_URL"
  echo "Iniciando a ponte em ${SHIM_URL} ..."
  start_shim
  restart_api
  echo
  echo "Narrador agora: llama.cpp (${MODEL}) com MTP."
  echo "O modelo carrega no primeiro turno (~30 s). Para adiantar: bash scripts/llamacpp.sh warm"
}

do_off() {
  env_unset
  echo "Parando a ponte e o llama-server ..."
  stop_shim
  restart_api
  echo
  echo "Narrador voltou ao Ollama (${OLLAMA_REAL})."
}

do_warm() {
  shim_up || { echo "Ponte desligada; rode: bash scripts/llamacpp.sh on" >&2; exit 1; }
  echo "Carregando o modelo (pode levar ~30 s) ..."
  curl -s -m 600 -X POST "${SHIM_URL}/api/generate" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"ok\",\"stream\":false,\"options\":{\"num_predict\":1,\"num_ctx\":$(cfg CTX 32768)}}" \
    | head -c 300
  echo
}

do_status() {
  echo "config : $CONFIG"
  if [ -f "$CONFIG" ]; then sed 's/^/  /' "$CONFIG"; else echo "  (nao instalado)"; fi
  echo "OLLAMA_URL em server/.env: $(grep -E '^[[:space:]]*OLLAMA_URL=' "$ENV_FILE" 2>/dev/null || echo '(padrao: Ollama 11434)')"
  echo "ponte  : $([ "$(shim_up && echo up || echo down)" = up ] && echo "up em $SHIM_URL" || echo down)"
  if have_systemd; then
    echo "servico: $UNIT $([ "$(systemctl --user is-enabled "$UNIT" 2>/dev/null)" = enabled ] && echo '(habilitado no boot)' || echo '(nao habilitado)')"
  fi
  if shim_up; then
    curl -s "${SHIM_URL}/luminus/status" | "$PY" -m json.tool 2>/dev/null | sed 's/^/  /'
  fi
  echo "GPUs:"
  nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/  /'
}

case "${1:-status}" in
  on)     do_on ;;
  off)    do_off ;;
  warm)   do_warm ;;
  status) do_status ;;
  log)    tail -n "${2:-40}" "$HOME_DIR/server.log" ;;
  restart) stop_shim; start_shim; restart_api ;;
  *)      echo "uso: $0 {on|off|status|warm|log|restart}" >&2; exit 2 ;;
esac
