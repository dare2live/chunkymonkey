#!/usr/bin/env bash
# auto_resume_v6_retrain.sh — Auto-resume + relaunch v6 retrain on spot preempt.
#
# 2026-05-22 用户 push '15-min trial 短跑 + preempt 反复' fix: 不要每次 preempt 手动 SSH 重启.
# 此 script 后台 loop, 60s 一次 poll VM status + retrain process, 自动恢复.
#
# Usage:
#   nohup bash scripts/auto_resume_v6_retrain.sh > /tmp/auto_resume_v6.log 2>&1 &
#   disown
#
# Exit condition:
#   - best.json 显示 best_trial_number >= TARGET_TRIALS (50)
#   - 或 manual kill
#
# Logs to /tmp/auto_resume_v6.log + sends Mac notification on each preempt + resume.

set -u

cd "$(dirname "$0")/.."
source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "$0"

MODEL_ID="lgbm_phase5_stability_v6_20260522T071500Z"
TARGET_TRIALS=50
POLL_SEC=60
MAX_ITERATIONS=480  # 8h × 60 polls = 8h cap (safety)
PROJECT="gen-lang-client-0821344445"
ZONE="us-central1-a"
VM_NAME="chunkymonkey-optuna"
REMOTE_HOME="/home/morrison416cn_gmail_com/chunkymonkey"

# Exclude cols (30: 24 noise + 6 sector)
EXCLUDE_COLS='pe_ttm,pb,ps_ttm,roe_q,pe_ttm_z_1y,pb_z_1y,ps_ttm_z_1y,roe_q_z_4q,survey_count_30d,survey_count_60d,survey_inst_30d,survey_inst_60d,event_lhb_7d,event_lhb_30d,lhb_count_30d,lhb_net_buy_pct_30d,lhb_inst_buy_30d,lhb_count_90d,lhb_inst_buy_90d,exec_buy_60d,exec_sell_60d,exec_buy_pct_60d,exec_sell_pct_60d,exec_net_signal,sector_ret_5d,sector_ret_20d,sector_ret_60d,sector_excess_20d,sector_excess_60d,industry_pit_confidence'

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

notify() {
    osascript -e "display notification \"$1\" with title \"v6 auto-resume\"" 2>/dev/null || true
}

remote_pgrep_retrain() {
    gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap \
        --command="pgrep -f retrain_lambdamart_v6 | head -1" 2>/dev/null | tail -1 | tr -d '[:space:]'
}

# Check best.json trial number on remote
remote_trial_number() {
    gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap \
        --command="python3 -c 'import json; d = json.load(open(\"$REMOTE_HOME/data/reports/optuna/$MODEL_ID.best.json\")); print(d.get(\"best_trial_number\", -1))'" 2>/dev/null | tail -1 | tr -d '[:space:]'
}

remote_completed_trial_count() {
    gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap \
        --command="grep -c COMPLETE $REMOTE_HOME/logs/retrain_${MODEL_ID}.log 2>/dev/null || echo 0" 2>/dev/null | tail -1 | tr -d '[:space:]'
}

launch_retrain() {
    log "RELAUNCH v6 retrain on VM"
    gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap \
        --command="cd $REMOTE_HOME && sudo shutdown -c 2>/dev/null || true && SKIP_LEAKAGE_AUDIT=1 OMP_NUM_THREADS=32 OPTUNA_N_JOBS=1 PYTHONPATH=backend nohup setsid $REMOTE_HOME/.venv/bin/python backend/scripts/retrain_lambdamart_v6.py --model-id $MODEL_ID --study-storage sqlite:///data/reports/optuna/$MODEL_ID.db --study-name $MODEL_ID --feature-panel mart_p0a_feature_label_panel_v4 --label fwd_cost_after_20d --n-trials 50 --n-estimators 100 --exclude-cols '$EXCLUDE_COLS' --window-rank-ic-std-penalty-weight 0.50 --window-rank-ic-negative-rate-penalty-weight 0.20 > logs/retrain_${MODEL_ID}.log 2>&1 < /dev/null & disown" 2>&1 | tail -3
}

log "=== auto_resume_v6_retrain start (model=$MODEL_ID target=$TARGET_TRIALS) ==="
notify "v6 auto-resume started ($MODEL_ID)"

ITER=0
while [[ $ITER -lt $MAX_ITERATIONS ]]; do
    ITER=$((ITER + 1))
    VM_STATUS=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --format='value(status)' 2>/dev/null || echo UNKNOWN)
    log "iter=$ITER VM=$VM_STATUS"

    if [[ "$VM_STATUS" == "TERMINATED" ]]; then
        log "VM TERMINATED — auto-resume"
        notify "VM preempt detected, resuming"
        bash gcp/vm_start.sh 2>&1 | tail -2 | while read -r l; do log "  vm_start: $l"; done
        sleep 30
        continue
    fi

    if [[ "$VM_STATUS" != "RUNNING" ]]; then
        log "VM=$VM_STATUS (unexpected), wait + retry"
        sleep $POLL_SEC
        continue
    fi

    # VM RUNNING — check if retrain process alive
    RETRAIN_PID=$(remote_pgrep_retrain || echo "")
    if [[ -z "$RETRAIN_PID" ]]; then
        log "retrain not running, relaunch"
        notify "v6 process not found, relaunching"
        launch_retrain
        sleep 30
        continue
    fi

    # Check trial progress
    TRIAL_N=$(remote_trial_number || echo "-1")
    log "retrain alive pid=$RETRAIN_PID, best_trial=$TRIAL_N"
    if [[ "$TRIAL_N" =~ ^[0-9]+$ ]] && [[ "$TRIAL_N" -ge "$TARGET_TRIALS" ]]; then
        log "TARGET REACHED: best_trial_number=$TRIAL_N >= $TARGET_TRIALS"
        notify "v6 50 trials done — auto-resume exit"
        break
    fi

    sleep $POLL_SEC
done

log "=== auto_resume_v6_retrain exit (iter=$ITER) ==="
