#!/usr/bin/env bash
# daily_update.sh — 手动数据底座更新 (K线/sync/L1k/retention/audit)
#
# 2026-06-14 地基-reset 收口: model refresh/paper_sim/champion (旧 Step4-8) 移出, 走 alpha 验证程序
# (experiment_store, 见 analysis/alpha_validation_program_spec_20260614.md); 本脚本只碰数据底座层。
#
# 设计原则:
# 1. 不需要 Claude / Codex 干预 (纯 cron 或用户 1 click)
# 2. 失败有明确 alert (log + email/notification)
# 3. 资源自适应 (本地 Mac 优先; 大任务先登记 experiment job contract)
# 4. 数据完整性 gate (preflight 检查 K-line continuity, sync gap auto alert)
# 5. 增量更新 (不重建全量, 只追新)
#
# Usage:
#   bash scripts/daily_update.sh          # 默认: 全流程
#   bash scripts/daily_update.sh --dry    # dry-run, 不写 DB
#   bash scripts/daily_update.sh --skip-sync  # 跳数据 sync (用现有)
#
# 运行方式 (2026-06-13 用户决议: 本地未上云 + 定时不保证开机时刻在线 → 手动运行, 成熟后再上云自动跑):
#   手动: 收盘后 (15:00 + 2h 数据 publish 容缓, ~17:00 之后) 跑 `bash scripts/daily_update.sh`
#   原 launchd plist (configs/launchd/com.chunkymonkey.daily-update.plist) 已于 2026-06-12 (commit ce461328) 退役删除;
#   归档副本在 backend/scripts/launchd/ — 待上云后再恢复自动调度.
#   注意: 本脚本不覆盖 fact_feature_panel + 其下游 (drift/gpcw/prune/picture/financial_pit/holder/shareholder) —
#   这批 builder 原属旧 cron_daily.py, 迁移时未并入本脚本 (2026-06-13 体检发现的孤儿管道, 见 goal.md 数据底座节).
#
# Log: /tmp/chunkymonkey_daily_update_<YYYYMMDD>.log
#
# 流程 (数据底座 only):
#   0. experiment job contract sanity
#   1. preflight (watermark SLA + K线新鲜度)
#   2. 数据 sync (tdxhub K线/HS300 + xdxr/LHB/risk/institution_survey/tdx_industry/external_attention/
#      profit_forecast 内联 + sync_runner registry drain + watermark refresh)
#   3. L1k 中间层增量 (macd_state; signal/feature/label panel 移出走 alpha 验证程序)
#   4. storage retention plan (dry-run, append-only 防膨胀)
#   5. 报告 (data-health: SLA + regime + sync status) + degraded 汇总 + 告警送达

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# launchd/Homebrew 环境无裸 `python` (只有 python3) — 前置项目 venv bin (含 python symlink),
# 44 处 `PYTHONPATH=backend python` 一并解决; venv 解释器 = 有 FDA 的 python3.13 (TCC 链依赖)
export PATH="$REPO_ROOT/.venv/bin:$PATH"

# 统一 env 真相源 = .env (gitignored): TUSHARE token/URL (Step 2.95 drain 必需) +
# CM_TDX_SERVERS 可达池 (Step 2/2b2; 只放 plist 的话手动跑会退化死池 — 2026-06-11 实测教训)
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a; source "$REPO_ROOT/.env"; set +a
fi

DATE=$(date +%Y%m%d)
LOG="/tmp/chunkymonkey_daily_update_${DATE}.log"
# Env var override (e.g. DRY=1 SKIP_SYNC=1 bash daily_update.sh)
DRY=${DRY:-0}
SKIP_SYNC=${SKIP_SYNC:-0}
# (MODEL_ID_DATE / DOW / CHAMPION_MODEL_ID reset 删除 — model refresh/champion 步骤移出数据底座流)

# Parse args
for arg in "$@"; do
    case "$arg" in
        --dry) DRY=1 ;;
        --skip-sync) SKIP_SYNC=1 ;;
        *)
            echo "ERROR: unknown daily_update argument: $arg" >&2
            exit 2
            ;;
    esac
done

log() {
    echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"
}

fatal() {
    log "FATAL: $*"
    exit 1
}

