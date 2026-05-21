#!/usr/bin/env bash
# Launch a controlled GCP LambdaMART retrain with opt-in window RankIC stability penalties.
#
# This wrapper does not git-pull on the VM. Sync scoped code/data first when the
# remote workspace is stale, then launch this script with CHUNKYMONKEY_GCP_EXPLICIT_OK=1.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "scripts/gcp_stability_retrain.sh"

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
MODEL_ID="${MODEL_ID:-lgbm_phase5_stability_$(date -u +%Y%m%dT%H%M%SZ)}"
LABEL="${LABEL:-fwd_cost_after_20d}"
START_DATE="${START_DATE:-2023-01-03}"
END_DATE="${END_DATE:-2026-04-14}"
MIN_TRAIN_MONTHS="${MIN_TRAIN_MONTHS:-6}"
FORWARD_MONTHS="${FORWARD_MONTHS:-1}"
N_TRIALS="${N_TRIALS:-80}"
N_ESTIMATORS="${N_ESTIMATORS:-300}"
TOP_K="${TOP_K:-5}"
TURNOVER_LIMIT="${TURNOVER_LIMIT:-3.0}"
TURNOVER_PENALTY_WEIGHT="${TURNOVER_PENALTY_WEIGHT:-0.02}"
WINDOW_RANK_IC_STD_PENALTY_WEIGHT="${WINDOW_RANK_IC_STD_PENALTY_WEIGHT:-0.50}"
WINDOW_RANK_IC_NEGATIVE_RATE_PENALTY_WEIGHT="${WINDOW_RANK_IC_NEGATIVE_RATE_PENALTY_WEIGHT:-0.20}"
OPTUNA_N_JOBS_REMOTE="${OPTUNA_N_JOBS_REMOTE:-8}"
OMP_NUM_THREADS_REMOTE="${OMP_NUM_THREADS_REMOTE:-4}"
LIGHTGBM_NUM_THREADS_REMOTE="${LIGHTGBM_NUM_THREADS_REMOTE:-$OMP_NUM_THREADS_REMOTE}"
REMOTE_MAX_THREADS="${REMOTE_MAX_THREADS:-32}"
WARM_START_CHECKPOINT="${WARM_START_CHECKPOINT:-data/reports/optuna/lgbm_phase5_gcp_20260520T010718.best.json}"
STUDY_STORAGE="${STUDY_STORAGE:-sqlite:///data/reports/optuna/${MODEL_ID}.db}"
STUDY_NAME="${STUDY_NAME:-$MODEL_ID}"
CHECKPOINT_PATH="${CHECKPOINT_PATH:-data/reports/optuna/${MODEL_ID}.best.json}"
REPORT_DIR="${REPORT_DIR:-data/reports/stability_retrain}"
GCS_DIR="${GCS_DIR:-gs://chunkymonkey-data-0517/phase5/stability_retrain}"
FALLBACK_SHUTDOWN_MINUTES="${FALLBACK_SHUTDOWN_MINUTES:-720}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--dry) DRY_RUN=1; shift ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

echo "[stability-retrain] model_id=$MODEL_ID"
echo "[stability-retrain] dates=$START_DATE..$END_DATE label=$LABEL windows=${MIN_TRAIN_MONTHS}m/${FORWARD_MONTHS}m"
echo "[stability-retrain] trials=$N_TRIALS estimators=$N_ESTIMATORS top_k=$TOP_K"
echo "[stability-retrain] penalties: std=$WINDOW_RANK_IC_STD_PENALTY_WEIGHT negative_rate=$WINDOW_RANK_IC_NEGATIVE_RATE_PENALTY_WEIGHT"
echo "[stability-retrain] parallelism: optuna_jobs=$OPTUNA_N_JOBS_REMOTE omp=$OMP_NUM_THREADS_REMOTE lightgbm=$LIGHTGBM_NUM_THREADS_REMOTE max_threads=$REMOTE_MAX_THREADS"
echo "[stability-retrain] checkpoint=$CHECKPOINT_PATH study=$STUDY_STORAGE"
echo "[stability-retrain] artifacts=$REPORT_DIR gcs=$GCS_DIR"

