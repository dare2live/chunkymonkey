#!/usr/bin/env bash
# Start GCP VM chunkymonkey-optuna (us-central1-a, n2-standard-32 spot).
# Use before Codex compute / Optuna grid / data backfill / VM-side tdxhub fetch.
#
# 配套 vm_stop.sh - 跑完任务记得 stop, 否则每小时 $0.376 / 24/7 跑 $275/月.
#
# Usage:
#   bash gcp/vm_start.sh                  # 默认 start + verify SSH
#   bash gcp/vm_start.sh --no-wait        # start 后立刻退出, 不等 SSH ready

set -euo pipefail

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
WAIT_SSH="${WAIT_SSH:-1}"
FORCE="${FORCE:-0}"
DRY="${DRY:-0}"

for arg in "$@"; do
    case "$arg" in
        --no-wait) WAIT_SSH=0 ;;
        --force) FORCE=1 ;;
        --dry|--dry-run) DRY=1 ;;
    esac
done

# Budget enforcement: cost_tracker RED alert 拒绝启动 (用户 push back: GCP 不浪费资源具体方案)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$REPO_ROOT/gcp/cost_tracker.sh" && "$FORCE" != "1" ]]; then
    echo "[vm_start] Pre-flight: GCP budget check..."
    set +e
    bash "$REPO_ROOT/gcp/cost_tracker.sh" --quiet >/dev/null 2>&1
    COST_EXIT=$?
    set -e
    case "$COST_EXIT" in
        2)
            echo "[vm_start] BLOCK: 月度预算 RED (> 100%), 拒绝启动 VM 防超支."
            echo "[vm_start]   解锁: bash gcp/vm_start.sh --force (慎用)"
            echo "[vm_start]   或等下个月 budget reset, 或调高 GCP_BUDGET_USD env var"
            exit 2
            ;;
        1)
            echo "[vm_start] WARN: 月度预算 YELLOW (> 80%), 谨慎使用 VM"
            echo "[vm_start]   建议: 跑完立即 bash gcp/vm_stop.sh"
            ;;
        0)
            echo "[vm_start] Budget OK, 继续启动"
            ;;
    esac
fi

# Mark active_job marker (cost_tracker idle 检测 + TTL check 用)
# F5 P1 (docs/gcp_reliability_root_cause_fix.md): marker 加 model_id / owner / TTL
# 防"跑完 batch 忘 rm marker, idle VM 假装有 active job 长跑浪费"场景
# 调用方可 export MODEL_ID / JOB_TYPE / EXPECTED_MAX_HOURS 覆盖默认
mkdir -p "$REPO_ROOT/data/reports"
RUN_MARKER="$REPO_ROOT/data/reports/gcp_vm_active_job.marker"
cat > "$RUN_MARKER" <<EOF
model_id=${MODEL_ID:-unknown}
job_type=${JOB_TYPE:-manual}
started_at=$(date -Iseconds)
expected_max_hours=${EXPECTED_MAX_HOURS:-24}
owner_script=${BASH_SOURCE[0]}
EOF

# Check current state
status=$(gcloud compute instances describe "${VM_NAME}" --zone="${ZONE}" --format='value(status)' 2>/dev/null || echo "MISSING")

case "$status" in
    RUNNING)
        echo "[vm_start] VM ${VM_NAME} 已经 RUNNING"
        ip=$(gcloud compute instances describe "${VM_NAME}" --zone="${ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null)
        echo "[vm_start] IP: ${ip}"
        exit 0
        ;;
    TERMINATED|STOPPED)
        echo "[vm_start] VM ${VM_NAME} 状态: ${status}, starting..."
        gcloud compute instances start "${VM_NAME}" --zone="${ZONE}"
        ;;
    PROVISIONING|STAGING)
        echo "[vm_start] VM ${VM_NAME} 状态: ${status}, 等启动完成"
        ;;
    *)
        echo "[vm_start] 未知状态: ${status}"
        exit 1
        ;;
esac

if [[ "${WAIT_SSH}" == "1" ]]; then
    echo "[vm_start] 等 SSH ready..."
    for i in {1..30}; do
        if gcloud compute ssh "${VM_NAME}" --zone="${ZONE}" --tunnel-through-iap --command='echo ok' 2>/dev/null | grep -q "ok"; then
            echo "[vm_start] SSH ready"
            break
        fi
        echo "  attempt $i/30..."
        sleep 5
    done
fi

ip=$(gcloud compute instances describe "${VM_NAME}" --zone="${ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null)
echo "[vm_start] VM ready. IP: ${ip}"
echo "[vm_start] 用完记得跑 gcp/vm_stop.sh"
