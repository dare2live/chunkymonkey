"""① 获取 (Acquire) — 纯采集: 只下载/同步外部 vendor 数据进 raw/L0, 不计算。

ACQUIRE 阶段步骤:
  LHB / institution_survey / holders_aif10 / aif10 capabilities / QFII / org_holding / sync_runner drain。
  (HS300 benchmark 2026-06-28 退役 akshare 备援步: 主源=tushare raw_tushare_index_daily 000300 走
   sync_runner registry 同步, 旧 akshare→price_kline 备援脚本删; profit_forecast/external_attention/
   xdxr 热备早退役: 通达信全删 + 复权走 tushare adj_factor)
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

    # HS300 benchmark K线: 主源 = tushare raw_tushare_index_daily 000300 (sync_runner registry 同步,
    #   下方 drain 覆盖)。2026-06-28 删旧 akshare→price_kline 备援步 (akshare 源退役)。
    # xdxr 除权 sync 已移除 (2026-06-28 重建: tdx 热备退役, 复权走 tushare adj_factor)

    # Step 2d: LHB event sync
    ctx.step(_sync_lhb, degraded_msg="LHB event sync 失败")

    # Step 2i: institution_survey aif10 sync
    ctx.step(_sync_institution_survey, degraded_msg="institution_survey sync 失败")

    # Step 2i2: 十大流通股东 aif10 增量 (主源, 替退役中的 tdxhub; 按披露日只拉新披露股)
    ctx.step(lambda: _sync_holders_aif10(ctx), degraded_msg="holders aif10 sync 失败")

    # Step 2i3: aif10 估值分位/同行估值 (LIVE, v3_picture 消费; 2026-06-24 迁自旧 updater)
    ctx.step(_sync_aif10_capabilities, degraded_msg="aif10 capability sync 失败")

    # Step 2j: QFII 季度持股 (外资维度; 2026-06-24 迁自旧 updater)
    ctx.step(_sync_qfii, degraded_msg="QFII sync 失败")

    # Step 2j2: 机构持仓明细 aif10 (非公募机构分桶; 2026-06-24 aif10 例外扩展, 替退役 tdx F10 控股股东表)
    ctx.step(_sync_org_holding, degraded_msg="org_holding aif10 sync 失败")

    # Step 2k (external_attention 快照) 已退役 2026-06-27 (通达信全删 M4: akshare 东财人气/关注度退役, 用户决cut, 无tushare等价=永久丢):
    #   消费侧 scoring 外部关注 boost/池升级/crowding penalty 优雅降级 (external_attention_score→None)。

    # Step 2l (profit_forecast EPS 快照) 已退役 2026-06-27 (通达信全删 M4: akshare 退役, 用户决cut):
    #   raw_profit_forecast_snapshot_daily 0 live 读者 (snapshot 设计防leakage但无消费); 档B 若需景气度走 tushare forecast/report_rc。

    # Step 2.95: sync_registry 域日历 gap 重放 = 增量 + 修洞统一机制 (终败/漏跑/历史空洞)
    _sync_registry_drain(ctx)


# ── 步骤实现 (in-process, 直调 service) ──────────────────────────


def _sync_lhb() -> None:
    import asyncio, duckdb, json
    from services.lhb_client import sync_lhb_incremental  # 2026-06-24 解耦: 直调 service, 不再依赖 routers.updater
    from .context import db_path
    # rule-compliance: ok evidence=数据模块管线 member; sync_lhb_incremental 需 raw duckdb conn (非 dict-row adapter), 路径走 manifest
    conn = duckdb.connect(db_path("smartmoney"))
    try:
        print(json.dumps(asyncio.run(sync_lhb_incremental(conn)), ensure_ascii=False, default=str))
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
    """十大流通股东 aif10 增量 (主源). 水位驱动: 扫存量 MAX(披露日) 之后有新披露的股, 无 wall-clock."""
    from services.db import get_conn
    from services.holders_aif10 import sync_holders_aif10_incremental
    conn = get_conn()
    try:
        result = sync_holders_aif10_incremental(conn)
        print(f"holders_aif10: watermark={result.get('watermark')} "
              f"affected={result.get('affected_stocks', 0)} rows={result.get('rows_written', 0)} "
              f"exits={result.get('exit_rows', 0)} errors={result.get('errors', [])[:3]}")
    finally:
        conn.close()


def _sync_aif10_capabilities() -> None:
    """aif10 估值分位/同行估值 (LIVE, v3_picture 消费). sync_capability 自管连接, 直调."""
    from services.aif10_capability_client import sync_capability
    # forecast_consensus 已 deprecated (走 pipeline profit_forecast); 只迁 v3_picture 在用的 2 个
    for cap in ("valuation_quantile", "peer_valuation"):
        try:
            r = sync_capability(cap)
            print(f"aif10/{cap}: rows={r.get('rows', 0)} table={r.get('raw_table')}")
        except Exception as e:  # noqa: BLE001 — capability 失败不阻塞主流程 (degraded)
            print(f"aif10/{cap} 失败: {type(e).__name__}: {str(e)[:80]}")


def _sync_qfii() -> None:
    """QFII 季度持股增量 (外资维度). 水位=最近已披露季度末, 已有则跳过."""
    import asyncio
    from services.duck_adapter import connect as duck_connect
    from services.qfii_client import sync_qfii_incremental
    from .context import db_path
    conn = duck_connect(db_path("smartmoney"))
    try:
        import json
        print(json.dumps(asyncio.run(sync_qfii_incremental(conn)), ensure_ascii=False, default=str))
    finally:
        conn.close()


def _sync_org_holding() -> None:
    """机构持仓明细 aif10 季度增量 (非公募机构分桶). 水位=最近足量披露季度末, 已有则跳过."""
    import asyncio
    import json
    from services.duck_adapter import connect as duck_connect
    from services.org_holding_aif10 import sync_org_holding_incremental
    from .context import db_path
    conn = duck_connect(db_path("smartmoney"))
    try:
        print(json.dumps(asyncio.run(sync_org_holding_incremental(conn)), ensure_ascii=False, default=str))
    finally:
        conn.close()


# _sync_external_attention 已退役 2026-06-27 (通达信全删 M4: akshare external_attention.py 物删, 用户决cut)


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
