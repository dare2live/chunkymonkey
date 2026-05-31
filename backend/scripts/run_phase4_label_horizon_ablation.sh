#!/bin/bash
# Phase 4 #4 label horizon ablation (5d / 10d / 20d 哪个 RankIC 最强).
#
# 按 Phase 4 roadmap (docs/strategy_validation_contract.md):
# - 3 horizons × governance v1 framework
# - 用 default LightGBM params (跳 Optuna 节省时间; 后续单独跑 Optuna best)
# - 输出 mart_p0b_oos_predictions × 3 model_id + mart_p0b_walkforward_eval × 3
# - 比较 RankIC 跨 horizon
#
# 前置: Optuna PID 25088 (Phase 4 #3) 完成, DuckDB lock 释放.
#
# 用法:
#   bash backend/scripts/run_phase4_label_horizon_ablation.sh
#
# 预计耗时: 3 × ~10 min = 30 min Mac CPU
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH=backend
LOG_DIR="data/audit/logs/phase4_label_ablation_$(date +%Y%m%dT%H%M%S)"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [phase4_label] $*" | tee -a "$LOG_DIR/chain.log"; }

# from yaml: configs/data_governance.yaml — governance v1 baseline naming
LABEL_VERSION="p0a_v2_governance_v1"
FEATURE_VERSION="p0a_v3"
FEATURE_PANEL="mart_p0a_feature_label_panel_v3"
TIMESTAMP=$(date +%Y%m%d)

log "=== Phase 4 #4 label horizon ablation start ==="
log "label_version=$LABEL_VERSION feature_version=$FEATURE_VERSION"

# 1. Wait Optuna done (Phase 4 #3 PID 25088)
log "Step 0: wait Optuna PID 25088 done (DuckDB lock)"
while pgrep -f "run_p0b_lightgbm_optuna_v3" > /dev/null 2>&1; do
    sleep 60
    log "  Optuna still running..."
done
log "  Optuna done, DuckDB lock released"

# 2. 3 horizons train (sequential, single writer lock)
for HORIZON in 5d 10d 20d; do
    MODEL_ID="lgbm_${TIMESTAMP}_governance_v1_${HORIZON}"
    log "Step 1: train ${HORIZON} → ${MODEL_ID}"
    python backend/scripts/train_p0b_lightgbm.py \
        --label "fwd_cost_after_${HORIZON}" \
        --model-id "$MODEL_ID" \
        --feature-version "$FEATURE_VERSION" \
        --label-version "$LABEL_VERSION" \
        --feature-panel "$FEATURE_PANEL" \
        --enforce-rankic-gate \
        2>&1 | tee "$LOG_DIR/train_${HORIZON}.log"
    log "  ${HORIZON} done"
done

# 3. 比较 RankIC 跨 horizon
log "Step 2: aggregate RankIC results"
python3 -c "
import duckdb
c = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = c.execute(\"\"\"
SELECT model_id, label_version,
       AVG(rank_ic) avg_ic, AVG(rank_ic_ir) avg_ic_ir,
       COUNT(*) n_windows
FROM mart_p0b_walkforward_eval
WHERE model_id LIKE 'lgbm_${TIMESTAMP}_governance_v1_%'
GROUP BY model_id, label_version ORDER BY avg_ic DESC
\"\"\").fetchall()
print('Phase 4 #4 label horizon ablation results:')
for row in r: print(f'  {row[0]:40s} ic={row[2]:.4f} ic_ir={row[3]:.4f} n={row[4]}')
c.close()
" 2>&1 | tee "$LOG_DIR/ablation_results.log"

log "=== Phase 4 #4 label horizon ablation DONE ==="
log "log dir: $LOG_DIR"
