#!/usr/bin/env bash
# Instala/remove o vigia de ambiente das GPUs (camada systemd).
#
# O server/gpu_guard.py já troca o modelo quando uma GPU cai. Esta camada
# garante que o Ollama seja reiniciado com a lista correta de GPUs visíveis,
# inclusive quando o driver reenumera o barramento PCIe (placa "desaparece").
#
#   sudo bash scripts/install_gpu_guard.sh           # instala
#   sudo bash scripts/install_gpu_guard.sh --remove  # remove
#   sudo bash scripts/install_gpu_guard.sh --status  # status
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET=/usr/local/sbin/luminus-gpu-env
UNIT=/etc/systemd/system/luminus-gpu-env.service
TIMER=/etc/systemd/system/luminus-gpu-env.timer
CONF_DIR=/etc/luminus
CONF="$CONF_DIR/gpu-guard.conf"
RESTART_DIR=/var/lib/luminus-gpu-guard
APP_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute com sudo: sudo bash $0 $*" >&2
  exit 1
fi

case "${1:-}" in
  --remove)
    systemctl disable --now luminus-gpu-env.timer 2>/dev/null || true
    rm -f "$TARGET" "$UNIT" "$TIMER"
    systemctl daemon-reload
    echo "Removido. Os drop-ins zz-luminus-gpu-env.conf foram mantidos;"
    echo "apague manualmente se quiser voltar ao CUDA_VISIBLE_DEVICES antigo:"
    echo "  /etc/systemd/system/ollama.service.d/zz-luminus-gpu-env.conf"
    echo "  /etc/systemd/system/ollama-utility.service.d/zz-luminus-gpu-env.conf"
    exit 0
    ;;
  --status)
    systemctl status luminus-gpu-env.timer --no-pager || true
    echo
    "$TARGET" --dry-run || true
    exit 0
    ;;
esac

install -m 0755 "$DIR/gpu_guard_env.sh" "$TARGET"

mkdir -p "$CONF_DIR"
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$RESTART_DIR"
if [ ! -f "$CONF" ]; then
  cat > "$CONF" <<EOF
# Filtros usados para separar as GPUs do Narrador (ollama principal) da GPU
# utilitária (ollama-utility). Edite se trocar de placa.
LUMINUS_GPU_MAIN_MATCH="3060 Ti"
LUMINUS_GPU_UTILITY_MATCH="2060"
# Usuário dono do servidor Luminus; só ele pode pedir reinício do Ollama.
LUMINUS_APP_USER="$APP_USER"
EOF
fi
if ! grep -q '^LUMINUS_APP_USER=' "$CONF" 2>/dev/null; then
  echo "LUMINUS_APP_USER=\"$APP_USER\"" >> "$CONF"
fi

cat > "$UNIT" <<'EOF'
[Unit]
Description=Luminus GPU env guard (CUDA_VISIBLE_DEVICES)
After=ollama.service ollama-utility.service
Wants=ollama.service ollama-utility.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/luminus-gpu-env
EOF

cat > "$TIMER" <<'EOF'
[Unit]
Description=Executa o Luminus GPU env guard periodicamente

[Timer]
OnBootSec=30s
OnUnitActiveSec=20s
AccuracySec=5s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now luminus-gpu-env.timer
echo "Instalado. O timer roda a cada 20s."
echo "Teste: sudo $TARGET --dry-run"
echo "Logs : journalctl -u luminus-gpu-env -n 50 --no-pager"
