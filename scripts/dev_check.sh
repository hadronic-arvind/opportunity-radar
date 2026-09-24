#!/bin/bash
# Contributor checks only; never called by the runtime or installers.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
PYTHON_BIN="${OPPORTUNITY_RADAR_DEV_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]] || ! "$PYTHON_BIN" -m ruff --version >/dev/null 2>&1; then
  echo "Developer setup required: see docs/DEVELOPMENT.md" >&2
  exit 1
fi

"$PYTHON_BIN" -m ruff check monitor scripts tests extras
# Expand this explicit formatting baseline when a module is deliberately migrated.
"$PYTHON_BIN" -m ruff format --check scripts/dev_nav.py tests/test_dev_nav.py tests/test_cross_industry.py
if command -v node >/dev/null 2>&1; then
  node --check dashboard/app.js
else
  echo "JavaScript syntax check skipped locally: Node is unavailable (required in CI)."
fi
while IFS= read -r -d '' script; do
  /bin/bash -n "$script"
done < <(find scripts -type f -name '*.sh' -print0)
git diff --check

if [[ $# -gt 0 ]]; then
  PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m unittest "$@"
fi
echo "Developer checks passed. Full release gate: ./scripts/check.sh"
