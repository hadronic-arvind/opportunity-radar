#!/bin/bash
set -Eeuo pipefail
umask 077

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The automatic scheduler installer currently supports macOS only." >&2
  exit 1
fi

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
SWAPPED=0
WAS_LOADED=0
HAD_PLIST=0
USE_LAUNCHD=0
USE_EXISTING_CRON=0
SCHEDULER_MUTATION_STARTED=0
CRON_SNAPSHOT_READY=0
CRON_MUTATION_ATTEMPTED=0

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
FAILED_RUNTIME="${RUNTIME_DIR}.failed-$STAMP"
FAILED_TARGET="${TARGET}.failed-$STAMP"
DASHBOARD_BACKUP="$PROJECT_DIR/dashboard/index.pre-runtime-$STAMP.html"
DATABASE_BACKUP="$PROJECT_DIR/data/opportunities.pre-runtime-$STAMP.sqlite3"
SCHEDULER_OUT_BACKUP="$PROJECT_DIR/logs/scheduler.pre-runtime-$STAMP.out.log"
SCHEDULER_ERR_BACKUP="$PROJECT_DIR/logs/scheduler.pre-runtime-$STAMP.err.log"

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

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/dashboard" "$PROJECT_DIR/logs" "$RUNTIME_PARENT" "$TARGET_DIR"
STAGE="$(/usr/bin/mktemp -d "$RUNTIME_PARENT/OpportunityRadar-stage.XXXXXX")"

cleanup_stage() {
  if [[ -n "$STAGE" && -d "$STAGE" && "$STAGE" == "$RUNTIME_PARENT/OpportunityRadar-stage."* ]]; then
    /bin/rm -rf "$STAGE"
  fi
  if [[ -f "$CRON_BACKUP" && ! -L "$CRON_BACKUP" ]]; then
    /bin/rm -f "$CRON_BACKUP"
  fi
}

rollback() {
  local exit_code="$1"
  trap - ERR
  echo "Installation failed; restoring the previous runtime." >&2
  if [[ "$SCHEDULER_MUTATION_STARTED" -eq 1 ]]; then
    if /bin/launchctl print "$SERVICE" >/dev/null 2>&1; then
      /bin/launchctl bootout "$SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  if [[ "$SWAPPED" -eq 1 && -d "$RUNTIME_DIR" ]]; then
    /bin/mv "$RUNTIME_DIR" "$FAILED_RUNTIME" || true
  fi
  if [[ "$SWAPPED" -eq 1 && -d "$PREVIOUS_RUNTIME" ]]; then
    /bin/mv "$PREVIOUS_RUNTIME" "$RUNTIME_DIR" || true
  fi
  if [[ "$SCHEDULER_MUTATION_STARTED" -eq 1 ]]; then
    if [[ "$HAD_PLIST" -eq 1 && -f "$PLIST_BACKUP" ]]; then
      /usr/bin/install -m 600 "$PLIST_BACKUP" "$TARGET" || true
    elif [[ "$HAD_PLIST" -eq 0 && -f "$TARGET" ]]; then
      /bin/mv "$TARGET" "$FAILED_TARGET" || true
    fi
    if [[ "$CRON_MUTATION_ATTEMPTED" -eq 1 && "$CRON_SNAPSHOT_READY" -eq 1 ]]; then
      "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" restore \
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

cd "$PROJECT_DIR"
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
if [[ -f "$PROJECT_DIR/config/profile.local.json" ]]; then
  /bin/cp "$PROJECT_DIR/config/profile.local.json" "$STAGE/config/profile.local.json"
fi
if [[ -f "$PROJECT_DIR/config/sources.local.json" ]]; then
  /bin/cp "$PROJECT_DIR/config/sources.local.json" "$STAGE/config/sources.local.json"
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
"$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" snapshot > "$CRON_BACKUP"
/bin/chmod 600 "$CRON_BACKUP"
CRON_SNAPSHOT_READY=1

if /bin/launchctl print "$SERVICE" >/dev/null 2>&1; then
  WAS_LOADED=1
fi
if [[ "$USE_LAUNCHD" -eq 1 && "$WAS_LOADED" -eq 0 && "$HAD_PLIST" -eq 0 ]] && \
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" verify \
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
      PYTHONPYCACHEPREFIX="$STAGE/data/pycache" \
      "$PYTHON_BIN" -m monitor scan --force --quiet
  )