# degraded 级失败: 继续跑, 但必须送达 (宪法第 5 条; Platform Runtime Contract 失败分级).
# 旧 || log "WARN" 吞错继续是 external_attention 14 天断流无人知的根因链第一环.
DEGRADED_FLAG="/tmp/chunkymonkey_ALERT_daily_update_degraded.flag"
step_degraded() {
    log "DEGRADED: $*"
    # || true: flag 写失败 (如 /tmp 满) 不能把 degraded 升级成链中断 (set -e 上下文)
    echo "[$(date '+%F %T')] $*" >> "$DEGRADED_FLAG" || true
}

# (run_backtest_validation_gate 函数 reset 删除 — model refresh/gate 步骤已移出 daily 数据底座流)

log "=== ChunkyMonkey daily update ${DATE} ==="
log "  dry=$DRY skip_sync=$SKIP_SYNC"
# 每次链起跑清前日 degraded flag — 本次跑完仍存在 = 本次产生的真实降级
rm -f "$DEGRADED_FLAG"

# Step 0: experiment job contract sanity (provider-neutral)
log "--- Step 0: experiment job contract ---"
PYTHONPATH=backend python - <<'PY' >> "$LOG" 2>&1
from services.experiment_jobs import load_experiment_job_contract

contract = load_experiment_job_contract()
print({
    "backends": sorted(contract.backends),
    "families": sorted(contract.families),
    "local_active": contract.backends["local"].active,
})
PY

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
    step_degraded "watermark SLA alert (见 data/audit/watermark_sla_${DATE}.json)"
elif [[ "$sla_exit" != "0" ]]; then
    # 复审 MEDIUM: 检查器本身 crash 比检查出 alert 更该送达 (旧版严重度倒挂只 log)
    step_degraded "watermark SLA 检查器 crash (exit $sla_exit) — SLA 体系失明"
fi

# 1b. K-line continuity preflight — builder (preflight_panel_build) reset 删除 (L2 panel 层);
#     K线 gap/新鲜度由 watermark SLA (1a) + Step 2.95 sync_runner 日历 gap 扫描覆盖。

# Step 2: Data sync (tdxhub + akshare)
if [[ "$SKIP_SYNC" == "0" ]]; then
    log "--- Step 2: Data sync ---"
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
        # 复审 HIGH: K 线是全链最关键路径, 失败必须送达 — 旧版只 log 即丢弃,
        # 恰好复刻"断流 4+ 日无人知"的当天静默 (用 if 不用 &&, 防 set -e 误杀)
        if [[ "$sync_exit" != "0" ]]; then
            step_degraded "tdxhub K线 sync exit $sync_exit (链继续但 K 线可能 stale)"
        fi
        # HS300 benchmark
        PYTHONPATH=backend python backend/scripts/sync_hs300_benchmark_kline.py \
            >> "$LOG" 2>&1 || step_degraded "HS300 sync 失败 (非 fatal)"
        # Step 2b2: xdxr 除权事件 sync — 热备链路 (主源 = tushare dividend/adj_factor)
        # 根因记录 2026-06-11: 旧 cron→HTTP updater 路径含 xdxr 段, 切 launchd 直跑本脚本后
        # xdxr 成孤儿步断流 17 天, 被 registry 驱动 SLA 防线抓出。纳回调度; cooldown 24h 幂等。
        log "--- Step 2b2: xdxr sync (热备链路) ---"
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || step_degraded "xdxr sync 失败 (热备链路, 主源 tushare 在 registry)"
import asyncio
from services.duck_adapter import connect  # xdxr_client 需要 dict-row 包装, 裸 duckdb 会 ValueError
from services.xdxr_client import sync_xdxr_for_codes

conn = connect("data/market.duckdb")
try:
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM price_kline_tdxhub WHERE freq='daily' "
        "AND CAST(date AS DATE) >= current_date - INTERVAL 45 DAY").fetchall()]
    st = asyncio.run(sync_xdxr_for_codes(conn, codes))
    print({k: st.get(k) for k in ("status", "total_codes", "success_codes", "rows", "failed_count")})
    total = max(1, st.get("total_codes") or len(codes))
    if (st.get("failed_count") or 0) > total * 0.2:  # >20% 失败 = 链路级故障非个股噪音
        raise SystemExit(1)
finally:
    conn.close()
PYEOF
    else
        log "DRY: skip actual sync"
    fi
fi

