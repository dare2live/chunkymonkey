"""十大流通股东按公告日取数账本 (刀 B1; spec_holders_pagination.md §4.4)。

``holders_notice_fetch_ledger``: 每个公告日分区每次取数的结果, append-only,
单 writer = 本模块。它取代「MAX(notice_date) 水位」: 到期集合不再是
"水位之后的日子", 而是「日历 [exposure_start .. provider_max] 减去已 settled
的日子」 —— 失败的日子永远留在到期集合里, 谁都盖不掉谁 (R1/R3)。

settled 的定义 (六条性质见 :func:`settled_notice_days` docstring, 各有一条隔离
用例, B18 族):
    存在一行 scope='day' AND outcome IN ('complete','empty')
    AND COALESCE(missing_rows, 0) = 0
    AND (outcome != 'empty' OR COALESCE(local_rows_before, 0) = 0)   -- V1
    AND 取数上海日历日 >= 公告日 + settle_days

第四行 (V1, spec_holders_pagination.md §17.3) 是对基础判据的收紧: 若某天本地
已有行 (之前的取数已经落过), 而这一次取数返回 9201 (空), 那不是"这一天什么都
没有" —— 是供应商这次抽风; 不应当被判定为"完整"。``local_rows_before`` 这一列
本来就在账本 schema 里, 此前没有被 :func:`settled_notice_days` 用到。

不建 VIEW: 今天没有消费方 (spec §4.1), as-of 语义写在
``services/holders_aif10.py`` 模块 docstring 与 ``aif10_scraper/VENDOR.md``。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

LEDGER_TABLE = "holders_notice_fetch_ledger"

_RUN_KINDS = ("daily", "reland")
_SCOPES = ("day", "stock")
_OUTCOMES = ("complete", "empty", "failed")

_LEDGER_COLUMNS = (
    "ledger_id",
    "notice_date",
    "fetched_at",
    "run_kind",
    "scope",
    "stock_code",
    "outcome",
    "reason",
    "count_declared",
    "pages_declared",
    "raw_rows",
    "unique_rows",
    "passes",
    "local_rows_before",
    "missing_rows",
    "revised_rows",
    "surplus_rows",
    "moved_rows",
    "dup_rows",
    "rows_inserted",
    "held_rows",
    "exit_rows_replaced",
    "batch_ids",
)

# append-only: 本模块源码不得含 UPDATE / DELETE SQL (B18 静态钉)。
_LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    ledger_id VARCHAR PRIMARY KEY,
    notice_date VARCHAR NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    run_kind VARCHAR NOT NULL CHECK (run_kind IN ('daily','reland')),
    scope VARCHAR NOT NULL CHECK (scope IN ('day','stock')),
    stock_code VARCHAR CHECK ((scope = 'stock') = (stock_code IS NOT NULL)),
    outcome VARCHAR NOT NULL CHECK (outcome IN ('complete','empty','failed')),
    reason VARCHAR,
    count_declared INTEGER,
    pages_declared INTEGER,
    raw_rows INTEGER,
    unique_rows INTEGER,
    passes INTEGER,
    local_rows_before INTEGER,
    missing_rows INTEGER,
    revised_rows INTEGER,
    surplus_rows INTEGER,
    moved_rows INTEGER,
    dup_rows INTEGER,
    rows_inserted INTEGER,
    held_rows INTEGER,
    exit_rows_replaced INTEGER,
    batch_ids VARCHAR
)
"""


class HoldersLedgerFloorError(RuntimeError):
    """账本里 floor 之前存在未 settled 的 scope='day' 分区 —— 到期集合不能越过它们
    (S6, floor 只能越过已 settled 的日子)。"""


def ensure_holders_notice_ledger(conn) -> None:
    conn.execute(_LEDGER_DDL)


