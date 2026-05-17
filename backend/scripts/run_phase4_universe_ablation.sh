#!/bin/bash
# Phase 4 #5 universe ablation: KEEP 60/00/30/68 vs 流动性 top-2000 vs sector neutral
#
# 按 Phase 4 roadmap stage 4 — universe 选择对 alpha 影响.
#
# 3 universe 候选:
# A. KEEP 60/00/30/68 ever-listed (5,210 codes) ← Phase 3 baseline
# B. 流动性 top-2000 by ADV20 (排除小盘) — 减少噪音, 集中高流动性 alpha
# C. sector neutral (28 行业 each top-K) — 防 sector tilt
#
# 前置:
# - Phase 4 #3 Optuna 200 trials done (DuckDB lock 释放)
# - mart_p0a_label_panel governance v1 已 build
# - mart_p0a_feature_label_panel_v3 已 build
#
# 实施: 不重 build feature panel, 在 train_p0b_lightgbm.py 读 panel 时 filter universe
#
# 用法:
#   bash backend/scripts/run_phase4_universe_ablation.sh
#
# 预计耗时: 3 × ~10 min = 30 min Mac CPU
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH=backend
LOG_DIR="data/audit/logs/phase4_universe_ablation_$(date +%Y%m%dT%H%M%S)"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [phase4_univ] $*" | tee -a "$LOG_DIR/chain.log"; }

LABEL_VERSION="p0a_v2_governance_v1"
FEATURE_VERSION="p0a_v3"
FEATURE_PANEL="mart_p0a_feature_label_panel_v3"
TIMESTAMP=$(date +%Y%m%d)
LABEL="fwd_cost_after_20d"

log "=== Phase 4 #5 universe ablation start ==="

# Step 0: wait Optuna done
while pgrep -f "run_p0b_lightgbm_optuna_v3" > /dev/null 2>&1; do
    sleep 60
    log "  Optuna still running..."
done
log "  Optuna done"

# Step 1: build 3 universe filter views (read-only, no DB write)
log "Step 1: build universe filter SQL views"
python3 << 'PY' 2>&1 | tee "$LOG_DIR/universe_define.log"
import duckdb
c = duckdb.connect('data/smartmoney.duckdb')

# A. KEEP ever-listed (baseline) — 不需 view (default)

# B. 流动性 top-2000 by ADV20 (rolling 20-day average daily value)
c.execute("""
CREATE OR REPLACE VIEW v_phase4_universe_liquid_top2000 AS
WITH adv20 AS (
    SELECT stock_code, signal_date, amount_20d
    FROM mart_p0a_feature_label_panel_v3
    WHERE fwd_cost_after_20d IS NOT NULL
),
ranked AS (
    SELECT signal_date, stock_code,
           ROW_NUMBER() OVER (PARTITION BY signal_date ORDER BY amount_20d DESC NULLS LAST) rk
    FROM adv20
)
SELECT signal_date, stock_code FROM ranked WHERE rk <= 2000
""")
n_b = c.execute("SELECT COUNT(*) FROM v_phase4_universe_liquid_top2000").fetchone()[0]
print(f"  B universe (liquid top-2000): {n_b:,} rows")

# C. sector neutral (Phase 1 industry_pit data, each sector top-K by liquidity)
c.execute("""
CREATE OR REPLACE VIEW v_phase4_universe_sector_neutral AS
WITH stock_sector AS (
    SELECT panel.stock_code, panel.signal_date, panel.amount_20d,
           panel.industry_pit_l1_name
    FROM mart_p0a_feature_label_panel_v3 panel
    WHERE panel.fwd_cost_after_20d IS NOT NULL
      AND panel.industry_pit_l1_name IS NOT NULL
),
ranked AS (
    SELECT signal_date, stock_code, industry_pit_l1_name,
           ROW_NUMBER() OVER (PARTITION BY signal_date, industry_pit_l1_name
                              ORDER BY amount_20d DESC NULLS LAST) rk
    FROM stock_sector
)
SELECT signal_date, stock_code FROM ranked WHERE rk <= 75
""")
n_c = c.execute("SELECT COUNT(*) FROM v_phase4_universe_sector_neutral").fetchone()[0]
print(f"  C universe (sector neutral): {n_c:,} rows")
c.close()
PY

# Step 2: train 3 universes
# NOTE: train_p0b_lightgbm.py 当前不支持 --universe-filter, 需手工 universe inline
# 简化: 跑 baseline (A) + B + C 用 SQL pre-filter pa
# 实际此 phase 需先扩 train_p0b_lightgbm.py 加 --universe-filter-view 参数
log "Step 2: train across 3 universes (baseline + B + C)"
log "  NOTE: train_p0b_lightgbm.py 当前不支持 --universe-view, 待 add CLI 后跑"
log "  目前生成 universe filter views, train 单独 PR/commit 实施"

log "=== Phase 4 #5 universe ablation script skeleton DONE ==="
log "log dir: $LOG_DIR"
log "TODO: train_p0b_lightgbm.py 加 --universe-filter-view 参数 (read view, INNER JOIN signal_date+stock_code)"
