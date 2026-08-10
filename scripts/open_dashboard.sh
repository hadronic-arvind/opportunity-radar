#!/bin/bash
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${OPPORTUNITY_RADAR_PYTHON:-${OPPORTUNITY_MONITOR_PYTHON:-$(command -v python3 || true)}}"
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3 is required." >&2
  exit 1
fi
export PYTHONPYCACHEPREFIX="$PROJECT_DIR/data/pycache"
cd "$PROJECT_DIR"
if [[ ! -f "$PROJECT_DIR/dashboard/index.html" ]]; then
  "$PYTHON_BIN" -m monitor dashboard --quiet
fi
if [[ "$(uname -s)" == "Darwin" ]]; then
  exec /usr/bin/open "$PROJECT_DIR/dashboard/index.html"
fi
if command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "$PROJECT_DIR/dashboard/index.html"
fi
echo "$PROJECT_DIR/dashboard/index.html"
