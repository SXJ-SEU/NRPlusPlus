#!/usr/bin/env bash
set -euo pipefail

SECONDS_ARG="${1:-180}"
APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
LOG="${2:-$APP_DIR/logs/queue_deploy_$(date +%Y%m%d_%H%M%S).log}"

adb forward tcp:27042 tcp:27042 >/dev/null
PID="$(adb shell pidof nullsroyale.rel.free | tr -d '\r' | awk '{print $1}')"
mkdir -p "$(dirname "$LOG")"
/opt/anaconda3/bin/python "$APP_DIR/queue_cli.py" "$PID" "$APP_DIR/hook_queue_deploy.js" \
  --seconds "$SECONDS_ARG" | tee "$LOG"
