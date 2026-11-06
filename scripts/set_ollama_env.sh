#!/usr/bin/env bash
# Configura o Ollama principal do Luminus: 27B nas 3x RTX 3060 Ti
# (PCI 01:00, 02:00 e 09:00). A RTX 2060 (05:00) fica para o utilitario/TTS.
set -euo pipefail
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
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
systemctl daemon-reload
systemctl restart ollama
sleep 3
systemctl show ollama -p Environment
echo "OK: reiniciado. CUDA 0,1,3 = as tres RTX 3060 Ti | RTX 2060 reservada."
