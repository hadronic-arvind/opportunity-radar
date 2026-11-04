#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

app="${RADAR_DESKTOP_APP:-$HOME/Applications/Opportunity Radar Dashboard.app}"
if [[ ! -d "$app" ]]; then
  echo "Build the Mac desktop app first with python3 extras/radar-desktop/install.py build." >&2
  exit 1
fi

if [[ -n "${RADAR_WORKSPACE:-}" ]]; then
  exec /usr/bin/open -n -a "$app" --env "RADAR_WORKSPACE=$RADAR_WORKSPACE"
fi
exec /usr/bin/open -a "$app"
