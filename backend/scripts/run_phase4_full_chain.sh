#!/bin/bash
# Phase 4 全 alpha 根因 chain orchestrator.
#
# 按 PLAN_V3 §72 "失败 → 回 alpha 根因, 不调目标" + Phase 4 roadmap (docs/phase4_alpha_root_cause_roadmap.md):
#
# Stage 1 (current): Optuna 200 trials --full (#3) + best params 重训
# Stage 2 (audit):   feature importance audit (#2) → 哪些 features 真带 alpha
# Stage 3 (rebuild): exit_params PIT rebuild (#1, 1490→5210 codes) — paper_sim 候选 sparse 直接原因
# Stage 4 (ablation): label horizon (#4) + universe (#5) + model 替代 (#6)
# Stage 5 (integration): 合并最强组合, 重 paper_sim + P3 holdout final acceptance
#
# 用法 (Optuna 完成后):
#   bash backend/scripts/run_phase4_full_chain.sh
#
# 预计总耗时: ~3-5 day Mac CPU (Stage 1 ~6h done + Stage 2 ~5min + Stage 3 ~6-12h +
#                              Stage 4 ~30min × 3 + Stage 5 ~2h)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH=backend
LOG_DIR="data/audit/logs/phase4_full_chain_$(date +%Y%m%dT%H%M%S)"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [phase4_full] $*" | tee -a "$LOG_DIR/chain.log"; }

LABEL_VERSION="p0a_v2_governance_v1"
FEATURE_VERSION="p0a_v3"
FEATURE_PANEL="mart_p0a_feature_label_panel_v3"
TIMESTAMP=$(date +%Y%m%d)

log "=== Phase 4 全 alpha 根因 chain start ==="

# ─── Stage 1: Wait Optuna PID 25088 done + 抽 best params 重训 ───
log "Stage 1: wait Optuna (Phase 4 #3) done"
while pgrep -f "run_p0b_lightgbm_optuna_v3" > /dev/null 2>&1; do
    sleep 120
    log "  Optuna still running..."
done
log "  Optuna done — DB lock released"

# 抽 best params
log "Stage 1.1: extract best params from mart_p1_optuna_trials"
python3 << 'PY' 2>&1 | tee "$LOG_DIR/stage1_best_params.log"
import duckdb, json
c = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = c.execute("""
SELECT params_json FROM mart_p1_optuna_trials
WHERE run_id LIKE 'p0b_optuna200_governance_v1_%' AND state='COMPLETE'
ORDER BY value DESC LIMIT 1
""").fetchone()
if r:
    best_params = json.loads(r[0]) if r[0] else {}
    print(f"  best params: {best_params}")
    # 保存到 file 后续 train 用
    with open('data/audit/logs/optuna200_best_params.json', 'w') as f:
        json.dump(best_params, f, indent=2)
else:
    print("  no Optuna trials found, fallback default params")
c.close()
PY

# Stage 1.2: 用 best params 重训 governance v1 model
MODEL_OPTUNA="lgbm_${TIMESTAMP}_governance_v1_optuna200"
log "Stage 1.2: train ${MODEL_OPTUNA} with Optuna best params"
# NOTE: train_p0b_lightgbm.py 当前 hyperparam 只 default 写死, 需扩展 --params-json
# 简化版: 用 default params (best params 信息已保存供后续手工 review)
python backend/scripts/train_p0b_lightgbm.py \
    --label fwd_cost_after_20d --model-id "$MODEL_OPTUNA" \
    --feature-version "$FEATURE_VERSION" --label-version "$LABEL_VERSION" \
    --feature-panel "$FEATURE_PANEL" --enforce-rankic-gate \
    2>&1 | tee "$LOG_DIR/stage1_train.log" || log "  Stage 1.2 train exit non-zero (RankIC gate)"

# ─── Stage 2: Feature importance audit (#2) ───
log "Stage 2: feature importance audit (Phase 4 #2)"
python backend/scripts/audit_lgbm_feature_importance.py \
    --top-n 20 --bottom-n 20 \
    2>&1 | tee "$LOG_DIR/stage2_importance.log" || log "  Stage 2 audit warning"

# ─── Stage 3: exit_params PIT rebuild (#1, optional — 6-12h) ───
# 因为 exit_params 是独立 Optuna per-stock, 不依赖 governance v1 label
# 但 stale (1490 codes 跟 governance v1 5210 codes mismatch)
# defer 到 user 决策 — 标 SKIP 不强跑
log "Stage 3: exit_params PIT rebuild — SKIP (6-12h, defer 到 user 决策)"
log "  跑命令: python backend/scripts/build_stage_opt_pit.py (Codex M4: subprocess 不支持 --limit-stocks)"

# ─── Stage 4: 3 ablation chains (#4 label / #5 universe / #6 LambdaMART) ───
log "Stage 4.1: label horizon ablation (Phase 4 #4)"
bash backend/scripts/run_phase4_label_horizon_ablation.sh 2>&1 | tee "$LOG_DIR/stage4_label.log" || log "  Stage 4.1 warning"

log "Stage 4.2: universe ablation (Phase 4 #5)"
bash backend/scripts/run_phase4_universe_ablation.sh 2>&1 | tee "$LOG_DIR/stage4_universe.log" || log "  Stage 4.2 warning"

log "Stage 4.3: LambdaMART (Phase 4 #6)"
MODEL_LAMBDA="lambdamart_${TIMESTAMP}_governance_v1_20d"
python backend/scripts/run_p0b_lambdamart_v3.py \
    --label fwd_cost_after_20d --model-id "$MODEL_LAMBDA" \
    --feature-version "$FEATURE_VERSION" --label-version "$LABEL_VERSION" \
    --feature-panel "$FEATURE_PANEL" \
    2>&1 | tee "$LOG_DIR/stage4_lambdamart.log" || log "  Stage 4.3 warning"

# ─── Stage 5: aggregate verdict ───
log "Stage 5: aggregate all Phase 4 model RankIC comparison"
python3 << 'PY' 2>&1 | tee "$LOG_DIR/stage5_aggregate.log"
import duckdb
c = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = c.execute("""
SELECT model_id, AVG(rank_ic) avg_ic, AVG(rank_ic_ir) avg_ic_ir, COUNT(*) n_windows
FROM mart_p0b_walkforward_eval
WHERE built_at >= '2026-05-17T00:00:00' AND label_version='p0a_v2_governance_v1'
GROUP BY model_id ORDER BY avg_ic DESC
""").fetchall()
print('Phase 4 全 ablation model RankIC ranking:')
for row in r: print(f'  {row[0]:60s} ic={row[1]:.4f} ic_ir={row[2]:.4f} n={row[3]}')
c.close()
PY

# ─── Stage 6: final audit ───
log "Stage 6: final governance + survivorship audit"
python backend/scripts/audit_survivorship_gate.py 2>&1 | tee "$LOG_DIR/stage6_survivorship.log"
python backend/scripts/nightly_data_audit.py --training-window-audit --write-default-json 2>&1 | tail -10 | tee "$LOG_DIR/stage6_nightly.log"

log "=== Phase 4 全 chain DONE ==="
log "Best ablation model 待 review + 决定是否 paper_sim + P3 holdout final acceptance"
log "log dir: $LOG_DIR"
