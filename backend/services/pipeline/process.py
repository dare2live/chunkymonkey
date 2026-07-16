"""③ 加工 (Process) — 手工管线中的现有跨 Tier 派生步骤。

本阶段当前会依次刷新：DC legacy 分类快照、Tier1 segment/context、Tier2 market sensing、
Tier1 stock form。它不是“纯数据只剩一步”，也不代表这些旧表已经满足新 contract manifest；
Phase 1/2 将按 owner contract 拆分后再收紧编排边界。
"""
from __future__ import annotations

from .context import PipelineContext


def run_process(ctx: PipelineContext) -> None:
    ctx.log("=== ③ 加工 PROCESS (L1 dim 物化) ===")
    if ctx.dry:
        ctx.log("DRY: 跳过加工 (全是写操作)")
        return

    # DC namespace 的 legacy 分类快照；不得与 SW namespace 拼成同一历史或互作 fallback。
    if not ctx.skip_sync:
        ctx.run_script("backend/scripts/build_dc_industry_view.py",
                       degraded_msg="DC namespace legacy 分类快照物化失败 — 对应展示将 stale")

    # Tier1 context: 股票分层 dim_stock_segment_daily 增量 (历史编号 B1):
    #   市值/换手当日分位段 + PIT 行业, 所有策略 cell/画像/筛选器的单一计算点)
    def _seg_latest():
        from services.segments import build_latest
        ctx.log(f"[segments] {build_latest()}")
    ctx.step(_seg_latest, degraded_msg="股票分层增量失败 — segment 标签将 stale (策略 cell/筛选器缺当日)")

    # Tier2 market sensing: mart_sector/market_pulse_daily 增量 (历史编号 B4/C4):
    #   必须在 segments 之后 — B1 表是 sw 链广度/涨跌停聚合的输入, 顺序不可反)
    def _pulse_latest():
        from services.market_pulse import build_latest
        ctx.log(f"[market_pulse] {build_latest()}")
    ctx.step(_pulse_latest, degraded_msg="Tier2 市场感知增量失败 — pulse 面板与研究上下文将 stale")

    # Tier1 state: 形态识别 fact_stock_form_daily 增量 (历史编号 B2):
    #   必须在 segments 之后 — E 轴消费 B1 的 rv_pctile/vol_regime 列, 缺列 fail loud)
    def _form_latest():
        from services.technical_states import build_latest
        ctx.log(f"[technical_states] {build_latest()}")
    ctx.step(_form_latest, degraded_msg="Tier1 形态状态增量失败 — form 标签与 Tier3 研究输入将 stale")
