"""③ 加工 (Process) — L1→L1 dim: 从清洗后数据派生维度物化。

当前唯一步: 东财行业物化 dim_stock_dc_industry (L1 dim), 在清洗 (qfq build) 之后跑。
纯数据平台重建 (2026-06-28) 后只剩这一步中性 dim 物化:
  - risk_factors 步 (services.risk_factors) U5 退役 (模块+表 fact_risk_factors 已删) → 移除死调用。
  - macd_state 步 (build_macd_state_history) U3 退役 (脚本+表 mart_macd_state_history 已删) → 移除死调用。
  - label/feature panel 早已移出 (走 alpha 验证程序, 不进数据底座流)。
策略/技术状态/风险因子等加工属未来 edge 层, 在干净平台上从零重建, 不在数据底座流。
"""
from __future__ import annotations

from .context import PipelineContext


def run_process(ctx: PipelineContext) -> None:
    ctx.log("=== ③ 加工 PROCESS (L1 dim 物化) ===")
    if ctx.dry:
        ctx.log("DRY: 跳过加工 (全是写操作)")
        return

    # 东财行业物化 dim_stock_dc_industry (+ dim_stock_dc_concept + v_dc_industry_pit)
    #   行业真相源 = 东财 (raw_tushare_dc_index/dc_member, 2.95 已 drain; 深史 PIT 走 v_sw_industry_pit)
    if not ctx.skip_sync:
        ctx.run_script("backend/scripts/build_dc_industry_view.py",
                       degraded_msg="东财行业物化失败 — serving 行业将 stale (消费方 LEFT JOIN 退化 NULL)")

    # 股票分层 dim_stock_segment_daily 增量 (B1 2026-07-02 用户定调"前置在所有策略之前":
    #   市值/换手当日分位段 + PIT 行业, 所有策略 cell/画像/筛选器的单一计算点)
    def _seg_latest():
        from services.segments import build_latest
        ctx.log(f"[segments] {build_latest()}")
    ctx.step(_seg_latest, degraded_msg="股票分层增量失败 — segment 标签将 stale (策略 cell/筛选器缺当日)")
