"""Phase ε — 反馈闭环 / 优选追踪。

每日跑批:
  1. 收集所有"选中"事件 (daily-topk + 公式信号) → fact_stock_selection_log
  2. 算每选中日 5/10/30d forward outcome → mart_stock_selection_outcome
  3. 聚合每股 rolling stats → mart_stock_selection_summary
  4. (反馈) signal IC → 公式权重表 → 下次 daily-topk 使用

模块:
  - ddl.py             : 4 张新表 (selection_log / outcome / summary / formula_weight_history)
  - logger.py          : 单次"选中"事件入库 (纯函数 + DB I/O)
  - outcome.py         : 复用 paper_engine.outcomes 包装为 selection 通用
  - summary.py         : 聚合 30d / 90d / total per-stock
  - feedback.py        : 从 mart_signal_ic rolling IC 派生 formula_weight

复用现有:
  - services.paper_engine.outcomes (compute_forward_returns + classify_outcome)
  - services.paper_engine.signal_ic (Spearman)
"""
