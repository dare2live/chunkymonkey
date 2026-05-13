"""Phase γ — 股票画像子包。

聚合每只股票每日的完整画像 (基本面 + 技术面 + 估值 + 机构信号 + 公式触发),
落入 mart_stock_picture_daily, 供 v3 UI 的 STOCKS 卡片消费。

模块:
  - ddl.py                  : 5 张新表的 CREATE
  - fundamental_stage.py    : dim_stock_stage_latest.stage_reason 模板 → 6 状态
  - stage_days.py           : 当前阶段已持续天数 (numpy run-length)
  - stock_type.py           : 5 状态 primary_type 分类 (Phase γ D2)
  - valuation.py            : PE / PE 分位 / upside (Phase γ D2)
  - institution_signal.py   : 机构信号 0-100 + top N (Phase γ D2)
  - kline_latest.py         : 最新 close + chg_pct (Phase γ D2)
"""
