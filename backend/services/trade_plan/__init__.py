"""Phase γ D4 — 交易计划生成。

包装 fact_stock_turtle_features 输出 (ATR + entry_level + stop_level)
+ mart_stage_formula_fitness 的胜率数据, 算 8 个 trade plan 字段:

  入场 3 价: entry_target / entry_aggressive / entry_max
  出场 3 价: exit_target_1 / exit_target_2 / exit_stop
  风险报酬: risk_reward_ratio
  持仓预期: expected_horizon_days

DDL 在 services/picture/ddl.py 的 mart_stock_trade_plan。
"""
