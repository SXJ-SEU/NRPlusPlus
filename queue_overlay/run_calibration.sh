#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
/opt/anaconda3/bin/python "$APP_DIR/arena_overlay.py" "$@"