TOTAL_THREADS=$((OPTUNA_N_JOBS_REMOTE * OMP_NUM_THREADS_REMOTE))
if (( TOTAL_THREADS > REMOTE_MAX_THREADS )); then
    echo "[stability-retrain] refusing oversubscribed settings: optuna_jobs * omp = $TOTAL_THREADS > $REMOTE_MAX_THREADS" >&2
    exit 3
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "[stability-retrain] dry run only; no VM or GCP command executed."
    exit 0
fi

bash gcp/vm_start.sh

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap \
  --command="CHUNKYMONKEY_GCP_EXPLICIT_OK='$CHUNKYMONKEY_GCP_EXPLICIT_OK' MODEL_ID='$MODEL_ID' LABEL='$LABEL' START_DATE='$START_DATE' END_DATE='$END_DATE' MIN_TRAIN_MONTHS='$MIN_TRAIN_MONTHS' FORWARD_MONTHS='$FORWARD_MONTHS' N_TRIALS='$N_TRIALS' N_ESTIMATORS='$N_ESTIMATORS' TOP_K='$TOP_K' TURNOVER_LIMIT='$TURNOVER_LIMIT' TURNOVER_PENALTY_WEIGHT='$TURNOVER_PENALTY_WEIGHT' WINDOW_RANK_IC_STD_PENALTY_WEIGHT='$WINDOW_RANK_IC_STD_PENALTY_WEIGHT' WINDOW_RANK_IC_NEGATIVE_RATE_PENALTY_WEIGHT='$WINDOW_RANK_IC_NEGATIVE_RATE_PENALTY_WEIGHT' OPTUNA_N_JOBS_REMOTE='$OPTUNA_N_JOBS_REMOTE' OMP_NUM_THREADS_REMOTE='$OMP_NUM_THREADS_REMOTE' LIGHTGBM_NUM_THREADS_REMOTE='$LIGHTGBM_NUM_THREADS_REMOTE' WARM_START_CHECKPOINT='$WARM_START_CHECKPOINT' STUDY_STORAGE='$STUDY_STORAGE' STUDY_NAME='$STUDY_NAME' CHECKPOINT_PATH='$CHECKPOINT_PATH' REPORT_DIR='$REPORT_DIR' GCS_DIR='$GCS_DIR' FALLBACK_SHUTDOWN_MINUTES='$FALLBACK_SHUTDOWN_MINUTES' bash -s" <<'REMOTE'
set -euo pipefail

cd ~/chunkymonkey
sudo shutdown -c >/dev/null 2>&1 || true

mkdir -p "$REPORT_DIR"
mkdir -p "$(dirname "$CHECKPOINT_PATH")"
RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${REPORT_DIR}/${MODEL_ID}_stability_retrain_${RUN_TS}.log"
SUMMARY="${REPORT_DIR}/${MODEL_ID}_stability_retrain_${RUN_TS}.json"
BEST_ARTIFACT="${REPORT_DIR}/${MODEL_ID}_best_${RUN_TS}.json"
TRAIN_LOG_ARTIFACT="${REPORT_DIR}/${MODEL_ID}_train_log_${RUN_TS}.json"

printf "%s\n" "$LOG" > "${REPORT_DIR}/current.logpath"
printf "%s\n" "$SUMMARY" > "${REPORT_DIR}/current.artifact"
printf "%s\n" "$GCS_DIR" > "${REPORT_DIR}/current.gcs_dir"

. .venv/bin/activate
export PYTHONPATH=backend
python -m py_compile backend/scripts/retrain_lambdamart_v6.py backend/scripts/run_p0b_lambdamart_v6.py

