"""Survivorship-bias mitigation.

dim_active_a_stock 只含当前在市股，训练时会产生幸存者偏差。
本模块汇聚多个历史数据源，构建 dim_all_ever_listed（含退市股）。

数据源（优先级从高到低）:
1. dim_active_a_stock (当前在市)
2. price_kline (有过行情)
3. fact_institution_event (有过机构事件)
4. inst_holdings (有过持仓)
5. raw_gpcw_detail (有过财务)

is_active = 1 if 最近 30 个交易日内有成交, else 0
delisted_date = 最后一次有成交的日期 (is_active=0)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.survivorship")


def _latest_trade_date(mkt_conn: sqlite3.Connection) -> Optional[str]:
    row = mkt_conn.execute(
        "SELECT MAX(date) FROM price_kline WHERE freq='daily' AND adjust='qfq'"
    ).fetchone()
    if not row or not row[0]:
        return None
    d = row[0]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if "-" not in d else d


def build_dim_all_ever_listed(
    conn: sqlite3.Connection,
    mkt_conn: sqlite3.Connection,
    *,
    active_window_days: int = 30,
) -> dict:
    """幂等构建 dim_all_ever_listed。按来源合并股票池 + 行情活跃度判定."""

    latest = _latest_trade_date(mkt_conn)
    active_threshold = None
    if latest:
        active_threshold = (
            datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=active_window_days)
        ).strftime("%Y-%m-%d")

    # 1) 从 price_kline 聚合 first / last trade date
    # REPLACE(date, '-', '') 规范
    kline_rows = mkt_conn.execute(
        """
        SELECT code,
               MIN(date) AS first_dt,
               MAX(date) AS last_dt
        FROM price_kline
        WHERE freq='daily' AND adjust='qfq'
        GROUP BY code
        """
    ).fetchall()
    kline_map = {r[0]: (r[1], r[2]) for r in kline_rows}

    # 2) 从 smartmoney.db 各来源搜集曾出现的 code + name
    sources: dict[str, dict] = {}

    def _upsert(code: str, name: Optional[str], source: str, dt: Optional[str] = None):
        if not code:
            return
        s = sources.setdefault(
            code,
            {"name": None, "source": source, "first_dt": None, "last_dt": None},
        )
        if name and not s["name"]:
            s["name"] = name
        dt_iso = to_iso(dt) if dt else None
        if dt_iso:
            if not s["first_dt"] or dt_iso < s["first_dt"]:
                s["first_dt"] = dt_iso
            if not s["last_dt"] or dt_iso > s["last_dt"]:
                s["last_dt"] = dt_iso

    # 2a dim_active_a_stock
    for r in conn.execute(
        "SELECT stock_code, stock_name FROM dim_active_a_stock"
    ).fetchall():
        _upsert(r[0], r[1], "dim_active")

    # 2b fact_institution_event
    for r in conn.execute(
        "SELECT stock_code, stock_name, MIN(report_date), MAX(report_date) "
        "FROM fact_institution_event GROUP BY stock_code"
    ).fetchall():
        _upsert(r[0], r[1], "inst_event")
        if r[2]:
            _upsert(r[0], r[1], "inst_event", r[2])
        if r[3]:
            _upsert(r[0], r[1], "inst_event", r[3])

    # 2c inst_holdings
    for r in conn.execute(
        "SELECT stock_code, stock_name, MIN(report_date), MAX(report_date) "
        "FROM inst_holdings GROUP BY stock_code"
    ).fetchall():
        _upsert(r[0], r[1], "inst_holdings")
        if r[2]:
            _upsert(r[0], r[1], "inst_holdings", r[2])
        if r[3]:
            _upsert(r[0], r[1], "inst_holdings", r[3])

    # 2d raw_gpcw_detail（可能没 stock_name 列）
    try:
        for r in conn.execute(
            "SELECT stock_code, MIN(report_date), MAX(report_date) FROM raw_gpcw_detail "
            "GROUP BY stock_code"
        ).fetchall():
            _upsert(r[0], None, "gpcw")
            if r[1]:
                _upsert(r[0], None, "gpcw", r[1])
            if r[2]:
                _upsert(r[0], None, "gpcw", r[2])
    except sqlite3.OperationalError:
        logger.warning("raw_gpcw_detail 不存在或无 stock_code 列，跳过")

    # 3) 把 kline 的 first/last 合并进来（行情是最权威的活跃度信号）
    for code, (fd, ld) in kline_map.items():
        _upsert(code, None, "kline")
        if fd:
            _upsert(code, None, "kline", fd)
        if ld:
            _upsert(code, None, "kline", ld)

    # 4) 写入 dim_all_ever_listed
    now = datetime.utcnow().isoformat(timespec="seconds")
    written = active_cnt = delisted_cnt = 0
    for code, s in sources.items():
        kl = kline_map.get(code)
        last_dt = kl[1] if kl else s["last_dt"]
        first_dt = kl[0] if kl else s["first_dt"]

        is_active = 1
        delisted_date = None
        if active_threshold and last_dt and last_dt < active_threshold:
            is_active = 0
            delisted_date = last_dt
        elif not last_dt:
            # 没有任何时间线索，默认 active=1（依赖 dim_active_a_stock 主管）
            is_active = 1 if code in {
                row[0] for row in conn.execute(
                    "SELECT stock_code FROM dim_active_a_stock"
                ).fetchall()
            } else 0
            if is_active == 0:
                delisted_date = None

        conn.execute(
            """
            INSERT INTO dim_all_ever_listed(
                stock_code, stock_name, first_seen_date, last_seen_date,
                is_active, delisted_date, source, updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name = COALESCE(excluded.stock_name, stock_name),
                first_seen_date = COALESCE(excluded.first_seen_date, first_seen_date),
                last_seen_date = COALESCE(excluded.last_seen_date, last_seen_date),
                is_active = excluded.is_active,
                delisted_date = excluded.delisted_date,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                code,
                s["name"],
                first_dt,
                last_dt,
                is_active,
                delisted_date,
                s["source"],
                now,
            ),
        )
        written += 1
        if is_active:
            active_cnt += 1
        else:
            delisted_cnt += 1

    conn.commit()
    stats = {
        "total": written,
        "active": active_cnt,
        "delisted_or_inactive": delisted_cnt,
        "active_threshold_date": active_threshold,
    }
    logger.info("[SEF] dim_all_ever_listed 构建完成：%s", stats)
    return stats