# Step 2c: alpha158 — 移除 (2026-06-14 地基-reset 收口)。
# 旧步骤每日检测 alpha158 stale 即 build_alpha158_duck.py 自动重建 = **重建循环** (光删 panel
# 下次 daily_update 会重造)。alpha158 是特征层 (L2_feature), reset 范围内; 旧 panel PIT 不可信已删。
# 重算契约: 验证 Alpha158 时手动用干净 PIT 管道重建 (build_alpha158_duck.py + pit_guard 核证), 不进
# daily_update 自动循环。见 analysis/model_validation_reliability_design_20260614.md §3/§7 + manifest alpha158。

# Step 2d-2h: satellite fact/mart syncs needed before panel/live consumers
if [[ "$SKIP_SYNC" == "0" ]]; then
    if [[ "$DRY" == "0" ]]; then
        log "--- Step 2d: LHB event sync ---"
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || step_degraded "LHB event sync 失败"
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
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || step_degraded "risk factors sync 失败"
import duckdb
from services.risk_factors import calc_risk_factors
conn = duckdb.connect("data/smartmoney.duckdb")
try:
    print(calc_risk_factors(conn))
finally:
    conn.close()
PYEOF

        # Step 2f/2g/2h (sector_momentum / capital_flow PIT backfill / sniper+institution score marts)
        # — builder reset 删除 (L2_feature 特征/打分层); 走 alpha 验证程序重建, 不进 daily 数据底座流。

        # 2026-05-21 加: institution_survey aif10 sync (修 lag 6d alert)
        # 之前不在 daily_update sync 范围 → watermark SLA 持续 alert
        # 实测 sync: written=3920 raw, mart=3805 rows
        log "--- Step 2i: institution_survey aif10 sync ---"
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || step_degraded "institution_survey sync 失败"
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
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || step_degraded "tdx_industry sync 失败"
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

        # 2026-05-29 加 (市场感知数据接入 P0): external_attention 快照接线
        # 反例: attention 断更 14 天 (停 2026-05-15) 没人发现, 因不在 daily_update + 不在 watermark SLA
        # (memory feedback-data-sync-silent-failure). sync 函数 external_attention.py:387 现成, 纯没接线.
        # append-only PIT 关注度/调研, 每拖一天永久丢一天历史.
        log "--- Step 2k: external_attention snapshot (累积 PIT 关注度/调研) ---"
        PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || step_degraded "external_attention sync 失败"
from services.duck_adapter import connect as duck_connect
from services.external_attention import sync_external_attention_snapshot
conn = duck_connect("data/smartmoney.duckdb")
try:
    n = sync_external_attention_snapshot(conn)
    r = conn.execute("SELECT COUNT(DISTINCT snapshot_date), MAX(snapshot_date) FROM fact_stock_attention_snapshot").fetchone()
    print(f"external_attention: rows={n} history_dates={r[0]} latest={r[1]}")
finally:
    conn.close()
PYEOF

        # 2026-05-29 加 (市场感知数据接入 P0): profit_forecast EPS 快照接线
        # 反例: launchd plist 未 load, 只跑过 1 次 (2026-05-17). 景气度 forward 因子源 (研究证实
        # 多因子行业轮动有效), immutable PIT (INSERT OR IGNORE 同日 skip).
        # 2026-06-04: 同一 daily snapshot 必须同步生成 shadow mart, 否则 mart_forecast_upside_live
        # 会 stale 并触发 blocking data-health yellow. 显式传同一个日期, 防跨午夜 raw/mart 错位.
        log "--- Step 2l: profit_forecast EPS snapshot (景气度 immutable PIT) ---"
        FORECAST_SNAPSHOT_DATE=$(date +%Y-%m-%d)
        PYTHONPATH=backend python backend/scripts/ingest_profit_forecast_snapshot.py \
            --snapshot-date "$FORECAST_SNAPSHOT_DATE" >> "$LOG" 2>&1 \
            || step_degraded "profit_forecast sync 失败"
        # Step 2m forecast_upside live shadow mart — builder reset 删除 (L2 live mart), 走 alpha 验证程序
    else
        log "DRY: skip Step 2d-2m satellite syncs"
    fi
fi

# Step 2.95: sync_registry 域日历 gap 重放 = 增量 + 修洞统一机制 (终败/漏跑/历史空洞)
# drain 只拉今日之前的确定性缺口 (当日数据多在 18:00 到位, runner 内置排除当日;
# 昨日数据在今天 17:00 这里落库, 正好赶上 panel 构建的 JOIN t-1 语义)。
# 回填链占写锁期间会显式 error → degraded 送达, 不静默; 回填完成后自动恢复。
if [[ "$SKIP_SYNC" == "0" && "$DRY" == "0" ]]; then
    log "--- Step 2.95: sync_registry drain (gap 重放即增量) ---"
    PYTHONPATH=backend python -m services.data_sources.sync_runner --all-due --drain --max-dates 30 \
        >> "$LOG" 2>&1 || step_degraded "sync_registry drain 有残余缺口或域错误 (见 log)"
