"""Phase η+ — 组合仓位 / 风险偏好 / Kelly + Wilson 分仓器。

模块:
  - wilson.py        — Wilson Score 下界 (修正小样本胜率)
  - kelly.py         — Kelly Criterion 派生 + fractional Kelly
  - profiles.py      — 3 risk profile (短/中/长) 参数表
  - sizing.py        — 主入口: 信号 + 历史 metrics + profile → 推荐仓位
  - sell_rules.py    — 卖出规则 (trailing stop / hp 到期 / 止损)
"""
