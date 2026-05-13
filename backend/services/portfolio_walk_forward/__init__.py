"""Phase π — Portfolio Walk-Forward Backtest package.

⚠ 终极验证: 整套系统 (寻优 → buy_signal → daily 推荐 → sizing → sell_rules)
   在历史 2023-2026 上跑 paper trading.

模块化:
  - simulator.py:   walk-forward 状态机 (NAV / 仓位 / 现金)
  - regime.py:      市场环境识别 (牛/熊/震荡, 基于 HS300 60d 收益率)
  - cash_manager.py: 动态现金管理 (STRONG_BUY 不足时空仓)
  - liquidity.py:   流动性 + 资金量过滤
  - metrics.py:     NAV → 年化/max_dd/calmar/sharpe/超额 alpha
"""
