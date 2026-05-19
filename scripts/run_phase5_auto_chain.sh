#!/usr/bin/env bash
# Phase 5 autonomous chain — waits panel rebuild PID, runs parquet export + GCS sync +
# GCP retrain + post-retrain pipeline + final audit.
#
# Design: sub-agent af3bf472 (CLAUDE.md §11.5 multi-agent workflow).
# GCS sync optim: sub-agent a267bf47 partial parquet export 14× speedup.
#
# Usage:
#   bash scripts/run_phase5_auto_chain.sh --panel-pid 41023
#   bash scripts/run_phase5_auto_chain.sh --skip-panel-wait  # panel already done
#   bash scripts/run_phase5_auto_chain.sh --dry-run

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

STATUS_DIR="$REPO_ROOT/data/reports/phase5_chain"
mkdir -p "$STATUS_DIR"
MODEL_ID="lgbm_phase5_extended_$(date +%Y%m%dT%H%M%S)"
LOG=/tmp/phase5_auto_chain.log
PANEL_PID=""
SKIP_PANEL_WAIT=0
DRY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --panel-pid) PANEL_PID="$2"; shift 2 ;;
        --skip-panel-wait) SKIP_PANEL_WAIT=1; shift ;;
        --dry-run) DRY=1; shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

write_status() {
    local step="$1"; local msg="$2"
    echo "{\"step\": \"$step\", \"msg\": \"$msg\", \"at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"model_id\": \"$MODEL_ID\"}" > "$STATUS_DIR/status.json"
}
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

write_status "start" "Phase 5 auto chain launched"
log "=== Phase 5 auto chain start, model_id=$MODEL_ID ==="

# === Step 0: pre-flight disk check ===
AVAIL_GB=$(df -g . | awk 'NR==2{print $4}')
log "disk avail: ${AVAIL_GB}GB"
if [[ "$AVAIL_GB" -lt 10 ]]; then
    log "FATAL: disk < 10GB, abort"
    write_status "abort" "disk < 10GB"
    exit 2
fi

# === Step 1: wait panel rebuild ===
if [[ "$SKIP_PANEL_WAIT" == "0" && -n "$PANEL_PID" ]]; then
    log "Step 1: wait panel rebuild PID $PANEL_PID..."
    write_status "wait_panel" "PID=$PANEL_PID"
    while kill -0 "$PANEL_PID" 2>/dev/null; do sleep 60; done
    log "panel rebuild PID $PANEL_PID exited"
    # Verify panel range
    PANEL_RANGE=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute('SELECT MIN(signal_date), MAX(signal_date), COUNT(*) FROM mart_p0a_feature_label_panel_v4').fetchone()
print(f'{r[0]}|{r[1]}|{r[2]}')
" 2>&1 | tail -1)
    log "v4 panel: $PANEL_RANGE"
    write_status "panel_done" "$PANEL_RANGE"
    MIN_DATE=$(echo "$PANEL_RANGE" | cut -d'|' -f1)
    if [[ "$MIN_DATE" > "2024-01-01" ]]; then
        log "WARN: panel min_date=$MIN_DATE > 2024-01-01, expected 2023-01-03 (rebuild may be incremental only)"
    fi
fi

# === Step 2: partial parquet export ===
log "Step 2: partial parquet export"
write_status "parquet_export" "exporting mart_p0a_feature_label_panel_v4 + dim_trading_calendar"
EXPORT_DIR=/tmp/phase5_parquet_$(date +%Y%m%d_%H%M%S)
mkdir -p "$EXPORT_DIR"
if [[ "$DRY" == "0" ]]; then
    PYTHONPATH=backend python3 -c "
