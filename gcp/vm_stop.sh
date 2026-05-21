#!/usr/bin/env bash
# Stop GCP VM chunkymonkey-optuna 省钱 (compute $0/h after stop, disk $4/月 仍收).
#
# 必须每次 batch 跑完后 stop. 配套 vm_start.sh.
#
# Usage:
#   bash gcp/vm_stop.sh                  # 默认 stop + verify
#   bash gcp/vm_stop.sh --force          # 即使有 running 进程也 stop

set -euo pipefail

source "$(cd "$(dirname "$0")/.." && pwd)/scripts/lib/gcp_guard.sh"
require_gcp_explicit_ok "gcp/vm_stop.sh"

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
FORCE="${FORCE:-0}"

if [[ "${1:-}" == "--force" ]]; then
    FORCE=1
fi

# 1. Check current state
status=$(gcloud compute instances describe "${VM_NAME}" --zone="${ZONE}" --format='value(status)' 2>/dev/null || echo "MISSING")

if [[ "$status" != "RUNNING" ]]; then
    echo "[vm_stop] VM ${VM_NAME} 状态: ${status} (已经不是 RUNNING)"
    exit 0
fi

# 2. Check active processes (除非 --force)
if [[ "${FORCE}" != "1" ]]; then
    echo "[vm_stop] 检查 VM 上是否有 active 任务..."
    procs=$(gcloud compute ssh "${VM_NAME}" --zone="${ZONE}" --tunnel-through-iap \
        --command='ps -ef | grep -E "(run_p0b|rebuild_p0a|fetch_kline|build_p0a|optuna)" | grep -v grep | wc -l' \
        2>/dev/null || echo "0")
    if [[ "$procs" -gt 0 ]]; then
        echo "[vm_stop] WARNING: VM 有 ${procs} 个 active 任务 (run_p0b / rebuild / fetch / build / optuna)"
        echo "[vm_stop] 用 --force 强制 stop, 或先手动 kill 任务"
        exit 1
    fi
fi

# 3. Stop
echo "[vm_stop] Stopping ${VM_NAME}..."
gcloud compute instances stop "${VM_NAME}" --zone="${ZONE}"

# 3b. Clear active_job marker (cost_tracker idle 检测用)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rm -f "$REPO_ROOT/data/reports/gcp_vm_active_job.marker"

# 4. Verify
status_after=$(gcloud compute instances describe "${VM_NAME}" --zone="${ZONE}" --format='value(status)' 2>/dev/null || echo "?")
echo "[vm_stop] VM 状态: ${status_after}"
echo "[vm_stop] 每小时省 \$0.376 (spot rate), 月省 ~\$271 (vs 24/7)"
echo "[vm_stop] 保留 disk pd-standard 100GB (\$4/月 仍收)"
echo "[vm_stop] GCS 数据 (smartmoney+market+alpha158) 保留 (\$0.50/月)"
