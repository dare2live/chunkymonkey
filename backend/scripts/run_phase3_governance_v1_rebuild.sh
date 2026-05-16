#!/bin/bash
# Phase 3 governance v1 rebuild chain — sequential 跑完整 chain
# Source: Codex round 16 governance.yaml deliver, Phase 3 step 1-5
#
# 前置:
#   - Phase 2 step 3 EXECUTED (DELETE 4.88M rows from price_kline) ✓ commit 6828ea7b
#   - tdxhub sync 已追上最新 (建议先 trigger POST /api/inst/update/smart tdxhub native path)
#
# 用法:
#   bash backend/scripts/run_phase3_governance_v1_rebuild.sh
#
# 预计耗时:
#   Step 1: rebuild_p0a_label_panel ~20-40 min
#   Step 2: rebuild mart_p0a_feature_label_panel_v3 (feature join) ~10-20 min
#   Step 3: lgbm_v5 LightGBM Optuna 200 trial walk-forward ~6-10h Mac CPU
#   Step 4: paper_sim 重跑 (新 model_id) ~30 min - 1h
#   Step 5: P3 holdout ~5-10 min
#   总计: ~7-12h

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH=backend
LOG_DIR="data/audit/logs/phase3_$(date +%Y%m%dT%H%M%S)"
mkdir -p "$LOG_DIR"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [phase3] $*" | tee -a "$LOG_DIR/chain.log"
}

# evidence: governance v1 LABEL_VERSION (services/labels/build.py)
LABEL_VERSION="p0a_v2_governance_v1"
# evidence: governance v1 model_id naming (区分 corrupt v3, 新版本 v5 governance era)
MODEL_ID="lgbm_v5_governance_v1_20d"
LABEL_HORIZON="20"

log "=== Phase 3 governance v1 rebuild start ==="
log "LABEL_VERSION=$LABEL_VERSION MODEL_ID=$MODEL_ID horizon=$LABEL_HORIZON"
log "log dir: $LOG_DIR"

# --- Step 1: rebuild mart_p0a_label_panel (governance v1 contract) ---
log "Step 1: rebuild mart_p0a_label_panel ..."
python backend/scripts/rebuild_p0a_label_panel.py \
    2>&1 | tee "$LOG_DIR/step1_p0a_label.log"
log "Step 1 done."

# --- Step 2: rebuild mart_p0a_feature_label_panel_v3 (feature join from new label) ---
log "Step 2: rebuild mart_p0a_feature_label_panel_v3 (feature join) ..."
python backend/scripts/build_p0a_feature_panel_v3.py \
    --start-date 2024-01-01 \
    --end-date 2026-05-15 \
    2>&1 | tee "$LOG_DIR/step2_feature_panel.log"
log "Step 2 done."

# --- Step 3: lgbm_v5 LightGBM Optuna walk-forward ---
log "Step 3: lgbm_v5 LightGBM walk-forward (--n-trials 200 --full, ~6-10h) ..."
python backend/scripts/run_p0b_lightgbm_optuna_v3.py \
    --label fwd_cost_after_${LABEL_HORIZON}d \
    --run-id "$MODEL_ID" \
    --n-trials 200 \
    --full \
    2>&1 | tee "$LOG_DIR/step3_lgbm_train.log"
log "Step 3 done."

# --- Step 4: paper_sim 重跑 (新 model_id) ---
# 历史 mart_paper_sim_kpi 不 DELETE (Codex round 15 Q3 verdict deprecated marker), 新 sim_run_id 独立
log "Step 4: paper_sim (新 model_id) ..."
# TODO: invoke paper_sim runner with ml_score_loader → new MODEL_ID
# 占位: 实际入口取决于 run_paper_sim_live_daily.py / 或 ad-hoc sim_run
log "Step 4 placeholder — manually invoke paper_sim runner with model_id=$MODEL_ID"

# --- Step 5: P3 holdout (governance v1 final acceptance gate) ---
log "Step 5: P3 final holdout ..."
python backend/scripts/run_p3_final_holdout.py \
    --model-id "$MODEL_ID" \
    --run-id "p3_${MODEL_ID}_$(date +%Y%m%dT%H%M%S)" \
    --last-n-months 6 \
    2>&1 | tee "$LOG_DIR/step5_p3_holdout.log"
log "Step 5 done."

# --- Step 6: Final governance audit ---
log "Step 6: governance v1 final audit ..."
python backend/scripts/nightly_data_audit.py \
    --write-default-json \
    --lookback-days 30 \
    2>&1 | tee "$LOG_DIR/step6_audit.log"

log "=== Phase 3 governance v1 rebuild COMPLETE ==="
log "Check audit severity 应全 ok:"
python3 -c "
import json
d = json.load(open('data/audit/nightly_data_audit_latest.json'))
for chk in d.get('checks', []):
    sev = chk.get('severity', 'unknown')
    print(f'  {chk[\"name\"]:35s} severity={sev}')
print(f'\\noverall severity: {d.get(\"severity\")}')
"
