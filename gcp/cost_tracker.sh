#!/usr/bin/env bash
# GCP 成本 tracker — 实时跟 VM uptime + 估算月度费用
#
# 用户原则 (CLAUDE.md §9): $15/月 budget (2026-05-21 用户放宽), 不浪费. Alert-only, 不 auto-stop.
# 跑此 script 输出:
#   - 当前 VM 状态 (RUNNING / TERMINATED)
#   - 本月已运行小时 (uptime 累计)
#   - 估算 月底总成本 (按当前 burn rate 推)
#   - 距离 $15 budget 还有多少 wall-time
#   - 80%/100% threshold alert (仅日志, 不触发 stop)
#
# Usage:
#   bash gcp/cost_tracker.sh                       # text output
#   bash gcp/cost_tracker.sh --json                # JSON to stdout (for daily_update)
#   bash gcp/cost_tracker.sh --json --quiet        # JSON only, no stderr log
#
# Outputs:
#   - stdout: text summary (or JSON if --json)
#   - data/reports/gcp_cost_summary.json (always)
#
# Exit codes:
#   0: under 80% budget
#   1: 80%-100% (yellow alert)
#   2: > 100% (red alert)

set -uo pipefail

source "$(cd "$(dirname "$0")/.." && pwd)/scripts/lib/gcp_guard.sh"

# 2026-05-20 user hard rule: no GCP usage unless explicitly authorized.
# This script calls gcloud, so it is blocked by default too.
require_gcp_explicit_ok "gcp/cost_tracker.sh"

VM_NAME="${GCP_VM_NAME:-chunkymonkey-optuna}"        # rule-compliance: ok evidence=existing-vm-name
VM_ZONE="${GCP_VM_ZONE:-us-central1-a}"              # rule-compliance: ok evidence=user-vm-zone
SPOT_RATE_HOUR="${GCP_SPOT_RATE_HOUR:-0.376}"        # rule-compliance: ok evidence=n2-standard-32-spot-2026-rate
DISK_MONTHLY="${GCP_DISK_MONTHLY:-4.0}"              # rule-compliance: ok evidence=100gb-pd-standard
BUDGET="${GCP_BUDGET_USD:-15.0}"                     # rule-compliance: ok evidence=user-15usd-budget-2026-05-21

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT="$REPO_ROOT/data/reports/gcp_cost_summary.json"
mkdir -p "$(dirname "$REPORT")"

JSON_MODE=0
QUIET=0
for arg in "$@"; do
    case "$arg" in
        --json) JSON_MODE=1 ;;
        --quiet) QUIET=1 ;;
    esac
done

log() {
    [[ "$QUIET" == "1" ]] && return
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
}

# 1. 当前 VM 状态
VM_STATUS=$(gcloud compute instances describe "$VM_NAME" --zone "$VM_ZONE" \
    --format="value(status)" 2>/dev/null || echo "UNKNOWN")

# 2. 本月开始日 (POSIX)
MONTH_START=$(date -j -f "%Y-%m-%d" "$(date +%Y-%m-01)" "+%s" 2>/dev/null || date -d "$(date +%Y-%m-01)" "+%s")
NOW=$(date "+%s")
MONTH_ELAPSED_SEC=$((NOW - MONTH_START))
MONTH_ELAPSED_DAYS=$((MONTH_ELAPSED_SEC / 86400))
DAYS_IN_MONTH=$(date -j -v 1m -v -1d -f "%Y-%m-%d" "$(date +%Y-%m-01)" "+%d" 2>/dev/null || \
    date -d "$(date +%Y-%m-01) + 1 month - 1 day" "+%d")
MONTH_REMAIN_DAYS=$((DAYS_IN_MONTH - MONTH_ELAPSED_DAYS))

# 3. 本月 VM uptime 估算 (从 GCP operations log; fallback 用 status snapshot)
# 简化: 每次跑此 script 写 timestamp + status, 用 history 推 uptime
UPTIME_LOG="$REPO_ROOT/data/reports/gcp_vm_uptime_log.csv"
mkdir -p "$(dirname "$UPTIME_LOG")"
if [[ ! -f "$UPTIME_LOG" ]]; then
    echo "timestamp,status" > "$UPTIME_LOG"