@dataclass(frozen=True)
class LedgerRow:
    """账本一行的 typed 载体, 字段与列一一对应, 缺字段 fail (dataclass 位置/关键字必填)。"""

    ledger_id: str
    notice_date: str
    fetched_at: datetime
    run_kind: str
    scope: str
    outcome: str
    stock_code: Optional[str] = None
    reason: Optional[str] = None
    count_declared: Optional[int] = None
    pages_declared: Optional[int] = None
    raw_rows: Optional[int] = None
    unique_rows: Optional[int] = None
    passes: Optional[int] = None
    local_rows_before: Optional[int] = None
    missing_rows: Optional[int] = None
    revised_rows: Optional[int] = None
    surplus_rows: Optional[int] = None
    moved_rows: Optional[int] = None
    dup_rows: Optional[int] = None
    rows_inserted: Optional[int] = None
    held_rows: Optional[int] = None
    exit_rows_replaced: Optional[int] = None
    batch_ids: Optional[str] = None


def append_ledger(conn, row: LedgerRow) -> None:
    """append-only 写一行账本。

    ``fetched_at`` 必须 timezone-aware —— naive datetime 在「按上海日历日比较」
    的 settled 判据里含义不明确 (T4), 抛 ``ValueError`` 而不是静默假设 UTC/本地。
    """
    if not isinstance(row.fetched_at, datetime):
        raise ValueError(
            f"append_ledger: fetched_at 必须是 datetime, got {type(row.fetched_at).__name__}"
        )
    if row.fetched_at.tzinfo is None or row.fetched_at.utcoffset() is None:
        raise ValueError(
            f"append_ledger: fetched_at 必须 timezone-aware, got naive {row.fetched_at!r}"
        )
    if row.run_kind not in _RUN_KINDS:
        raise ValueError(f"append_ledger: run_kind 必须是 {_RUN_KINDS!r}, got {row.run_kind!r}")
    if row.scope not in _SCOPES:
        raise ValueError(f"append_ledger: scope 必须是 {_SCOPES!r}, got {row.scope!r}")
    if row.outcome not in _OUTCOMES:
        raise ValueError(f"append_ledger: outcome 必须是 {_OUTCOMES!r}, got {row.outcome!r}")
    if (row.scope == "stock") != (row.stock_code is not None):
        raise ValueError(
            "append_ledger: scope='stock' 必须带 stock_code, scope='day' 不许带 "
            f"(scope={row.scope!r} stock_code={row.stock_code!r})"
        )
    if not str(row.notice_date or "").strip():
        raise ValueError("append_ledger: notice_date 不能为空")
    values = [getattr(row, name) for name in _LEDGER_COLUMNS]
    placeholders = ", ".join("?" for _ in _LEDGER_COLUMNS)
    conn.execute(
        f"INSERT INTO {LEDGER_TABLE} ({', '.join(_LEDGER_COLUMNS)}) VALUES ({placeholders})",
        values,
    )


def settled_notice_days(conn, *, settle_days: int) -> frozenset:
    """「已完整取到」的公告日集合。

    六条性质 (各一条隔离用例, B18 族):
      (i)   单调 —— 一旦 settled 不因后来的 failed 行取消 (B18d);
      (ii)  failed 永不使一天 settled (B18);
      (iii) complete 且 missing_rows > 0 不使之 settled —— 那一次正在接住迟到
            的行, 不是"什么都没发现" (B18b, S1);
      (iv)  revised / surplus / moved / dup 不阻塞settle —— 否则带永久修订的
            分区 (如 0731) 永远 settle 不了 (B18c);
      (v)   scope='stock' 的行不参与 (按股回补不是整天, B7e);
      (vi)  按上海日历日比, fetched_at 存 UTC 时 D+1 07:40 CST (= D 23:40 UTC)
            算 D+1 (B18)。
    另加 V1: outcome='empty' 时只有 local_rows_before=0 才算数 —— 本地已有行
    时供应商这次抽风返回 9201 不代表"这天没有数据"。
    """
    rows = conn.execute(
        f"""
        SELECT DISTINCT notice_date
          FROM {LEDGER_TABLE}
         WHERE scope = 'day'
           AND outcome IN ('complete', 'empty')
           AND COALESCE(missing_rows, 0) = 0
           AND (outcome != 'empty' OR COALESCE(local_rows_before, 0) = 0)
           AND CAST(fetched_at AT TIME ZONE 'Asia/Shanghai' AS DATE)
               >= CAST(strptime(notice_date, '%Y%m%d') AS DATE) + CAST(? AS INTEGER)
        """,
        [int(settle_days)],
    ).fetchall()
    return frozenset(str(r[0]) for r in rows)


