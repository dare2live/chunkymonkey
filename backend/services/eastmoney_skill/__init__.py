"""eastmoney_skill: 东方财富 API 自封装 (Phase 1, 内部模块).

替代 akshare 对东财的薄包装. 工程改进:
- Session(trust_env=False) 避代理/Surge 影响
- 显式 timeout / Referer / User-Agent
- 内置 retry (exponential backoff)
- 字段中英文双向映射

后续 (Phase 2/3) 计划: 加妙想 F10 (aif10) 模块, 提取成独立 GitHub 项目.

参见 docs/model-data-optimization-discussion.md §8.
"""

from .client import EastMoneyClient, EastMoneyError, default_client
from .datacenter import call_datacenter, fetch_all_pages
from .quote import (
    fetch_fund_flow_history,
    fetch_fund_flow_latest,
)
from . import aif10  # 妙想 F10 (Phase 2): aif10.fetch_company_type / fetch_top_holders / ...

__all__ = [
    "EastMoneyClient",
    "EastMoneyError",
    "default_client",
    "call_datacenter",
    "fetch_all_pages",
    "fetch_fund_flow_history",
    "fetch_fund_flow_latest",
    "aif10",
]
