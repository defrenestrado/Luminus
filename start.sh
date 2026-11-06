#!/usr/bin/env bash
# Sobe o Luminus: Ollama (se preciso), API do jogo e servidor de download do APK.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IP="$(hostname -I | awk '{print $1}')"

if ! systemctl is-active --quiet ollama; then
  echo "Iniciando servico principal do Ollama (27B nas 3x 3060 Ti)..."
  sudo systemctl start ollama || true
fi

if ! systemctl is-active --quiet ollama-utility; then
  echo "Iniciando servico utilitario do Ollama (9B na 2060)..."
  sudo systemctl start ollama-utility || true
fi

# Narrador: se o server/.env aponta para a ponte do llama.cpp (MTP), garante
# que ela esteja de pe. Desligado = narrador segue no Ollama normal.
NARRATOR_BACKEND="Ollama"
if grep -qE '^[[:space:]]*OLLAMA_URL=http://127\.0\.0\.1:11436' "$DIR/server/.env" 2>/dev/null; then
  NARRATOR_BACKEND="llama.cpp + MTP"
  if ! systemctl --user is-active --quiet luminus-llamacpp 2>/dev/null; then
    echo "Iniciando ponte do llama.cpp (narrador com MTP)..."
    systemctl --user start luminus-llamacpp 2>/dev/null || true
  fi
fi

# API (inclui XTTS): usar a RTX 2060 (PCI 05:00) quando o CUDA estiver saudavel
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES=2
# IP atual da maquina: usado pela pagina/API e pela descoberta automatica (UDP)
export LUMINUS_HOST="$IP"
export LUMINUS_API_PORT=5111

if [ ! -f "$DIR/server/.env" ]; then
  echo "Dica: crie server/.env com DEEPSEEK_API_KEY para ativar a direcao de arte da DeepSeek."
fi

if pgrep -f "uvicorn main:app" >/dev/null; then
  echo "API ja rodando."
else
  cd "$DIR/server"
  nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 5111 > "$DIR/server/server.log" 2>&1 &
  echo "API iniciada."
fi

if pgrep -f "http.server 8000" >/dev/null; then
  echo "Servidor do APK ja rodando."
else
  cd "$DIR/dist"
  nohup python3 -m http.server 8000 --bind 0.0.0.0 > "$DIR/dist/apk.log" 2>&1 &
  echo "Servidor do APK iniciado."
fi

# Pre-carrega o narrador em segundo plano (evita esperar ~25 s no 1o turno).
curl -s -m 5 -X POST http://127.0.0.1:5111/api/warmup >/dev/null 2>&1 || true

echo
echo "No celular (mesma rede Wi-Fi):"
echo "  Baixar APK : http://$IP:8000"
echo "  API        : http://$IP:5111"
echo "  Modelo     : luminus-27b (3x 3060 Ti, $NARRATOR_BACKEND) + planner/resumo e TTS na RTX 2060"
