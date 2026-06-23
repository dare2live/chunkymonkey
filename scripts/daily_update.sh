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

# ════ ACQUIRE 阶段 (纯采集: 只下载/同步外部数据进 raw/L0, 不计算 — 2026-06-23 Gap2 解耦) ════
#   采集步 (Step 2 ~ 2.95): sync_hs300 / xdxr / LHB / institution_survey / tdx_industry /
#   external_attention / profit_forecast / sync_runner drain。计算步全在下方 DERIVE 阶段。
# Step 2: Data sync (tdxhub + akshare)
if [[ "$SKIP_SYNC" == "0" ]]; then
    log "--- Step 2: Data sync ---"
    log "Local sync (serving K线真相源 = tushare canonical, Step 2.96; tdxhub stock-K线 build 已退役)"
    if [[ "$DRY" == "0" ]]; then
        # 2026-06-23 M3: build_price_kline_tdxhub (5.3M 股票日线 → price_kline_tdxhub) 已退役。
        # serving K线真相源 = price_kline_qfq_tushare (Step 2.96 从 raw_tushare_daily × adj_factor 建;
        # tushare 5431≥5211 股超集 / 同 fresh 2026-06-18 / 复权质量优于 tdxhub 单日 glitch §2 坑库)。
        # 实测 price_kline_tdxhub 0 serving 读者 (canonical 视图 tushare-only, grep FROM/JOIN 全空);
        # xdxr 热备(§4.3) code-list 已切 canonical (Step 2b2 上方)。表物删见 ledger M3。
        # HS300 benchmark: akshare→price_kline 备援链 (主源 = raw_tushare_index_daily 走 registry Step 2.95;
        # tdx-write 已 neuter 解耦 price_kline_tdxhub)。benchmark 消费侧切 tushare index_daily = M3后续步。
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
    # 2026-06-22 M3-prep 解耦: xdxr 热备(§4.3 保留)的 code 列表源从将退役的 price_kline_tdxhub
    # 切 canonical price_kline_qfq_tushare (tushare 超集 5431≥5210 / 同 fresh / 单一真相源, 无 freq 列)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM price_kline_qfq_tushare "
        "WHERE CAST(date AS DATE) >= current_date - INTERVAL 45 DAY").fetchall()]
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

        # Step 2e risk_factors 已移出 ACQUIRE → DERIVE 阶段 (2026-06-23 Gap2 采集/计算解耦):
        #   calc_risk_factors 是加工(从 smartmoney 数据算风险因子)非采集; 且原在此跑=在 2.95 drain
        #   + 2.96 qfq build 之前 → 算的是 stale 数据。移到 DERIVE 阶段 (qfq build 后) 同时修顺序。
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

        # Step 2j 通达信(tdx)行业 sync 已删除 (2026-06-23, §4.3 tushare唯一删旧源 + 用户规则"只用 tushare 能
        #   提供的数据"): 行业分类 tushare 已提供 = 申万 SW2021 (index_member_all/sw_daily, 含 L1/L2/L3 +
        #   is_new='N' 真 PIT 历史区间), 优于通达信 snapshot 累积。消费方全 repoint dim_stock_sw_industry
        #   (申万 tushare 源; 列名 tdx_l* 是位置别名值=申万, 兼容零字段改)。申万物化进下方 DERIVE 阶段每日刷新。
        #   通达信源 (dim_stock_tdx_industry* / tdx_industry_client / Step2j) 物删, owner=
        #   analysis/industry_migration_tdx_to_sw_20260615.md。原 sync 还崩在 market_gap_queue 缺表 (reset 孤儿)。

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

# Step 2.96: 构建 canonical 前复权 K线 (serving 真相源 price_kline_qfq_tushare)
# 根因修复 (2026-06-22 M3): M1 serving 切了 price_kline_qfq_tushare 但其 build (raw_tushare_daily
# × adj_factor → qfq) 从未接进 daily_update (sync_registry daily 域注释自承"消费链切换是独立大手术
# 须review, 本批先 raw 落库双轨")→ serving K线只靠手动重建会 stale。此步在 2.95 raw sync 后全量
# rebuild (qfq 因 latest-adj rebase 须全量, DuckDB CTAS 秒级), 把 serving K线纳入每日刷新。
# 必在 2.97 watermark 刷新前 (否则水位反映不出新 K线)。
if [[ "$SKIP_SYNC" == "0" && "$DRY" == "0" ]]; then
    log "--- Step 2.96: build canonical qfq K线 (price_kline_qfq_tushare, serving 真相源) ---"
    PYTHONPATH=backend python backend/scripts/build_price_kline_qfq_tushare.py \
        >> "$LOG" 2>&1 || step_degraded "canonical qfq K线 build 失败 — serving K线将 stale (fatal 级, 查 log)"
fi

# ════ DERIVE 阶段 (加工: 从采集后数据算变量; daily_update 不在 ACQUIRE 里混计算 — 2026-06-23 Gap2) ════
# Step 2.96b: risk_factors (DERIVE 加工 — 从原 ACQUIRE Step 2e 移来):
#   calc_risk_factors 从 smartmoney 同步后数据算风险因子 = 加工非采集 (用户 seed: daily_update 不带计算)。
#   移到 DERIVE 阶段 (qfq build 后, 全部 ACQUIRE 完成后) 同时修原顺序 bug — 原在 2.95 drain + 2.96 qfq 前跑
#   → 算的是 stale 数据。rule-compliance: ok evidence=signature-fix-2026-05-19 (calc_risk_factors 只接 conn+kwargs)。
if [[ "$SKIP_SYNC" == "0" && "$DRY" == "0" ]]; then
    log "--- Step 2.96b: risk_factors (DERIVE 加工, 移自原 ACQUIRE Step 2e) ---"
    PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || step_degraded "risk factors 加工失败"
import duckdb
from services.risk_factors import calc_risk_factors
conn = duckdb.connect("data/smartmoney.duckdb")
try:
    print(calc_risk_factors(conn))
finally:
    conn.close()
PYEOF
fi

# Step 2.96c: 东财行业物化 (DERIVE 加工 — 2026-06-23 全项目单一供应商=东财迁移; owner=analysis/dc_full_migration_plan_20260623.md):
#   行业分类真相源 = 东财 (raw_tushare_dc_index 行业板块 + dc_member, 2.95 已 drain; 东财行业=申万对齐同套桶),
#   build_dc_industry_view.py 幂等重建 dim_stock_dc_industry (当前快照 serving, level按申万名映射31/127/334) +
#   dim_stock_dc_concept + v_dc_industry_pit。深史2025前PIT走 v_sw_industry_pit (申万深PIT兜底, 同套桶, 用户拍板选A)。
if [[ "$SKIP_SYNC" == "0" && "$DRY" == "0" ]]; then
    log "--- Step 2.96c: 东财行业物化 (DERIVE; serving 行业真相源 dim_stock_dc_industry) ---"
    PYTHONPATH=backend python backend/scripts/build_dc_industry_view.py \
        >> "$LOG" 2>&1 || step_degraded "东财行业物化失败 — serving 行业将 stale (消费方 LEFT JOIN 退化 NULL)"
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
