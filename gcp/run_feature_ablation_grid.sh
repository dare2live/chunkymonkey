#!/usr/bin/env bash
# Codex Round 23 verdict — Feature Ablation Grid 4 parallel jobs on 32-core VM.
#
# Runs 4 Optuna sweeps simultaneously, each 8 cores, per-job SQLite storage,
# final merge to mart_p1_optuna_trials.
#
# Usage (on VM after data downloaded):
#   cd ~/chunkymonkey
#   source .venv/bin/activate
#   bash gcp/run_feature_ablation_grid.sh
#
# Output: runs/roi_grid/<job_label>/{trials.jsonl,study.db,summary.json}
# Then call merge script to write to local DuckDB.

set -euo pipefail
cd "$(dirname "$0")/.."

OUTDIR="runs/roi_grid_$(date -u +%Y%m%dT%H%M%S)"
mkdir -p "$OUTDIR"

LOG_BASE="$OUTDIR/logs"
mkdir -p "$LOG_BASE"

echo "================================================"
echo "Feature Ablation Grid (Codex Round 23)"
echo "  outdir: ${OUTDIR}"
echo "  cores per job: 8 (OMP_NUM_THREADS=8)"
echo "  parallel jobs: 4"
echo "================================================"
echo

# Sanity: panels exist
PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
for t in ['mart_p0a_feature_label_panel_v3', 'mart_p0a_feature_label_panel_v4']:
    try:
        n = con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'  {t}: {n:,} rows')
    except Exception as e:
        print(f'  {t}: MISSING — {e}')
        exit(1)
"

# Job configs (Codex 推荐 4 jobs)
declare -A JOBS=(
    [v3_all_20d]="mart_p0a_feature_label_panel_v3|92|none"
    [v4_all_20d]="mart_p0a_feature_label_panel_v4|122|none"
    [v4_drop_dead_20d]="mart_p0a_feature_label_panel_v4|109|sm_holder_survey_tom"
    [v4_a158_lhb_mc_20d]="mart_p0a_feature_label_panel_v4|100|keep_a158_lhb_mc_only"
)

# Build per-job exclude col lists (passed via env to Optuna script)
# Phase 4 dead cols (sm_*, holder_count_change_q_pct)
DEAD_COLS="sm_ret_5d,sm_ret_20d,sm_ret_60d,sm_ret_120d,sm_excess_20d,sm_excess_60d,sm_price_vs_ma20,sm_price_vs_ma60,sm_vol_60d,holder_count_change_q_pct"
# Phase 4 weak cols (survey, tom — keep mcap_decile + lhb_*)
WEAK_COLS="survey_count_30d,survey_count_60d,survey_inst_30d,survey_inst_60d,tom_day_of_month,tom_days_to_month_end,tom_days_from_month_start,tom_month_phase,tom_is_first_week,tom_is_last_week,tom_is_month_turn"
# v4_a158_lhb_mc: ALSO exclude exec_* + beta_* (only keep a158, lhb_*, mcap_decile)
EXTRA_KEEP_ONLY="exec_buy_60d,exec_sell_60d,exec_buy_pct_60d,exec_sell_pct_60d,exec_net_signal,beta_60d,beta_60d_zscore"

launch_job() {
    local label="$1"
    local panel="$2"
    local extra_excl="$3"
    local logfile="$LOG_BASE/${label}.log"
    local jobdir="$OUTDIR/$label"
    mkdir -p "$jobdir"

    # Build --exclude-cols arg
    local excl=""
    case "$extra_excl" in
        sm_holder_survey_tom) excl="--exclude-cols ${DEAD_COLS},${WEAK_COLS}" ;;
        keep_a158_lhb_mc_only) excl="--exclude-cols ${DEAD_COLS},${WEAK_COLS},${EXTRA_KEEP_ONLY}" ;;
        *) excl="" ;;
    esac

    echo "[launch] ${label} | panel=${panel} | exclude=${extra_excl}"
    OMP_NUM_THREADS=8 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
        nohup python backend/scripts/run_p0b_lightgbm_optuna_v4.py \
            --feature-panel "$panel" \
            --label fwd_cost_after_20d \
            --n-trials 50 --full \
            --start-date 2024-01-01 --end-date 2026-04-13 \
            --min-train-months 12 \
            --run-id "${label}_$(date -u +%Y%m%dT%H%M%S)" \
            $excl \
            > "$logfile" 2>&1 &
    local pid=$!
    echo "  PID: $pid, log: $logfile"
    echo "$pid" > "$jobdir/pid"
}

# Launch 4 jobs in parallel
for label in v3_all_20d v4_all_20d v4_drop_dead_20d v4_a158_lhb_mc_20d; do
    IFS='|' read -r panel n_features extra_excl <<< "${JOBS[$label]}"
    launch_job "$label" "$panel" "$extra_excl"
    sleep 5  # stagger start to avoid race on DuckDB read open
done

echo
echo "All 4 jobs launched. Monitor with:"
echo "  tail -f $OUTDIR/logs/*.log"
echo "Check PIDs:"
echo "  cat $OUTDIR/*/pid"
echo
echo "After all done (5-8h):"
echo "  python gcp/merge_grid_results.py $OUTDIR"
