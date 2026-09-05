#!/usr/bin/env bash
set -euo pipefail

# Use the Python environment that already has frida==17.16.4 installed.
# Falls back to python3 if the Anaconda interpreter is absent.
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN=python3
fi

exec "$PYTHON_BIN" tools/run_frida_hook.py "$@"