(
  set +e
  cd ~/chunkymonkey
  . .venv/bin/activate
  export PYTHONPATH=backend
  export OPTUNA_N_JOBS="$OPTUNA_N_JOBS_REMOTE"
  export OMP_NUM_THREADS="$OMP_NUM_THREADS_REMOTE"
  export LIGHTGBM_NUM_THREADS="$LIGHTGBM_NUM_THREADS_REMOTE"
  export MODEL_ID SUMMARY BEST_ARTIFACT TRAIN_LOG_ARTIFACT CHECKPOINT_PATH

  WARM_ARGS=()
  if [ -n "$WARM_START_CHECKPOINT" ] && [ -f "$WARM_START_CHECKPOINT" ]; then
    WARM_ARGS=(--warm-start-checkpoint "$WARM_START_CHECKPOINT")
  else
    echo "[remote] warm-start checkpoint unavailable, continuing without it: $WARM_START_CHECKPOINT"
  fi

  echo "[remote] stability retrain start $(date -Iseconds)"
  python backend/scripts/retrain_lambdamart_v6.py \
    --model-id "$MODEL_ID" \
    --label "$LABEL" \
    --n-trials "$N_TRIALS" \
    --n-estimators "$N_ESTIMATORS" \
    --start-date "$START_DATE" \
    --end-date "$END_DATE" \
    --min-train-months "$MIN_TRAIN_MONTHS" \
    --forward-months "$FORWARD_MONTHS" \
    --top-k "$TOP_K" \
    --turnover-limit "$TURNOVER_LIMIT" \
    --turnover-penalty-weight "$TURNOVER_PENALTY_WEIGHT" \
    --window-rank-ic-std-penalty-weight "$WINDOW_RANK_IC_STD_PENALTY_WEIGHT" \
    --window-rank-ic-negative-rate-penalty-weight "$WINDOW_RANK_IC_NEGATIVE_RATE_PENALTY_WEIGHT" \
    --study-storage "$STUDY_STORAGE" \
    --study-name "$STUDY_NAME" \
    --checkpoint-path "$CHECKPOINT_PATH" \
    "${WARM_ARGS[@]}"
  rc=$?
  export RETRAIN_EXIT="$rc"
  echo "[remote] retrain exit $rc $(date -Iseconds)"

  if [ -f "$CHECKPOINT_PATH" ]; then
    cp "$CHECKPOINT_PATH" "$BEST_ARTIFACT"
  fi

  python - <<'PY'
import json
import os
from pathlib import Path

from services.db import DB_PATH
from services.duck_adapter import connect

model_id = os.environ["MODEL_ID"]
summary_path = Path(os.environ["SUMMARY"])
train_log_artifact = Path(os.environ["TRAIN_LOG_ARTIFACT"])
best_artifact = Path(os.environ["BEST_ARTIFACT"])
checkpoint_path = Path(os.environ["CHECKPOINT_PATH"])
payload = {
    "model_id": model_id,
    "retrain_exit": int(os.environ.get("RETRAIN_EXIT", "99")),
    "checkpoint_path": str(checkpoint_path),
    "best_artifact": str(best_artifact) if best_artifact.exists() else None,
    "train_log_artifact": None,
    "prediction_rows": None,
    "train_log_found": False,
}
conn = connect(str(DB_PATH), read_only=True)
try:
    pred_row = conn.execute(
        "SELECT COUNT(*) FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = ?",
        [model_id],
    ).fetchone()
    payload["prediction_rows"] = int(pred_row[0] or 0)
    train_log = conn.execute(
        "SELECT * FROM fact_model_train_log WHERE model_id = ? ORDER BY built_at DESC LIMIT 1",
        [model_id],
    ).fetchone()
finally:
    conn.close()
if train_log is not None:
    train_log_artifact.write_text(
        json.dumps(dict(train_log), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    payload["train_log_artifact"] = str(train_log_artifact)
    payload["train_log_found"] = True
summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"[remote] wrote summary {summary_path}")
PY

  [ -f "$SUMMARY" ] && gcloud storage cp "$SUMMARY" "$GCS_DIR/$(basename "$SUMMARY")" || true
  [ -f "$BEST_ARTIFACT" ] && gcloud storage cp "$BEST_ARTIFACT" "$GCS_DIR/$(basename "$BEST_ARTIFACT")" || true
  [ -f "$TRAIN_LOG_ARTIFACT" ] && gcloud storage cp "$TRAIN_LOG_ARTIFACT" "$GCS_DIR/$(basename "$TRAIN_LOG_ARTIFACT")" || true
  gcloud storage cp "$LOG" "$GCS_DIR/$(basename "$LOG")" || true
  sudo shutdown -h +1 "chunkymonkey stability retrain complete"
  exit "$rc"
) > "$LOG" 2>&1 &

pid=$!
printf "%s\n" "$pid" > "${REPORT_DIR}/current.pid"
sudo shutdown -h +"$FALLBACK_SHUTDOWN_MINUTES" "chunkymonkey stability retrain fallback ttl" >/dev/null 2>&1 || true
echo "STARTED pid=$pid log=$LOG summary=$SUMMARY gcs=$GCS_DIR"
REMOTE
