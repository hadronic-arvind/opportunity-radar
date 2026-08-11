#!/bin/bash
set -Eeuo pipefail
umask 077

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The automatic scheduler uninstaller currently supports macOS only." >&2
  exit 1
fi

reject_writable_by_others() {
  local path="$1"
  local label="$2"
  local mode
  if ! mode="$(/usr/bin/stat -f '%Lp' "$path")" || [[ ! "$mode" =~ ^[0-7]{3,4}$ ]]; then
    echo "Refusing to use an unsafe $label." >&2
    return 1
  fi
  if (( (8#$mode & 8#22) != 0 )); then
    echo "Refusing to use a group/world-writable $label." >&2
    return 1
  fi
}

service_uses_target() {
  local description="$1"
  local line
  local trimmed
  while IFS= read -r line; do
    trimmed="${line#"${line%%[![:space:]]*}"}"
    if [[ "$trimmed" == "path = $TARGET" ]]; then
      return 0
    fi
  done <<< "$description"
  return 1
}

LABEL="${OPPORTUNITY_RADAR_LABEL:-io.github.opportunity-radar.monitor}"
if [[ ${#LABEL} -gt 128 || ! "$LABEL" =~ ^[A-Za-z0-9]+([.-][A-Za-z0-9]+)*$ ]]; then
  echo "The launch label contains unsafe characters." >&2
  exit 1
fi

if [[ -z "${HOME:-}" || "$HOME" != /* || -L "$HOME" || ! -d "$HOME" || ! -O "$HOME" ]]; then
  echo "Refusing to use an unsafe home directory." >&2
  exit 1
fi
HOME_DIR="$(cd "$HOME" && pwd -P)"
reject_writable_by_others "$HOME_DIR" "home directory"

TARGET_DIR="${OPPORTUNITY_RADAR_LAUNCH_AGENTS_DIR:-$HOME_DIR/Library/LaunchAgents}"
if [[ "$TARGET_DIR" != /* || -L "$TARGET_DIR" ]]; then
  echo "Refusing to use an unsafe LaunchAgents directory." >&2
  exit 1
fi
if [[ -e "$TARGET_DIR" ]]; then
  if [[ ! -d "$TARGET_DIR" ]]; then
    echo "Refusing to use a non-directory LaunchAgents path." >&2
    exit 1
  fi
  TARGET_DIR="$(cd "$TARGET_DIR" && pwd -P)"
  reject_writable_by_others "$TARGET_DIR" "LaunchAgents directory"
fi
TARGET="$TARGET_DIR/$LABEL.plist"
SERVICE="gui/$(/usr/bin/id -u)/$LABEL"

if [[ -L "$TARGET" ]]; then
  echo "Refusing to mutate a symbolic-link launch-agent path." >&2
  exit 1
fi

TRASH_DIR="$HOME_DIR/.Trash"
if [[ -L "$TRASH_DIR" ]]; then
  echo "Refusing to use a symbolic-link Trash directory." >&2
  exit 1
fi
if [[ -e "$TRASH_DIR" ]]; then
  if [[ ! -d "$TRASH_DIR" || ! -O "$TRASH_DIR" ]]; then
    echo "Refusing to use a Trash directory owned by another user." >&2
    exit 1
  fi
  reject_writable_by_others "$TRASH_DIR" "Trash directory"
fi

TRASH_TARGET=""
if [[ -e "$TARGET" ]]; then
  if [[ ! -f "$TARGET" || ! -O "$TARGET" || ! -O "$TARGET_DIR" || ! -w "$TARGET_DIR" ]]; then
    echo "Refusing to mutate an unsafe launch-agent destination." >&2
    exit 1
  fi
  mkdir -p "$TRASH_DIR"
  if [[ ! -d "$TRASH_DIR" || ! -O "$TRASH_DIR" ]]; then
    echo "Refusing to use a Trash directory owned by another user." >&2
    exit 1
  fi
  reject_writable_by_others "$TRASH_DIR" "Trash directory"
  TRASH_TARGET="$TRASH_DIR/$LABEL-$(/bin/date +%Y%m%d-%H%M%S).plist"
  if [[ -e "$TRASH_TARGET" || -L "$TRASH_TARGET" ]]; then
    echo "Refusing to replace an existing Trash item." >&2
    exit 1
  fi
fi

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
PYTHON_BIN="${OPPORTUNITY_RADAR_PYTHON:-${OPPORTUNITY_MONITOR_PYTHON:-$(command -v python3 || true)}}"
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python is unavailable to inspect the cron fallback safely." >&2
  exit 1
fi

LOCK_PARENT="$HOME_DIR/Library/Application Support"
LOCK_DIR="$LOCK_PARENT/.OpportunityRadar.lifecycle-lock"
LOCK_OWNER="$LOCK_DIR/owner.pid"
if [[ -L "$LOCK_PARENT" ]]; then
  echo "Refusing to use a symbolic-link lifecycle-lock parent." >&2
  exit 1
fi
mkdir -p "$LOCK_PARENT"
if [[ ! -d "$LOCK_PARENT" || ! -O "$LOCK_PARENT" ]]; then
  echo "Refusing to use an unsafe lifecycle-lock parent." >&2
  exit 1
fi
reject_writable_by_others "$LOCK_PARENT" "lifecycle-lock parent"

CRON_BACKUP=""
CRON_SNAPSHOT_READY=0
CRON_MUTATION_ATTEMPTED=0
SCHEDULER_MUTATION_STARTED=0
WAS_LOADED=0
MOVED_TARGET=0
LOCK_HELD=0

cleanup() {
  if [[ -n "$CRON_BACKUP" && -f "$CRON_BACKUP" && ! -L "$CRON_BACKUP" ]]; then
    /bin/rm -f "$CRON_BACKUP"
  fi
  if [[ "$LOCK_HELD" -eq 1 && -d "$LOCK_DIR" && ! -L "$LOCK_DIR" && -O "$LOCK_DIR" ]]; then
    if [[ -f "$LOCK_OWNER" && ! -L "$LOCK_OWNER" && -O "$LOCK_OWNER" ]]; then
      /bin/rm -f "$LOCK_OWNER"
    fi
    /bin/rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
    LOCK_HELD=0
  fi
}

rollback() {
  local exit_code="$1"
  trap - ERR HUP INT TERM
  echo "Uninstall failed; restoring the previous scheduler state." >&2
  if [[ "$CRON_MUTATION_ATTEMPTED" -eq 1 && "$CRON_SNAPSHOT_READY" -eq 1 ]]; then
    "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" restore \
      --label "$LABEL" \
      < "$CRON_BACKUP" >/dev/null 2>&1 || true
  fi
  if [[ "$MOVED_TARGET" -eq 1 && -n "$TRASH_TARGET" && -f "$TRASH_TARGET" ]]; then
    /bin/mv "$TRASH_TARGET" "$TARGET" || true
  fi
  if [[ "$WAS_LOADED" -eq 1 && -f "$TARGET" ]]; then
    /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$TARGET" >/dev/null 2>&1 || true
  fi
  cleanup
  exit "$exit_code"
}

trap 'rollback $?' ERR
trap cleanup EXIT
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

"$PYTHON_BIN" "$PROJECT_DIR/scripts/recover_lifecycle_lock.py"
if ! mkdir "$LOCK_DIR"; then
  echo "Another Opportunity Radar install or uninstall is already running." >&2
  exit 1
fi
LOCK_HELD=1
if ! /usr/bin/printf '%s\n%s\n' "$$" "$(/bin/date +%s)" > "$LOCK_OWNER" || \
   ! /bin/chmod 600 "$LOCK_OWNER"; then
  false
fi
CRON_BACKUP="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/opportunity-radar-crontab.XXXXXX")"

"$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" snapshot \
  --label "$LABEL" > "$CRON_BACKUP"
CRON_SNAPSHOT_READY=1
SERVICE_DESCRIPTION=""
if SERVICE_DESCRIPTION="$(
  trap - ERR
  /bin/launchctl print "$SERVICE" 2>/dev/null
)"; then
  WAS_LOADED=1
  if ! service_uses_target "$SERVICE_DESCRIPTION"; then
    echo "The loaded launch agent was bootstrapped from a different property list." >&2
    false
  fi
fi
if [[ "$WAS_LOADED" -eq 1 && ! -f "$TARGET" ]]; then
  echo "The loaded launch agent has no restorable property list." >&2
  false
fi

SCHEDULER_MUTATION_STARTED=1
if [[ "$WAS_LOADED" -eq 1 ]]; then
  /bin/launchctl bootout "$SERVICE"
fi
if [[ -f "$TARGET" ]]; then
  MOVED_TARGET=1
  /bin/mv "$TARGET" "$TRASH_TARGET"
fi
CRON_MUTATION_ATTEMPTED=1
"$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" remove \
  --label "$LABEL" >/dev/null

trap - ERR HUP INT TERM
echo "Opportunity Radar scheduling is disabled."
echo "The runtime and database remain in Library/Application Support/OpportunityRadar for recovery."
