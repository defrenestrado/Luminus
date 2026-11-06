#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute com sudo: sudo bash $0" >&2
  exit 1
fi

OLLAMA_DROPIN=/etc/systemd/system/ollama.service.d/override.conf
UTIL_DIR=/etc/systemd/system/ollama-utility.service.d
UTIL_DROPIN=$UTIL_DIR/override.conf
STAMP=$(date +%Y%m%d%H%M%S)

mkdir -p "$(dirname "$OLLAMA_DROPIN")"
[ -f "$OLLAMA_DROPIN" ] && cp -a "$OLLAMA_DROPIN" "$OLLAMA_DROPIN.bak.$STAMP"

cat > "$OLLAMA_DROPIN" <<'EOF'
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="CUDA_DEVICE_ORDER=PCI_BUS_ID"
Environment="CUDA_VISIBLE_DEVICES=0,1,3"
Environment="GGML_VK_VISIBLE_DEVICES=0,1,3"
Environment="OLLAMA_VULKAN=false"
EOF

mkdir -p "$UTIL_DIR"
[ -f "$UTIL_DROPIN" ] && cp -a "$UTIL_DROPIN" "$UTIL_DROPIN.bak.$STAMP"

cat > "$UTIL_DROPIN" <<'EOF'
[Service]
Environment="CUDA_DEVICE_ORDER=PCI_BUS_ID"
Environment="CUDA_VISIBLE_DEVICES=2"
Environment="GGML_VK_VISIBLE_DEVICES=2"
Environment="OLLAMA_VULKAN=false"
EOF

systemctl daemon-reload
systemctl restart ollama ollama-utility
sleep 3

systemctl is-active ollama ollama-utility
nvidia-smi --query-gpu=index,pci.bus_id,name,memory.used,memory.free --format=csv
