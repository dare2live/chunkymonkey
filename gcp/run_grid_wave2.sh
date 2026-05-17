#!/usr/bin/env bash
# Wave 2: 在 Wave 1 完成后用满 32 cores 探索 horizon + seed + multi-model.
# Decision gate: only run Wave 2 if Wave 1 produced top config with rank_ic_mean >= 0.0275 (Codex yellow gate).
#
# Plan:
#   8 parallel jobs × 4 cores each = 32 cores fully utilized
#   - 4 horizon variants (5d / 10d / 20d / 60d) × top config from Wave 1
#   - 4 seed variants (seed=2, 3, 4, 5) × top config from Wave 1
#
# Usage:
#   bash gcp/run_grid_wave2.sh <top_config_label_from_wave1>
#
# Example:
#   bash gcp/run_grid_wave2.sh v4_drop_dead_20d

set -euo pipefail
cd "$(dirname "$0")/.."

TOP_CONFIG="${1:-}"
if [ -z "$TOP_CONFIG" ]; then
    echo "Usage: $0 <top_config_label>"
    echo "  e.g.: $0 v4_drop_dead_20d"
    exit 1
fi

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

# Gate check
GATE_PASS=$(PYTHONPATH=backend python -c "
ic = ${BEST_IC}
print('PASS' if ic >= 0.0275 else 'FAIL')
" 2>&1)
if [ "$GATE_PASS" = "FAIL" ]; then
    echo "WARN: Top config below yellow gate 0.0275. Wave 2 may be wasted."
    echo "If proceeding anyway, run with --force"
    if [ "${2:-}" != "--force" ]; then
        echo "ABORT: not --force"
        exit 2
    fi
fi

OUTDIR="runs/grid_wave2_$(date -u +%Y%m%dT%H%M%S)"
mkdir -p "$OUTDIR/logs"

# Determine panel + excludes from top config name
case "$TOP_CONFIG" in
    v3_*) PANEL="mart_p0a_feature_label_panel_v3"; EXCL="" ;;
    v4_all_*) PANEL="mart_p0a_feature_label_panel_v4"; EXCL="" ;;
    v4_drop_dead_*) PANEL="mart_p0a_feature_label_panel_v4"
                     EXCL="--exclude-cols sm_ret_5d,sm_ret_20d,sm_ret_60d,sm_ret_120d,sm_excess_20d,sm_excess_60d,sm_price_vs_ma20,sm_price_vs_ma60,sm_vol_60d,holder_count_change_q_pct,survey_count_30d,survey_count_60d,survey_inst_30d,survey_inst_60d,tom_day_of_month,tom_days_to_month_end,tom_days_from_month_start,tom_month_phase,tom_is_first_week,tom_is_last_week,tom_is_month_turn"
                     ;;
    *) PANEL="mart_p0a_feature_label_panel_v4"; EXCL="" ;;
esac

launch() {
    local label="$1"
    local label_args="$2"
    local pid_file="$OUTDIR/$label.pid"
    local logfile="$OUTDIR/logs/$label.log"
    echo "[launch] $label: $label_args"
    OMP_NUM_THREADS=4 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
        nohup python backend/scripts/run_p0b_lightgbm_optuna_v4.py \
            --feature-panel "$PANEL" \
            --n-trials 50 --full \
            --start-date 2024-01-01 --end-date 2026-04-13 \
            --min-train-months 12 \
            --run-id "${label}_$(date -u +%Y%m%dT%H%M%S)" \
            $EXCL \
            $label_args \
            > "$logfile" 2>&1 &
    echo $! > "$pid_file"
    echo "  PID: $(cat $pid_file), log: $logfile"
}

echo "================================================"
echo "Wave 2: 8 jobs × 4 cores from top config ${TOP_CONFIG}"
echo "================================================"

# 4 horizon variants
for horizon in 5 10 20 60; do
    launch "wave2_horizon_${horizon}d" "--label fwd_cost_after_${horizon}d --seed 42"
    sleep 5
done

# 4 seed variants on 20d
for seed in 2 3 4 5; do
    launch "wave2_seed_${seed}_20d" "--label fwd_cost_after_20d --seed ${seed}"
    sleep 5
done

echo
echo "Wave 2 8 jobs launched. Monitor:"
echo "  tail -f $OUTDIR/logs/*.log"
echo
echo "Wall time estimate: 2-4h × 32 cores @ 4 cores/job"
