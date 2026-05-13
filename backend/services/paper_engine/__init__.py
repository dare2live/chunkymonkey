"""Phase δ — Paper Engine 子包: 虚拟交易闭环。

每日跑批:
  1. 读 mart_daily_recommendation 当日 topk
  2. 派生 target_weights (等权 / 分数权)
  3. 调 services.portfolio_backtest.run_portfolio_backtest()
  4. 落库 mart_paper_nav (NAV 曲线 + benchmark) + fact_paper_position (持仓事件)
  5. 调 services.prediction_outcome (已有) 算决策→后续收益
  6. 计算 mart_signal_ic (每公式每日 IC)

模块:
  - ddl.py             : 3 张新表 (mart_paper_nav / fact_paper_position / mart_signal_ic)
  - weights.py         : rank → target_weight
  - benchmarks.py      : HS300 (price_kline code='000300') + 等权基准 NAV
  - driver.py          : run_paper_day(conn, snapshot_date) 一日驱动
  - signal_ic.py       : 包装 run_feature_ablation.compute_ic + 落库
  - decision_outcome.py: 包装现有 prediction_outcome (做 mart_decision_outcome 视图/重命名)

复用现有:
  - services.portfolio_backtest.run_portfolio_backtest (456 LOC backtest engine)
  - services.prediction_outcome.calc_outcomes (= 即 mart_decision_outcome)
  - services.return_engine (forward returns)
  - scripts.run_feature_ablation.compute_ic (Spearman IC)
"""
