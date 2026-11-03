#!/usr/bin/env bash
# Build the Linux app: creates app/.venv if needed, then runs PyInstaller. Output: app/dist/jarvis-panel/
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt -r requirements-build.txt
.venv/bin/python -m PyInstaller --noconfirm --clean jarvis-panel.spec
echo
echo "Built: $(pwd)/dist/jarvis-panel/jarvis-panel"
