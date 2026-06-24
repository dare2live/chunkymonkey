"""① 获取 (Acquire) — 纯采集: 只下载/同步外部 vendor 数据进 raw/L0, 不计算。

旧 daily_update.sh ACQUIRE 阶段 Step 2 ~ 2.95:
  HS300 K线 / xdxr 热备 / LHB / institution_survey / external_attention / profit_forecast / sync_runner drain。
skip_sync=1 跳整个阶段; dry=1 只跑只读不写。
"""
from __future__ import annotations

from .context import PipelineContext


def run_acquire(ctx: PipelineContext) -> None:
    if ctx.skip_sync:
        ctx.log("=== ① 获取 ACQUIRE: SKIP (--skip-sync) ===")
        return
    ctx.log("=== ① 获取 ACQUIRE (纯采集 →L0, 不计算) ===")
    if ctx.dry:
        ctx.log("DRY: 跳过实际 sync (获取阶段全是写操作)")
        return

    # Step 2: HS300 benchmark K线 (akshare→price_kline 备援; 主源 raw_tushare_index_daily 走 Step 2.95 registry)
    ctx.run_script("backend/scripts/sync_hs300_benchmark_kline.py",
                   degraded_msg="HS300 sync 失败 (非 fatal)")

    # Step 2b2: xdxr 除权事件 sync (热备链路, 主源 = tushare dividend/adj_factor)
    ctx.step(_sync_xdxr, degraded_msg="xdxr sync 失败 (热备链路, 主源 tushare 在 registry)")

    # Step 2d: LHB event sync
    ctx.step(_sync_lhb, degraded_msg="LHB event sync 失败")

    # Step 2i: institution_survey aif10 sync
    ctx.step(_sync_institution_survey, degraded_msg="institution_survey sync 失败")

    # Step 2i2: 十大流通股东 aif10 增量 (主源, 替退役中的 tdxhub; 按披露日只拉新披露股)
    ctx.step(lambda: _sync_holders_aif10(ctx), degraded_msg="holders aif10 sync 失败")

    # Step 2k: external_attention 快照 (累积 PIT 关注度/调研; 反例 14 天断流无人知)
    ctx.step(_sync_external_attention, degraded_msg="external_attention sync 失败")

    # Step 2l: profit_forecast EPS 快照 (景气度 immutable PIT)
    import datetime as _dt
    snap = f"{ctx.date[:4]}-{ctx.date[4:6]}-{ctx.date[6:]}"
    ctx.run_script("backend/scripts/ingest_profit_forecast_snapshot.py",
                   ["--snapshot-date", snap], degraded_msg="profit_forecast sync 失败")

    # Step 2.95: sync_registry 域日历 gap 重放 = 增量 + 修洞统一机制 (终败/漏跑/历史空洞)
    _sync_registry_drain(ctx)


# ── 步骤实现 (in-process, 直调 service) ──────────────────────────

def _sync_xdxr() -> None:
    import asyncio
    from services.duck_adapter import connect  # xdxr_client 需 dict-row 包装
    from services.xdxr_client import sync_xdxr_for_codes
    from .context import db_path
    conn = connect(db_path("market"))
    try:
        # code 列表源 = canonical price_kline_qfq_tushare (M3 后切, tushare 超集单一真相源)
        # xdxr 同步范围 = 近45日有交易的 code 集 (回溯窗口非 end-date; 非策略universe — 除权事件adj完整性反需含ST/退市, §4.5污染指GT/backtest不适用)
        _xdxr_code_sql = "SELECT DISTINCT code FROM price_kline_qfq_tushare WHERE CAST(date AS DATE) >= current_date - INTERVAL 45 DAY"  # rule-compliance: ok evidence=xdxr除权sync回溯窗口, 非策略universe/非end-date
        codes = [r[0] for r in conn.execute(_xdxr_code_sql).fetchall()]
        st = asyncio.run(sync_xdxr_for_codes(conn, codes))
        print({k: st.get(k) for k in ("status", "total_codes", "success_codes", "rows", "failed_count")})
        total = max(1, st.get("total_codes") or len(codes))
        if (st.get("failed_count") or 0) > total * 0.2:  # >20% 失败 = 链路级故障非个股噪音
            raise RuntimeError(f"xdxr 失败率 {st.get('failed_count')}/{total} > 20%")
    finally:
        conn.close()


def _sync_lhb() -> None:
    import asyncio, duckdb, json
    from routers.updater import _step_sync_lhb
    from .context import db_path
    # rule-compliance: ok evidence=数据模块管线 member; _step_sync_lhb 需 raw duckdb conn (非 dict-row adapter), 路径走 manifest
    conn = duckdb.connect(db_path("smartmoney"))
    try:
        print(json.dumps(asyncio.run(_step_sync_lhb(conn)), ensure_ascii=False, default=str))
    finally:
        conn.close()


def _sync_institution_survey() -> None:
    from services.duck_adapter import connect as duck_connect
    from services.institution_survey_client import sync_institution_surveys
    from .context import db_path
    conn = duck_connect(db_path("smartmoney"))
    try:
        result = sync_institution_surveys(conn, days_back=180)
        print(f"institution_survey: written={result.get('rows_upserted', 0)} "
              f"mart={result.get('mart_rows', 0)} errors={result.get('errors', [])}")
    finally:
        conn.close()


def _sync_holders_aif10(ctx) -> None:
    """十大流通股东 aif10 增量 (主源). since_date 从 ctx.date 推 (注入非 wall-clock)."""
    import datetime as _dt
    from services.db import get_conn
    from services.holders_aif10 import sync_holders_aif10_incremental
    # 回溯窗口: ctx.date - 45 天 (catch 近期披露 + 漏跑日); ctx.date 由 run.py 注入
    run_d = _dt.datetime.strptime(ctx.date, "%Y%m%d")  # evidence: ctx.date 注入非 wall-clock (run.py 防跨午夜)
    since = (run_d - _dt.timedelta(days=45)).strftime("%Y-%m-%d")  # evidence: holder披露增量回溯窗口45天
    conn = get_conn()
    try:
        result = sync_holders_aif10_incremental(conn, since_date=since)
        print(f"holders_aif10: affected={result.get('affected_stocks', 0)} "
              f"rows={result.get('rows_written', 0)} exits={result.get('exit_rows', 0)} "
              f"errors={result.get('errors', [])[:3]}")
    finally:
        conn.close()


def _sync_external_attention() -> None:
    from services.duck_adapter import connect as duck_connect
    from services.external_attention import sync_external_attention_snapshot
    from .context import db_path
    conn = duck_connect(db_path("smartmoney"))
    try:
        n = sync_external_attention_snapshot(conn)
        r = conn.execute("SELECT COUNT(DISTINCT snapshot_date), MAX(snapshot_date) "
                         "FROM fact_stock_attention_snapshot").fetchone()
        print(f"external_attention: rows={n} history_dates={r[0]} latest={r[1]}")
    finally:
        conn.close()


def _sync_registry_drain(ctx: PipelineContext) -> None:
    """sync_runner --all-due --drain (module 调用, subprocess 隔离)。"""
    import subprocess, sys as _sys
    from .context import REPO
    cmd = [_sys.executable, "-m", "services.data_sources.sync_runner",
           "--all-due", "--drain", "--max-dates", "30"]
    ctx.log(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=ctx._subprocess_env())
    if ctx._log_fh:
        ctx._log_fh.write((proc.stdout or "") + (proc.stderr or "")); ctx._log_fh.flush()
    if proc.returncode != 0:
        ctx.degraded("sync_registry drain 有残余缺口或域错误 (见 log)")