import duckdb, os, time
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
for tbl in ['mart_p0a_label_panel', 'mart_p0a_feature_label_panel_v4', 'dim_trading_calendar']:
    out = f'$EXPORT_DIR/{tbl}.parquet'
    t0 = time.time()
    con.execute(f\"COPY {tbl} TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)\")
    sz_mb = os.path.getsize(out) / 1024 / 1024
    print(f'{tbl}: {sz_mb:.1f} MB / {time.time()-t0:.1f}s')
con.close()
" 2>&1 | tee -a "$LOG"
    TOTAL_MB=$(du -m "$EXPORT_DIR" | tail -1 | cut -f1)
    log "parquet total: ${TOTAL_MB} MB"
fi

# === Step 3: GCS sync (partial parquet) ===
log "Step 3: GCS sync"
write_status "gcs_sync" "uploading $EXPORT_DIR → gs://chunkymonkey-data-0517/phase5/"
if [[ "$DRY" == "0" ]]; then
    gcloud storage cp -r "$EXPORT_DIR" "gs://chunkymonkey-data-0517/phase5/panel_$(date +%Y%m%d)/" 2>&1 | tee -a "$LOG"
fi

# === Step 4: start GCP VM ===
log "Step 4: start GCP VM"
write_status "vm_start" "starting chunkymonkey-optuna"
if [[ "$DRY" == "0" ]]; then
    bash gcp/vm_start.sh 2>&1 | tee -a "$LOG"
fi

# === Step 5: SSH retrain (self-shutdown on completion) ===
log "Step 5: SSH retrain on VM, model_id=$MODEL_ID"
write_status "retrain_launched" "$MODEL_ID"
if [[ "$DRY" == "0" ]]; then
    REMOTE_CMD="cd ~/chunkymonkey && \
        git pull origin main 2>&1 | tail -3 && \
        echo '[remote] download panel from GCS' && \
        gcloud storage cp -r gs://chunkymonkey-data-0517/phase5/panel_$(date +%Y%m%d)/ ./data/imports/ 2>&1 | tail -3 && \
        echo '[remote] start retrain nohup + self-shutdown' && \
        PYTHONPATH=backend nohup bash -c \"\
            python backend/scripts/retrain_lambdamart_v6.py \\
                --model-id '$MODEL_ID' --start-date 2023-01-03 --end-date 2026-05-19 \\
                --n-trials 50 --min-train-months 6 --top-k 20; \
            RC=\\\$?; \
            echo '[remote] retrain rc=\\\$RC @ \\\$(date)'; \
            sudo shutdown -h +1 'chunkymonkey retrain done'; \
        \" > /tmp/retrain_${MODEL_ID}.log 2>&1 & \
        sleep 5 && pgrep -f 'retrain_lambdamart_v6.*${MODEL_ID}' | head -1"
    gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
        --command="$REMOTE_CMD" 2>&1 | tee -a "$LOG"
    echo "$MODEL_ID" > "$STATUS_DIR/model_id.txt"
fi

# === Step 6: watcher VM TERMINATED → pull → post-retrain ===
log "Step 6: watcher VM TERMINATED → pull predictions"
write_status "wait_vm_terminated" "$MODEL_ID"
if [[ "$DRY" == "0" ]]; then
    while true; do
        STATUS=$(gcloud compute instances describe chunkymonkey-optuna --zone=us-central1-a --format='value(status)' 2>/dev/null || echo UNKNOWN)
        [[ "$STATUS" == "TERMINATED" ]] && break
        sleep 180  # 3min poll
    done
    log "VM TERMINATED @ $(date)"

    # Start VM read-only to pull predictions
    log "starting VM read-only to pull predictions..."
    bash gcp/vm_start.sh 2>&1 | tee -a "$LOG"
    gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
        --command="gcloud storage cp ~/chunkymonkey/data/smartmoney.duckdb gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb" 2>&1 | tee -a "$LOG"
    gcloud storage cp "gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb" "data/smartmoney_post_${MODEL_ID}.duckdb.bak" 2>&1 | tee -a "$LOG"
    bash gcp/vm_stop.sh 2>&1 | tee -a "$LOG"

    log "Step 7: post-retrain pipeline"
    write_status "post_retrain" "$MODEL_ID"
    bash scripts/run_phase5_post_retrain.sh "$MODEL_ID" 2>&1 | tee -a "$LOG"
fi

# === Step 8: final audit ===
log "Step 8: final audit"
write_status "final_audit" "running"
if [[ "$DRY" == "0" ]]; then
    PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py 2>&1 | tee -a "$LOG"
    PYTHONPATH=backend python backend/scripts/audit_data_completeness.py 2>&1 | tee -a "$LOG"
fi

write_status "done" "all 8 steps completed"
log "=== Phase 5 auto chain DONE @ $(date) ==="
