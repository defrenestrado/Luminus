#!/usr/bin/env python3
"""Reconstrói o APK automaticamente quando o código do app muda."""

import hashlib
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
BUILD_SCRIPT = os.path.join(ROOT, "scripts", "build_apk.sh")
DIST_APK = os.path.join(ROOT, "dist", "luminus.apk")

WATCHED = [
    os.path.join(APP, "lib"),
    os.path.join(APP, "assets"),
    os.path.join(APP, "pubspec.yaml"),
    os.path.join(APP, "android", "app", "src", "main"),
]


def snapshot() -> tuple[str, int]:
    items: list[tuple[str, int, int]] = []
    for path in WATCHED:
        if os.path.isfile(path):
            stat = os.stat(path)
            items.append((path, stat.st_mtime_ns, stat.st_size))
        elif os.path.isdir(path):
            for dirpath, _, files in os.walk(path):
                for name in files:
                    file_path = os.path.join(dirpath, name)
                    try:
                        stat = os.stat(file_path)
                    except OSError:
                        continue
                    items.append((file_path, stat.st_mtime_ns, stat.st_size))
    items.sort()
    digest = hashlib.sha1()
    for path, mtime, size in items:
        digest.update(path.encode("utf-8", "ignore"))
        digest.update(str(mtime).encode())
        digest.update(str(size).encode())
    newest = max((mtime for _, mtime, _ in items), default=0)
    return digest.hexdigest(), newest


def build() -> bool:
    print("[watch] mudanca detectada; gerando APK", flush=True)
    result = subprocess.run([BUILD_SCRIPT], cwd=ROOT)
    print(f"[watch] build terminou com codigo {result.returncode}", flush=True)
    return result.returncode == 0


def main() -> None:
    last, newest = snapshot()
    try:
        apk_mtime = os.stat(DIST_APK).st_mtime_ns
    except OSError:
        apk_mtime = 0
    if apk_mtime < newest:
        build()
        last, _ = snapshot()
    print("[watch] monitorando app/lib e app/assets", flush=True)
    while True:
        time.sleep(2)
        current, _ = snapshot()
        if current == last:
            continue
        time.sleep(3)
        settled, _ = snapshot()
        if settled != current:
            last = settled
            continue
        last = settled
        build()
        last, _ = snapshot()


if __name__ == "__main__":
    main()
