#!/usr/bin/env bash
# Wave 3: Model variants — LambdaMART / CatBoost / XGBoost / NN
# 4 parallel × 8 cores = 32 cores fully utilized.
#
# prerequisite: Wave 1 + 2 完成, gate Yellow pass (top config rank_ic >= 0.0275)
#
# Usage:
#   bash gcp/run_grid_wave3_models.sh <top_config_label> [horizon_days]
#
# Example:
#   bash gcp/run_grid_wave3_models.sh v4_drop_dead_20d 20

set -euo pipefail
cd "$(dirname "$0")/.."

TOP_CONFIG="${1:-v4_drop_dead_20d}"
HORIZON="${2:-20}"

# Validate top config gate
BEST_IC=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute(\"\"\"
    SELECT MAX(rank_ic_mean) FROM mart_p1_optuna_trials
     WHERE run_id LIKE '${TOP_CONFIG}_%' AND state='COMPLETE'
\"\"\").fetchone()
print(f'{r[0]:.4f}' if r[0] is not None else 'NONE')
")
echo "Top config ${TOP_CONFIG} best rank_ic: ${BEST_IC}"

GATE_PASS=$(PYTHONPATH=backend python -c "
ic = ${BEST_IC}
print('PASS' if ic >= 0.0275 else 'FAIL')
" 2>&1)
if [ "$GATE_PASS" = "FAIL" ] && [ "${3:-}" != "--force" ]; then
    echo "ABORT: top config below yellow gate 0.0275, would waste W3 budget"
    exit 2
fi

OUTDIR="runs/grid_wave3_$(date -u +%Y%m%dT%H%M%S)"
mkdir -p "$OUTDIR/logs"
echo "OUTDIR: $OUTDIR"

# Determine panel from top config
case "$TOP_CONFIG" in
    v3_*) PANEL="mart_p0a_feature_label_panel_v3"; EXCL="" ;;
    v4_drop_dead_*) PANEL="mart_p0a_feature_label_panel_v4"
                     EXCL="--exclude-cols sm_ret_5d,sm_ret_20d,sm_ret_60d,sm_ret_120d,sm_excess_20d,sm_excess_60d,sm_price_vs_ma20,sm_price_vs_ma60,sm_vol_60d,holder_count_change_q_pct,survey_count_30d,survey_count_60d,survey_inst_30d,survey_inst_60d,tom_day_of_month,tom_days_to_month_end,tom_days_from_month_start,tom_month_phase,tom_is_first_week,tom_is_last_week,tom_is_month_turn"
                     ;;
    *) PANEL="mart_p0a_feature_label_panel_v4"; EXCL="" ;;
esac

echo "================================================"
echo "Wave 3 Model Grid: 4 × 8 cores"
echo "Panel: $PANEL, horizon: ${HORIZON}d"
echo "================================================"

# LambdaMART (priority P0)
echo "[launch] LambdaMART (P0)"
OMP_NUM_THREADS=8 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    nohup python backend/scripts/run_p0b_lambdamart_v3.py \
        --feature-panel "$PANEL" \
        --label "fwd_cost_after_${HORIZON}d" \
        --n-trials 50 \
        --start-date 2024-01-01 --end-date 2026-04-13 \
        --min-train-months 12 \
        --run-id "wave3_lambdamart_$(date -u +%Y%m%dT%H%M%S)" \
        $EXCL \
        > "$OUTDIR/logs/lambdamart.log" 2>&1 &
sleep 5

# CatBoost (P0) — need to write new script. For now stub
echo "[stub] CatBoost — script not yet implemented (TODO if Wave 3 needed)"

# XGBoost (P1) — same stub
echo "[stub] XGBoost — script not yet implemented (TODO if Wave 3 needed)"

# NN (P2) — same stub
echo "[stub] NN — script not yet implemented (TODO if Wave 3 needed)"

echo
echo "Wave 3 launched (LambdaMART only for now). To add CatBoost/XGBoost/NN, write scripts:"
echo "  - backend/scripts/run_p0b_catboost_v1.py"
echo "  - backend/scripts/run_p0b_xgboost_v1.py"
echo "  - backend/scripts/run_p0b_nn_v1.py"
echo "  (parallel to run_p0b_lambdamart_v3.py 模板)"
