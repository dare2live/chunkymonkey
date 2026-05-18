#!/usr/bin/env bash
# daily_update.sh — 全自动化每天数据更新 + model refresh + paper_sim + 报告
#
# 用户终极交付标准 #4: "用户每天跑数据更新就全自动化了, 不需要大模型维护"
#
# 设计原则:
# 1. 不需要 Claude / Codex 干预 (纯 cron 或用户 1 click)
# 2. 失败有明确 alert (log + email/notification)
# 3. 资源自适应 (本地 Mac 优先, 需要 compute 时 auto start/stop VM)
# 4. 数据完整性 gate (preflight 检查 K-line continuity, sync gap auto alert)
# 5. 增量更新 (不重建全量, 只追新)
#
# Usage:
#   bash scripts/daily_update.sh          # 默认: 全流程
#   bash scripts/daily_update.sh --dry    # dry-run, 不写 DB
#   bash scripts/daily_update.sh --skip-sync  # 跳数据 sync (用现有)
#   bash scripts/daily_update.sh --gcp    # 强制用 GCP VM (Optuna refresh)
#
# Cron schedule (launchd plist 在 configs/launchd/com.chunkymonkey.daily-update.plist):
#   每天 17:00 (A 股收盘 15:00 + 2h 容缓数据 publish)
#
# Log: /tmp/chunkymonkey_daily_update_<YYYYMMDD>.log
#
# 流程 (从轻到重):
#   1. preflight 检查 (K-line continuity / watermark SLA)
#   2. 数据 sync (tdxhub + akshare 增量)
#   3. label / panel rebuild (增量)
#   4. model refresh (每周 1 次 Optuna, 其它 days 用 cached)
#   5. paper_sim live (今日预测 + 历史 backtest 增量)
#   6. backtester-mcp gate (PBO/DSR check, alert if regression)
#   7. champion promote (若 gate pass)
#   8. 报告生成 (HTML / JSON / log to /tmp)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DATE=$(date +%Y%m%d)
LOG="/tmp/chunkymonkey_daily_update_${DATE}.log"
DRY=0
SKIP_SYNC=0
USE_GCP=0

# Parse args
for arg in "$@"; do
    case "$arg" in
        --dry) DRY=1 ;;
        --skip-sync) SKIP_SYNC=1 ;;
        --gcp) USE_GCP=1 ;;
    esac
done

log() {
    echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"
}

fatal() {
    log "FATAL: $*"
    exit 1
}

log "=== ChunkyMonkey daily update ${DATE} ==="
log "  dry=$DRY skip_sync=$SKIP_SYNC use_gcp=$USE_GCP"

# Step 1: Preflight
log "--- Step 1: Preflight ---"
if [[ -f "backend/scripts/preflight_panel_build.py" ]]; then
    if ! PYTHONPATH=backend python backend/scripts/preflight_panel_build.py >> "$LOG" 2>&1; then
        log "WARN: preflight failed, K-line gap or watermark stale 可能"
        # 不 fatal — 后续 sync 修
    fi
else
    log "preflight script missing, skip"
fi

# Step 2: Data sync (tdxhub + akshare)
if [[ "$SKIP_SYNC" == "0" ]]; then
    log "--- Step 2: Data sync ---"
    if [[ "$USE_GCP" == "1" ]]; then
        log "Using GCP VM for backfill (tdxhub + akshare)"
        bash gcp/vm_start.sh >> "$LOG" 2>&1 || fatal "VM start failed"
        # TODO: 在 VM 跑 sync, 然后 stop
        log "GCP sync TBD (待 Phase 2: SUE/PEAD 接入后)"
        bash gcp/vm_stop.sh >> "$LOG" 2>&1 || log "WARN: VM stop failed"
    else
        log "Local sync (tdxhub via raw_incremental)"
        # TODO: invoke existing sync script
        # PYTHONPATH=backend python backend/scripts/sync_tdxhub_daily.py >> "$LOG" 2>&1
        log "local sync TBD (待集成现有 sync 路径)"
    fi
fi

# Step 3: Label / panel rebuild (增量)
log "--- Step 3: Label + panel incremental rebuild ---"
if [[ "$DRY" == "0" ]]; then
    # TODO: 增量 rebuild_p0a_label_panel + build_p0a_feature_panel_v6
    log "increment rebuild TBD (待 Phase 1.1 horizon governance + Phase 1.3 PIT gate 实施完)"
else
    log "DRY: skip rebuild"
fi

# Step 4: Model refresh (weekly Optuna or cached)
log "--- Step 4: Model refresh ---"
DOW=$(date +%u)
if [[ "$DOW" == "1" ]]; then
    log "Monday: trigger weekly Optuna refresh"
    if [[ "$USE_GCP" == "1" || "$DRY" == "0" ]]; then
        # TODO: 自动 start VM + 跑 Optuna 50 trials × 3 类策略 + stop
        log "weekly Optuna TBD (待 Phase 2: 3 类策略 wave 启动器)"
    fi
else
    log "Weekday (DOW=$DOW): use cached model"
fi

# Step 5: paper_sim live update
log "--- Step 5: paper_sim live ---"
# TODO: 增量跑 paper_sim (今日 prediction + 累积 NAV)
log "paper_sim live TBD (待 Phase 3: ensemble + regime gate)"

# Step 6: backtester-mcp gate check
log "--- Step 6: PBO/DSR/conservative gate ---"
# MSAF Phase 1.5 完成: backend/services/backtest_validation/ 集成
# 调用 gate.run_all_gates() — 若 promote_action == "block" → log + alert + 不 promote
GATE_OUT=$(mktemp)
PYTHONPATH=backend python - >> "$LOG" 2>&1 <<PYEOF
import json
import sys
sys.path.insert(0, "backend")
try:
    from services.backtest_validation.gate import run_all_gates
    # TODO: 实测 paper_sim KPI 接入 (待 Phase 3 ensemble + regime gate 完成)
    # 当前只 import check, 不跑 (缺 paper_sim KPI input)
    print("[gate] module import OK, awaiting paper_sim KPI inputs")
    sys.exit(0)
except Exception as e:
    print(f"[gate] import failed: {e}")
    sys.exit(1)
PYEOF
log "gate module import check OK (full evaluation 待 Phase 3 paper_sim ensemble 输出 KPI 接入)"

# Step 7: Champion promote
log "--- Step 7: Champion promote ---"
# TODO: 若 gate pass + Sharpe 提升 → promote
log "champion promote TBD"

# Step 8: Report
log "--- Step 8: Report ---"
# 生成 HTML / JSON summary 给用户
mkdir -p data/reports
REPORT_JSON="data/reports/daily_${DATE}.json"
cat > "$REPORT_JSON" <<JSONEOF
{
  "date": "${DATE}",
  "dry_run": ${DRY},
  "log": "${LOG}",
  "delivery_status": "in_progress",
  "phase_status": {
    "preflight": "running",
    "data_sync": "TBD",
    "panel_rebuild": "TBD",
    "model_refresh": "TBD",
    "paper_sim_live": "TBD",
    "gate_check": "TBD",
    "champion_promote": "TBD"
  }
}
JSONEOF
log "Report written: $REPORT_JSON"

log "=== daily_update DONE (实施待 Phase 1.5 + Phase 2 + Phase 3 完成 fill TBD steps) ==="
