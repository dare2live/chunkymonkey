"""optimization — 寻参治理层 (地基-reset 后最小重建, owner=analysis/l0_bare_kline_baseline_spec_20260614.md)。

reset 删了原 10 模块 (多为策略 backtest 优化 = Tier-2/已删层)。本次只重建 L0 Tier-1 裸K线
RankIC 寻参所需的防过拟合治理 (Occam: 不复活策略机器):
  deflated_sharpe — Bailey-LdP DSR 多重比较去过拟合 (对 IC_IR = information ratio)
  plan_validator  — 搜索空间非空闸 (2026-05-26 29/34 公式白跑反例)
  formula_param_search — 受治理的网格寻参 (OOS RankIC 目标, 只读 OOS, DSR deflate)
"""
