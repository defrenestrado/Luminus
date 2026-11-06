#!/usr/bin/env bash
# Importa um GGUF para o Ollama via hardlink + manifesto (sem copiar/requantizar).
# Uso: import_gguf.sh <arquivo.gguf> <nome> [familia] [parametros] [tipo]
set -euo pipefail

SRC="$1"
NAME="$2"
FAMILY="${3:-qwen3}"
TYPE="${4:-model}"
MODELS=/usr/share/ollama/.ollama/models
BLOBS=$MODELS/blobs
MAN=$MODELS/manifests/registry.ollama.ai/library/$NAME

[ -f "$SRC" ] || { echo "GGUF nao encontrado: $SRC"; exit 1; }

echo "[1/4] sha256 (pode demorar)..."
SHA=$(sha256sum "$SRC" | cut -d' ' -f1)
SIZE=$(stat -c%s "$SRC")
echo "    sha256=$SHA size=$SIZE"

echo "[2/4] hardlink no blob store..."
[ -f "$BLOBS/sha256-$SHA" ] || ln "$SRC" "$BLOBS/sha256-$SHA" 2>/dev/null || cp "$SRC" "$BLOBS/sha256-$SHA"

case "$SRC" in
  *Q8_0*) FT=Q8_0;; *Q6_K*) FT=Q6_K;; *Q5_K_M*) FT=Q5_K_M;;
  *Q5_K_S*) FT=Q5_K_S;; *Q4_K_M*) FT=Q4_K_M;; *) FT=Q4_K_M;;
esac

echo "[3/4] template/params/config..."
T=$(mktemp -d)
printf '%s' '<|im_start|>system
{{ .System }}<|im_end|>
<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant' > "$T/template"
printf '%s' '{"num_ctx":32768,"repeat_penalty":1.05,"temperature":0.8,"stop":["<|im_start|>","<|im_end|>"]}' > "$T/params"
printf '%s' "{\"model_format\":\"gguf\",\"model_family\":\"$FAMILY\",\"model_families\":[\"$FAMILY\"],\"model_type\":\"$TYPE\",\"file_type\":\"$FT\",\"architecture\":\"amd64\",\"os\":\"linux\"}" > "$T/config"

put(){ d=$(sha256sum "$1" | cut -d' ' -f1); s=$(stat -c%s "$1"); install -m644 "$1" "$BLOBS/sha256-$d"; echo "$d $s"; }
read CS CSZ <<<"$(put "$T/config")"
read PS PSZ <<<"$(put "$T/params")"
read TS TSZ <<<"$(put "$T/template")"

echo "[4/4] manifesto..."
mkdir -p "$MAN"
cat > "$MAN/latest" <<EOF
{"schemaVersion":2,"mediaType":"application/vnd.docker.distribution.manifest.v2+json","config":{"mediaType":"application/vnd.docker.container.image.v1+json","digest":"sha256:$CS","size":$CSZ},"layers":[{"mediaType":"application/vnd.ollama.image.model","digest":"sha256:$SHA","size":$SIZE},{"mediaType":"application/vnd.ollama.image.params","digest":"sha256:$PS","size":$PSZ},{"mediaType":"application/vnd.ollama.image.template","digest":"sha256:$TS","size":$TSZ}]}
EOF

chown -R ollama:ollama "$BLOBS/sha256-$SHA" "$BLOBS/sha256-$CS" "$BLOBS/sha256-$PS" "$BLOBS/sha256-$TS" "$MAN"
echo "OK -> modelo '$NAME' (sha256:$SHA, $SIZE bytes)"
