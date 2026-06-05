"""Built-in source adapters.

按 priority 顺序:
- tdxhub  (10) 主源: 通达信 K 线/财务/行业/板块
- aif10   (20) 妙想: tdxhub 没的 (估值分位/股东户数/同行排名/一致预期 等)
- tushare (30) TuShare Pro: token-backed structured probes
- akshare (99) 兜底: 含真 ak.X (交易日历/ETF 等)
"""
