#!/usr/bin/env bash
# gcp_retrain_completion_monitor.sh — 监控 GCP stability retrain 完成, 触发通知.
#
# 2026-05-21 22:40 加, 用户 push back: "你设置一个 gcp 的监控吧".
#
# 检测条件 (任一满足 → 通知 + 退出):
#   1. Optuna DB COMPLETE >= 80   (全部 trial 跑完)
#   2. summary JSON 出现           (data/reports/stability_retrain/<MODEL_ID>_stability_retrain_*.json)
#   3. VM TERMINATED               (preempt / 主动 stop)
#   4. cost projected > 95% budget (异常高代价)
#
# 通知方式 (并发):
#   - macOS osascript notification (用户即使 close terminal 也可见)
#   - 写 data/reports/gcp_retrain_completion_flag.json (next Claude session 读)
#   - log 到 data/reports/gcp_retrain_completion_monitor.log
#
# Usage:
#   nohup bash scripts/gcp_retrain_completion_monitor.sh > /tmp/gcp_completion.log 2>&1 &
#   disown
#
# 频率: 15 min poll. Mac sleep 期间 launchd 才 reliable, 但用户 say "你设置一个", 我用简单
# nohup loop. 用户睡 Mac 期间 loop 阻塞, 醒来继续, 不丢通知 (但延迟检测).
#
# 退出码:
#   0 - 完成条件满足, 通知发出
#   1 - max duration 超时 (24h)

set -uo pipefail
cd "$(dirname "$0")/.."

# 不直接 source gcp_guard (我们的查询都是 read-only via gcp_stability_status), 但仍 set ENV
export CHUNKYMONKEY_GCP_EXPLICIT_OK=1

MODEL_ID="${MODEL_ID:-lgbm_phase5_stability_20260521T055800Z}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-900}"        # 15 min
MAX_DURATION_HOURS="${MAX_DURATION_HOURS:-24}"        # 24h 安全上限
COMPLETE_THRESHOLD="${COMPLETE_THRESHOLD:-80}"        # 全 80 trial 跑完阈值
COST_BUDGET_PCT_THRESHOLD="${COST_BUDGET_PCT_THRESHOLD:-95}"  # 95% budget 警报

STATUS_DIR="data/reports"
FLAG_FILE="$STATUS_DIR/gcp_retrain_completion_flag.json"
MONITOR_LOG="$STATUS_DIR/gcp_retrain_completion_monitor.log"
mkdir -p "$STATUS_DIR"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg" | tee -a "$MONITOR_LOG"
}

notify_macos() {
    local title="$1"
    local message="$2"
    osascript -e "display notification \"$message\" with title \"$title\" sound name \"Glass\"" 2>/dev/null || true
}

write_flag() {
    local reason="$1"
    local detail="$2"
    cat > "$FLAG_FILE" <<JSON
{
  "triggered_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "model_id": "$MODEL_ID",
  "reason": "$reason",
  "detail": "$detail",
  "next_action": "bash scripts/post_retrain_pipeline.sh"
}
JSON
    log "wrote flag: $FLAG_FILE"
}

log "=== monitor start model_id=$MODEL_ID poll=${POLL_INTERVAL_SEC}s max=${MAX_DURATION_HOURS}h threshold=${COMPLETE_THRESHOLD} ==="
log "  log:  $MONITOR_LOG"
log "  flag: $FLAG_FILE (will be created on completion)"

START_EPOCH=$(date +%s)
MAX_SEC=$((MAX_DURATION_HOURS * 3600))
LOOP=0

