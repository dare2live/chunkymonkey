"""A-share periodic-report calendars: two clocks, one map.

Statutory completeness (「全体发行人应当已经披露」; **not** PIT known-at):

- Annual + interim: CSRC 《上市公司信息披露管理办法》令第226号
  (2025-03-26 公布, 2025-07-01 施行) 第十三条 —
  年报: 会计年度结束之日起四个月内 (12-31 → 次年 04-30);
  中报: 上半年结束之日起两个月内 (06-30 → 08-31).
  第十二条 现行定期报告只列年报、中报, **不再写季报**.
- Quarterly: 交易所上市规则 (上交所股票上市规则 / 深交所股票上市规则 同文) —
  每个会计年度前三个月、九个月结束后的一个月内 (03-31 → 04-30, 09-30 → 10-31);
  一季报披露不得早于上年年报.

Acquire clock: latest quarter-end that has already occurred. Filings appear
as companies announce; do not wait for the statutory completeness date.

Completeness clock (all four period types, not only H1 08-31): after that
period's deadline, missing local data is a miss (漏抓), not "still waiting".
Late issuers exist; treat source-empty vs we-empty separately.
"""
from __future__ import annotations

from typing import Optional

_DEADLINE_BY_MD = {
    "0331": lambda y: f"{y}0430",
    "0630": lambda y: f"{y}0831",
    "0930": lambda y: f"{y}1031",
    "1231": lambda y: f"{int(y) + 1}0430",
}


def _compact8(value: str | None) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def disclosure_deadline_compact(report_date: str) -> Optional[str]:
    """Report period → statutory completeness date (YYYYMMDD)."""
    compact = _compact8(report_date)
    if compact is None:
        return None
    y, md = compact[:4], compact[4:]
    fn = _DEADLINE_BY_MD.get(md)
    return fn(y) if fn else None


def disclosure_deadline_iso(report_date: str) -> Optional[str]:
    compact = disclosure_deadline_compact(report_date)
    if compact is None:
        return None
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def latest_ended_report_period(today: str) -> str:
    """Latest quarter-end that has already occurred (acquire clock)."""
    days = _compact8(today)
    if days is None:
        raise ValueError(f"unparseable today={today!r}")
    y = int(days[:4])
    for period in (f"{y}1231", f"{y}0930", f"{y}0630", f"{y}0331", f"{y - 1}1231"):
        if days >= period:
            return period
    return f"{y - 1}0930"


def is_past_completeness_deadline(report_date: str, today: str) -> bool:
    """True when this period's statutory filing window has closed.

    Q1→04-30, H1→08-31, Q3→10-31, 年报→次年 04-30. After that day, absence
    in our store is a completeness miss (漏抓), not a pending-clock skip.
    Not PIT known-at and not an acquire gate. 年报 and Q1 share 04-30;
    check each period, do not assume ``latest_statutory_complete`` covers both.
    """
    deadline = disclosure_deadline_compact(report_date)
    day = _compact8(today)
    if deadline is None or day is None:
        return False
    return day >= deadline


def latest_statutory_complete_report_period(today: str) -> str:
    """Latest period whose statutory completeness date is already <= today.

    Completeness / SLA clock. Not acquire, not PIT, not org accept.
    04-30 is shared by 年报 and Q1; this returns the later of the two (Q1).
    Use ``is_past_completeness_deadline`` per period when both must be checked.
    """
    days = _compact8(today)
    if days is None:
        raise ValueError(f"unparseable today={today!r}")
    y = int(days[:4])
    for deadline, period in (
        (f"{y}1031", f"{y}0930"),
        (f"{y}0831", f"{y}0630"),
        (f"{y}0430", f"{y}0331"),
        (f"{y}0430", f"{y - 1}1231"),
        (f"{y - 1}1031", f"{y - 1}0930"),
    ):
        if days >= deadline:
            return period
    return f"{y - 1}0930"


__all__ = (
    "disclosure_deadline_compact",
    "disclosure_deadline_iso",
    "is_past_completeness_deadline",
    "latest_ended_report_period",
    "latest_statutory_complete_report_period",
)
