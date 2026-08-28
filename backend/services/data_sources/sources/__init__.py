"""Built-in source adapters.

Physical checkouts: backend/config/sibling_repos.yaml (miaoxiang / tdxhub / fuyao).
- tushare  TuShare Pro via tinyshare (K线/财务/行业/资金流/龙虎榜 top_list/调研 stk_surv)
- miaoxiang 东财 aif10 datacenter (十大流通股东/QFII/机构持仓明细)
- fuyao    同花顺官方 REST/dump（FuyaoSource.fetch_raw；不进他们的 marketdb DuckDB；qfq 禁 SSOT）
- tdxhub   通达信协议层（未复权日 K 对账；MAC capital_flow 为独立 vendor 不平衡代理；qfq 禁止当 SSOT）
"""
