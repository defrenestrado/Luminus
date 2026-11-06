#!/usr/bin/env bash
# luminus-gpu-env — mantém CUDA_VISIBLE_DEVICES alinhado às GPUs saudáveis.
#
# Por que isso existe:
#   O serviço ollama é iniciado com CUDA_VISIBLE_DEVICES fixo (0,1,3 = as três
#   RTX 3060 Ti). Se uma placa cai do barramento PCIe, os índices do driver
#   mudam e o Ollama pode acabar enxergando a RTX 2060 (do serviço utilitário)
#   ou uma placa inexistente. Este script recalcula os índices sadios pelo
#   nvidia-smi e reescreve um drop-in systemd com a lista correta, reiniciando
#   apenas o serviço afetado.
#
# O servidor (server/gpu_guard.py) cuida da outra metade: escolhe um modelo que
# caiba na quantidade de GPUs que restou.
#
# Uso:
#   sudo /usr/local/sbin/luminus-gpu-env            # aplica se necessário
#   sudo /usr/local/sbin/luminus-gpu-env --dry-run  # só mostra o que faria
#
# Instalação (timer de 20s): sudo bash scripts/install_gpu_guard.sh

set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "Execute com sudo: sudo $0" >&2
  exit 1
fi

CONF=/etc/luminus/gpu-guard.conf
MAIN_MATCH="${LUMINUS_GPU_MAIN_MATCH:-3060 Ti}"
UTILITY_MATCH="${LUMINUS_GPU_UTILITY_MATCH:-2060}"
OLLAMA_DROPIN=/etc/systemd/system/ollama.service.d/zz-luminus-gpu-env.conf
UTILITY_DROPIN=/etc/systemd/system/ollama-utility.service.d/zz-luminus-gpu-env.conf
STATE_DIR=/run/luminus-gpu-env
LOCK_FILE=/run/luminus-gpu-env.lock
RESTART_DIR="${LUMINUS_GPU_RESTART_DIR:-/var/lib/luminus-gpu-guard}"
RESTART_MAIN=0
RESTART_UTILITY=0

if [ -f "$CONF" ]; then
  # shellcheck disable=SC1090
  . "$CONF"
fi

[ "$DRY_RUN" -eq 1 ] || mkdir -p "$STATE_DIR"

if [ "$DRY_RUN" -eq 0 ] && command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || exit 0
fi

# O servidor grava um pedido quando um erro fatal de CUDA envenena o contexto
# (llama-server abortou, "failed to initialize CUDA: unknown error" etc.).
# Aqui só reiniciamos o serviço; nenhum comando vem do arquivo.
check_restart_request() {
  local service="$1" file="$RESTART_DIR/restart-$1.request"
  [ -f "$file" ] || return 1
  local ts now age owner reason
  ts="$(sed -n '1p' "$file" 2>/dev/null | tr -dc '0-9' || true)"
  now="$(date +%s)"
  if [ -z "$ts" ] || [ "$((now - ts))" -gt 300 ] || [ "$((now - ts))" -lt -60 ]; then
    [ "$DRY_RUN" -eq 1 ] || rm -f "$file"
    return 1
  fi
  owner="$(stat -c '%U' "$file" 2>/dev/null || echo '')"
  if [ -n "${LUMINUS_APP_USER:-}" ] && [ "$owner" != "$LUMINUS_APP_USER" ]; then
    echo "[gpu-env] pedido de reinício ignorado (dono: $owner)" >&2
    [ "$DRY_RUN" -eq 1 ] || rm -f "$file"
    return 1
  fi
  reason="$(sed -n '3p' "$file" 2>/dev/null | cut -c1-200)"
  echo "[gpu-env] pedido de reinício de $service: ${reason:-sem motivo}"
  [ "$DRY_RUN" -eq 1 ] || rm -f "$file"
  return 0
}

if check_restart_request ollama; then
  RESTART_MAIN=1
fi
if check_restart_request ollama-utility; then
  RESTART_UTILITY=1
fi

