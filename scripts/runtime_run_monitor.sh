#!/bin/bash
set -euo pipefail
umask 077

RUNTIME_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${OPPORTUNITY_RADAR_PYTHON:-${OPPORTUNITY_MONITOR_PYTHON:-}}"
if [[ -z "$PYTHON_BIN" && -f "$RUNTIME_DIR/config/python-path" ]]; then
  IFS= read -r PYTHON_BIN < "$RUNTIME_DIR/config/python-path"
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Configured Python interpreter is unavailable." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR/logs" "$RUNTIME_DIR/data" "$RUNTIME_DIR/reports/daily"
export PYTHONPYCACHEPREFIX="$RUNTIME_DIR/data/pycache"
if [[ -f "$RUNTIME_DIR/seed/curated-pipeline.md" ]]; then
  export OPPORTUNITY_RADAR_CURATED_PATH="$RUNTIME_DIR/seed/curated-pipeline.md"
fi

CURRENT_USER="$(/usr/bin/id -un)"
if WEBHOOK_SECRET="$(/usr/bin/security find-generic-password -a "$CURRENT_USER" -s OpportunityRadarWebhook -w 2>/dev/null)"; then
  export OPPORTUNITY_MONITOR_WEBHOOK_URL="$WEBHOOK_SECRET"
fi

cd "$RUNTIME_DIR"
exec /usr/bin/nice -n 10 "$PYTHON_BIN" -m monitor scan --notify --quiet
