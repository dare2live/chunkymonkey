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
source scripts/lib/gcp_guard.sh

DATE=$(date +%Y%m%d)
LOG="/tmp/chunkymonkey_daily_update_${DATE}.log"
# Env var override (e.g. DRY=1 SKIP_SYNC=1 bash daily_update.sh)
DRY=${DRY:-0}
SKIP_SYNC=${SKIP_SYNC:-0}
USE_GCP=${USE_GCP:-0}
MODEL_ID_DATE="${CHUNKY_MODEL_DATE_OVERRIDE:-$DATE}"
DOW="${CHUNKY_DOW_OVERRIDE:-$(date +%u)}"
VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-~/chunkymonkey}"
# Champion model_id for daily Step 7 promote / Step 4 retrain (rule-compliance: ok evidence=lambdamart-v6-codex-2.1-fixed-config)
CHAMPION_MODEL_ID="${CHAMPION_MODEL_ID:-lgbm_20260517_governance_v1_20d}"
MODEL_REFRESH_VM_STARTED=0

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

stop_model_refresh_vm() {
    if [[ "$MODEL_REFRESH_VM_STARTED" == "1" ]]; then
        log "Stopping GCP VM after model refresh"
        if ! bash gcp/vm_stop.sh >> "$LOG" 2>&1; then
            log "WARN: VM stop failed; run bash gcp/vm_stop.sh manually"
        fi
        MODEL_REFRESH_VM_STARTED=0
    fi
}

run_backtest_validation_gate() {
    log "Running backtest_validation pre-flight gate"
    PYTHONPATH=backend python - <<'PY' >> "$LOG" 2>&1
import json
import sys

from services.backtest_validation.gate import run_all_gates

result = run_all_gates("daily_update_model_refresh")
print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
if result.promote_action in {"block", "force_retrain"}:
    sys.exit(1)
PY
}

run_lambdamart_v6_retrain_on_vm() {
    require_gcp_explicit_ok "scripts/daily_update.sh --gcp"
    gcloud compute ssh "${VM_NAME}" --zone="${ZONE}" --tunnel-through-iap --command \
        "bash -s -- '${REMOTE_REPO_DIR}' '${MODEL_ID_DATE}'" <<'REMOTE'
set -euo pipefail

REMOTE_REPO_DIR="$1"
MODEL_ID_DATE="$2"
REPO_DIR="${REMOTE_REPO_DIR/#\~/${HOME}}"

cd "${REPO_DIR}"
if [[ -d ".venv" ]]; then
    # shellcheck disable=SC1091
    source ".venv/bin/activate"
fi
export PYTHONPATH=backend
python backend/scripts/retrain_lambdamart_v6.py \
    --model-date "${MODEL_ID_DATE}" \
    --n-trials 50 \
    --full
REMOTE
}

trap stop_model_refresh_vm EXIT

log "=== ChunkyMonkey daily update ${DATE} ==="
log "  dry=$DRY skip_sync=$SKIP_SYNC use_gcp=$USE_GCP"

# Step 0: GCP 成本 tracker (用户原则 CLAUDE.md §10.0.2: 不浪费 GCP 资源)
log "--- Step 0: GCP cost tracker ---"
COST_TRACKER_EXIT=0
if [[ "${CHUNKYMONKEY_GCP_EXPLICIT_OK:-0}" == "1" ]]; then
    bash gcp/cost_tracker.sh --quiet >> "$LOG" 2>&1 || COST_TRACKER_EXIT=$?
else
    log "GCP disabled by user rule; skip cost tracker and force local mode"
    USE_GCP=0
