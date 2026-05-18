#!/usr/bin/env bash
# GCP 成本 tracker — 实时跟 VM uptime + 估算月度费用
#
# 用户原则 (CLAUDE.md §10.0.2): $10/月 budget, 不浪费.
# 跑此 script 输出:
#   - 当前 VM 状态 (RUNNING / TERMINATED)
#   - 本月已运行小时 (uptime 累计)
#   - 估算 月底总成本 (按当前 burn rate 推)
#   - 距离 $10 budget 还有多少 wall-time
#   - 80%/100% threshold alert
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

VM_NAME="${GCP_VM_NAME:-chunkymonkey-optuna}"        # rule-compliance: ok evidence=existing-vm-name
VM_ZONE="${GCP_VM_ZONE:-us-central1-a}"              # rule-compliance: ok evidence=user-vm-zone
SPOT_RATE_HOUR="${GCP_SPOT_RATE_HOUR:-0.376}"        # rule-compliance: ok evidence=n2-standard-32-spot-2026-rate
DISK_MONTHLY="${GCP_DISK_MONTHLY:-4.0}"              # rule-compliance: ok evidence=100gb-pd-standard
BUDGET="${GCP_BUDGET_USD:-10.0}"                     # rule-compliance: ok evidence=user-10usd-budget

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

exit $EXIT_CODE
