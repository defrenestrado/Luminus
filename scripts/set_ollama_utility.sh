#!/usr/bin/env bash
# Segunda instancia do Ollama dedicada as funcoes auxiliares (planner/resumo/
# escolhas/capítulos) na RTX 2060 12GB (PCI 05:00). O 27B fica nas 3x 3060 Ti.
set -euo pipefail
cat > /etc/systemd/system/ollama-utility.service <<'EOF'
[Unit]
Description=Ollama Utility (9B: planner/resumo/escolhas) na RTX 2060
After=network.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="OLLAMA_HOST=127.0.0.1:11435"
Environment="CUDA_DEVICE_ORDER=PCI_BUS_ID"
Environment="CUDA_VISIBLE_DEVICES=2"
Environment="GGML_VK_VISIBLE_DEVICES=2"
Environment="OLLAMA_VULKAN=false"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ollama-utility >/dev/null 2>&1 || true
systemctl restart ollama-utility
sleep 3
echo -n "ollama-utility: "; systemctl is-active ollama-utility
echo -n "modelos na 11435: "; curl -s --max-time 10 http://127.0.0.1:11435/api/tags | head -c 160
echo