else
  (
    cd "$STAGE"
    PYTHONPYCACHEPREFIX="$STAGE/data/pycache" \
      "$PYTHON_BIN" -m monitor scan --force --quiet
  )
fi

/bin/chmod -R go-rwx "$STAGE"
if [[ -d "$PREVIOUS_RUNTIME" ]]; then
  /bin/mv "$PREVIOUS_RUNTIME" "${PREVIOUS_RUNTIME}-$STAMP"
fi
if [[ -d "$RUNTIME_DIR" ]]; then
  /bin/mv "$RUNTIME_DIR" "$PREVIOUS_RUNTIME"
fi
/bin/mv "$STAGE" "$RUNTIME_DIR"
STAGE=""
SWAPPED=1

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
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" remove
elif [[ "$USE_EXISTING_CRON" -eq 1 ]]; then
  SCHEDULER_KIND="existing cron fallback"
else
  CRON_MUTATION_ATTEMPTED=1
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_cron.py" install \
    --runtime "$RUNTIME_DIR" \
    --morning-hour "$MORNING_HOUR" \
    --morning-minute "$MORNING_MINUTE" \
    --afternoon-hour "$AFTERNOON_HOUR" \
    --afternoon-minute "$AFTERNOON_MINUTE"
  SCHEDULER_KIND="cron fallback"
fi

if [[ -e "$PROJECT_DIR/dashboard/index.html" && ! -L "$PROJECT_DIR/dashboard/index.html" ]]; then
  /bin/mv "$PROJECT_DIR/dashboard/index.html" "$DASHBOARD_BACKUP"
fi
/bin/ln -sfn "$RUNTIME_DIR/dashboard/index.html" "$PROJECT_DIR/dashboard/index.html"

if [[ -e "$PROJECT_DIR/data/opportunities.sqlite3" && ! -L "$PROJECT_DIR/data/opportunities.sqlite3" ]]; then
  /bin/mv "$PROJECT_DIR/data/opportunities.sqlite3" "$DATABASE_BACKUP"
fi
/bin/ln -sfn "$RUNTIME_DIR/data/opportunities.sqlite3" "$PROJECT_DIR/data/opportunities.sqlite3"

for stream in out err; do
  project_log="$PROJECT_DIR/logs/scheduler.$stream.log"
  if [[ "$SCHEDULER_KIND" == *"cron fallback" ]]; then
    runtime_log="$RUNTIME_DIR/logs/cron.$stream.log"
  else
    runtime_log="$RUNTIME_DIR/logs/launchd.$stream.log"
  fi
  if [[ -e "$project_log" && ! -L "$project_log" ]]; then
    if [[ "$stream" == "out" ]]; then
      /bin/mv "$project_log" "$SCHEDULER_OUT_BACKUP"
    else
      /bin/mv "$project_log" "$SCHEDULER_ERR_BACKUP"
    fi
  fi
  /bin/ln -sfn "$runtime_log" "$project_log"
done

/usr/bin/touch "$PROJECT_DIR/data/install-complete"
if [[ -f "$PLIST_BACKUP" ]]; then
  /bin/rm -f "$PLIST_BACKUP"
fi
trap - ERR

echo "Installed the private runtime and verified one complete scan."
echo "Scheduler: $SCHEDULER_KIND."
echo "Scheduled scans run at $(/usr/bin/printf '%02d:%02d' "$MORNING_HOUR" "$MORNING_MINUTE") and $(/usr/bin/printf '%02d:%02d' "$AFTERNOON_HOUR" "$AFTERNOON_MINUTE") local time."
echo "No Opportunity Radar process remains running between scans."
