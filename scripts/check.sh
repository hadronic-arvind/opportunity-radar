#!/bin/bash
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${OPPORTUNITY_RADAR_PYTHON:-${OPPORTUNITY_MONITOR_PYTHON:-$(command -v python3 || true)}}"
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3 is required." >&2
  exit 1
fi

CHECK_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/opportunity-radar-check.XXXXXX")"
cleanup() {
  if [[ -d "$CHECK_DIR" && "$CHECK_DIR" == "${TMPDIR:-/tmp}/opportunity-radar-check."* ]]; then
    /bin/rm -rf "$CHECK_DIR"
  fi
}
trap cleanup EXIT

cd "$PROJECT_DIR"
"$PYTHON_BIN" -m json.tool config/profile.json >/dev/null
"$PYTHON_BIN" -m json.tool config/sources.json >/dev/null
PYTHONPYCACHEPREFIX="$CHECK_DIR/pycache" "$PYTHON_BIN" -m compileall -q monitor scripts tests extras
PYTHONPYCACHEPREFIX="$CHECK_DIR/pycache" "$PYTHON_BIN" -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX="$CHECK_DIR/pycache" "$PYTHON_BIN" -m monitor doctor >/dev/null

while IFS= read -r -d '' script; do
  /bin/bash -n "$script"
done < <(find scripts -type f -name '*.sh' -print0)

"$PYTHON_BIN" scripts/render_launch_agent.py \
  --runtime "$CHECK_DIR/Runtime & Data" \
  --output "$CHECK_DIR/opportunity-radar.plist"
if [[ "$(uname -s)" == "Darwin" ]]; then
  /usr/bin/plutil -lint "$CHECK_DIR/opportunity-radar.plist" >/dev/null
else
  "$PYTHON_BIN" -c 'import plistlib, sys; plistlib.load(open(sys.argv[1], "rb"))' "$CHECK_DIR/opportunity-radar.plist"
fi

"$PYTHON_BIN" scripts/privacy_check.py --history
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check
fi
echo "All Opportunity Radar checks passed."
