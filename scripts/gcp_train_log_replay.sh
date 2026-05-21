#!/usr/bin/env bash
# Launch a controlled GCP train-log-only replay for an existing LambdaMART model.
#
# This script intentionally sends a small remote wrapper instead of running a
# fragile one-line SSH command. It assumes the VM already has the required DB,
# checkpoint, venv, and current enough code; copy scoped files first when needed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "scripts/gcp_train_log_replay.sh"

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
MODEL_ID="${MODEL_ID:-lgbm_phase5_gcp_20260520T010718}"
LABEL="${LABEL:-fwd_cost_after_20d}"
START_DATE="${START_DATE:-2023-01-03}"
END_DATE="${END_DATE:-2026-04-14}"
MIN_TRAIN_MONTHS="${MIN_TRAIN_MONTHS:-6}"
FORWARD_MONTHS="${FORWARD_MONTHS:-1}"
N_TRIALS="${N_TRIALS:-50}"
N_ESTIMATORS="${N_ESTIMATORS:-300}"
OMP_NUM_THREADS_REMOTE="${OMP_NUM_THREADS_REMOTE:-32}"
CHECKPOINT_PATH="${CHECKPOINT_PATH:-data/reports/optuna/${MODEL_ID}.best.json}"
REPORT_DIR="${REPORT_DIR:-data/reports/train_log_replay}"
GCS_DIR="${GCS_DIR:-gs://chunkymonkey-data-0517/phase5/train_log_replay}"
TRAIN_LOG_REPLAY_ID="${TRAIN_LOG_REPLAY_ID:-}"

bash gcp/vm_start.sh

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap \
  --command="CHUNKYMONKEY_GCP_EXPLICIT_OK='$CHUNKYMONKEY_GCP_EXPLICIT_OK' MODEL_ID='$MODEL_ID' LABEL='$LABEL' START_DATE='$START_DATE' END_DATE='$END_DATE' MIN_TRAIN_MONTHS='$MIN_TRAIN_MONTHS' FORWARD_MONTHS='$FORWARD_MONTHS' N_TRIALS='$N_TRIALS' N_ESTIMATORS='$N_ESTIMATORS' OMP_NUM_THREADS_REMOTE='$OMP_NUM_THREADS_REMOTE' CHECKPOINT_PATH='$CHECKPOINT_PATH' REPORT_DIR='$REPORT_DIR' GCS_DIR='$GCS_DIR' TRAIN_LOG_REPLAY_ID='$TRAIN_LOG_REPLAY_ID' bash -s" <<'REMOTE'
set -euo pipefail

cd ~/chunkymonkey
sudo shutdown -c >/dev/null 2>&1 || true

mkdir -p "$REPORT_DIR"
RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${REPORT_DIR}/${MODEL_ID}_train_log_${RUN_TS}.log"
ARTIFACT="${REPORT_DIR}/${MODEL_ID}_train_log_${RUN_TS}.json"

printf "%s\n" "$LOG" > "${REPORT_DIR}/current.logpath"
printf "%s\n" "$ARTIFACT" > "${REPORT_DIR}/current.artifact"
printf "%s\n" "$GCS_DIR" > "${REPORT_DIR}/current.gcs_dir"

(
  set +e
  cd ~/chunkymonkey
  . .venv/bin/activate
  export PYTHONPATH=backend
  export OMP_NUM_THREADS="$OMP_NUM_THREADS_REMOTE"
  export MODEL_ID ARTIFACT

  echo "[remote] train-log replay start $(date -Iseconds)"
  python backend/scripts/retrain_lambdamart_v6.py \
    --model-id "$MODEL_ID" \
    --label "$LABEL" \
    --n-trials "$N_TRIALS" \
    --n-estimators "$N_ESTIMATORS" \
    --start-date "$START_DATE" \
    --end-date "$END_DATE" \
    --min-train-months "$MIN_TRAIN_MONTHS" \
	    --forward-months "$FORWARD_MONTHS" \
	    --checkpoint-path "$CHECKPOINT_PATH" \
	    --use-checkpoint-best \
	    --train-log-only \
	    --resume-train-log \
	    --train-log-replay-id "$TRAIN_LOG_REPLAY_ID"
  rc=$?
  echo "[remote] retrain exit $rc $(date -Iseconds)"

  if [ "$rc" -eq 0 ]; then
    python - <<'PY'
import json
import os
from pathlib import Path

from services.duck_adapter import connect
from services.db import DB_PATH

model_id = os.environ["MODEL_ID"]
artifact = os.environ["ARTIFACT"]
conn = connect(str(DB_PATH), read_only=True)
try:
    row = conn.execute(
        "SELECT * FROM fact_model_train_log WHERE model_id = ? ORDER BY built_at DESC LIMIT 1",
        [model_id],
    ).fetchone()
finally:
    conn.close()
if row is None:
    raise SystemExit("no train-log row found")
Path(artifact).write_text(
    json.dumps(dict(row), ensure_ascii=False, indent=2, default=str) + "\n",
    encoding="utf-8",
)
print(f"[remote] wrote artifact {artifact}")
PY
    gcloud storage cp "$ARTIFACT" "$GCS_DIR/$(basename "$ARTIFACT")" || true
  fi

  gcloud storage cp "$LOG" "$GCS_DIR/$(basename "$LOG")" || true
  sudo shutdown -h +1 "chunkymonkey train-log replay complete"
  exit "$rc"
) > "$LOG" 2>&1 &

pid=$!
printf "%s\n" "$pid" > "${REPORT_DIR}/current.pid"
echo "STARTED pid=$pid log=$LOG artifact=$ARTIFACT gcs=$GCS_DIR"
REMOTE
