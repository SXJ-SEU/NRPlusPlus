#!/usr/bin/env bash
set -euo pipefail

adb forward tcp:27042 tcp:27042 >/dev/null
PID="$(adb shell pidof nullsroyale.rel.free | tr -d '\r' | awk '{print $1}')"
APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
/opt/anaconda3/bin/python "$APP_DIR/arena_overlay.py" --locked --monitor --target "$PID"