fi

# Step 2.97: 源域水位刷新 (从真实表派生, 写 mart_data_source_watermark)
# 根因 (2026-06-13): 调度手动化时旧 cron_daily phase_watermarks 孤儿化 — 检查器 (Step 1
# SLA) 只读水位从不写, 刷新器无人调 → kline_daily 水位卡死 06-03 而真实表已到 06-12。
# 必须在全部数据步 (Step 2.x) 之后跑, 否则刷出来的还是旧水位。
if [[ "$SKIP_SYNC" == "0" && "$DRY" == "0" ]]; then
    log "--- Step 2.97: source watermark refresh (派生自真实表) ---"
    PYTHONPATH=backend python backend/scripts/refresh_source_watermarks.py \
        >> "$LOG" 2>&1 || step_degraded "watermark refresh 失败 — SLA 体系将持续误报 stale"
fi

# Step 3: Label / panel rebuild (增量)
log "--- Step 3: Label + panel incremental rebuild ---"
if [[ "$DRY" == "0" ]]; then
    # 增量 rebuild: 仅最近 7 天 (训练 cutoff 不变, 只补最新数据让 live 推理可用)
    REBUILD_END=$(date +%Y-%m-%d)
    REBUILD_START=$(date -v-7d +%Y-%m-%d 2>/dev/null || date --date='7 days ago' +%Y-%m-%d)
    log "rebuild range: $REBUILD_START → $REBUILD_END (last 7d incremental)"

    # 3-pre. 三件套增量 rebuild (signal_context / technical_trigger / macd_state)
    # 2026-06-11 fix (体检 HIGH daily-update-wiring): 这三表是 panel/stage-opt 下游消费的真相源,
    # 之前不在 daily_update → 每次靠人手刷 (反例: 2026-06-06 stage-opt freshness repair 全手动串行).
    # PIT 安全: 三脚本 --end 均默认 calendar-gated (latest_completed_trade_date / K线max),
    #   不传 --end 让其自 resolve, 绝不喂 wall-clock today.
    # --write-start = REBUILD_START (last-7d 替换窗口, 只删改近 7 天, 不动历史);
    # --start = 2025-01-01 给滚动计算 (signal_context 120d / formula lookback) 预热 (rule-compliance: ok evidence=warmup-window-for-rolling-calc).
    # macd 只有 --start/--end, 其 --start 同时是写窗口起点 (脚本内部自加 180d warmup), 故传 REBUILD_START.
    # Step 3-pre: signal_context / formula_signals builder reset 删除 (L2_feature);
    #   仅 macd_state (L1k kline-intermediate, 纯 OHLCV, builder 在盘) 保留为数据底座步。
    log "--- Step 3-pre: macd_state 增量 (L1k kline-intermediate) ---"
    PYTHONPATH=backend python backend/scripts/build_macd_state_history.py \
        --start "$REBUILD_START" \
        >> "$LOG" 2>&1 || step_degraded "macd_state 增量 rebuild 失败"

    # Step 3a/3b: label panel + v4 feature panel builder reset 删除 (L2_feature 层),
    #   走 alpha 验证程序 (experiment_store) 用干净 PIT 管道重建, 不进 daily 数据底座流。
    log "L1k macd 增量 done (feature/label panel 不在数据底座流)"

    # 3c. data_audit — 宪法第六条: sync 后必跑审计
    log "--- Step 3c: data_audit post-sync ---"
    PYTHONPATH=backend python -c "
import os; os.environ['DATA_AUDIT_STRICT'] = '0'
from services.data_audit import run_post_sync_audit
r = run_post_sync_audit('step3_label_panel', strict=False)
checks = r.get('checks', [])
n_pass = sum(1 for c in checks if c['status'] == 'PASS')
n_fail = len(checks) - n_pass
print(f'data_audit: {n_pass} PASS, {n_fail} FAIL')
for c in checks:
    if c['status'] != 'PASS':
        print(f'  FAIL: {c[\"name\"]}: {c[\"detail\"][:60]}')
" >> "$LOG" 2>&1 || step_degraded "data_audit 失败"
else
    log "DRY: skip rebuild"
fi

