"""aif10_scraper: 东方财富妙想 F10 全量解析项目.

完整 spec 见 docs/eastmoney-aif10-spec.md.
"""

__version__ = "0.1.0"

from .client import AIF10Client, default_client
from .registry import REPORTS, REPORT_BY_NAME, get_report, reports_by_module, stats
from .batch import (
    fetch_all_pages,
    fetch_all_pages_concurrent,
    iter_pages,
    fetch_report,
)
from .orm import (
    generate_ddl,
    generate_ddl_for_report,
    infer_schema,
    infer_column_type,
)

__all__ = [
    # client
    "AIF10Client",
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
    "iter_pages",
    "fetch_report",
    # orm / DDL
    "generate_ddl",
    "generate_ddl_for_report",
    "infer_schema",
    "infer_column_type",
]