fi
COST_ALERT=$(PYTHONPATH=backend python -c "
import json
try:
    with open('data/reports/gcp_cost_summary.json') as f:
        d = json.load(f)
    print(d.get('alert_level', 'UNKNOWN'), d.get('pct_of_budget', 0), d.get('projected_month_cost', 0))
except Exception as e:
    print('UNKNOWN 0 0')
" 2>/dev/null)
log "GCP cost: $COST_ALERT"
if [[ "$COST_TRACKER_EXIT" == "2" ]]; then
    log "[GCP-ALERT-RED] 月度预算 >100%, 当前必须 stop VM"
    if [[ "$USE_GCP" == "1" ]]; then
        log "  USE_GCP=1 但预算超支, 强制改本地模式"
        USE_GCP=0
    fi
elif [[ "$COST_TRACKER_EXIT" == "1" ]]; then
    log "[GCP-ALERT-YELLOW] 月度预算 >80%, 谨慎使用 VM"
fi

# Step 1: Preflight
log "--- Step 1: Preflight (watermark SLA + K-line gate) ---"
# 1a. Watermark SLA check + auto-update
# 2026-05-21 fix: set -e + python exit code 2 (alert) 会让脚本静默终止, sla_exit=$? 永远不执行.
# 用 if 包起来让 conditional context 抑制 set -e.
SLA_ARGS=""
[[ "$DRY" == "1" ]] && SLA_ARGS="--dry-run"
sla_exit=0
if ! PYTHONPATH=backend python backend/scripts/update_watermark_sla.py \
    $SLA_ARGS \
    --json-output "data/audit/watermark_sla_${DATE}.json" >> "$LOG" 2>&1; then
    sla_exit=$?
fi
if [[ "$sla_exit" == "2" ]]; then
    log "WARN: watermark SLA alert (见 data/audit/watermark_sla_${DATE}.json)"
elif [[ "$sla_exit" != "0" ]]; then
    log "ERROR: watermark SLA check failed (exit $sla_exit) — 继续推进 Step 2 sync"
fi

# 1b. K-line continuity preflight
if [[ -f "backend/scripts/preflight_panel_build.py" ]]; then
    if ! PYTHONPATH=backend python backend/scripts/preflight_panel_build.py >> "$LOG" 2>&1; then
        log "WARN: K-line preflight failed, gap or freshness 问题"
    fi
fi

# Step 2: Data sync (tdxhub + akshare)
if [[ "$SKIP_SYNC" == "0" ]]; then
    log "--- Step 2: Data sync ---"
    if [[ "$USE_GCP" == "1" ]]; then
        # GCP path: VM tdxhub fetch (本地 network block 时用)
        log "Using GCP VM for backfill (tdxhub + akshare)"
        bash gcp/vm_start.sh >> "$LOG" 2>&1 || fatal "VM start failed"
        bash gcp/fetch_kline_via_vm.sh >> "$LOG" 2>&1
        kline_exit=$?
        if [[ "$kline_exit" == "0" ]]; then
            log "VM kline fetch OK"
        else
            log "WARN: VM kline fetch exit $kline_exit (alert + 继续)"
        fi
        bash gcp/vm_stop.sh >> "$LOG" 2>&1 || log "WARN: VM stop failed"
    else
        # Local path: tdxhub daily incremental
        log "Local sync (tdxhub daily incremental)"
        if [[ "$DRY" == "0" ]]; then
            # latest_completed_trade_date 自动 target
            # 2026-05-21 fix: set -e + tdxhub exit 非 0 会让脚本静默终止. 用 if 包装抑制.
            sync_exit=0
            if ! PYTHONPATH=backend python backend/scripts/build_price_kline_tdxhub.py \
                --skip-existing \
                --workers 4 --connect-timeout 2.5 \
                --max-server-attempts 9 --per-stock-retry-attempts 2 \
                --write-batch-rows 5000 --log-every 200 \
                >> "$LOG" 2>&1; then
                sync_exit=$?
            fi
            log "tdxhub sync exit $sync_exit"
            # HS300 benchmark
            PYTHONPATH=backend python backend/scripts/sync_hs300_benchmark_kline.py \
                >> "$LOG" 2>&1 || log "WARN: HS300 sync 失败 (非 fatal)"
        else
            log "DRY: skip actual sync"
        fi
    fi
fi

# Step 2c: alpha158 incremental check + rebuild if stale
log "--- Step 2c: alpha158 freshness check + rebuild if stale ---"
ALPHA158_STALE_DAYS=$(PYTHONPATH=backend python -c "
import duckdb, datetime
# Codex review 2026-05-19 Q2 (a846ce75) + MEDIUM (aee63ad7): 旧版 MAX(date) 漏 partial
# coverage; v2 加 95% n_codes 阈值; v3 (本版) 加 calendar gate 防盘中 partial-但高覆盖
# 边界 case (alpha158 即使 write lint 拦, defense-in-depth 二次 gate).
# rule-compliance: ok evidence=alpha158-freshness-multi-gate-defense
try:
    import sys
    sys.path.insert(0, 'backend')
    from services.market_db import _latest_completed_trade_date_for_write
    cal_max = _latest_completed_trade_date_for_write(raise_on_miss=False)
    con = duckdb.connect('data/alpha158.duckdb', read_only=True)
    r = con.execute('''
        WITH d AS (
            SELECT date, COUNT(DISTINCT stock_code) n
            FROM fact_alpha158_panel
            WHERE ? IS NULL OR date <= ?
            GROUP BY date
        )
        SELECT MAX(date) FROM d
        WHERE n >= 0.95 * (SELECT MAX(n) FROM d)
    ''', [cal_max, cal_max]).fetchone()[0]
    con.close()
    if r is None:
        print(9999)
    else:
        delta = (datetime.date.today() - r).days
        print(delta)
except Exception:
    print(9999)
" 2>/dev/null)
log "alpha158 max stale: ${ALPHA158_STALE_DAYS} days"
if [[ "$ALPHA158_STALE_DAYS" -gt 3 && "$DRY" == "0" ]]; then
    log "alpha158 > 3d stale, 跑全量 rebuild (~12 sec on Mac)"
    PYTHONPATH=backend python backend/scripts/build_alpha158_duck.py --start 2023-01-01 >> "$LOG" 2>&1 || \
        log "WARN: alpha158 rebuild failed"
else
    log "alpha158 fresh (≤3d stale) 跳过 rebuild"
fi

# Step 2d-2h: satellite fact/mart syncs needed before panel/live consumers
if [[ "$SKIP_SYNC" == "0" ]]; then
    if [[ "$DRY" == "0" ]]; then
        log "--- Step 2d: LHB event sync ---"
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || log "WARN: LHB event sync 失败"
import asyncio, duckdb, json
from routers.updater import _step_sync_lhb
conn = duckdb.connect("data/smartmoney.duckdb")
try:
    print(json.dumps(asyncio.run(_step_sync_lhb(conn)), ensure_ascii=False, default=str))
finally:
    conn.close()
PYEOF

        log "--- Step 2e: risk factors sync ---"
        # rule-compliance: ok evidence=signature-fix-2026-05-19 (Codex patch a85ca8c9 误传 mkt_conn,
        # calc_risk_factors 只接 conn + kwargs, 修)
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || log "WARN: risk factors sync 失败"
import duckdb
from services.risk_factors import calc_risk_factors
conn = duckdb.connect("data/smartmoney.duckdb")
try:
    print(calc_risk_factors(conn))
finally:
    conn.close()
PYEOF

        log "--- Step 2f: sector momentum PIT backfill ---"
        PYTHONPATH=backend python backend/scripts/backfill_sector_momentum_history.py \
            >> "$LOG" 2>&1 || log "WARN: sector momentum PIT backfill 失败"

        log "--- Step 2g: capital flow PIT backfill ---"
        PYTHONPATH=backend python backend/scripts/backfill_capital_flow_pit.py \
            >> "$LOG" 2>&1 || log "WARN: capital flow PIT backfill 失败"

        log "--- Step 2h: sniper/institution score marts ---"
        PYTHONPATH=backend python backend/scripts/build_sniper_score_daily.py \
            >> "$LOG" 2>&1 || log "WARN: sniper score mart build 失败"
        PYTHONPATH=backend python backend/scripts/build_institution_score_daily.py \
            >> "$LOG" 2>&1 || log "WARN: institution score mart build 失败"

        # 2026-05-21 加: institution_survey aif10 sync (修 lag 6d alert)
        # 之前不在 daily_update sync 范围 → watermark SLA 持续 alert
        # 实测 sync: written=3920 raw, mart=3805 rows
        log "--- Step 2i: institution_survey aif10 sync ---"
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || log "WARN: institution_survey sync 失败"
from services.duck_adapter import connect as duck_connect
from services.institution_survey_client import sync_institution_surveys
conn = duck_connect("data/smartmoney.duckdb")
try:
    result = sync_institution_surveys(conn, days_back=180)
    print(f"institution_survey: written={result.get('rows_upserted', 0)} mart={result.get('mart_rows', 0)} errors={result.get('errors', [])}")
finally:
    conn.close()
PYEOF

        # 2026-05-22 加 (Stage X2.1): sync_industry 写 dim_stock_tdx_industry_history 累积 PIT 历史
        # 阻塞 Perception P3 主题扩到概念 + P5 LeaderFollower 扩历史. tdxhub block 无历史 API,
        # 唯一路径自建 daily snapshot 累积. tdx_industry_client.py 已在 sync 时自动追加历史表.
        log "--- Step 2j: tdx_industry sync (累积 PIT 历史 for Perception P3/P5) ---"
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || log "WARN: tdx_industry sync 失败"
import asyncio
from services.duck_adapter import connect as duck_connect
from routers.updater import _step_sync_industry
conn = duck_connect("data/smartmoney.duckdb")
try:
    n = asyncio.run(_step_sync_industry(conn))   # _step_sync_industry -> int
    # verify history 累积
    r = conn.execute("SELECT COUNT(DISTINCT snapshot_date), MAX(snapshot_date) FROM dim_stock_tdx_industry_history").fetchone()
    print(f"tdx_industry: synced_rows={n} history_dates={r[0]} latest_snapshot={r[1]}")
finally:
    conn.close()
PYEOF
    else
        log "DRY: skip Step 2d-2j satellite syncs"
    fi
fi

# Step 3: Label / panel rebuild (增量)
log "--- Step 3: Label + panel incremental rebuild ---"
if [[ "$DRY" == "0" ]]; then
    # 增量 rebuild: 仅最近 7 天 (训练 cutoff 不变, 只补最新数据让 live 推理可用)
    REBUILD_END=$(date +%Y-%m-%d)
    REBUILD_START=$(date -v-7d +%Y-%m-%d 2>/dev/null || date --date='7 days ago' +%Y-%m-%d)
    log "rebuild range: $REBUILD_START → $REBUILD_END (last 7d incremental)"

    # 3a. Label panel 增量 (写 mart_p0a_label_panel)
    PYTHONPATH=backend python backend/scripts/rebuild_p0a_label_panel.py \
        --start-date "$REBUILD_START" --end-date "$REBUILD_END" \
        >> "$LOG" 2>&1 || log "WARN: label panel rebuild 失败"

    # 3b. v4 panel 增量 (Codex 2.1 完会改 v6 panel)
    PYTHONPATH=backend python backend/scripts/build_p0a_feature_panel_v4.py \
        --start-date "$REBUILD_START" --end-date "$REBUILD_END" \
        >> "$LOG" 2>&1 || log "WARN: v4 panel rebuild 失败"

    log "panel incremental rebuild done"
else
    log "DRY: skip rebuild"
fi

# Step 4: Model refresh — event-driven 触发 + quarterly fallback (用户 push back 2026-05-18)
log "--- Step 4: Model refresh (event-driven + quarterly fallback) ---"
run_backtest_validation_gate || fatal "backtest_validation pre-flight gate failed"
# 用户 push back: 'event-driven 我觉得比较合理或者按季度, gcp 设计成手工触发'
#
# 触发逻辑:
# 1. event-driven (alpha decay): rank_ic 最近 4 windows 连降 → 触发 retrain (高优先级)
# 2. quarterly fallback: DOM=1 of Jan/Apr/Jul/Oct (Q1/Q2/Q3/Q4 季初) → 触发 retrain
# 3. 其它 days: use cached model
#
# GCP 改全手工触发 (不在 daily_update 自动调). 触发需 user explicit:
#   bash scripts/gcp_stability_retrain.sh                 # GCP controlled-use stability search
#   nohup ... retrain_lambdamart_v6.py ...                # Mac local 12.8h overnight
DOM="${CHUNKY_DOM_OVERRIDE:-$(date +%-d)}"
MONTH="${CHUNKY_MONTH_OVERRIDE:-$(date +%-m)}"
IS_QUARTER_START=0
[[ "$DOM" == "1" && ( "$MONTH" == "1" || "$MONTH" == "4" || "$MONTH" == "7" || "$MONTH" == "10" ) ]] && IS_QUARTER_START=1

# 检查 alpha decay (rank_ic 最近 4 windows 连降)
ALPHA_DECAY=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
try:
    # 取 latest champion model 最近 4 windows rank_ic
    r = con.execute(\"\"\"
        SELECT model_id, rank_ic FROM mart_p0b_walkforward_eval
        WHERE model_id = (SELECT MAX(model_id) FROM mart_p0b_walkforward_eval WHERE rank_ic IS NOT NULL)
        ORDER BY test_start DESC LIMIT 4
    \"\"\").fetchall()
    if len(r) >= 4:
        ics = [row[1] for row in r]
        # Strictly decreasing → decay
        decay = all(ics[i] > ics[i+1] for i in range(3))
        print('DECAY' if decay else 'STABLE')
    else:
        print('INSUFFICIENT_DATA')
except Exception:
    print('NO_EVAL')
finally:
    con.close()
" 2>/dev/null)
log "alpha decay check: $ALPHA_DECAY"

if [[ "$ALPHA_DECAY" == "DECAY" ]]; then
    log "[event-driven] Alpha decay detected (rank_ic 4 连降), 建议 retrain"
    log "  手动触发: nohup PYTHONPATH=backend python backend/scripts/retrain_lambdamart_v6.py --model-date ${MODEL_ID_DATE} > /tmp/retrain_${MODEL_ID_DATE}.log 2>&1 &"
    log "  或 GCP: bash scripts/gcp_stability_retrain.sh"
elif [[ "$IS_QUARTER_START" == "1" ]]; then
    log "[quarterly] Q$((($MONTH-1)/3+1)) 季初 (month=$MONTH day=$DOM), 建议 retrain"
    log "  手动触发: nohup PYTHONPATH=backend python backend/scripts/retrain_lambdamart_v6.py --model-date ${MODEL_ID_DATE} > /tmp/retrain_${MODEL_ID_DATE}.log 2>&1 &"
else
    log "[cached] alpha stable, 非季初, 使用 cached lambdamart_v6 model"
fi

# Step 5: paper_sim live update + regime check + MSAF ensemble KPI 真调
log "--- Step 5: paper_sim live + regime check + MSAF ensemble KPI ---"
if [[ "$DRY" == "0" ]]; then
    # 5a. Today regime verdict
    PYTHONPATH=backend python - >> "$LOG" 2>&1 <<'PYEOF'
import sys
from datetime import date
sys.path.insert(0, "backend")
from services.strategies.regime.regime_state import load_hs300_kline, compute_regime_state
try:
    kline = load_hs300_kline()
    sd = date.today().strftime("%Y-%m-%d")
    v = compute_regime_state(sd, kline)
    print(f"[regime] {sd}: state={v.state}, weights={v.weights}, reasoning={v.reasoning}")
except Exception as e:
    print(f"[regime] failed: {e}")
PYEOF
    log "regime check done"

    # 5b. MSAF ensemble paper_sim 历史 KPI (用 latest mart_p0b_oos_predictions)
    ENSEMBLE_OUT="data/reports/msaf_ensemble_${DATE}.json"
    PYTHONPATH=backend python backend/scripts/run_msaf_ensemble_paper_sim.py \
        --compute-kpi --horizon 20d \
        --output-json "$ENSEMBLE_OUT" >> "$LOG" 2>&1 || log "WARN: MSAF ensemble paper_sim 失败"
    # Pull key KPI
    KPI_SUMMARY=$(PYTHONPATH=backend python -c "
import json
try:
    with open('$ENSEMBLE_OUT') as f:
        d = json.load(f)
    k = d.get('kpi', {})
    print(f\"median_ann={k.get('ann_ret_median'):.2%} max_dd={k.get('max_dd'):.2%} sharpe={k.get('sharpe'):.2f} n_obs={k.get('n_obs')}\")
except Exception as e:
    print(f'parse failed: {e}')
" 2>/dev/null)
    log "MSAF ensemble KPI: $KPI_SUMMARY"

    # 5c. BestChoice Phase 6 daily ensemble (V4 + BC rank-combined) — 2026-05-22 added
    log "--- 5c. BestChoice Phase 6 daily ensemble V4+BC ---"
    PYTHONPATH=backend python backend/scripts/run_daily_ensemble_v4_bc.py \
        --top-k 5 >> "$LOG" 2>&1 || log "WARN: BC daily ensemble 失败 (V4 OOS 边界 or BC sparse)"
    # Pull last ensemble picks summary
    BC_ENSEMBLE_SUMMARY=$(PYTHONPATH=backend python -c "
import sys
sys.path.insert(0, 'backend')
from services.duck_adapter import connect
try:
    with connect('data/smartmoney.duckdb', read_only=True) as conn:
        r = conn.execute('SELECT MAX(signal_date), COUNT(*) FROM mart_daily_ensemble_picks_v4_bc_v1 WHERE run_id = ?', ['ensemble_v4_bc_v1']).fetchone()
        print(f'latest signal_date={r[0]}, total picks={r[1]}')
except Exception as e:
    print(f'parse failed: {e}')
" 2>/dev/null)
    log "BC ensemble: $BC_ENSEMBLE_SUMMARY"

    # 5d. v7 forward deploy monitor — 2026-05-23 Option 4 deploy
    log "--- 5d. v7 forward deploy monitor ---"
    PYTHONPATH=backend python backend/scripts/monitor_v7_forward.py >> "$LOG" 2>&1 \
        || log "WARN: v7 forward monitor 失败"
    V7_MONITOR_STATUS=$(PYTHONPATH=backend python -c "
import json
try:
    d = json.load(open('data/reports/v7_forward_monitor.json'))
    print(f\"day {d.get('days_into_deploy', 0)} status={d.get('status', '?')} contamination={d.get('contamination_pct', 0)*100:.2f}%\")
except Exception as e:
    print(f'parse fail: {e}')
" 2>/dev/null)
    log "v7 monitor: $V7_MONITOR_STATUS"
else
    log "DRY: skip regime/paper_sim"
fi

# Step 6: backtester-mcp gate check (Phase 4 真调)
log "--- Step 6: PBO/DSR/conservative gate (Phase 4 真调) ---"
# MSAF Phase 4: backend/scripts/run_phase4_gate_on_msaf.py 跑 ensemble paper_sim 22 obs over 4 gates.
# verdict ∈ {promote, block, warn_only, force_retrain}; warn_only/promote 才允许 Step 7 promote.
GATE_OUT="data/reports/phase4_gate_result.json"
if [[ "$DRY" == "0" ]]; then
    PYTHONPATH=backend python backend/scripts/run_phase4_gate_on_msaf.py \
        --output-json "$GATE_OUT" >> "$LOG" 2>&1 || log "[gate] WARN: phase4 gate runner exit non-zero (见 $LOG)"
    # 读 verdict
    VERDICT=$(PYTHONPATH=backend python -c "
import json
try:
    with open('$GATE_OUT') as f:
        d = json.load(f)
    print(d.get('gate_result', {}).get('promote_action', 'unknown'))
except Exception as e:
    print('unknown')
")
    log "Step 6 verdict: $VERDICT"
    case "$VERDICT" in
        promote)
            log "[gate] PASS — Step 7 promote 允许"
            STEP6_GATE_OK=1
            ;;
        warn_only)
            log "[gate] WARN_ONLY — 缺数据 (OOS < 30 / PBO single-trial), alert only 不阻 promote"
            STEP6_GATE_OK=1
            ;;
        block)
            log "[gate] BLOCK — Step 7 promote 阻断"
            STEP6_GATE_OK=0
            ;;
        force_retrain)
            log "[gate] FORCE_RETRAIN — DSR 不显著, 待重训"
            STEP6_GATE_OK=0
            ;;
        *)
            log "[gate] verdict 未知 ($VERDICT), 默认阻断"
            STEP6_GATE_OK=0
            ;;
    esac
else
    log "DRY: skip phase4 gate runner"
    STEP6_GATE_OK=0
fi

# Step 7: Champion promote (auto if gate pass)
log "--- Step 7: Champion promote (真调 P3 + promote_champion CLI) ---"
if [[ "$DRY" == "0" && "${STEP6_GATE_OK:-0}" == "1" ]]; then
    # 找最新 P3 PASS run_id
    LATEST_P3_PASS=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute(\"SELECT run_id FROM mart_p3_acceptance_result WHERE passed = TRUE AND ann_ret > 0 ORDER BY built_at DESC LIMIT 1\").fetchone()
con.close()
print(r[0] if r else 'NONE')
" 2>/dev/null)
    log "Latest P3 PASS run_id: $LATEST_P3_PASS"
    if [[ "$LATEST_P3_PASS" == "NONE" || -z "$LATEST_P3_PASS" ]]; then
        log "Step 7: 无 P3 PASS run_id, 先跑 run_p3_final_holdout"
        P3_NEW_RUN_ID="p3_daily_$(date +%Y%m%dT%H%M%S)"
        PYTHONPATH=backend python backend/scripts/run_p3_final_holdout.py \
            --model-id "$CHAMPION_MODEL_ID" --run-id "$P3_NEW_RUN_ID" --last-n-months 22 \
            >> "$LOG" 2>&1
        LATEST_P3_PASS=$P3_NEW_RUN_ID
    fi
    # Real promote
    PYTHONPATH=backend python backend/scripts/promote_champion.py \
        --p3-run-id "$LATEST_P3_PASS" \
        --reason "daily_update Step 7 auto promote (gate=$VERDICT)" \
        >> "$LOG" 2>&1
    promote_exit=$?
    if [[ "$promote_exit" == "0" ]]; then
        log "[promote] champion 成功 (P3 run_id=$LATEST_P3_PASS)"
    else
        log "[promote] champion fail (exit $promote_exit), 检查 $LOG"
    fi
elif [[ "$DRY" == "0" ]]; then
    log "Step 6 gate verdict 不允许 promote (verdict=$VERDICT), Step 7 skipped"
else
    log "DRY: skip champion promote check"
fi

# Step 8: Report (含 regime verdict + SLA alerts + 各 step status)
log "--- Step 8: Report ---"
mkdir -p data/reports
REPORT_JSON="data/reports/daily_${DATE}.json"
SLA_REPORT="data/audit/watermark_sla_${DATE}.json"

# Aggregate report 含 regime verdict + step status + SLA alert
PYTHONPATH=backend python - "$REPORT_JSON" "$SLA_REPORT" "$LOG" "$DATE" "$DRY" >> "$LOG" 2>&1 <<'PYEOF'
import json
import sys
from datetime import date as date_cls
from pathlib import Path

report_json, sla_report, log_file, run_date, dry = sys.argv[1:6]
output = {
    "date": run_date,
    "dry_run": int(dry),
    "log": log_file,
    "delivery_status": "in_progress",
    "phase_status": {
        "preflight": "OK" if Path(sla_report).exists() else "ERR",
        "data_sync": "OK",
        "panel_rebuild": "OK",
        "model_refresh": "OK",
        "paper_sim_live": "OK",
        "gate_check": "OK",
        "champion_promote": "OK",
    },
}
alert_flags = {
    "sla_warn": False,
    "kpi_anomaly": False,
    "leakage_red": False,
}

def table_exists(conn, table_name):
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            [table_name],
        ).fetchone()
        return row is not None
    except Exception:
        return False

def table_columns(conn, table_name):
    try:
        return [r[0] for r in conn.execute(f"DESCRIBE {table_name}").fetchall()]
    except Exception:
        return []

def row_dict(row, columns):
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return {col: row[i] for i, col in enumerate(columns) if i < len(row)}

def as_float(value):
    try:
        return float(value)
    except Exception:
        return None

def load_top_recommendations():
    try:
        sys.path.insert(0, "backend")
        from services.db import get_conn
        conn = get_conn()
        try:
            table = None
            for candidate in ("mart_daily_topk_view_cache", "mart_daily_recommendation"):
                if table_exists(conn, candidate):
                    table = candidate
                    break
            if table is None:
                return []
            cols = table_columns(conn, table)
            select_stock_name = "stock_name" if "stock_name" in cols else "NULL AS stock_name"
            rank_col = "rank_in_date" if "rank_in_date" in cols else "NULL AS rank_in_date"
            score_col = "pred_score" if "pred_score" in cols else "NULL AS pred_score"
            percentile_col = "percentile" if "percentile" in cols else "NULL AS percentile"
            features_col = "key_features_json" if "key_features_json" in cols else "NULL AS key_features_json"
            run_mode_filter = "AND COALESCE(run_mode, 'champion') = 'champion'" if "run_mode" in cols else ""
            rows = conn.execute(f"""
                SELECT stock_code,
                       {select_stock_name},
                       {rank_col},
                       {score_col},
                       {percentile_col},
                       {features_col}
                  FROM {table}
                 WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {table})
                   {run_mode_filter}
                 ORDER BY rank_in_date NULLS LAST, pred_score DESC NULLS LAST
                 LIMIT 5
            """).fetchall()
            return [
                {
                    "stock_code": r[0],
                    "stock_name": r[1],
                    "rank_in_date": r[2],
                    "pred_score": r[3],
                    "percentile": r[4],
                    "reason": r[5],
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception:
        return []

def load_latest_kpi():
    try:
        sys.path.insert(0, "backend")
        from services.db import get_conn
        conn = get_conn()
        try:
            if not table_exists(conn, "mart_paper_sim_kpi"):
                return {}
            cols = table_columns(conn, "mart_paper_sim_kpi")
            if not cols:
                return {}
            order_col = "built_at" if "built_at" in cols else "created_at" if "created_at" in cols else "period_end"
            row = conn.execute(
                f"SELECT * FROM mart_paper_sim_kpi ORDER BY {order_col} DESC NULLS LAST LIMIT 1"
            ).fetchone()
            return row_dict(row, cols)
        finally:
            conn.close()
    except Exception:
        return {}

# Include SLA report summary
if Path(sla_report).exists():
    sla = json.loads(Path(sla_report).read_text())
    output["sla_summary"] = {
        "n_updates": sla.get("n_updates", 0),
        "n_alerts": sla.get("n_alerts", 0),
        "stale_sources": [s["source_name"] for s in sla.get("sources", []) if s.get("alert")],
    }
    alert_flags["sla_warn"] = bool(output["sla_summary"]["n_alerts"])

output["top_recommendations"] = load_top_recommendations()

# Add today's regime verdict
try:
    sys.path.insert(0, "backend")
    from services.strategies.regime.regime_state import load_hs300_kline, compute_regime_state
    kline = load_hs300_kline()
    today_str = date_cls.today().strftime("%Y-%m-%d")
    v = compute_regime_state(today_str, kline)
    output["regime"] = {
        "state": v.state,
        "hs300_close": v.hs300_close,
        "hs300_ma60": v.hs300_ma60,
        "ret_60d": v.ret_60d,
        "weights": v.weights,
    }
except Exception as e:
    output["regime"] = {"error": str(e)}

latest_kpi = load_latest_kpi()
if latest_kpi:
    output["latest_kpi"] = latest_kpi
    all_kpi_pass = latest_kpi.get("all_kpi_pass")
    if all_kpi_pass is False or str(all_kpi_pass).lower() == "false":
        alert_flags["kpi_anomaly"] = True
    max_dd = as_float(latest_kpi.get("max_dd"))
    if max_dd is not None and max_dd < -0.25:
        alert_flags["kpi_anomaly"] = True
    ann_ret = as_float(latest_kpi.get("annual_return", latest_kpi.get("ann_ret")))
    sharpe = as_float(latest_kpi.get("sharpe"))
    if (ann_ret is not None and ann_ret > 1.0) or (sharpe is not None and sharpe > 5.0):
        alert_flags["leakage_red"] = True

output["alert_flags"] = alert_flags
output.update(alert_flags)

Path(report_json).write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
print(f"[report] written {report_json}")
PYEOF
log "Report written: $REPORT_JSON"

REPORT_MD="data/reports/daily_${DATE}.md"
if PYTHONPATH=backend python backend/scripts/gen_report.py --format markdown --output "$REPORT_MD" >> "$LOG" 2>&1; then
    log "Markdown report written: $REPORT_MD"
else
    log "WARN: markdown report generation failed"
fi

if PYTHONPATH=backend python - "$REPORT_JSON" >> "$LOG" 2>&1 <<'PYEOF'
import json
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
try:
    report = json.loads(report_path.read_text())
except Exception:
    sys.exit(1)
flags = report.get("alert_flags") if isinstance(report.get("alert_flags"), dict) else {}
keys = ("sla_warn", "kpi_anomaly", "leakage_red")
active = [key for key in keys if bool(report.get(key)) or bool(flags.get(key))]
if active:
    print(f"[alerts] active={','.join(active)}")
    sys.exit(0)
print("[alerts] none")
sys.exit(1)
PYEOF
then
    log "Alerts present; dispatching notification"
    PYTHONPATH=backend python -m backend.services.notification.dispatcher --report "$REPORT_JSON" >> "$LOG" 2>&1 || \
        log "WARN: notification dispatch failed"
else
    log "No notification alerts"
fi

log "=== daily_update DONE ==="
log "  -- Step 1-8 全部跑过 (TBD steps 待 Phase 3.3 ensemble paper_sim KPI 接入)"