while true; do
    LOOP=$((LOOP + 1))
    NOW=$(date +%s)
    ELAPSED=$((NOW - START_EPOCH))

    if [[ "$ELAPSED" -gt "$MAX_SEC" ]]; then
        log "MAX_DURATION_HOURS=$MAX_DURATION_HOURS 超时, 退出"
        notify_macos "GCP monitor 超时" "max ${MAX_DURATION_HOURS}h 到了, retrain 仍未完成, 请人工查"
        write_flag "timeout" "monitor ran ${MAX_DURATION_HOURS}h without completion"
        exit 1
    fi

    log "loop #$LOOP (elapsed $(( ELAPSED / 60 )) min)"

    # 1. 检查 summary JSON (最强信号: retrain 真完成)
    SUMMARY_GLOB="data/reports/stability_retrain/${MODEL_ID}_stability_retrain_*.json"
    SUMMARY=$(ls -1 $SUMMARY_GLOB 2>/dev/null | tail -1 || true)
    if [[ -n "$SUMMARY" ]]; then
        SUMMARY_SIZE=$(stat -f%z "$SUMMARY" 2>/dev/null || echo "0")
        if [[ "$SUMMARY_SIZE" -gt 100 ]]; then
            log "summary JSON 出现且 size=$SUMMARY_SIZE: $SUMMARY"
            notify_macos "GCP retrain 完成" "summary JSON 出, model_id=$MODEL_ID, 跑 post_retrain_pipeline 接 P1"
            write_flag "summary_json_appeared" "$SUMMARY size=$SUMMARY_SIZE"
            exit 0
        fi
    fi

    # 2. 检查 Optuna DB trial COMPLETE count via remote stability_status (本地 db 是 stub, 真 db 在 VM)
    STATUS_OUT=$(TAIL_LINES=5 bash scripts/gcp_stability_status.sh 2>/dev/null || true)
    COMPLETE_COUNT=$(echo "$STATUS_OUT" | grep "^complete_count " | awk '{print $2}' || echo "0")
    [[ -z "$COMPLETE_COUNT" ]] && COMPLETE_COUNT=0
    log "  Optuna COMPLETE count: $COMPLETE_COUNT / $COMPLETE_THRESHOLD"

    if [[ "$COMPLETE_COUNT" -ge "$COMPLETE_THRESHOLD" ]]; then
        log "全 $COMPLETE_THRESHOLD trial 跑完!"
        notify_macos "GCP retrain 全完成" "$COMPLETE_COUNT/$COMPLETE_THRESHOLD trial COMPLETE, 跑 post_retrain_pipeline"
        write_flag "complete_threshold_reached" "$COMPLETE_COUNT >= $COMPLETE_THRESHOLD"
        exit 0
    fi

    # 3. 检查 VM status + cost
    COST_JSON="data/reports/gcp_cost_summary.json"
    if [[ -f "$COST_JSON" ]]; then
        VM_STATUS=$(python3 -c "import json; print(json.load(open('$COST_JSON')).get('vm_status', 'UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
        COST_PCT=$(python3 -c "import json; print(int(json.load(open('$COST_JSON')).get('pct_of_budget', 0)))" 2>/dev/null || echo "0")
        log "  VM status: $VM_STATUS, cost: ${COST_PCT}% of budget"

        if [[ "$VM_STATUS" == "TERMINATED" ]]; then
            log "VM TERMINATED (preempt 或主动 stop)"
            notify_macos "GCP VM stopped" "VM TERMINATED model_id=$MODEL_ID, COMPLETE=$COMPLETE_COUNT, 检查日志"
            write_flag "vm_terminated" "VM_STATUS=TERMINATED, COMPLETE=$COMPLETE_COUNT"
            exit 0
        fi

        if [[ "$COST_PCT" -ge "$COST_BUDGET_PCT_THRESHOLD" ]]; then
            log "cost > ${COST_BUDGET_PCT_THRESHOLD}% budget!"
            notify_macos "GCP cost 警报" "cost ${COST_PCT}% of budget (阈值 ${COST_BUDGET_PCT_THRESHOLD}%), 考虑 stop VM"
            # 不退出, 继续监控 (用户放宽到 $15 alert-only)
        fi
    fi

    log "sleep $POLL_INTERVAL_SEC sec..."
    sleep "$POLL_INTERVAL_SEC"
done
