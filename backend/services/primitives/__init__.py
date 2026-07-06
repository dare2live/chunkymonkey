"""Phase ε+ §3.4 — 原 10 项基础设施 (历史枚举, 现 9 项存活)。

每项 1 张表 + 数据来源 + 消费者 (开发手册 §3.4):
  1. dim_price_limit_rules         涨跌停规则
  2. dim_market_segment            市场细分
  3. dim_trading_rule              T+1 / 手数 / tick
  4. dim_fee_schedule              佣金 / 印花税 / 过户费
  5. dim_trading_session           盘口时段
  6. fact_daily_price_status       一字板 / 涨跌停 / 停牌
  7. dim_liquidity_threshold + fact_stock_liquidity_daily 流动性
  8. (已退役) dim_listing_status   退市风险 — 2026-07-07 随 dim_all_ever_listed 一并整表退役
     (0 业务读者, 唯一 builder 无生产调用方; 见 PROJECT_INDEX.md 决策收口); DDL 早于此前
     §9 拆库已迁出本模块, 此条目仅留历史编号占位。
  9. dim_style_factor + fact_stock_style_daily 风格因子
  10. fact_stock_market_cap_daily  市值

包结构:
  - ddl.py        : 10 张表的 CREATE
  - seed.py       : 规则类 dim_* 写死的 seed 数据
  - price_status.py : 算 fact_daily_price_status (推自 K 线 + price_limit_rules)
"""
