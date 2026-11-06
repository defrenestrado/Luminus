#!/usr/bin/env bash
# Instala/remove o gpu-unlock (Zagitta/gpu-unlock) nas GPUs do Luminus.
#
# O modulo remove, antes do driver nvidia carregar, o limite que a NVIDIA
# impoe as GeForce em FP16/BF16/TF32 com acumulo FP32. Ganho esperado nas
# RTX 3060 Ti: ~+80% de tensor core (imagem, prefill de LLM).
#
# Nossas placas:
#   RTX 3060 Ti (Ampere) -> suporte estavel
#   RTX 2060    (Turing) -> experimental (sem BF16 nativo; ganho em FP16)
#
# Uso:
#   sudo bash scripts/install_gpu_unlock.sh              # instala + ativa no boot
#   sudo bash scripts/install_gpu_unlock.sh --no-opt-in  # instala sem mexer no boot
#   sudo bash scripts/install_gpu_unlock.sh --targets=0000:01:00.0,0000:02:00.0
#   sudo bash scripts/install_gpu_unlock.sh --status
#   sudo bash scripts/install_gpu_unlock.sh --remove
#
# O modulo so roda antes do nvidia no boot: reinicie depois de instalar.
# Medir antes/depois: server/.venv/bin/python scripts/gpu_unlock_bench.py --phase post
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${LUMINUS_GPU_UNLOCK_SRC:-/opt/luminus/gpu-unlock}"
CONF_DIR=/etc/luminus
CONF="$CONF_DIR/gpu-unlock.conf"
RUN_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
CACHE="${GPU_UNLOCK_CACHE:-$RUN_HOME/.cache/gpu-unlock}"
TARGETS="${LUMINUS_GPU_UNLOCK_TARGETS:-all}"
ACTION=install
OPT_IN=1

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --status)   ACTION=status ;;
    --remove)   ACTION=remove ;;
    --no-opt-in) OPT_IN=0 ;;
    --targets=*) TARGETS="${1#--targets=}" ;;
    --help|-h)  usage; exit 0 ;;
    *) echo "Opcao desconhecida: $1 (use --help)" >&2; exit 2 ;;
  esac
  shift
done

case "$TARGETS" in
  ""|*[!a-zA-Z0-9:.,]*) echo "targets invalido: '$TARGETS'" >&2; exit 2 ;;
esac

have() { command -v "$1" >/dev/null 2>&1; }
need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Execute com sudo: sudo bash $0 $*" >&2
    exit 1
  fi
}
repo_version() {
  if [ -f "$SRC/VERSION" ]; then cat "$SRC/VERSION"; else
    local conf
    conf=$(ls -d /usr/src/gpu-unlock-*/dkms.conf 2>/dev/null | head -1) || return 1
    sed -n 's/^PACKAGE_VERSION="\(.*\)"/\1/p' "$conf"
  fi
}

