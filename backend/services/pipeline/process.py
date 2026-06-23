"""③ 加工 (Process) — L1→L1k/L2: 从清洗后数据派生维度/特征/中间层。

旧 daily_update.sh DERIVE 阶段 + Step 3-pre:
  Step 2.96b risk_factors (L2) / 2.96c 东财行业物化 dim_stock_dc_industry (L1 dim) / 3-pre macd_state (L1k)。
全部在清洗 (qfq build) 之后跑 (否则算 stale 数据 — Gap2 解耦修的原顺序 bug)。
label/feature panel 已 reset 移出 (走 alpha 验证程序, 不进数据底座流)。
"""
from __future__ import annotations

from datetime import date, timedelta

from .context import PipelineContext


def run_process(ctx: PipelineContext) -> None:
    ctx.log("=== ③ 加工 PROCESS (L1→L1k/L2 派生) ===")
    if ctx.dry:
        ctx.log("DRY: 跳过加工 (全是写操作)")
        return

    # Step 2.96b: risk_factors (从 smartmoney 同步后数据算风险因子 = 加工; 在 qfq build 后)
    if not ctx.skip_sync:
        ctx.step(_calc_risk_factors, degraded_msg="risk factors 加工失败")

    # Step 2.96c: 东财行业物化 dim_stock_dc_industry (+ dc_concept + v_dc_industry_pit)
    #   行业真相源 = 东财 (raw_tushare_dc_index/dc_member, 2.95 已 drain; 深史 PIT 走 v_sw_industry_pit)
    if not ctx.skip_sync:
        ctx.run_script("backend/scripts/build_dc_industry_view.py",
                       degraded_msg="东财行业物化失败 — serving 行业将 stale (消费方 LEFT JOIN 退化 NULL)")

    # Step 3-pre: macd_state 增量 (L1k kline-intermediate, 纯 OHLCV)
    #   --start = 近 7 天替换窗口 (脚本内部自加 180d warmup; --end 自 resolve 为 calendar-gated 防 wall-clock)
    rebuild_start = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")  # rule-compliance: ok evidence=近7天写替换窗口start; macd --end 自解析calendar-gated, 此为写窗口非latest-trade-date
    ctx.log(f"  macd 增量窗口 start={rebuild_start} (近7天增量, 不动历史)")
    ctx.run_script("backend/scripts/build_macd_state_history.py",
                   ["--start", rebuild_start], degraded_msg="macd_state 增量 rebuild 失败")


def _calc_risk_factors() -> None:
    import duckdb
    from services.risk_factors import calc_risk_factors
    from .context import db_path
    # rule-compliance: ok evidence=数据模块管线 member; calc_risk_factors 需 raw duckdb conn, 路径走 manifest
    conn = duckdb.connect(db_path("smartmoney"))
    try:
        print(calc_risk_factors(conn))
    finally:
        conn.close()
