#!/usr/bin/env bash
# post_retrain_chain.sh — 自动链接 final fit 完成 → export → pull → post_retrain_pipeline
#
# 2026-05-22 用户 "全部跑完": Final fit (--use-checkpoint-best) → export prediction → vm_stop → local import + paper_sim + Phase4 gate

set -uo pipefail
cd "$(dirname "$0")/.."
export CHUNKYMONKEY_GCP_EXPLICIT_OK=1

MODEL_ID="${MODEL_ID:-lgbm_phase5_stability_20260521T055800Z}"
POLL_SEC="${POLL_SEC:-300}"  # 5 min poll for fit completion
MAX_FIT_HOURS="${MAX_FIT_HOURS:-3}"
LOG="data/reports/post_retrain_chain.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

notify_macos() {
    osascript -e "display notification \"$2\" with title \"$1\" sound name \"Glass\"" 2>/dev/null || true
}

log "=== post_retrain chain start model_id=$MODEL_ID ==="

# Stage 1: wait for fit completion
log "Stage 1: wait final fit completion (poll ${POLL_SEC}s, max ${MAX_FIT_HOURS}h)"
START=$(date +%s)
MAX_SEC=$((MAX_FIT_HOURS * 3600))
loop=0

while true; do
    loop=$((loop + 1))
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))

    if [[ "$ELAPSED" -gt "$MAX_SEC" ]]; then
        log "FATAL: max fit timeout ${MAX_FIT_HOURS}h reached"
        notify_macos "Post retrain TIMEOUT" "final fit > ${MAX_FIT_HOURS}h, manual review"
        exit 1
    fi

    # Check remote fit process
    REMOTE_STATUS=$(gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap --command "
PID=\$(cat ~/chunkymonkey/data/reports/stability_retrain/current.pid 2>/dev/null)
if [ -z \"\$PID\" ]; then echo 'NO_PID'; exit; fi
if ps -p \$PID > /dev/null 2>&1; then
  echo 'RUNNING pid=' \$PID 'elapsed=' \$(ps -p \$PID -o etime --no-headers 2>/dev/null | tr -d ' ')
else
  echo 'EXITED pid=' \$PID
fi
tail -5 /tmp/final_fit.log 2>/dev/null
" 2>&1 | tail -10)

    log "loop #$loop elapsed=$((ELAPSED/60))min status:"
    log "$REMOTE_STATUS"

    if echo "$REMOTE_STATUS" | grep -q "EXITED"; then
        log "final fit EXITED, checking prediction rows"
        break
    fi

    log "sleep $POLL_SEC sec..."
    sleep "$POLL_SEC"
done

# Stage 2: export prediction parquet to GCS
log ""
log "Stage 2: export prediction parquet via gcp_export_model_predictions.sh"
if MODEL_ID="$MODEL_ID" bash scripts/gcp_export_model_predictions.sh 2>&1 | tee -a "$LOG" | tail -5; then
    log "export OK"
else
    log "FATAL: export failed"
    notify_macos "Post retrain FAIL" "export failed, manual review"
    exit 2
fi

# Stage 3: pull parquet locally
log ""
log "Stage 3: pull parquet to local"
mkdir -p "data/phase5_exports/$MODEL_ID"
if gcloud storage cp -r "gs://chunkymonkey-data-0517/phase5/stability_retrain/$MODEL_ID/predictions/*" "data/phase5_exports/$MODEL_ID/" 2>&1 | tee -a "$LOG" | tail -5; then
    log "pull OK"
    ls -la "data/phase5_exports/$MODEL_ID/" | tee -a "$LOG" | head -5
else
    log "FATAL: pull failed"
    notify_macos "Post retrain FAIL" "GCS pull failed, manual review"
    exit 3
fi

# Stage 4: vm_stop (cost saving)
log ""
log "Stage 4: vm_stop"
bash gcp/vm_stop.sh 2>&1 | tee -a "$LOG" | tail -3 || log "WARN: vm_stop failed (manual: bash gcp/vm_stop.sh)"

# Stage 5: post_retrain_pipeline (import + paper_sim + Phase4 + registry)
log ""
log "Stage 5: post_retrain_pipeline (import + paper_sim + Phase4 gate + registry)"
if MODEL_ID="$MODEL_ID" bash scripts/post_retrain_pipeline.sh 2>&1 | tee -a "$LOG"; then
    log "post_retrain pipeline OK"
else
    log "post_retrain pipeline had errors (exit non-zero), verdict can still be parseable"
fi

# Final: read verdict
VERDICT_FILE="data/reports/post_retrain/$MODEL_ID/phase4_gate_${MODEL_ID}.json"
if [[ -f "$VERDICT_FILE" ]]; then
    VERDICT=$(python3 -c "import json; print(json.load(open('$VERDICT_FILE'))['verdict'])" 2>/dev/null || echo "unknown")
    log ""
    log "=== FINAL VERDICT: $VERDICT ==="
    notify_macos "Post retrain DONE" "Phase4 verdict=$VERDICT for $MODEL_ID"
else
    log ""
    log "=== FINAL: verdict file not found ==="
    notify_macos "Post retrain DONE" "pipeline ran but verdict file missing, review log"
fi

log "=== chain complete ==="