restart_requested_only() {
  if [ "$RESTART_MAIN" -eq 1 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[gpu-env] (dry-run) systemctl restart ollama"
    else
      systemctl restart ollama || true
    fi
  fi
  if [ "$RESTART_UTILITY" -eq 1 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[gpu-env] (dry-run) systemctl restart ollama-utility"
    else
      systemctl restart ollama-utility || true
    fi
  fi
}

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "[gpu-env] nvidia-smi não encontrado" >&2
  restart_requested_only
  exit 0
fi

ROWS="$(nvidia-smi \
  --query-gpu=index,uuid,pci.bus_id,name,memory.total \
  --format=csv,noheader,nounits 2>/dev/null || true)"
# Falha total do nvidia-smi: não reescreve nada, mas atende pedido de reinício.
if [ -z "$ROWS" ]; then
  restart_requested_only
  exit 0
fi

MAIN_IDS=""
MAIN_UUIDS=""
UTIL_IDS=""
UTIL_UUIDS=""

while IFS=, read -r idx uuid bus name total; do
  idx="$(echo "$idx" | xargs)"
  uuid="$(echo "$uuid" | xargs)"
  bus="$(echo "$bus" | xargs)"
  name="$(echo "$name" | xargs)"
  total="$(echo "$total" | xargs)"

  # Linhas com erro/VRAM inválida são ignoradas.
  case "$total" in
    ''|N/A|*[!0-9]*) continue ;;
  esac
  [ "$total" -gt 0 ] || continue

  if [[ "$name" == *"$MAIN_MATCH"* ]]; then
    MAIN_IDS="${MAIN_IDS:+$MAIN_IDS,}$idx"
    MAIN_UUIDS="${MAIN_UUIDS:+$MAIN_UUIDS,}$uuid"
  elif [[ "$name" == *"$UTILITY_MATCH"* ]]; then
    UTIL_IDS="${UTIL_IDS:+$UTIL_IDS,}$idx"
    UTIL_UUIDS="${UTIL_UUIDS:+$UTIL_UUIDS,}$uuid"
  fi
done <<< "$ROWS"

# Nenhuma GPU reconhecida: não é seguro reescrever a configuração, mas um
# pedido explícito de reinício ainda é atendido.
if [ -z "$MAIN_UUIDS$UTIL_UUIDS" ]; then
  restart_requested_only
  exit 0
fi

current_applied() {
  local dropin="$1"
  if [ -f "$dropin" ]; then
    sed -n 's/^# luminus-gpu-env:\(.*\)$/\1/p' "$dropin" | head -n1
  fi
}

#Debounce: exige duas leituras consecutivas iguais antes de agir.
needs_change() {
  local pool="$1" uuids="$2" dropin="$3"
  local pending_file="$STATE_DIR/$pool.pending"
  local count_file="$STATE_DIR/$pool.count"
  local previous="" count=0

  local force=0
  if [ "$pool" = "main" ]; then
    force="$RESTART_MAIN"
  else
    force="$RESTART_UTILITY"
  fi
  if [ -f "$pending_file" ]; then
    previous="$(cat "$pending_file" 2>/dev/null || true)"
  fi
  if [ "$DRY_RUN" -eq 1 ] || [ "$force" -eq 1 ]; then
    count=2
  else
    if [ "$previous" = "$uuids" ]; then
      count="$(cat "$count_file" 2>/dev/null || echo 0)"
      count="$((count + 1))"
    else
      count=1
    fi
    printf '%s\n' "$uuids" > "$pending_file"
    printf '%s\n' "$count" > "$count_file"
  fi
  [ "$count" -ge 2 ] || return 1
  [ "$(current_applied "$dropin")" != "$uuids" ]
}

write_dropin() {
  local dropin="$1" ids="$2" uuids="$3" label="$4"
  local dir
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[gpu-env] (dry-run) $label: CUDA_VISIBLE_DEVICES=${ids:-<vazio/CPU>} ($uuids) -> $dropin"
    return
  fi
  dir="$(dirname "$dropin")"
  mkdir -p "$dir"
  cat > "$dropin" <<EOF
# Gerado por luminus-gpu-env ($label). Não edite à mão.
# luminus-gpu-env:$uuids
[Service]
Environment="CUDA_DEVICE_ORDER=PCI_BUS_ID"
Environment="CUDA_VISIBLE_DEVICES=$ids"
Environment="GGML_VK_VISIBLE_DEVICES="
Environment="OLLAMA_VULKAN=false"
EOF
  echo "[gpu-env] $label: CUDA_VISIBLE_DEVICES=${ids:-<vazio/CPU>} ($uuids)"
}

restart_service() {
  local service="$1"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[gpu-env] (dry-run) systemctl restart $service"
    return
  fi
  systemctl daemon-reload
  systemctl restart "$service" || true
}

CHANGED=0
MAIN_CHANGED=0
UTIL_CHANGED=0

# Sem nenhuma 3060 Ti saudável o principal fica em CPU (o Narrador cai para a
# 2060 via server/gpu_guard.py). O marcador "cpu" também passa pelo debounce.
MAIN_MARKER="${MAIN_UUIDS:-cpu}"
UTIL_MARKER="${UTIL_UUIDS:-cpu}"

if needs_change main "$MAIN_MARKER" "$OLLAMA_DROPIN"; then
  write_dropin "$OLLAMA_DROPIN" "$MAIN_IDS" "$MAIN_MARKER" "ollama principal"
  MAIN_CHANGED=1
  CHANGED=1
fi

if needs_change utility "$UTIL_MARKER" "$UTILITY_DROPIN"; then
  write_dropin "$UTILITY_DROPIN" "$UTIL_IDS" "$UTIL_MARKER" "ollama utilitário"
  UTIL_CHANGED=1
  CHANGED=1
fi

if [ "$MAIN_CHANGED" -eq 1 ] || [ "$RESTART_MAIN" -eq 1 ]; then
  restart_service ollama
  CHANGED=1
fi
if [ "$UTIL_CHANGED" -eq 1 ] || [ "$RESTART_UTILITY" -eq 1 ]; then
  restart_service ollama-utility
  CHANGED=1
fi

if [ "$CHANGED" -eq 0 ]; then
  echo "[gpu-env] sem mudanças (main=${MAIN_IDS:-none} util=${UTIL_IDS:-none})"
fi
