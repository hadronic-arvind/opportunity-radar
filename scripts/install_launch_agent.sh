#!/bin/bash
set -Eeuo pipefail
umask 077

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The automatic scheduler installer currently supports macOS only." >&2
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

private_local_config() {
  local runtime_path="$1"
  local repository_path="$2"
  local candidate=""
  local mode
  if [[ -e "$runtime_path" || -L "$runtime_path" ]]; then
    candidate="$runtime_path"
  elif [[ -e "$repository_path" || -L "$repository_path" ]]; then
    candidate="$repository_path"
  else
    return 0
  fi
  if [[ -L "$candidate" || ! -f "$candidate" || ! -O "$candidate" ]]; then
    echo "Refusing to copy an unsafe local configuration file." >&2
    return 1
  fi
  if ! mode="$(/usr/bin/stat -f '%Lp' "$candidate")" || [[ ! "$mode" =~ ^[0-7]{3,4}$ ]]; then
    echo "Refusing to copy an unreadable local configuration file." >&2
    return 1
  fi
  if (( (8#$mode & 8#77) != 0 )); then
    echo "Refusing to copy a local configuration file that is not private." >&2
    return 1
  fi
  /usr/bin/printf '%s\n' "$candidate"
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

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
LABEL="${OPPORTUNITY_RADAR_LABEL:-io.github.opportunity-radar.monitor}"
RUNTIME_DIR="${OPPORTUNITY_RADAR_RUNTIME_DIR:-$HOME/Library/Application Support/OpportunityRadar}"
TARGET_DIR="${OPPORTUNITY_RADAR_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
MORNING_HOUR="${OPPORTUNITY_RADAR_MORNING_HOUR:-7}"
MORNING_MINUTE="${OPPORTUNITY_RADAR_MORNING_MINUTE:-30}"
AFTERNOON_HOUR="${OPPORTUNITY_RADAR_AFTERNOON_HOUR:-16}"
AFTERNOON_MINUTE="${OPPORTUNITY_RADAR_AFTERNOON_MINUTE:-30}"
PYTHON_BIN="${OPPORTUNITY_RADAR_PYTHON:-${OPPORTUNITY_MONITOR_PYTHON:-$(command -v python3 || true)}}"
STAMP="$(/bin/date +%Y%m%d-%H%M%S)"
PLIST_BACKUP="$PROJECT_DIR/data/previous-launch-agent-$STAMP.plist"
CRON_BACKUP="$PROJECT_DIR/data/previous-crontab-$STAMP.txt"
STAGE=""
ARCHIVED_PREVIOUS_RUNTIME=0
OLD_RUNTIME_MOVED=0
STAGE_PROMOTED=0
WAS_LOADED=0
HAD_PLIST=0
USE_LAUNCHD=0
USE_EXISTING_CRON=0
SCHEDULER_MUTATION_STARTED=0
CRON_SNAPSHOT_READY=0
CRON_MUTATION_ATTEMPTED=0
LOCK_HELD=0
LINK_MUTATION_STARTED=0
HAD_DASHBOARD_PATH=0
HAD_DATABASE_PATH=0
HAD_SCHEDULER_OUT_PATH=0
HAD_SCHEDULER_ERR_PATH=0
SCHEDULER_OUT_TARGET=""
SCHEDULER_ERR_TARGET=""

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3.9 or newer is required." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is required." >&2
  exit 1
fi

if [[ ${#LABEL} -gt 128 || ! "$LABEL" =~ ^[A-Za-z0-9]+([.-][A-Za-z0-9]+)*$ ]]; then
  echo "The launch label contains unsafe characters." >&2
  exit 1
fi

VALIDATED_PATHS="$(
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/render_launch_agent.py" \
    --validate-install \
    --project "$PROJECT_DIR" \
    --runtime "$RUNTIME_DIR" \
    --target-dir "$TARGET_DIR" \
    --python "$PYTHON_BIN" \
    --backup-stamp "$STAMP" \
    --label "$LABEL" \
    --morning-hour "$MORNING_HOUR" \
    --morning-minute "$MORNING_MINUTE" \
    --afternoon-hour "$AFTERNOON_HOUR" \
    --afternoon-minute "$AFTERNOON_MINUTE"
)"
IFS=$'\t' read -r RUNTIME_DIR TARGET_DIR PYTHON_BIN EXTRA_PATH <<< "$VALIDATED_PATHS"
if [[ -z "$RUNTIME_DIR" || -z "$TARGET_DIR" || -z "$PYTHON_BIN" || -n "${EXTRA_PATH:-}" ]]; then
  echo "Installer path validation returned an invalid result." >&2
  exit 1
fi

RUNTIME_PARENT="$(dirname "$RUNTIME_DIR")"
TARGET="$TARGET_DIR/$LABEL.plist"
RENDERED="$PROJECT_DIR/data/$LABEL.plist"
SERVICE="gui/$(/usr/bin/id -u)/$LABEL"
PREVIOUS_RUNTIME="${RUNTIME_DIR}.previous"
ARCHIVED_PREVIOUS_RUNTIME_PATH="${PREVIOUS_RUNTIME}-$STAMP"
FAILED_RUNTIME="${RUNTIME_DIR}.failed-$STAMP"
FAILED_TARGET="${TARGET}.failed-$STAMP"
DASHBOARD_BACKUP="$PROJECT_DIR/dashboard/index.pre-runtime-$STAMP.html"
DATABASE_BACKUP="$PROJECT_DIR/data/opportunities.pre-runtime-$STAMP.sqlite3"
SCHEDULER_OUT_BACKUP="$PROJECT_DIR/logs/scheduler.pre-runtime-$STAMP.out.log"
SCHEDULER_ERR_BACKUP="$PROJECT_DIR/logs/scheduler.pre-runtime-$STAMP.err.log"
DASHBOARD_PATH="$PROJECT_DIR/dashboard/index.html"
DATABASE_PATH="$PROJECT_DIR/data/opportunities.sqlite3"
SCHEDULER_OUT_PATH="$PROJECT_DIR/logs/scheduler.out.log"
SCHEDULER_ERR_PATH="$PROJECT_DIR/logs/scheduler.err.log"

if [[ -z "${HOME:-}" || "$HOME" != /* || -L "$HOME" || ! -d "$HOME" || ! -O "$HOME" ]]; then
  echo "Refusing to use an unsafe home directory." >&2
  exit 1
fi
HOME_DIR="$(cd "$HOME" && pwd -P)"
reject_writable_by_others "$HOME_DIR" "home directory"
LOCK_PARENT="$HOME_DIR/Library/Application Support"
LOCK_DIR="$LOCK_PARENT/.OpportunityRadar.lifecycle-lock"
LOCK_OWNER="$LOCK_DIR/owner.pid"

if [[ -L "$RENDERED" || ( -e "$RENDERED" && ! -O "$RENDERED" ) ]]; then
  echo "Refusing to replace an unsafe rendered launch-agent path." >&2
  exit 1
fi
for reserved_path in \
  "$FAILED_RUNTIME" \
  "${PREVIOUS_RUNTIME}-$STAMP" \
  "$FAILED_TARGET" \
  "$PLIST_BACKUP" \
  "$CRON_BACKUP" \
  "$DASHBOARD_BACKUP" \
  "$DATABASE_BACKUP" \
  "$SCHEDULER_OUT_BACKUP" \
  "$SCHEDULER_ERR_BACKUP"; do
  if [[ -e "$reserved_path" || -L "$reserved_path" ]]; then
    echo "A reserved installer recovery path already exists." >&2
    exit 1
  fi
done

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
"$PYTHON_BIN" "$PROJECT_DIR/scripts/recover_lifecycle_lock.py"
if ! mkdir "$LOCK_DIR"; then
  echo "Another Opportunity Radar install or uninstall is already running." >&2
  exit 1
fi
if ! /usr/bin/printf '%s\n%s\n' "$$" "$(/bin/date +%s)" > "$LOCK_OWNER" || \
   ! /bin/chmod 600 "$LOCK_OWNER"; then
  /bin/rm -f "$LOCK_OWNER"
  /bin/rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
  echo "Could not record the lifecycle-lock owner safely." >&2
  exit 1
fi
LOCK_HELD=1

cleanup_stage() {
  if [[ -n "$STAGE" && -d "$STAGE" && "$STAGE" == "$RUNTIME_PARENT/OpportunityRadar-stage."* ]]; then
    /bin/rm -rf "$STAGE"
  fi
  if [[ -f "$CRON_BACKUP" && ! -L "$CRON_BACKUP" ]]; then
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

restore_managed_link() {
  local live_path="$1"
  local backup_path="$2"
  local expected_target="$3"
  local had_previous="$4"
  local current_target
  if [[ "$had_previous" -eq 1 && ! -e "$backup_path" && ! -L "$backup_path" ]]; then
    return 0
  fi
  if [[ -L "$live_path" ]]; then
    current_target="$(/usr/bin/readlink "$live_path")"
    if [[ "$current_target" != "$expected_target" ]]; then
      return 1
    fi
    /bin/rm -f "$live_path"
  elif [[ -e "$live_path" ]]; then
    return 1
  fi
  if [[ "$had_previous" -eq 1 && ( -e "$backup_path" || -L "$backup_path" ) ]]; then
    /bin/mv "$backup_path" "$live_path"
  fi
}

rollback() {
  local exit_code="$1"
  trap - ERR HUP INT TERM
  echo "Installation failed; restoring the previous runtime." >&2
  if [[ "$SCHEDULER_MUTATION_STARTED" -eq 1 ]]; then
    if /bin/launchctl print "$SERVICE" >/dev/null 2>&1; then
      /bin/launchctl bootout "$SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  if [[ "$LINK_MUTATION_STARTED" -eq 1 ]]; then
    restore_managed_link \
      "$SCHEDULER_ERR_PATH" "$SCHEDULER_ERR_BACKUP" "$SCHEDULER_ERR_TARGET" \
      "$HAD_SCHEDULER_ERR_PATH" || true
    restore_managed_link \
      "$SCHEDULER_OUT_PATH" "$SCHEDULER_OUT_BACKUP" "$SCHEDULER_OUT_TARGET" \
      "$HAD_SCHEDULER_OUT_PATH" || true
    restore_managed_link \
      "$DATABASE_PATH" "$DATABASE_BACKUP" "$RUNTIME_DIR/data/opportunities.sqlite3" \
      "$HAD_DATABASE_PATH" || true
    restore_managed_link \
      "$DASHBOARD_PATH" "$DASHBOARD_BACKUP" "$RUNTIME_DIR/dashboard/index.html" \
      "$HAD_DASHBOARD_PATH" || true
  fi
  if [[ "$STAGE_PROMOTED" -eq 1 && -d "$RUNTIME_DIR" ]]; then
    /bin/mv "$RUNTIME_DIR" "$FAILED_RUNTIME" || true
  fi
  if [[ "$OLD_RUNTIME_MOVED" -eq 1 && -d "$PREVIOUS_RUNTIME" ]]; then
    if [[ -e "$RUNTIME_DIR" || -L "$RUNTIME_DIR" ]]; then
      echo "The prior runtime remains at $PREVIOUS_RUNTIME for manual recovery." >&2
    else
      /bin/mv "$PREVIOUS_RUNTIME" "$RUNTIME_DIR" || true
    fi
  fi
  if [[ "$ARCHIVED_PREVIOUS_RUNTIME" -eq 1 && -d "$ARCHIVED_PREVIOUS_RUNTIME_PATH" && ! -e "$PREVIOUS_RUNTIME" ]]; then
    /bin/mv "$ARCHIVED_PREVIOUS_RUNTIME_PATH" "$PREVIOUS_RUNTIME" || true
  fi
  if [[ "$SCHEDULER_MUTATION_STARTED" -eq 1 ]]; then
    if [[ "$HAD_PLIST" -eq 1 && -f "$PLIST_BACKUP" ]]; then
      /usr/bin/install -m 600 "$PLIST_BACKUP" "$TARGET" || true
    elif [[ "$HAD_PLIST" -eq 0 && -f "$TARGET" ]]; then
      /bin/mv "$TARGET" "$FAILED_TARGET" || true
    fi
    if [[ "$CRON_MUTATION_ATTEMPTED" -eq 1 && "$CRON_SNAPSHOT_READY" -eq 1 ]]; then
      "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" restore \
        --label "$LABEL" \
        < "$CRON_BACKUP" >/dev/null 2>&1 || true
    fi
    if [[ "$WAS_LOADED" -eq 1 && -f "$TARGET" ]]; then
      /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$TARGET" >/dev/null 2>&1 || true
    fi
  fi
  cleanup_stage
  exit "$exit_code"
}

trap 'rollback $?' ERR
trap cleanup_stage EXIT
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/dashboard" "$PROJECT_DIR/logs" "$RUNTIME_PARENT" "$TARGET_DIR"
STAGE="$(/usr/bin/mktemp -d "$RUNTIME_PARENT/OpportunityRadar-stage.XXXXXX")"

cd "$PROJECT_DIR"
# The lifecycle lock prevents new scans. Verify that a scan which started just
# before the lock is no longer using the live runtime before taking snapshots.
PYTHONPYCACHEPREFIX="$PROJECT_DIR/data/pycache" \
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/check_scan_idle.py"
PYTHONPYCACHEPREFIX="$PROJECT_DIR/data/pycache" "$PYTHON_BIN" -m monitor doctor >/dev/null

mkdir -p \
  "$STAGE/config" \
  "$STAGE/dashboard" \
  "$STAGE/data" \
  "$STAGE/logs" \
  "$STAGE/reports/daily" \
  "$STAGE/scripts" \
  "$STAGE/seed"

/usr/bin/ditto --norsrc --noextattr "$PROJECT_DIR/monitor" "$STAGE/monitor"
/bin/cp "$PROJECT_DIR/config/profile.json" "$STAGE/config/profile.json"
/bin/cp "$PROJECT_DIR/config/sources.json" "$STAGE/config/sources.json"
PROFILE_LOCAL_SOURCE="$(
  private_local_config \
    "$RUNTIME_DIR/config/profile.local.json" \
    "$PROJECT_DIR/config/profile.local.json"
)"
SOURCES_LOCAL_SOURCE="$(
  private_local_config \
    "$RUNTIME_DIR/config/sources.local.json" \
    "$PROJECT_DIR/config/sources.local.json"
)"
if [[ -n "$PROFILE_LOCAL_SOURCE" ]]; then
  /bin/cp "$PROFILE_LOCAL_SOURCE" "$STAGE/config/profile.local.json"
fi
if [[ -n "$SOURCES_LOCAL_SOURCE" ]]; then
  /bin/cp "$SOURCES_LOCAL_SOURCE" "$STAGE/config/sources.local.json"
fi
/bin/cp "$PROJECT_DIR/dashboard/template.html" "$STAGE/dashboard/template.html"
/bin/cp "$PROJECT_DIR/dashboard/styles.css" "$STAGE/dashboard/styles.css"
/bin/cp "$PROJECT_DIR/dashboard/app.js" "$STAGE/dashboard/app.js"
/bin/cp "$PROJECT_DIR/scripts/runtime_run_monitor.sh" "$STAGE/scripts/run_monitor.sh"
/bin/chmod 700 "$STAGE/scripts/run_monitor.sh"
/usr/bin/printf '%s\n' "$PYTHON_BIN" > "$STAGE/config/python-path"

CURATED_PATH="$(
  "$PYTHON_BIN" -c 'from monitor.config import load_profile, resolve_project_value; value = str(load_profile().get("curated_pipeline_path", "")).strip(); print(resolve_project_value(value) if value else "")'
)"
if [[ -n "$CURATED_PATH" ]]; then
  if [[ ! -f "$CURATED_PATH" ]]; then
    echo "Configured curated pipeline is unavailable." >&2
    false
  fi
  /bin/cp "$CURATED_PATH" "$STAGE/seed/curated-pipeline.md"
fi

if [[ -w "$TARGET_DIR" ]]; then
  USE_LAUNCHD=1
elif [[ -e "$TARGET" || -L "$TARGET" ]]; then
  echo "The existing launch-agent file cannot be removed, so the installer cannot switch safely to the cron fallback." >&2
  false
fi

if [[ -f "$TARGET" ]]; then
  HAD_PLIST=1
  /bin/cp "$TARGET" "$PLIST_BACKUP"
fi
"$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" snapshot \
  --label "$LABEL" > "$CRON_BACKUP"
/bin/chmod 600 "$CRON_BACKUP"
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
if [[ "$USE_LAUNCHD" -eq 1 && "$WAS_LOADED" -eq 0 && "$HAD_PLIST" -eq 0 ]] && \
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" verify \
    --label "$LABEL" \
    --runtime "$RUNTIME_DIR" \
    --morning-hour "$MORNING_HOUR" \
    --morning-minute "$MORNING_MINUTE" \
    --afternoon-hour "$AFTERNOON_HOUR" \
    --afternoon-minute "$AFTERNOON_MINUTE"; then
  USE_LAUNCHD=0
  USE_EXISTING_CRON=1
fi
if [[ "$WAS_LOADED" -eq 1 && "$HAD_PLIST" -eq 0 ]]; then
  echo "The loaded launch agent has no restorable property list." >&2
  false
fi
SCHEDULER_MUTATION_STARTED=1
if [[ "$WAS_LOADED" -eq 1 ]]; then
  /bin/launchctl bootout "$SERVICE"
fi

DATABASE_SOURCE=""
if [[ -f "$RUNTIME_DIR/data/opportunities.sqlite3" ]]; then
  DATABASE_SOURCE="$RUNTIME_DIR/data/opportunities.sqlite3"
elif [[ -f "$PROJECT_DIR/data/opportunities.sqlite3" ]]; then
  DATABASE_SOURCE="$PROJECT_DIR/data/opportunities.sqlite3"
fi
if [[ -n "$DATABASE_SOURCE" ]]; then
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/copy_database.py" \
    "$DATABASE_SOURCE" "$STAGE/data/opportunities.sqlite3"
fi

if [[ -f "$STAGE/seed/curated-pipeline.md" ]]; then
  (
    cd "$STAGE"
    OPPORTUNITY_RADAR_CURATED_PATH="$STAGE/seed/curated-pipeline.md" \
      OPPORTUNITY_RADAR_LIFECYCLE_OWNER=installer \
      PYTHONPYCACHEPREFIX="$STAGE/data/pycache" \
      "$PYTHON_BIN" -m monitor scan --force --quiet
  )
else
  (
    cd "$STAGE"
    OPPORTUNITY_RADAR_LIFECYCLE_OWNER=installer \
    PYTHONPYCACHEPREFIX="$STAGE/data/pycache" \
      "$PYTHON_BIN" -m monitor scan --force --quiet
  )
fi

/bin/chmod -R go-rwx "$STAGE"
if [[ -d "$PREVIOUS_RUNTIME" ]]; then
  ARCHIVED_PREVIOUS_RUNTIME=1
  /bin/mv "$PREVIOUS_RUNTIME" "$ARCHIVED_PREVIOUS_RUNTIME_PATH"
fi
if [[ -d "$RUNTIME_DIR" ]]; then
  OLD_RUNTIME_MOVED=1
  /bin/mv "$RUNTIME_DIR" "$PREVIOUS_RUNTIME"
fi
STAGE_PROMOTED=1
/bin/mv "$STAGE" "$RUNTIME_DIR"
STAGE=""

"$PYTHON_BIN" "$PROJECT_DIR/scripts/render_launch_agent.py" \
  --runtime "$RUNTIME_DIR" \
  --output "$RENDERED" \
  --label "$LABEL" \
  --morning-hour "$MORNING_HOUR" \
  --morning-minute "$MORNING_MINUTE" \
  --afternoon-hour "$AFTERNOON_HOUR" \
  --afternoon-minute "$AFTERNOON_MINUTE"
/usr/bin/plutil -lint "$RENDERED"
SCHEDULER_KIND="launchd"
if [[ "$USE_LAUNCHD" -eq 1 ]]; then
  /usr/bin/install -m 600 "$RENDERED" "$TARGET"
  /usr/bin/plutil -lint "$TARGET"
  /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$TARGET"
  /bin/launchctl enable "$SERVICE"
  /bin/launchctl print "$SERVICE" >/dev/null
  CRON_MUTATION_ATTEMPTED=1
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" remove --label "$LABEL"
elif [[ "$USE_EXISTING_CRON" -eq 1 ]]; then
  SCHEDULER_KIND="existing cron fallback"
else
  CRON_MUTATION_ATTEMPTED=1
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" install \
    --label "$LABEL" \
    --runtime "$RUNTIME_DIR" \
    --morning-hour "$MORNING_HOUR" \
    --morning-minute "$MORNING_MINUTE" \
    --afternoon-hour "$AFTERNOON_HOUR" \
    --afternoon-minute "$AFTERNOON_MINUTE"
  SCHEDULER_KIND="cron fallback"
fi

if [[ "$SCHEDULER_KIND" == *"cron fallback" ]]; then
  SCHEDULER_OUT_TARGET="$RUNTIME_DIR/logs/cron.out.log"
  SCHEDULER_ERR_TARGET="$RUNTIME_DIR/logs/cron.err.log"
else
  SCHEDULER_OUT_TARGET="$RUNTIME_DIR/logs/launchd.out.log"
  SCHEDULER_ERR_TARGET="$RUNTIME_DIR/logs/launchd.err.log"
fi

LINK_MUTATION_STARTED=1
if [[ -e "$DASHBOARD_PATH" || -L "$DASHBOARD_PATH" ]]; then
  HAD_DASHBOARD_PATH=1
  /bin/mv "$DASHBOARD_PATH" "$DASHBOARD_BACKUP"
fi
/bin/ln -s "$RUNTIME_DIR/dashboard/index.html" "$DASHBOARD_PATH"

if [[ -e "$DATABASE_PATH" || -L "$DATABASE_PATH" ]]; then
  HAD_DATABASE_PATH=1
  /bin/mv "$DATABASE_PATH" "$DATABASE_BACKUP"
fi
/bin/ln -s "$RUNTIME_DIR/data/opportunities.sqlite3" "$DATABASE_PATH"

if [[ -e "$SCHEDULER_OUT_PATH" || -L "$SCHEDULER_OUT_PATH" ]]; then
  HAD_SCHEDULER_OUT_PATH=1
  /bin/mv "$SCHEDULER_OUT_PATH" "$SCHEDULER_OUT_BACKUP"
fi
/bin/ln -s "$SCHEDULER_OUT_TARGET" "$SCHEDULER_OUT_PATH"

if [[ -e "$SCHEDULER_ERR_PATH" || -L "$SCHEDULER_ERR_PATH" ]]; then
  HAD_SCHEDULER_ERR_PATH=1
  /bin/mv "$SCHEDULER_ERR_PATH" "$SCHEDULER_ERR_BACKUP"
fi
/bin/ln -s "$SCHEDULER_ERR_TARGET" "$SCHEDULER_ERR_PATH"

/usr/bin/touch "$PROJECT_DIR/data/install-complete"
trap - ERR HUP INT TERM
if [[ -f "$PLIST_BACKUP" ]]; then
  /bin/rm -f "$PLIST_BACKUP"
fi
if [[ "$ARCHIVED_PREVIOUS_RUNTIME" -eq 1 ]]; then
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/remove_superseded_runtime.py" \
    --runtime "$RUNTIME_DIR" \
    --stamp "$STAMP"
fi

echo "Installed the private runtime and verified one complete scan."
echo "Scheduler: $SCHEDULER_KIND."
echo "Scheduled scans run at $(/usr/bin/printf '%02d:%02d' "$MORNING_HOUR" "$MORNING_MINUTE") and $(/usr/bin/printf '%02d:%02d' "$AFTERNOON_HOUR" "$AFTERNOON_MINUTE") local time."
echo "No Opportunity Radar process remains running between scans."
