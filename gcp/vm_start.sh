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

if [[ "${1:-}" == "--no-wait" ]]; then
    WAIT_SSH=0
fi

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
