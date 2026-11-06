#!/usr/bin/env bash
# Instala o llama.cpp (binario CUDA, sem compilar) para o narrador do Luminus.
# Nao usa sudo. Baixa ~750 MB uma unica vez em ~/.local/share/luminus/llamacpp.
#
#   bash scripts/install_llamacpp.sh            # instala / atualiza
#   bash scripts/install_llamacpp.sh --status
#   bash scripts/install_llamacpp.sh --remove
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${LUMINUS_LLAMACPP_HOME:-$HOME/.local/share/luminus/llamacpp}"
TAG="${LUMINUS_LLAMACPP_TAG:-b11998}"
MODEL="${LUMINUS_MODEL:-luminus-27b}"
PKG="llama-${TAG}-bin-ubuntu-cuda-12.8-x64"
CUDART="cudart-llama-${TAG}-bin-ubuntu-cuda-12.8-x64"
CONFIG="$HOME_DIR/config.env"

say() { printf '%s\n' "$*"; }

status() {
  say "config : $CONFIG"
  if [ -f "$CONFIG" ]; then sed 's/^/  /' "$CONFIG"; else say "  (nao instalado)"; fi
  if [ -x "$HOME_DIR/llama-${TAG}/llama-server" ]; then
    say "binario: $HOME_DIR/llama-${TAG}/llama-server (ok)"
  else
    say "binario: ausente"
  fi
  if [ -e "$HOME_DIR/model.gguf" ]; then
    say "modelo : $(readlink -f "$HOME_DIR/model.gguf")"
  else
    say "modelo : link ausente"
  fi
}

if [ "${1:-}" = "--status" ]; then status; exit 0; fi

if [ "${1:-}" = "--remove" ]; then
  if [ -f "$HOME_DIR/shim.pid" ]; then
    kill "$(cat "$HOME_DIR/shim.pid")" 2>/dev/null || true
  fi
  rm -rf "$HOME_DIR"
  say "removido: $HOME_DIR"
  exit 0
fi

mkdir -p "$HOME_DIR"
cd "$HOME_DIR"

fetch() {
  local url="$1" out="$2"
  if [ -s "$out" ]; then say "ja existe: $out"; return 0; fi
  say "baixando $out ..."
  curl -L --retry 3 --fail -# -o "$out.part" "$url"
  mv "$out.part" "$out"
}

BASE="https://github.com/ggml-org/llama.cpp/releases/download/${TAG}"
if [ ! -d "$CUDART" ]; then
  fetch "$BASE/${CUDART}.tar.gz" "${CUDART}.tar.gz"
  say "extraindo runtime CUDA ..."
  tar xzf "${CUDART}.tar.gz"
  rm -f "${CUDART}.tar.gz"
else
  say "ja existe: $CUDART"
fi
if [ ! -d "llama-${TAG}" ]; then
  fetch "$BASE/${PKG}.tar.gz" "${PKG}.tar.gz"
  say "extraindo llama.cpp ..."
  tar xzf "${PKG}.tar.gz"
  rm -f "${PKG}.tar.gz"
else
  say "ja existe: llama-${TAG}"
fi

if [ ! -x "llama-${TAG}/llama-server" ]; then
  say "ERRO: $HOME_DIR/llama-${TAG}/llama-server nao apareceu" >&2
  exit 1
fi

# Link para o GGUF que o Ollama ja tem em disco.
BLOB="$(ollama show --modelfile "$MODEL" 2>/dev/null | awk '/^FROM /{print $2; exit}')"
if [ -z "$BLOB" ] || [ ! -f "$BLOB" ]; then
  say "ERRO: nao achei o GGUF de '$MODEL' via 'ollama show --modelfile'." >&2
  say "      Informe o caminho em LUMINUS_LLAMACPP_MODEL=/caminho/modelo.gguf" >&2
  exit 1
fi
ln -sfn "$BLOB" "$HOME_DIR/model.gguf"
say "modelo: $BLOB"

LIBDIR="$HOME_DIR/llama-${TAG}:$HOME_DIR/${CUDART}"
cat > "$CONFIG" <<EOF
# Gerado por scripts/install_llamacpp.sh -- nao edite a mao.
BIN=$HOME_DIR/llama-${TAG}/llama-server
LIBDIR=$LIBDIR
MODEL=$HOME_DIR/model.gguf
PORT=8199
CTX=32768
MAX_CTX=32768
GPUS=0,1,3
SPLIT=layer
SPEC=draft-mtp
DRAFT_N=2
KV=q8_0
REPEAT_PENALTY=1.05
EXTRA=
SHIM_PORT=11436
OLLAMA=http://127.0.0.1:11434
NARRATORS=$MODEL
LOG=$HOME_DIR/server.log
SHIM_LOG=$HOME_DIR/shim.log
PIDFILE=$HOME_DIR/shim.pid
EOF

say
say "Testando o binario ..."
LD_LIBRARY_PATH="$LIBDIR" "$HOME_DIR/llama-${TAG}/llama-server" --version 2>&1 | tail -3
say
say "Instalado em $HOME_DIR"
say "Ative com: bash scripts/llamacpp.sh on"
