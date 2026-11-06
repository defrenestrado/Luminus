#!/usr/bin/env bash
# Alterna workers ComfyUI entre as GPUs do Luminus.
#   GPU 0/1/3 = RTX 3060 Ti 8GB (portas 8189/8190/8191) -> lote SDXL / paralelo
#   GPU 2     = RTX 2060 12GB (porta 8188)             -> FLUX GGUF / imagem unica
# As 3060 Ti sao reservadas ao narrador (ollama 27B). O script se recusa a
# subir worker nelas enquanto houver modelo carregado, salvo FORCE=1.
set -u

OLLAMA=${OLLAMA_URL:-http://127.0.0.1:11434}

declare -A NAME=( [0]="RTX 3060 Ti" [1]="RTX 3060 Ti" [2]="RTX 2060 12GB" [3]="RTX 3060 Ti" )
declare -A PORT=( [0]=8189 [1]=8190 [2]=8188 [3]=8191 )
declare -A UNIT=( [0]=luminus-comfy-gpu0 [1]=luminus-comfy-gpu1 [2]=luminus-comfy [3]=luminus-comfy-gpu3 )

usage() {
  echo "uso: $0 {status|start|stop|restart} [gpu...|all]"
  echo "  gpu: 0 1 2 3   (2 = RTX 2060 / FLUX, 0/1/3 = 3060 Ti / lote)"
  echo "  FORCE=1 permite subir 3060 Ti com o narrador carregado."
}

models_loaded() {
  curl -s -m 3 "$OLLAMA/api/ps" 2>/dev/null \
    | grep -q '"name"' && return 0
  return 1
}

healthy() {
  curl -s -m 2 "http://127.0.0.1:$1/system_stats" >/dev/null 2>&1
}

wait_up() {
  local gpu=$1 port=${PORT[$1]}
  for _ in $(seq 1 90); do
    healthy "$port" && { echo "GPU $gpu pronta em http://127.0.0.1:$port"; return 0; }
    sleep 1
  done
  echo "ERRO: GPU $gpu nao respondeu na porta $port (veja: journalctl --user -u ${UNIT[$gpu]} -n 50)"
  return 1
}

resolve_gpus() {
  local out=()
  for arg in "$@"; do
    if [ "$arg" = "all" ]; then
      out+=(0 1 3)
    elif [[ "$arg" =~ ^[0-3]$ ]]; then
      out+=("$arg")
    else
      echo "GPU invalida: $arg" >&2
      exit 2
    fi
  done
  printf '%s\n' "${out[@]}"
}

do_status() {
  local now
  now=$(nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu \
        --format=csv,noheader 2>/dev/null)
  echo "$now" | while IFS=, read -r idx name used total util; do
    idx=$(echo "$idx" | tr -d ' ')
    name=$(echo "$name" | sed 's/^ *//')
    used=$(echo "$used" | sed 's/ MiB//;s/^ *//')
    total=$(echo "$total" | sed 's/ MiB//;s/^ *//')
    state=$(systemctl --user is-active "${UNIT[$idx]}" 2>/dev/null)
    port=${PORT[$idx]}
    if healthy "$port"; then api="up"; else api="down"; fi
    printf 'GPU %s  %-13s %5s/%-5s MiB  util=%-4s  %s:%s  service=%s  api=%s\n' \
      "$idx" "$name" "$used" "$total" "$util" "${UNIT[$idx]}" "$port" "$state" "$api"
  done
  if models_loaded; then
    echo
    echo "AVISO: ollama com modelo carregado nas 3060 Ti (narrador ativo)."
  fi
}

start_one() {
  local gpu=$1
  if [ "$gpu" != "2" ] && models_loaded && [ "${FORCE:-0}" != "1" ]; then
    echo "GPU $gpu ocupada pelo narrador (ollama). Pare o modelo ou use FORCE=1."
    return 1
  fi
  if healthy "${PORT[$gpu]}"; then
    echo "GPU $gpu ja esta rodando em http://127.0.0.1:${PORT[$gpu]}"
    return 0
  fi
  systemctl --user start "${UNIT[$gpu]}" || {
    echo "falha ao iniciar ${UNIT[$gpu]}"; return 1; }
  wait_up "$gpu"
}

stop_one() {
  local gpu=$1
  systemctl --user stop "${UNIT[$gpu]}" 2>/dev/null || true
  echo "GPU $gpu parada (${UNIT[$gpu]})"
}

action=${1:-status}
shift || true
if [ "$action" = "status" ]; then
  do_status
  exit 0
fi
if [ "$action" = "help" ] || [ "$action" = "-h" ] || [ "$action" = "--help" ]; then
  usage
  exit 0
fi
if [ "$#" -eq 0 ]; then
  usage
  exit 2
fi

mapfile -t gpus < <(resolve_gpus "$@")
rc=0
case "$action" in
  start)   for g in "${gpus[@]}"; do start_one "$g" || rc=1; done ;;
  stop)    for g in "${gpus[@]}"; do stop_one "$g"; done ;;
  restart) for g in "${gpus[@]}"; do stop_one "$g"; start_one "$g" || rc=1; done ;;
  *)       usage; exit 2 ;;
esac
exit "$rc"
