#!/usr/bin/env bash
# Read-only status probe for the active GCP stability retrain.
#
# This script never starts a VM, exports predictions, or writes remote files.
# It only SSHes to the configured VM and prints current pointers, process state,
# Optuna trial states, artifact existence, and the tail of the active log.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source scripts/lib/gcp_guard.sh

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
MODEL_ID="${MODEL_ID:-}"
TAIL_LINES="${TAIL_LINES:-120}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--dry) DRY_RUN=1; shift ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

if [[ "$DRY_RUN" == "1" ]]; then
    echo "[stability-status] dry run only; no VM or GCP command executed."
    echo "[stability-status] vm=$VM_NAME zone=$ZONE model_id=${MODEL_ID:-<infer-from-current-log>} tail_lines=$TAIL_LINES"
    exit 0
fi

require_gcp_explicit_ok "scripts/gcp_stability_status.sh"

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap \
  --command="MODEL_ID='$MODEL_ID' TAIL_LINES='$TAIL_LINES' bash -s" <<'REMOTE'
set -euo pipefail

cd ~/chunkymonkey

REPORT_DIR="data/reports/stability_retrain"
PID="$(cat "${REPORT_DIR}/current.pid" 2>/dev/null || true)"
LOG="$(cat "${REPORT_DIR}/current.logpath" 2>/dev/null || true)"
ART="$(cat "${REPORT_DIR}/current.artifact" 2>/dev/null || true)"
GCS_DIR="$(cat "${REPORT_DIR}/current.gcs_dir" 2>/dev/null || true)"

if [ -z "${MODEL_ID:-}" ] && [ -n "$LOG" ]; then
  base="$(basename "$LOG")"
  MODEL_ID="${base%%_stability_retrain_*}"
fi
export MODEL_ID ART

echo "model_id=${MODEL_ID:-}"
echo "pid=$PID"
echo "log=$LOG"
echo "summary=$ART"
echo "gcs_dir=$GCS_DIR"

if [ -n "$PID" ]; then
  ps -p "$PID" -o pid,ppid,stat,etime,pcpu,pmem,rss,nlwp,cmd || true
  CHILD="$(pgrep -P "$PID" | head -1 || true)"
  echo "child=$CHILD"
  if [ -n "$CHILD" ]; then
    ps -p "$CHILD" -o pid,ppid,stat,etime,pcpu,pmem,rss,nlwp,cmd || true
  fi
else
  echo "child="
fi

. .venv/bin/activate
python - <<'PY'
import json
import os
import pathlib
import sqlite3
from datetime import datetime

model_id = os.environ.get("MODEL_ID") or ""
if not model_id:
    print("optuna_status=missing_model_id")
    raise SystemExit(0)

db_path = pathlib.Path(f"data/reports/optuna/{model_id}.db")
best_path = pathlib.Path(f"data/reports/optuna/{model_id}.best.json")
summary_env = os.environ.get("ART") or ""
summary_path = pathlib.Path(summary_env) if summary_env else pathlib.Path(
    f"data/reports/stability_retrain/{model_id}_stability_retrain_unknown.json"
)

print("optuna_db", str(db_path), db_path.exists(), db_path.stat().st_size if db_path.exists() else None)
if db_path.exists():
    print("optuna_db_mtime", datetime.fromtimestamp(db_path.stat().st_mtime).isoformat())
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        cur = con.cursor()
        print("trial_states", cur.execute("select state, count(*) from trials group by state").fetchall())
        print("complete_count", cur.execute("select count(*) from trials where state=?", ("COMPLETE",)).fetchone()[0])
        print(
            "latest_trials",
            cur.execute(
                "select trial_id, number, state, datetime_start, datetime_complete "
                "from trials order by trial_id desc limit 12"
            ).fetchall(),
        )
        print(
            "best_complete",
            cur.execute(
                "select t.trial_id, t.number, tv.value, t.datetime_complete "
                "from trials t join trial_values tv on t.trial_id=tv.trial_id "
                "where t.state=? order by tv.value desc limit 8",
                ("COMPLETE",),
            ).fetchall(),
        )
    finally:
        con.close()

for path in [best_path, summary_path]:
    print("artifact", str(path), path.exists(), path.stat().st_size if path.exists() else None)
    if path.exists() and path.suffix == ".json":
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            print("artifact_json_error", str(path), repr(exc))
            continue
        summary = {
            key: data.get(key)
            for key in [
                "model_id",
                "best_value",
                "best_trial",
                "prediction_rows",
                "retrain_exit",
                "train_log_found",
                "best_artifact",
                "train_log_artifact",
            ]
        }
        print("artifact_json_summary", json.dumps(summary, ensure_ascii=False, sort_keys=True))
PY

if [ -n "$LOG" ] && [ -f "$LOG" ]; then
  echo "log_tail_begin"
  tail -n "${TAIL_LINES:-120}" "$LOG"
  echo "log_tail_end"
else
  echo "log_missing"
fi
REMOTE
