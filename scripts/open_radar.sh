#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -n "${RADAR_PYTHON:-}" ]]; then
  exec "$RADAR_PYTHON" -m radar "$@" gui
fi

# Use an existing interpreter with a current Tk. Leave installation to the user.
for radar_python in python3 "${CONDA_PREFIX:-/nonexistent}/bin/python" "$HOME/miniconda3/bin/python"; do
  if command -v "$radar_python" >/dev/null 2>&1 &&
    "$radar_python" -c 'import tkinter; assert tkinter.TkVersion >= 8.6' >/dev/null 2>&1; then
    exec "$radar_python" -m radar "$@" gui
  fi
done

echo "The desktop needs Python 3.9+ with Tk 8.6+. Set RADAR_PYTHON to that interpreter, or run python3 -m radar --help for terminal use." >&2
exit 1
