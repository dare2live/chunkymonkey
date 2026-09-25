"""aif10_scraper: 东方财富妙想 F10 全量解析项目.

字段表与接口清单的一手出处: 上游 dare2live/aif10-scraper@8326cb9 的
``600519_F10_data_source_report.md``(2026-04-27 逐栏目 DevTools 抓包, 附录 A 是完整接口清单)。
并入本仓时改: 原文写「完整 spec 见 docs/eastmoney-aif10-spec.md」, 而**那个文件在上游历史里
从未存在过**(实测 git log --all --diff-filter=A -- docs/ 只有 p6_probe.json) —— 上游这处
docstring 本身就是悬空的, 不是并入造成的。
"""

__version__ = "0.1.0"

from .client import (
    AIF10ApiError,
    AIF10BlockedError,
    AIF10Client,
    AIF10Error,
    AIF10NonJsonError,
    AIF10UnknownCodeError,
    KNOWN_RESPONSE_CODES,
    default_client,
)
from .registry import REPORTS, REPORT_BY_NAME, get_report, reports_by_module, stats
from .batch import (
    fetch_all_pages,
    fetch_all_pages_concurrent,
    fetch_all_pages_sharded,
    iter_pages,
    fetch_report,
)
from .pagination import (
    EASTMONEY_EMPTY_RESULT_CODE,
    PageLedger,
    PaginationIntegrityError,
    PaginationPolicy,
    STRICT_POLICY_FOR,
    fetch_pages_strict,
)
from .orm import (
    generate_ddl,
    generate_ddl_for_report,
    infer_schema,
    infer_column_type,
)

__all__ = [
    # client
    "AIF10ApiError",
    "AIF10BlockedError",
    "AIF10Client",
    "AIF10Error",
    "AIF10NonJsonError",
    "AIF10UnknownCodeError",
    "KNOWN_RESPONSE_CODES",
    "default_client",
    # registry
    "REPORTS",
    "REPORT_BY_NAME",
    "get_report",
    "reports_by_module",
    "stats",
    # batch
    "fetch_all_pages",
    "fetch_all_pages_concurrent",
    "fetch_all_pages_sharded",
    "iter_pages",
    "fetch_report",
    # pagination (严格引擎, 2026-09-25 刀 A)
    "EASTMONEY_EMPTY_RESULT_CODE",
    "PageLedger",
    "PaginationIntegrityError",
    "PaginationPolicy",
    "STRICT_POLICY_FOR",
    "fetch_pages_strict",
    # orm / DDL
    "generate_ddl",
    "generate_ddl_for_report",
    "infer_schema",
    "infer_column_type",
]
