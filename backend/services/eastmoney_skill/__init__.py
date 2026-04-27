"""eastmoney_skill: 东方财富 datacenter-web 自封装.

工程改进 (相对 akshare 薄包装):
- Session(trust_env=False) 避代理/Surge 影响
- 显式 timeout / Referer / User-Agent
- 内置 retry (exponential backoff)
- 字段中英文双向映射

注: 妙想 F10 (aif10 / datacenter.eastmoney.com/securities) 已迁出至独立仓库
    https://github.com/dare2live/aif10-scraper, 本模块只保留 datacenter-web 通用调用.
"""

from .client import EastMoneyClient, EastMoneyError, default_client
from .datacenter import call_datacenter, fetch_all_pages

__all__ = [
    "EastMoneyClient",
    "EastMoneyError",
    "default_client",
    "call_datacenter",
    "fetch_all_pages",
]
