#!/usr/bin/env bash
# Phase 5 Post-Retrain Pipeline — retrain 完成后 1-click 跑完整 audit + promote
#
# 配套 scripts/gcp_stability_retrain.sh (GCP controlled-use) 或 Mac local retrain.
# retrain 输出 mart_p0b_oos_predictions 含新 model_id (~40 monthly OOS), 此 script:
# 1. backfill walkforward_eval RankIC for new model_id
# 2. run P3 Final Holdout (新 model_id, last 40 months)
# 3. backfill 完成 → promote_champion (P3 PASS 自动)
# 4. re-run msaf_ensemble_paper_sim --compute-kpi (新 model_id)
# 5. re-run phase4_gate_on_msaf
# 6. re-run audit_delivery_readiness (预期 95%+)
# 7. commit + push (可选)
#
# Usage:
#   bash scripts/run_phase5_post_retrain.sh <new_model_id>
#   bash scripts/run_phase5_post_retrain.sh lgbm_phase5_session_20260518T160747

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

MODEL_ID="${1:-}"
if [[ -z "$MODEL_ID" ]]; then
    # 自动找最新 lgbm_phase5_* model_id
    MODEL_ID=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute(\"\"\"
    SELECT model_id, COUNT(DISTINCT signal_date) AS n_dates
    FROM mart_p0b_oos_predictions
    WHERE model_id LIKE 'lgbm_phase5_%'
    GROUP BY model_id ORDER BY n_dates DESC LIMIT 1
\"\"\").fetchone()
con.close()
print(r[0] if r else 'NONE')
" 2>/dev/null)
    if [[ "$MODEL_ID" == "NONE" || -z "$MODEL_ID" ]]; then
        echo "ERROR: 没有 lgbm_phase5_* model_id 找到, retrain 是否完成?"
        echo "       检查: tail -f /tmp/phase5_retrain_mac.log"
        exit 1
    fi
    echo "[post-retrain] Auto-detected latest model_id: $MODEL_ID"
fi

LOG="/tmp/phase5_post_retrain_$(date +%Y%m%d_%H%M%S).log"
echo "[post-retrain] log: $LOG"
echo "[post-retrain] model_id: $MODEL_ID"
echo "[post-retrain] === Step 1/6: Backfill walkforward_eval RankIC ==="
PYTHONPATH=backend python backend/scripts/backfill_walkforward_eval.py \
    --model-id "$MODEL_ID" >> "$LOG" 2>&1 || \
    { echo "ERROR: backfill walkforward_eval failed (见 $LOG)"; exit 1; }
echo "[post-retrain] ✓ backfill OK"

echo "[post-retrain] === Step 2/6: Run P3 Final Holdout (40 month) ==="
P3_RUN_ID="p3_phase5_$(date +%Y%m%dT%H%M%S)"
PYTHONPATH=backend python backend/scripts/run_p3_final_holdout.py \
    --model-id "$MODEL_ID" --run-id "$P3_RUN_ID" --last-n-months 40 >> "$LOG" 2>&1 || \
    { echo "ERROR: P3 holdout failed (见 $LOG)"; exit 1; }
echo "[post-retrain] ✓ P3 run_id: $P3_RUN_ID"

echo "[post-retrain] === Step 3/6: Promote Champion (P3 verdict-gated) ==="
PYTHONPATH=backend python backend/scripts/promote_champion.py \
    --p3-run-id "$P3_RUN_ID" \
    --reason "Phase 5 extended retrain (start=2023, n_trials=50)" >> "$LOG" 2>&1 || \
    echo "WARN: promote_champion 失败 (可能 P3 FAIL 或 gate inputs missing, 见 $LOG)"

echo "[post-retrain] === Step 4/6: Re-run ensemble paper_sim KPI (with new model_id) ==="
PYTHONPATH=backend python backend/scripts/run_msaf_ensemble_paper_sim.py \
    --compute-kpi --horizon 20d \
    --lambdamart-model-id "$MODEL_ID" \
    --output-json "data/reports/msaf_ensemble_phase5_$MODEL_ID.json" >> "$LOG" 2>&1 || \
    echo "WARN: ensemble paper_sim failed (见 $LOG)"

echo "[post-retrain] === Step 5/6: Re-run phase4 gate ==="
PYTHONPATH=backend python backend/scripts/run_phase4_gate_on_msaf.py \
    --model-id "$MODEL_ID" --challenger-id "msaf_phase5_$MODEL_ID" >> "$LOG" 2>&1 || \
    echo "WARN: phase4 gate failed"

echo "[post-retrain] === Step 6/6: Audit delivery readiness ==="
PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py 2>&1 | tee -a "$LOG"

# Update default model_id (commit later if want)
echo ""
echo "[post-retrain] === DONE ==="
echo "[post-retrain] new champion model_id: $MODEL_ID"
echo "[post-retrain] new P3 run_id: $P3_RUN_ID"
echo "[post-retrain] 完整 log: $LOG"
echo ""
echo "Optional: 更新 ensemble runner default model_id, 跑:"
echo "  sed -i '' 's/lgbm_20260517_governance_v1_20d/$MODEL_ID/g' backend/scripts/run_msaf_ensemble_paper_sim.py"
echo "Optional: commit + push 新 KPI:"
echo "  git add -A && bash scripts/safe_commit.sh 'Phase 5 retrain complete: $MODEL_ID (n_obs ~40)'"
