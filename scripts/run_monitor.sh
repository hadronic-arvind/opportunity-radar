#!/bin/bash
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${OPPORTUNITY_RADAR_PYTHON:-${OPPORTUNITY_MONITOR_PYTHON:-$(command -v python3 || true)}}"
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3 is required." >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data" "$PROJECT_DIR/reports/daily"
export PYTHONPYCACHEPREFIX="$PROJECT_DIR/data/pycache"

cd "$PROJECT_DIR"
exec /usr/bin/nice -n 10 "$PYTHON_BIN" -m monitor scan --notify
