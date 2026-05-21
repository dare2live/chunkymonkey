#!/usr/bin/env bash
# gcp_auto_resume_monitor.sh — 自动检测 spot preempt + resume retrain, 跑到完成才停
#
# 2026-05-22 用户 push back: "A，再中断就持续继续恢复执行，直到跑完开展下一阶段工作"
# 二次 preempt 后用户决策: 自动化 resume, 不再人工每次确认.
#
# 退出条件 (任一满足 → exit):
#   1. summary JSON 出现 size>100 (retrain normal exit)
#   2. Optuna COMPLETE >= COMPLETE_TARGET (默认 80)
#   3. budget >= BUDGET_PCT_STOP (默认 100%)
#   4. resume_count > MAX_RESUMES (默认 20)
#
# 完成后自动: macOS notify + 写 done flag + (可选) 触发 post_retrain_pipeline
#
# Usage:
#   nohup bash scripts/gcp_auto_resume_monitor.sh > /tmp/gcp_auto_resume.log 2>&1 </dev/null &
#   disown

set -uo pipefail
cd "$(dirname "$0")/.."
export CHUNKYMONKEY_GCP_EXPLICIT_OK=1

MODEL_ID="${MODEL_ID:-lgbm_phase5_stability_20260521T055800Z}"
POLL_SEC="${POLL_SEC:-600}"             # 10 min
MAX_RESUMES="${MAX_RESUMES:-20}"
COMPLETE_TARGET="${COMPLETE_TARGET:-80}"
BUDGET_PCT_STOP="${BUDGET_PCT_STOP:-100}"
AUTO_TRIGGER_POSTRETRAIN="${AUTO_TRIGGER_POSTRETRAIN:-0}"   # 0=人工触发, 1=自动跑 post_retrain_pipeline

LOG="data/reports/gcp_auto_resume_monitor.log"
DONE_FLAG="data/reports/gcp_auto_resume_done.json"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

notify_macos() {
    osascript -e "display notification \"$2\" with title \"$1\" sound name \"Glass\"" 2>/dev/null || true
}

write_done() {
    local reason="$1"
    local detail="$2"
    cat > "$DONE_FLAG" <<JSON
{
  "completed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "model_id": "$MODEL_ID",
  "reason": "$reason",
  "detail": "$detail",
  "resume_count": $resume_count,
  "next_action": "bash scripts/post_retrain_pipeline.sh"
}
JSON
    log "wrote done flag: $DONE_FLAG"
}

log "=== auto resume monitor start model_id=$MODEL_ID poll=${POLL_SEC}s max_resumes=$MAX_RESUMES target=$COMPLETE_TARGET budget_stop=${BUDGET_PCT_STOP}% auto_post_retrain=$AUTO_TRIGGER_POSTRETRAIN ==="

resume_count=0
loop=0
last_complete=0

while true; do
    loop=$((loop + 1))
    log ""
    log "--- loop #$loop ---"

    # 1. check budget (always, even if SSH fail)
    bash gcp/cost_tracker.sh --quiet > /dev/null 2>&1 || true
    BUDGET_PCT=$(python3 -c "import json; d=json.load(open('data/reports/gcp_cost_summary.json')); print(int(d.get('pct_of_budget', 0)))" 2>/dev/null || echo "0")
    log "cost: ${BUDGET_PCT}% of budget"

    if [[ "$BUDGET_PCT" -ge "$BUDGET_PCT_STOP" ]]; then
        log "STOP: budget ${BUDGET_PCT}% >= ${BUDGET_PCT_STOP}%"
        notify_macos "Auto-resume STOP" "budget ${BUDGET_PCT}% reached"
        write_done "budget_exhausted" "BUDGET_PCT=$BUDGET_PCT >= $BUDGET_PCT_STOP"
        break
    fi

    # 2. check VM status via describe (independent of SSH)
    VM_STATUS=$(gcloud compute instances describe chunkymonkey-optuna --zone=us-central1-a --format="value(status)" 2>/dev/null || echo "UNKNOWN")
    log "VM status: $VM_STATUS"

    # 3. check summary JSON locally (终极完成信号, retrain wrapper 在 normal exit 时 GCS 上传后我们再拉)
    # 跳过这步因为没自动拉, 走 trial count

    # 4. if VM RUNNING, try SSH 看 trial state
    if [[ "$VM_STATUS" == "RUNNING" ]]; then
        SSH_OUT=$(TAIL_LINES=3 bash scripts/gcp_stability_status.sh 2>&1)
        COMPLETE_COUNT=$(echo "$SSH_OUT" | grep "^complete_count " | awk '{print $2}' || echo "0")
        [[ -z "$COMPLETE_COUNT" ]] && COMPLETE_COUNT=0
        log "Optuna COMPLETE: $COMPLETE_COUNT / $COMPLETE_TARGET"

        # 看是否进展 (上次 vs 这次)
        if [[ "$COMPLETE_COUNT" -gt "$last_complete" ]]; then
            log "progress: +$((COMPLETE_COUNT - last_complete)) trial since last loop"
            last_complete=$COMPLETE_COUNT
        fi

        if [[ "$COMPLETE_COUNT" -ge "$COMPLETE_TARGET" ]]; then
            log "DONE: $COMPLETE_COUNT >= $COMPLETE_TARGET trials COMPLETE"
            notify_macos "Retrain DONE" "$COMPLETE_COUNT/$COMPLETE_TARGET trial COMPLETE"
            write_done "complete_target_reached" "COMPLETE=$COMPLETE_COUNT >= $COMPLETE_TARGET"
            if [[ "$AUTO_TRIGGER_POSTRETRAIN" == "1" ]]; then
                log "auto-triggering post_retrain_pipeline..."
                MODEL_ID="$MODEL_ID" bash scripts/post_retrain_pipeline.sh 2>&1 | tee -a "$LOG" | tail -10 &
            fi
            break
        fi
    fi

    # 5. if VM TERMINATED + not at target → resume
    if [[ "$VM_STATUS" == "TERMINATED" ]]; then
        resume_count=$((resume_count + 1))
        if [[ "$resume_count" -gt "$MAX_RESUMES" ]]; then
            log "STOP: $resume_count resumes > $MAX_RESUMES max"
            notify_macos "Auto-resume STOP" "max resumes ($MAX_RESUMES) reached"
            write_done "max_resumes_reached" "$resume_count > $MAX_RESUMES"
            break
        fi
        log "VM TERMINATED (likely preempt) → AUTO RESUME #$resume_count"
        notify_macos "Auto-resume #$resume_count" "VM preempt detected, restarting retrain"

        # 直接重启 retrain wrapper (它内部 vm_start + SSH start retrain)
        if MODEL_ID="$MODEL_ID" bash scripts/gcp_stability_retrain.sh 2>&1 | tee -a "$LOG" | tail -10; then
            log "resume #$resume_count launched OK"
        else
            log "resume #$resume_count FAILED, will retry next loop"
        fi
        # 给 VM 一点 warm-up 时间
        sleep 60
    fi

    log "sleep $POLL_SEC sec..."
    sleep "$POLL_SEC"
done

log "=== auto resume monitor exit (resumes=$resume_count) ==="
