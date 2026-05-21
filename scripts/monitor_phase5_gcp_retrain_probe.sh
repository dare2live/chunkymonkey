#!/usr/bin/env bash
# F4 单次 probe 版 monitor — 适合 cron / launchd 周期性调用, 不轮询.
#
# 跟 monitor_phase5_gcp_retrain.sh 同语义 (检测 VM TERMINATED → 拉 predictions DB),
# 但每次只跑 1 个 sample, 立刻退出. 由 launchd / cron 每 5 min 调一次,
# 不依赖 Mac 唤醒状态, 不怕 SSH 卡死, 不怕 shell 退出.
#
# Usage:
#   bash scripts/monitor_phase5_gcp_retrain_probe.sh
#   MONITOR_DRY_RUN=1 bash scripts/monitor_phase5_gcp_retrain_probe.sh  # mock 不真启 VM
#
# Args (env):
#   MODEL_ID            (default: read from data/reports/phase5_chain/model_id.txt)
#   MONITOR_DRY_RUN     (default: 0; 1 = 不真 SSH / 不真 pull / 不真 stop, 测试用)
#
# Exit codes:
#   0  probe 完成 (VM 任意状态), 下一轮 cron 继续
#   2  VM TERMINATED 且 pull 完成 → 创建 sentinel 文件防重跑 (cron 后续 silently skip)
#   3  MODEL_ID 缺失或 sentinel 已存在 (跑完了, no-op)
#   4  dry-run mode 正常退出

set -uo pipefail
cd "$(dirname "$0")/.."

# 2026-05-21 controlled-use: GCP probes still require the safety latch.
if [[ "${CHUNKYMONKEY_GCP_EXPLICIT_OK:-0}" != "1" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] BLOCK: GCP controlled-use requires CHUNKYMONKEY_GCP_EXPLICIT_OK=1; skip phase5 monitor probe." >&2
    exit 3
fi

MODEL_ID="${MODEL_ID:-$(cat data/reports/phase5_chain/model_id.txt 2>/dev/null | head -1)}"
DRY_RUN="${MONITOR_DRY_RUN:-0}"
STATUS_DIR="data/reports/phase5_chain"
PROBE_LOG="$STATUS_DIR/monitor_probe.log"
SENTINEL="$STATUS_DIR/monitor_done_${MODEL_ID}.sentinel"
mkdir -p "$STATUS_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$PROBE_LOG"; }

# 早退: MODEL_ID 没拿到
if [[ -z "$MODEL_ID" ]]; then
    log "no MODEL_ID, skip probe"
    exit 3
fi

# 早退: 已经 pull 完成 (sentinel 存在), cron 后续 silently skip
if [[ -f "$SENTINEL" ]]; then
    # 不写 log (避免 cron 每 5min 刷 disk)
    exit 3
fi

log "=== probe model_id=$MODEL_ID dry_run=$DRY_RUN ==="

# 1. VM status (gcloud read-only, 不写)
if [[ "$DRY_RUN" == "1" ]]; then
    VM_STATUS="${MOCK_VM_STATUS:-TERMINATED}"   # rule-compliance: ok evidence=dry-run-mock
    log "[DRY] VM=$VM_STATUS (mock)"
else
    VM_STATUS=$(gcloud compute instances describe chunkymonkey-optuna \
        --zone=us-central1-a --format='value(status)' 2>/dev/null || echo UNKNOWN)
    log "VM=$VM_STATUS"
fi

# 2. RUNNING: 仅记录 + 退出 (下次 cron 再 probe)
if [[ "$VM_STATUS" == "RUNNING" ]]; then
    log "VM RUNNING — wait next cron tick"
    exit 0
fi

# 3. PROVISIONING / STAGING: 也只等
if [[ "$VM_STATUS" == "PROVISIONING" || "$VM_STATUS" == "STAGING" || "$VM_STATUS" == "STOPPING" ]]; then
    log "VM $VM_STATUS — wait next cron tick"
    exit 0
fi

# 4. UNKNOWN: gcloud creds 没拿到, 不动 — 等下次
if [[ "$VM_STATUS" == "UNKNOWN" ]]; then
    log "VM UNKNOWN (gcloud 调用失败) — wait next cron tick"
    exit 0
fi

# 5. TERMINATED / STOPPED: trigger pull
if [[ "$VM_STATUS" == "TERMINATED" || "$VM_STATUS" == "STOPPED" ]]; then
    log "VM $VM_STATUS — triggering pull_predictions"
    echo "{\"step\":\"vm_terminated_pulling\",\"model_id\":\"$MODEL_ID\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$STATUS_DIR/status.json"

    if [[ "$DRY_RUN" == "1" ]]; then
        log "[DRY] skip vm_start / SSH / pull / vm_stop (mock 成功)"
        log "[DRY] would write sentinel: $SENTINEL"
        touch "$SENTINEL"
        echo "{\"step\":\"pull_done_dry_run\",\"model_id\":\"$MODEL_ID\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$STATUS_DIR/status.json"
        exit 4
    fi

    # Real pull (跟原 monitor 逻辑等价)
    bash gcp/vm_start.sh 2>&1 | tee -a "$PROBE_LOG"
    gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
        --command="gcloud storage cp ~/chunkymonkey/data/smartmoney.duckdb gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb 2>&1 | tail -3" 2>&1 | tee -a "$PROBE_LOG"
    gcloud storage cp "gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb" \
        "data/smartmoney_post_${MODEL_ID}.duckdb.bak" 2>&1 | tail -5 | tee -a "$PROBE_LOG"
    bash gcp/vm_stop.sh 2>&1 | tee -a "$PROBE_LOG"

    log "pull done, smartmoney_post_${MODEL_ID}.duckdb.bak local"
    echo "{\"step\":\"pull_done\",\"model_id\":\"$MODEL_ID\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$STATUS_DIR/status.json"
    touch "$SENTINEL"
    log "sentinel: $SENTINEL (后续 cron probe silently skip)"
    exit 2
fi

# 6. 未知未处理 status
log "VM status unhandled: $VM_STATUS — wait next cron tick"
exit 0
