#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN=python3
fi

exec "$PYTHON_BIN" tools/run_frida_binary_dump.py "$@"
