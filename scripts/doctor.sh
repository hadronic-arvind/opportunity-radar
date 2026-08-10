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
"$PYTHON_BIN" -m monitor doctor

LABEL="${OPPORTUNITY_RADAR_LABEL:-io.github.opportunity-radar.monitor}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME="$HOME/Library/Application Support/OpportunityRadar"
if [[ -d "$RUNTIME" ]]; then
  echo "launchd runtime: installed"
else
  echo "launchd runtime: not installed"
fi
if [[ -f "$PLIST" ]]; then
  echo "launchd plist: installed"
  /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1 \
    && echo "launchd service: loaded" \
    || echo "launchd service: plist exists but service is not loaded"
else
  echo "launchd plist: not installed"
  if "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" status >/dev/null 2>&1; then
    echo "cron fallback: installed"
  else
    echo "cron fallback: not installed or unavailable"
  fi
fi