fi
echo "$(date '+%Y-%m-%dT%H:%M:%S'),$VM_STATUS" >> "$UPTIME_LOG"

# 简易估算: 假设当前状态从 latest log entry 时点持续到现在
# 真实计费走 GCP billing API (复杂, P2 工作)
# 本月 RUNNING 记录数 × 检查间隔时长 (取 15 min cron 间隔) 算 uptime
RUNNING_LOGS=$(awk -F, -v month_start="$(date +%Y-%m)" '
    NR > 1 && $1 ~ "^"month_start && $2 == "RUNNING" { count++ }
    END { print count + 0 }
' "$UPTIME_LOG")
# 每次 log 假设 15 min interval (matching launchd cron)
EST_UPTIME_MIN=$((RUNNING_LOGS * 15))
EST_UPTIME_HOUR=$(echo "scale=2; $EST_UPTIME_MIN / 60" | bc)

# 4. 估算
# 当前已 burn: compute_running × spot_rate + disk_monthly × elapsed_fraction
COMPUTE_COST=$(echo "scale=4; $EST_UPTIME_HOUR * $SPOT_RATE_HOUR" | bc)
DISK_COST=$(echo "scale=4; $DISK_MONTHLY * $MONTH_ELAPSED_DAYS / $DAYS_IN_MONTH" | bc)
TOTAL_SO_FAR=$(echo "scale=4; $COMPUTE_COST + $DISK_COST" | bc)

# 月底估算: 按当前 burn rate 线性外推
if [[ "$MONTH_ELAPSED_DAYS" -gt 0 ]]; then
    BURN_PER_DAY=$(echo "scale=4; $TOTAL_SO_FAR / $MONTH_ELAPSED_DAYS" | bc)
    PROJECTED_MONTH=$(echo "scale=4; $BURN_PER_DAY * $DAYS_IN_MONTH" | bc)
else
    BURN_PER_DAY=0
    PROJECTED_MONTH=$DISK_MONTHLY
fi
# bc 输出 leading '.' (e.g. '.1290') 不是合法 JSON, 补 0 — 在 bc 计算后立刻 fix
[[ "$BURN_PER_DAY" =~ ^\. ]] && BURN_PER_DAY="0$BURN_PER_DAY"
[[ "$PROJECTED_MONTH" =~ ^\. ]] && PROJECTED_MONTH="0$PROJECTED_MONTH"
[[ "$COMPUTE_COST" =~ ^\. ]] && COMPUTE_COST="0$COMPUTE_COST"
[[ "$DISK_COST" =~ ^\. ]] && DISK_COST="0$DISK_COST"
[[ "$TOTAL_SO_FAR" =~ ^\. ]] && TOTAL_SO_FAR="0$TOTAL_SO_FAR"

PCT_OF_BUDGET=$(echo "scale=1; $PROJECTED_MONTH * 100 / $BUDGET" | bc)
REMAINING_BUDGET=$(echo "scale=2; $BUDGET - $TOTAL_SO_FAR" | bc)
REMAINING_HOURS=$(echo "scale=2; $REMAINING_BUDGET / $SPOT_RATE_HOUR" | bc)
# bc leading '.' fix (避 JSON parse fail)
[[ "$PCT_OF_BUDGET" =~ ^\. ]] && PCT_OF_BUDGET="0$PCT_OF_BUDGET"
[[ "$REMAINING_BUDGET" =~ ^\. ]] && REMAINING_BUDGET="0$REMAINING_BUDGET"
[[ "$REMAINING_HOURS" =~ ^\. ]] && REMAINING_HOURS="0$REMAINING_HOURS"

# 5. Alert level
if (( $(echo "$PCT_OF_BUDGET >= 100" | bc -l) )); then
    ALERT="RED"
    EXIT_CODE=2
elif (( $(echo "$PCT_OF_BUDGET >= 80" | bc -l) )); then
    ALERT="YELLOW"
    EXIT_CODE=1
else
    ALERT="OK"
    EXIT_CODE=0
fi

# 6. JSON output
cat > "$REPORT" <<EOF
{
  "checked_at": "$(date -Iseconds)",
  "vm_name": "$VM_NAME",
  "vm_zone": "$VM_ZONE",
  "vm_status": "$VM_STATUS",
  "month_elapsed_days": $MONTH_ELAPSED_DAYS,
  "month_remain_days": $MONTH_REMAIN_DAYS,
  "days_in_month": $DAYS_IN_MONTH,
  "est_uptime_hour_this_month": $EST_UPTIME_HOUR,
  "spot_rate_per_hour": $SPOT_RATE_HOUR,
  "disk_monthly_usd": $DISK_MONTHLY,
  "compute_cost_so_far": $COMPUTE_COST,
  "disk_cost_so_far": $DISK_COST,
  "total_cost_so_far": $TOTAL_SO_FAR,
  "burn_per_day": $BURN_PER_DAY,
  "projected_month_cost": $PROJECTED_MONTH,
  "budget": $BUDGET,
  "pct_of_budget": $PCT_OF_BUDGET,
  "remaining_budget_usd": $REMAINING_BUDGET,
  "remaining_hours_at_spot": $REMAINING_HOURS,
  "alert_level": "$ALERT"
}
EOF

if [[ "$JSON_MODE" == "1" ]]; then
    cat "$REPORT"
else
    log "=== GCP Cost Tracker @ $(date) ==="
    log "  VM: $VM_NAME ($VM_ZONE) status=$VM_STATUS"
    log "  Month elapsed: ${MONTH_ELAPSED_DAYS}d / ${DAYS_IN_MONTH}d (${MONTH_REMAIN_DAYS}d remain)"
    log "  Uptime this month: ${EST_UPTIME_HOUR}h (RUNNING logs: $RUNNING_LOGS × 15min)"
    log "  Cost so far:       \$$TOTAL_SO_FAR (compute \$$COMPUTE_COST + disk \$$DISK_COST)"
    log "  Burn per day:      \$$BURN_PER_DAY"
    log "  Projected month:   \$$PROJECTED_MONTH (${PCT_OF_BUDGET}% of \$$BUDGET budget)"
    log "  Remaining budget:  \$$REMAINING_BUDGET (~${REMAINING_HOURS}h spot)"
    log "  Alert: $ALERT"
fi

if [[ "$ALERT" != "OK" ]]; then
    log "  >>> ALERT $ALERT: projected month \$$PROJECTED_MONTH > $(echo "scale=0; $BUDGET * 0.8 / 1" | bc) (80% \$$BUDGET budget)"
fi

# 2026-05-21 用户放宽: budget RED 不再 auto-stop, 仅日志警告.
# 用户原话 "上限放宽到 $15, 不要触发 stop". 当前 active workload 不希望被预算自动打断.
if [[ "$ALERT" == "RED" && "$VM_STATUS" == "RUNNING" ]]; then
    log ""
    log "  !!! ALERT-ONLY: budget RED + VM RUNNING — 不 auto-stop (用户 2026-05-21 放宽), 请人工评估"
    log "  手动 stop: bash gcp/vm_stop.sh"
fi

# Actionable: VM RUNNING 无 active job marker > IDLE_GRACE 分钟 → 自动 stop (proactive cost-cutting)
# 防止"忘 stop" 用户场景, 用户 push back '主动 cost-cutting' (2026-05-18 stop hook)
RUN_MARKER="$REPO_ROOT/data/reports/gcp_vm_active_job.marker"
# F5 P1 (docs/gcp_reliability_root_cause_fix.md): 5 min 对 4-6h retrain 太激进, 改 30
# 反例: 5-19 22:30 retrain 跑 3.5h 中断 (60% likelihood spot, 但 5min cron-grace 也有触发风险)
IDLE_GRACE_MIN="${GCP_IDLE_GRACE_MIN:-30}"      # rule-compliance: ok evidence=docs/gcp_reliability_root_cause_fix.md-F5
IDLE_TRACK_FILE="$REPO_ROOT/data/reports/gcp_vm_idle_first_seen.marker"

# F5 marker TTL check (started_at + expected_max_hours 超时 → auto-stop)
# 用户场景: 跑 batch 写 marker 后, batch 自身 crash 但 marker 没清, VM 假装"有 active job" 长跑浪费
# TTL check 提供保险: marker 老于 expected_max_hours → 视为 stale, 拒绝豁免 idle 检测
MARKER_STALE=0
if [[ -f "$RUN_MARKER" ]]; then
    MARKER_STARTED=$(grep -E '^started_at=' "$RUN_MARKER" 2>/dev/null | head -1 | cut -d= -f2- || echo "")
    MARKER_MAX_HOURS=$(grep -E '^expected_max_hours=' "$RUN_MARKER" 2>/dev/null | head -1 | cut -d= -f2- || echo "24")
    # 默认 24h, 防 parse fail
    [[ -z "$MARKER_MAX_HOURS" ]] && MARKER_MAX_HOURS=24                          # rule-compliance: ok evidence=F5-default-24h-marker-TTL
    if [[ -n "$MARKER_STARTED" ]]; then
        # parse ISO timestamp, macOS date 兼容
        MARKER_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$MARKER_STARTED" "+%s" 2>/dev/null || \
                       date -j -f "%Y-%m-%dT%H:%M:%S" "${MARKER_STARTED%%+*}" "+%s" 2>/dev/null || \
                       echo 0)
        if [[ "$MARKER_EPOCH" -gt 0 ]]; then
            MARKER_AGE_HOUR=$(( (NOW - MARKER_EPOCH) / 3600 ))
            if [[ "$MARKER_AGE_HOUR" -ge "$MARKER_MAX_HOURS" ]]; then
                MARKER_STALE=1
                log "  WARN: active_job marker stale (age ${MARKER_AGE_HOUR}h ≥ TTL ${MARKER_MAX_HOURS}h) — 视为 idle, 走 grace check"
                log "       marker started_at=$MARKER_STARTED expected_max_hours=$MARKER_MAX_HOURS"
            fi
        fi
    fi
fi

if [[ "$VM_STATUS" == "RUNNING" ]] && { [[ ! -f "$RUN_MARKER" ]] || [[ "$MARKER_STALE" == "1" ]]; }; then
    # 无 active job marker OR marker TTL 超期 → idle
    log ""
    if [[ ! -f "$IDLE_TRACK_FILE" ]]; then
        # First time seeing idle, record timestamp
        date "+%s" > "$IDLE_TRACK_FILE"
        log "  WARN: VM RUNNING 但无 active_job marker — 首次记录 idle 时间戳"
        log "  → 跑 batch 前 touch $RUN_MARKER, 完后 rm; idle > ${IDLE_GRACE_MIN}min 自动 stop"
    else
        # Has idle timestamp — check how long
        IDLE_SINCE=$(cat "$IDLE_TRACK_FILE" 2>/dev/null || echo 0)
        IDLE_MIN=$(( (NOW - IDLE_SINCE) / 60 ))
        log "  WARN: VM RUNNING idle ${IDLE_MIN}min (grace ${IDLE_GRACE_MIN}min)"
        if [[ "$IDLE_MIN" -ge "$IDLE_GRACE_MIN" ]]; then
            log "  !!! AUTO-ACTION: idle > grace → 自动 stop VM (proactive cost-cutting)"
            if [[ -f "$REPO_ROOT/gcp/vm_stop.sh" ]]; then
                bash "$REPO_ROOT/gcp/vm_stop.sh" 2>&1 | tee -a "$REPO_ROOT/data/reports/gcp_auto_stop.log" >&2 || \
                    log "  WARN: auto stop failed, manual: bash gcp/vm_stop.sh"
                log "  ✓ VM auto-stopped (idle protection, saved ~\$0.376/h)"
                rm -f "$IDLE_TRACK_FILE"
            else
                log "  WARN: gcp/vm_stop.sh 不存在, 无法 auto-stop"
            fi
        fi
    fi
else
    # VM not idle (TERMINATED OR active marker exists) → clear idle track
    [[ -f "$IDLE_TRACK_FILE" ]] && rm -f "$IDLE_TRACK_FILE"
fi

exit $EXIT_CODE