def _calendar_range(start: str, end: str) -> list[str]:
    """闭区间 [start, end] 的日历日列表 (含周末), YYYYMMDD 排序。"""
    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def plan_due_notice_days(
    conn, *, provider_max: str, settle_days: int, floor: str, max_days: int
) -> list[str]:
    """到期集合 = 日历 [floor .. provider_max] 减去 settled, 取前 ``max_days`` 个。

    floor 守卫 (S6, B23): 账本里 scope='day' 且 notice_date < floor 的日期若有
    未 settled 的 —— floor 只能越过已经 settled 的日子, 不能静默把积压甩在
    曝露窗口之外, 抛 ``HoldersLedgerFloorError``。
    """
    settled = settled_notice_days(conn, settle_days=settle_days)

    before_floor = conn.execute(
        f"""
        SELECT DISTINCT notice_date
          FROM {LEDGER_TABLE}
         WHERE scope = 'day' AND notice_date < ?
        """,
        [floor],
    ).fetchall()
    unsettled_before_floor = sorted(
        str(r[0]) for r in before_floor if str(r[0]) not in settled
    )
    if unsettled_before_floor:
        raise HoldersLedgerFloorError(
            f"floor={floor!r} 之前存在未 settled 的账本日期: {unsettled_before_floor!r} "
            "—— floor 只能越过已 settled 的日子"
        )

    calendar_days = _calendar_range(floor, provider_max)
    due = [d for d in calendar_days if d not in settled]
    return due[: max(0, int(max_days))]


def settle_stats(conn, *, settle_days: int) -> list[dict]:
    """零网络; 只报不判 (B2 ``--settle-stats`` 数据源)。

    每个 settled 分区: settling 那次取数相对公告日的滞后天数, 以及在
    settled 之后仍发现 ``missing_rows > 0`` 的取数次数 (= ``settle_days``
    偏小的信号, 供人工调参用, 本函数不做任何判定/退出码)。
    """
    settled = settled_notice_days(conn, settle_days=settle_days)
    out: list[dict] = []
    for notice_date in sorted(settled):
        settle_row = conn.execute(
            f"""
            SELECT fetched_at
              FROM {LEDGER_TABLE}
             WHERE scope = 'day' AND notice_date = ?
               AND outcome IN ('complete', 'empty')
               AND COALESCE(missing_rows, 0) = 0
               AND (outcome != 'empty' OR COALESCE(local_rows_before, 0) = 0)
               AND CAST(fetched_at AT TIME ZONE 'Asia/Shanghai' AS DATE)
                   >= CAST(strptime(?, '%Y%m%d') AS DATE) + CAST(? AS INTEGER)
             ORDER BY fetched_at ASC
             LIMIT 1
            """,
            [notice_date, notice_date, int(settle_days)],
        ).fetchone()
        settle_lag_days = None
        if settle_row is not None and settle_row[0] is not None:
            fetched_at = settle_row[0]
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            nd_date = datetime.strptime(notice_date, "%Y%m%d").date()
            settle_lag_days = (fetched_at.astimezone(timezone.utc).date() - nd_date).days
        post_settle_missing = conn.execute(
            f"""
            SELECT COUNT(*)
              FROM {LEDGER_TABLE}
             WHERE scope = 'day' AND notice_date = ?
               AND CAST(fetched_at AT TIME ZONE 'Asia/Shanghai' AS DATE)
                   >= CAST(strptime(?, '%Y%m%d') AS DATE) + CAST(? AS INTEGER)
               AND COALESCE(missing_rows, 0) > 0
            """,
            [notice_date, notice_date, int(settle_days)],
        ).fetchone()[0]
        out.append(
            {
                "notice_date": notice_date,
                "settle_lag_days": settle_lag_days,
                "post_settle_missing_fetches": int(post_settle_missing or 0),
            }
        )
    return out


__all__ = [
    "LEDGER_TABLE",
    "HoldersLedgerFloorError",
    "LedgerRow",
    "append_ledger",
    "ensure_holders_notice_ledger",
    "plan_due_notice_days",
    "settle_stats",
    "settled_notice_days",
]
