#!/usr/bin/env bash
# Read-only job collection + daily report for automatejob.
#
# Designed to run on a schedule (Hermes cron, script/no-agent mode):
#   * flock(1) prevents overlapping runs (a second tick exits immediately)
#   * timeout(1) bounds the collection so a hung source can't wedge the job
#   * logs land in reports/collect-*.log; the digest is reports/daily.txt
#   * a failed collect yields a nonzero exit code and skips the daily report
#
# This script only READS public job-board APIs. It never applies, emails,
# or touches authenticated accounts.

set -uo pipefail

# --- stable absolute paths (do not depend on $PWD or $PATH) ----------------
PROJECT_DIR="/home/sahil/Work/automatejob"
PYTHON="/usr/bin/python3"          # system python (has `requests` installed)
CONFIG="$PROJECT_DIR/config.toml"
REPORTS_DIR="$PROJECT_DIR/reports"
LOCK_FILE="$REPORTS_DIR/collect.lock"
TIMEOUT_SECS="${AUTOMATEJOB_TIMEOUT:-540}"

mkdir -p "$REPORTS_DIR"

# --- single-run lock -------------------------------------------------------
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date -Is) collect: another run in progress, skipping this tick"
    exit 0
fi

cd "$PROJECT_DIR" || { echo "cannot cd to $PROJECT_DIR"; exit 1; }

run_log="$REPORTS_DIR/collect-$(date +%Y%m%d-%H%M%S).log"

collect_status=0
{
    echo "=== collect started $(date -Is) ==="
    timeout "$TIMEOUT_SECS" "$PYTHON" -m automatejob -c "$CONFIG" -v collect
    collect_status=$?
    echo "=== collect exit: $collect_status ==="

    if [ "$collect_status" -eq 0 ]; then
        "$PYTHON" -m automatejob -c "$CONFIG" daily -o "$REPORTS_DIR/daily.txt"
        echo "=== daily report -> $REPORTS_DIR/daily.txt ==="
    else
        echo "=== collect FAILED (exit $collect_status); daily report skipped ==="
    fi
} >> "$run_log" 2>&1

if [ "$collect_status" -ne 0 ]; then
    echo "automatejob collect failed (exit $collect_status); see $run_log"
    exit "$collect_status"
fi

# Concise stdout summary (this is what Hermes cron records/delivers).
echo "automatejob collect + daily report OK ($(date -Is))"
echo "actionable jobs:"
"$PYTHON" -m automatejob -c "$CONFIG" list --actionable --all --no-reasons --limit 10 2>/dev/null \
    || true
exit 0
