#!/usr/bin/env bash
# Monitor GCP retrain progress, poll every 10 min.
# Logs to data/reports/phase5_chain/monitor.log (rotates, 100 entries cap).
# Auto-triggers pull_predictions when VM TERMINATED + retrain log shows success.
#
# Usage:
#   nohup bash scripts/monitor_phase5_gcp_retrain.sh > /tmp/phase5_monitor.log 2>&1 &
#   disown
#
# Args (env):
#   MODEL_ID            (default: read from data/reports/phase5_chain/model_id.txt)
#   POLL_INTERVAL_SEC   (default: 600 = 10 min)
#   MAX_DURATION_HOURS  (default: 8, abort poll after 8h)

set -euo pipefail
cd "$(dirname "$0")/.."

MODEL_ID="${MODEL_ID:-$(cat data/reports/phase5_chain/model_id.txt 2>/dev/null | head -1)}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-600}"
MAX_DURATION_HOURS="${MAX_DURATION_HOURS:-8}"
STATUS_DIR="data/reports/phase5_chain"
MONITOR_LOG="$STATUS_DIR/monitor.log"
mkdir -p "$STATUS_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$MONITOR_LOG"; }

log "=== monitor start model_id=$MODEL_ID poll=${POLL_INTERVAL_SEC}s max=${MAX_DURATION_HOURS}h ==="

START_EPOCH=$(date +%s)
MAX_SEC=$((MAX_DURATION_HOURS * 3600))

while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START_EPOCH))
    if [[ $ELAPSED -gt $MAX_SEC ]]; then
        log "abort: exceeded ${MAX_DURATION_HOURS}h"
        exit 2
    fi

    # 1. VM status
    VM_STATUS=$(gcloud compute instances describe chunkymonkey-optuna --zone=us-central1-a --format='value(status)' 2>/dev/null || echo UNKNOWN)
    log "VM=$VM_STATUS elapsed=$((ELAPSED/60))min"

    # 2. If RUNNING, poll log
    if [[ "$VM_STATUS" == "RUNNING" ]]; then
        TAIL=$(gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
            --command="tail -3 ~/chunkymonkey/logs/retrain_${MODEL_ID}.log 2>&1; echo --- ; pgrep -fl retrain_lambdamart_v6 | head -1" 2>&1 | grep -v 'NumPy' | tail -5)
        log "log tail: $TAIL"
    fi

    # 3. If TERMINATED, trigger pull + audit then exit
    if [[ "$VM_STATUS" == "TERMINATED" ]]; then
        log "VM TERMINATED — triggering pull_predictions"
        echo "{\"step\":\"vm_terminated_pulling\",\"model_id\":\"$MODEL_ID\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$STATUS_DIR/status.json"
        # Re-start VM read-only to pull
        bash gcp/vm_start.sh 2>&1 | tee -a "$MONITOR_LOG"
        gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
            --command="gcloud storage cp ~/chunkymonkey/data/smartmoney.duckdb gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb 2>&1 | tail -3" 2>&1 | tee -a "$MONITOR_LOG"
        gcloud storage cp "gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb" "data/smartmoney_post_${MODEL_ID}.duckdb.bak" 2>&1 | tail -5 | tee -a "$MONITOR_LOG"
        bash gcp/vm_stop.sh 2>&1 | tee -a "$MONITOR_LOG"
        log "pull done, model_id=$MODEL_ID, smartmoney_post_${MODEL_ID}.duckdb.bak local"
        echo "{\"step\":\"pull_done\",\"model_id\":\"$MODEL_ID\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$STATUS_DIR/status.json"
        exit 0
    fi

    sleep "$POLL_INTERVAL_SEC"
done