# Step 4: Storage retention plan (dry-run; append-only 防膨胀, owner=storage_retention.yaml)
log "--- Step 4: Storage retention plan (dry-run) ---"
if [[ "$DRY" == "0" ]]; then
    PYTHONPATH=backend python backend/scripts/plan_storage_retention.py \
        >> "$LOG" 2>&1 || step_degraded "retention plan 失败 (非 fatal)"
else
    log "DRY: skip retention plan"
fi

# Step 5: Report (data-health: SLA + regime verdict + sync status) + degraded 汇总 + 告警送达
# (Step 4-8 旧 model refresh / paper_sim / phase4 gate / champion promote reset 删除 — L3/L4 模型层;
#  策略验证走 alpha 验证程序 experiment_store, 不进 daily 数据底座流。)
log "--- Step 5: Report (data-health) ---"
mkdir -p data/reports data/audit
REPORT_JSON="data/reports/daily_${DATE}.json"
SLA_REPORT="data/audit/watermark_sla_${DATE}.json"

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
    "scope": "data_foundation (L0/L1/L1k/snapshot/retention)",
    "phase_status": {
        "preflight": "OK" if Path(sla_report).exists() else "ERR",
        "data_sync": "OK",
        "l1k_rebuild": "OK",
        "retention_plan": "OK",
    },
}
alert_flags = {"sla_warn": False}

# SLA summary (源新鲜度 = 数据底座的核心健康信号)
if Path(sla_report).exists():
    sla = json.loads(Path(sla_report).read_text())
    output["sla_summary"] = {
        "n_updates": sla.get("n_updates", 0),
        "n_alerts": sla.get("n_alerts", 0),
        "stale_sources": [s["source_name"] for s in sla.get("sources", []) if s.get("alert")],
    }
    alert_flags["sla_warn"] = bool(output["sla_summary"]["n_alerts"])

# regime verdict (HS300 真相源, live service — 供策略层读, 非 daily 决策)
try:
    sys.path.insert(0, "backend")
    from services.strategies.regime.regime_state import load_hs300_kline, compute_regime_state
    kline = load_hs300_kline()
    v = compute_regime_state(date_cls.today().strftime("%Y-%m-%d"), kline)
    output["regime"] = {
        "state": v.state,
        "hs300_close": v.hs300_close,
        "ret_60d": v.ret_60d,
        "weights": v.weights,
    }
except Exception as e:
    output["regime"] = {"error": str(e)}

output["alert_flags"] = alert_flags
output.update(alert_flags)
Path(report_json).write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
print(f"[report] written {report_json}")
PYEOF
log "Report written: $REPORT_JSON"

# 告警送达 (SLA stale = 数据断流, 必须送达 — 反例: external_attention 14 天断流无人知)
if PYTHONPATH=backend python - "$REPORT_JSON" >> "$LOG" 2>&1 <<'PYEOF'
import json
import sys
from pathlib import Path

try:
    report = json.loads(Path(sys.argv[1]).read_text())
except Exception:
    sys.exit(1)
flags = report.get("alert_flags") if isinstance(report.get("alert_flags"), dict) else {}
active = [k for k in ("sla_warn",) if bool(report.get(k)) or bool(flags.get(k))]
if active:
    print(f"[alerts] active={','.join(active)}")
    sys.exit(0)
print("[alerts] none")
sys.exit(1)
PYEOF
then
    log "Alerts present; dispatching notification"
    PYTHONPATH=backend python -m backend.services.notification.dispatcher --report "$REPORT_JSON" >> "$LOG" 2>&1 || \
        step_degraded "notification dispatch failed"
else
    log "No notification alerts"
fi

# degraded 汇总送达: flag 仍在 = 本次有降级步 (链 exit 0 不经 wrapper 告警, 必须自己送)
if [[ -f "$DEGRADED_FLAG" ]]; then
    n_degraded=$(wc -l < "$DEGRADED_FLAG" | tr -d ' ')
    log "DEGRADED SUMMARY: 本次 $n_degraded 步降级 (明细 $DEGRADED_FLAG):"
    tee -a "$LOG" < "$DEGRADED_FLAG"
    osascript -e "display notification \"daily_update ${n_degraded} 步降级, 详见 ALERT flag\" with title \"ChunkyMonkey degraded\"" 2>/dev/null || true
else
    log "degraded: 0 步"
fi

log "=== daily_update DONE (数据底座: preflight / K线+sync / L1k macd / retention / audit) ==="
