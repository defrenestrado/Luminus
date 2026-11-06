#!/usr/bin/env bash
# Gera o APK release e publica em dist/ para o servidor da porta 8000.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/app"
DIST="$ROOT/dist"

export PATH="/home/ledilson/development/flutter/bin:$PATH"
export JAVA_HOME="/home/ledilson/development/jdk17"
export ANDROID_HOME="/home/ledilson/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"

STAMP="$(date +%Y%m%d%H%M%S)"
BUILD_NUMBER="$(date +%s)"
echo "[build] iniciando versao 1.0.$STAMP (build $BUILD_NUMBER)"

cd "$APP"
flutter pub get
flutter build apk --release \
  --build-name="1.0.$STAMP" \
  --build-number="$BUILD_NUMBER" \
  --dart-define=LUMINUS_BUILD_NUMBER="$BUILD_NUMBER"

APK="$APP/build/app/outputs/flutter-apk/app-release.apk"
test -f "$APK"
mkdir -p "$DIST"
cp -f "$APK" "$DIST/luminus.apk"

python3 - "$DIST" "$STAMP" "$BUILD_NUMBER" <<'PY'
import datetime
import json
import os
import sys

dist, stamp, build_number = sys.argv[1], sys.argv[2], int(sys.argv[3])
now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
with open(os.path.join(dist, "build.json"), "w", encoding="utf-8") as fh:
    json.dump(
        {"built_at": now, "version": stamp, "build_number": build_number},
        fh,
    )
print(f"[build] publicado em dist/luminus.apk ({now})")
PY

ADB="$ANDROID_HOME/platform-tools/adb"
if [ -x "$ADB" ] && "$ADB" devices 2>/dev/null | awk 'NR>1 && $2=="device"{f=1} END{exit !f}'; then
  echo "[build] celular no ADB detectado; instalando..."
  "$ADB" install -r "$DIST/luminus.apk" || echo "[build] adb install falhou"
else
  echo "[build] nenhum celular no ADB; baixe em http://IP:8000"
fi
