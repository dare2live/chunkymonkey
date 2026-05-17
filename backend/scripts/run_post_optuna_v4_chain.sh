#!/usr/bin/env bash
# Post-Optuna v4 chain: retrain best params → write predictions → paper_sim ablation → KPI report.
#
# Codex round 21 Path Z step 2: 当 Optuna v4 50 trials 完, 跑这条 chain.
#
# Run prerequisite check:
#   1. Optuna v4 PID 完成 (no more grep 'mean_ic=' < 50)
#   2. mart_p1_optuna_trials has 50 v4 trials persisted
#   3. Best trial value > baseline 0.0246 (gate)
#
# Usage:
#   bash backend/scripts/run_post_optuna_v4_chain.sh

set -euo pipefail
cd "$(dirname "$0")/../.."

V4_RUN_PREFIX="p0b_optuna_v4_"
LOG_DIR="data/audit/logs"
LOG="${LOG_DIR}/post_optuna_v4_chain_$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "${LOG}") 2>&1

echo "================================================"
echo "Post-Optuna v4 chain start: $(date)"
echo "================================================"

# Step 1: Verify Optuna v4 done
echo
echo "[1/5] Verify Optuna v4 complete + select best trial"
BEST_RUN_ID=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute(\"\"\"
    SELECT run_id, trial_number, value, rank_ic_mean, rank_ic_std, params_json
      FROM mart_p1_optuna_trials
     WHERE run_id LIKE '${V4_RUN_PREFIX}%' AND state='COMPLETE'
     ORDER BY value DESC LIMIT 1
\"\"\").fetchone()
if not r:
    print('NO_V4_TRIALS')
else:
    print(r[0])
")
if [ "${BEST_RUN_ID}" = "NO_V4_TRIALS" ]; then
    echo "ERROR: no v4 trials in mart_p1_optuna_trials. Wait for Optuna v4 to complete."
    exit 2
fi
echo "  best run_id: ${BEST_RUN_ID}"

# Show top 5 v4 trials
PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute(\"\"\"
    SELECT trial_number, value, rank_ic_mean, rank_ic_std
      FROM mart_p1_optuna_trials
     WHERE run_id LIKE '${V4_RUN_PREFIX}%' AND state='COMPLETE'
     ORDER BY value DESC LIMIT 5
\"\"\").fetchall()
print('Top 5 v4 trials:')
for row in r: print(f'  trial={row[0]}, value={row[1]:.4f}, rank_ic={row[2]:.4f}±{row[3]:.4f}')
"

# Step 2: Gate check — best mean_ic > baseline 0.0246
echo
echo "[2/5] Gate check: best rank_ic > baseline 0.0246"
BEST_IC=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute(\"\"\"
    SELECT MAX(rank_ic_mean) FROM mart_p1_optuna_trials
     WHERE run_id LIKE '${V4_RUN_PREFIX}%' AND state='COMPLETE'
\"\"\").fetchone()
print(f'{r[0]:.4f}' if r[0] is not None else 'NONE')
")
echo "  best v4 rank_ic_mean: ${BEST_IC}"
if [ "${BEST_IC}" = "NONE" ]; then
    echo "  ERROR: no trial rank_ic data, abort"
    exit 3
fi
# Compare (string compare 简化, 用户确认)
GATE_PASS=$(PYTHONPATH=backend python -c "
ic = ${BEST_IC}
print('PASS' if ic > 0.0246 else 'FAIL')
")
echo "  gate (>0.0246): ${GATE_PASS}"
if [ "${GATE_PASS}" = "FAIL" ]; then
    echo "  WARN: best v4 < baseline. Phase 4 features 未带 alpha提升. 继续但不 promote champion."
fi

# Step 3: Retrain final LGBM with best params on full v4 panel
echo
echo "[3/5] Retrain LGBM with best v4 params on full panel"
echo "  TODO: implement train_p0b_lightgbm.py --feature-panel v4 --params <best_json>"
echo "  manual: scripts/train_p0b_lightgbm.py supports --feature-panel arg"
# Placeholder: user runs train_p0b_lightgbm.py separately with best params

# Step 4: paper_sim sizer ablation (equal vs score_rank_diff_v1)
echo
echo "[4/5] paper_sim sizer ablation"
PYTHONPATH=backend python backend/scripts/run_paper_sim_sizer_ablation.py --dry-run
echo "  --dry-run shown. Actual run: drop --dry-run after retrain done."

# Step 5: KPI summary
echo
echo "[5/5] KPI summary (after paper_sim runs)"
PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
try:
    r = con.execute(\"\"\"
        SELECT variant, ann_ret, max_dd, monthly_win_rate, excess_vs_hs300, sharpe
          FROM mart_paper_sim_kpi
         WHERE variant LIKE 'sizer_ablation_%' OR variant LIKE 'v4_%'
         ORDER BY built_at DESC LIMIT 10
    \"\"\").fetchall()
    for row in r: print(f'  {row}')
except Exception as e:
    print(f'  no v4 KPI rows yet: {e}')
"

echo
echo "================================================"
echo "Post-Optuna v4 chain end: $(date)"
echo "================================================"
