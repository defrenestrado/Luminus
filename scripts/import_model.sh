#!/usr/bin/env bash
# Registra o GGUF do Luminus no Ollama via hardlink + manifesto (sem copiar/requantizar).
# Executar com sudo/pkexec.
set -euo pipefail

SRC="/home/ledilson/Documentos/rpggwang/model_cache/orcarouter/Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf"
NAME="luminus-27b"
MODELS=/usr/share/ollama/.ollama/models
BLOBS=$MODELS/blobs
MAN=$MODELS/manifests/registry.ollama.ai/library/$NAME

[ -f "$SRC" ] || { echo "GGUF nao encontrado: $SRC"; exit 1; }

echo "[1/4] Calculando sha256 (16GB, pode demorar)..."
SHA=$(sha256sum "$SRC" | cut -d' ' -f1)
SIZE=$(stat -c%s "$SRC")
echo "    sha256=$SHA size=$SIZE"

echo "[2/4] Blob do modelo (hardlink)..."
[ -f "$BLOBS/sha256-$SHA" ] || ln "$SRC" "$BLOBS/sha256-$SHA" 2>/dev/null || cp "$SRC" "$BLOBS/sha256-$SHA"

echo "[3/4] Blobs de template/params/config..."
T=$(mktemp -d)
printf '%s' '<|im_start|>system
{{ .System }}<|im_end|>
<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant' > "$T/template"
printf '%s' '{"num_ctx":8192,"repeat_penalty":1.05,"temperature":0.8,"stop":["<|im_start|>","<|im_end|>"]}' > "$T/params"
printf '%s' '{"model_format":"gguf","model_family":"qwen3","model_families":["qwen3"],"model_type":"27B","file_type":"Q4_K_M","architecture":"amd64","os":"linux"}' > "$T/config"

put(){ d=$(sha256sum "$1" | cut -d' ' -f1); s=$(stat -c%s "$1"); install -m644 "$1" "$BLOBS/sha256-$d"; echo "$d $s"; }
read CS CSZ <<<"$(put "$T/config")"
read PS PSZ <<<"$(put "$T/params")"
read TS TSZ <<<"$(put "$T/template")"

echo "[4/4] Escrevendo manifesto..."
mkdir -p "$MAN"
cat > "$MAN/latest" <<EOF
{"schemaVersion":2,"mediaType":"application/vnd.docker.distribution.manifest.v2+json","config":{"mediaType":"application/vnd.docker.container.image.v1+json","digest":"sha256:$CS","size":$CSZ},"layers":[{"mediaType":"application/vnd.ollama.image.model","digest":"sha256:$SHA","size":$SIZE},{"mediaType":"application/vnd.ollama.image.params","digest":"sha256:$PS","size":$PSZ},{"mediaType":"application/vnd.ollama.image.template","digest":"sha256:$TS","size":$TSZ}]}
EOF

chown -R ollama:ollama "$BLOBS/sha256-$SHA" "$BLOBS/sha256-$CS" "$BLOBS/sha256-$PS" "$BLOBS/sha256-$TS" "$MAN"
echo "OK -> modelo '$NAME' registrado (sha256:$SHA, $SIZE bytes)"
