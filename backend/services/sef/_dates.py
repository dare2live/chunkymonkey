"""日期字符串统一化。

research_holding_chains 用 YYYYMMDD，price_kline 用 YYYY-MM-DD。
SEF 内部统一使用 YYYY-MM-DD (ISO)，与 price_kline 对齐。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def to_iso(d: Optional[str]) -> Optional[str]:
    """把任意格式的日期字符串转 YYYY-MM-DD。None/'' → None。"""
    if not d:
        return None
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def to_compact(d: Optional[str]) -> Optional[str]:
    """YYYYMMDD 形式，用于与 research_* 旧列对齐。"""
    iso = to_iso(d)
    if not iso:
        return None
    return iso.replace("-", "")
