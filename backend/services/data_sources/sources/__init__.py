"""4 个内置 source adapter.

加载顺序按 priority:
- tdxhub (10): 通达信主源
- aif10 (20): 妙想 F10 datacenter
- em_datacenter (30): 东财 datacenter-web (lhb/qfii/margin/survey/money_flow)
- akshare (99): 兜底 (trading_calendar / etf_spot)
"""