cmd_status() {
  echo "== gpu-unlock: status =="
  nvidia-smi --query-gpu=index,name,pci.bus_id --format=csv,noheader | sed 's/^/  /'
  echo
  echo "-- DKMS --"
  dkms status 2>/dev/null | grep -i gpu-unlock || echo "  nao registrado"
  echo "-- modulo instalado --"
  modinfo gpu_unlock 2>/dev/null | grep -E '^(filename|version|description)' | sed 's/^/  /' \
    || echo "  nao encontrado"
  echo "-- modulo carregado neste boot --"
  if grep -q '^gpu_unlock ' /proc/modules; then echo "  sim"; else echo "  nao"; fi
  echo "-- opcao de boot (cmdline atual) --"
  tr ' ' '\n' < /proc/cmdline | grep gpu_unlock || echo "  ausente"
  echo "-- opcao salva (kernelstub) --"
  grep -o 'gpu_unlock.targets=[^"]*' /etc/kernelstub/configuration 2>/dev/null \
    || echo "  ausente"
  echo "-- firmware --"
  ls /usr/lib/firmware/gpu-unlock/*/ 2>/dev/null | sed 's/^/  /' || echo "  ausente"
  echo "-- log do boot --"
  journalctl -k -b --no-pager 2>/dev/null | grep -i gpu_unlock | tail -15 | sed 's/^/  /' \
    || echo "  sem entradas"
}

cmd_remove() {
  need_root "$@"
  echo "== removendo gpu-unlock =="
  local opt
  opt=$(grep -o 'gpu_unlock.targets=[^"]*' /etc/kernelstub/configuration 2>/dev/null | head -1) || true
  if [ -n "$opt" ] && have kernelstub; then
    echo "  removendo do boot: $opt"
    kernelstub -d "$opt" || true
  fi
  local ver
  ver=$(repo_version) || ver=""
  if [ -n "$ver" ]; then
    dkms remove -m gpu-unlock -v "$ver" --all 2>/dev/null || true
    rm -rf "/usr/src/gpu-unlock-$ver"
  fi
  rm -rf /usr/lib/firmware/gpu-unlock \
         /usr/share/initramfs-tools/modules.d/gpu-unlock.conf \
         /usr/share/initramfs-tools/scripts/init-top/gpu-unlock \
         /usr/lib/dracut/dracut.conf.d/gpu-unlock.conf \
         /usr/share/doc/gpu-unlock /usr/share/licenses/gpu-unlock \
         /usr/share/licenses/gpu-unlock-firmware
  rm -f "$CONF"
  update-initramfs -u -k all
  echo "Removido. Reinicie para voltar ao driver sem o modulo."
  echo "O checkout em $SRC e o cache em $CACHE foram mantidos."
}

cmd_install() {
  need_root "$@"
  have nvidia-smi || { echo "nvidia-smi nao encontrado" >&2; exit 1; }

  echo "== gpu-unlock no Luminus =="
  nvidia-smi --query-gpu=index,name,pci.bus_id --format=csv,noheader | sed 's/^/  /'
  echo "Alvo no boot: gpu_unlock.targets=$TARGETS"
  echo

  local missing=()
  have dkms || missing+=(dkms)
  have git || missing+=(git)
  have make || missing+=(make)
  have gcc || missing+=(gcc)
  have python3 || missing+=(python3)
  have zstd || missing+=(zstd)
  have xz || missing+=(xz-utils)
  have update-initramfs || missing+=(initramfs-tools)
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "== Dependencias: ${missing[*]} =="
    apt-get update -qq
    apt-get install -y "${missing[@]}"
  fi

  local kernel
  kernel="$(uname -r)"
  if [ ! -d "/lib/modules/$kernel/build" ]; then
    echo "== Headers do kernel $kernel =="
    apt-get install -y "linux-headers-$kernel"
  fi

  echo "== Checkout em $SRC =="
  if [ -d "$SRC/.git" ]; then
    git -C "$SRC" fetch --depth 1 origin main
    git -C "$SRC" reset --hard FETCH_HEAD
    git -C "$SRC" clean -fdx >/dev/null
  else
    mkdir -p "$(dirname "$SRC")"
    git clone --depth 1 https://github.com/Zagitta/gpu-unlock "$SRC"
  fi
  local version commit
  version="$(cat "$SRC/VERSION")"
  commit="$(git -C "$SRC" rev-parse --short HEAD)"
  echo "  versao $version (commit $commit)"

  echo "== Firmware (baixa ~370 MB da NVIDIA na primeira vez) =="
  mkdir -p "$CACHE/inputs" "$CACHE/assets"
  if [ ! -f "$CACHE/assets/gsp_ga10x.bin" ] || [ ! -f "$CACHE/assets/booter_load_ga102.bin" ]; then
    make -C "$SRC" assets INPUTS="$CACHE/inputs" ASSETS="$CACHE/assets"
    chown -R "$RUN_USER" "$CACHE" 2>/dev/null || true
  else
    echo "  reaproveitando $CACHE/assets"
  fi

  echo "== Instalando modulo, firmware e hooks de initramfs =="
  make -C "$SRC" install-dkms install-firmware install-initramfs-tools \
    install-licenses DESTDIR=/ ASSETS="$CACHE/assets"

  echo "== Registrando no DKMS =="
  dkms add -m gpu-unlock -v "$version" 2>/dev/null || true
  local kdir kver
  for kdir in /lib/modules/*/build; do
    [ -d "$kdir" ] || continue
    kver="$(basename "$(dirname "$kdir")")"
    echo "  kernel $kver"
    dkms build -m gpu-unlock -v "$version" -k "$kver" --force >/dev/null 2>&1 || true
    dkms install -m gpu-unlock -v "$version" -k "$kver" --force
  done

  echo "== Regerando initramfs =="
  update-initramfs -u -k all

  mkdir -p "$CONF_DIR"
  printf 'LUMINUS_GPU_UNLOCK_TARGETS="%s"\n' "$TARGETS" > "$CONF"

  if [ "$OPT_IN" -eq 1 ]; then
    local option="gpu_unlock.targets=$TARGETS"
    if have kernelstub; then
      if grep -q "gpu_unlock.targets=" /etc/kernelstub/configuration 2>/dev/null; then
        echo "== Ajustando opcao de boot: $option =="
        kernelstub -d "$(grep -o 'gpu_unlock.targets=[^"]*' /etc/kernelstub/configuration | head -1)" || true
      fi
      echo "== Ativando no boot: $option =="
      kernelstub -a "$option"
    else
      echo "kernelstub nao encontrado. Adicione aos parametros do kernel:"
      echo "  $option"
    fi
  else
    echo "Instalado sem ativar no boot."
    echo "Para ativar: sudo kernelstub -a \"gpu_unlock.targets=$TARGETS\""
  fi

  echo
  echo "== Resumo =="
  modinfo gpu_unlock 2>/dev/null | grep -E '^(filename|version|description)' | sed 's/^/  /' || true
  echo "  firmware: $(find /usr/lib/firmware/gpu-unlock -type f 2>/dev/null | wc -l) arquivos"
  echo
  echo "REINICIE para o modulo rodar antes do driver NVIDIA."
  echo "Depois do boot: journalctl -k -b | grep gpu_unlock"
  echo "E meça: server/.venv/bin/python scripts/gpu_unlock_bench.py --phase post"
}

case "$ACTION" in
  status) cmd_status ;;
  remove) cmd_remove "$@" ;;
  install) cmd_install "$@" ;;
esac
