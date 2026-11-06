#!/usr/bin/env bash
# Gera a versao web (PC/navegador) e publica em dist/web para a porta 8000.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/app"
DIST="$ROOT/dist"

export PATH="/home/ledilson/development/flutter/bin:$PATH"

cd "$APP"
flutter pub get
flutter build web --release --base-href /web/

rm -rf "$DIST/web"
cp -r "$APP/build/web" "$DIST/web"
echo "[web] publicado em $DIST/web"
echo "[web] abra http://IP:8000/web/"
