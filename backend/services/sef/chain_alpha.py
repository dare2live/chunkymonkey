"""SEF Phase I Layer 1 α 真相层回填.

对 research_holding_chains 15009 条链逐条计算：
- chain_follow_pnl : 跟投者视角 PnL (entry_follow_price → eval close)
- chain_follow_max_dd : 跟投期间最大回撤
- chain_inst_pnl : 机构视角 PnL (entry_inst_cost → exit_inst_cost 或 eval close)
- eval_date : closed 链取 chain_end_date，open 链取 as_of_date
- entry_price / eval_price : 实际取到的行情价

open 链按"浮动盈亏"口径（截止到 as_of_date），closed 链按"已兑现"口径。

依赖:
- smartmoney.db: research_holding_chains, fact_institution_event
- market_data.db: price_kline (freq=daily, adjust=qfq)
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.chain_alpha")


def _fetch_chains(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """取全部 chain（closed + open），按 stock_code 排序便于后续批量取行情。"""
    return conn.execute(
        """
        SELECT institution_id, stock_code, chain_id, chain_start_date, chain_end_date,
               chain_status, chain_days, entry_inst_cost, exit_inst_cost,
               entry_follow_price, industry_l1, industry_l2
        FROM research_holding_chains
        ORDER BY stock_code, chain_start_date
        """
    ).fetchall()


def _fetch_kline_series(
    mkt_conn: sqlite3.Connection,
    stock_code: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, float, float, float]]:
    """Return [(date_iso, close, high, low)] for stock between dates (inclusive).

    Accepts both YYYYMMDD and YYYY-MM-DD. Normalizes via _dates.to_iso.
    """
    start_iso = to_iso(start_date)
    end_iso = to_iso(end_date)
    if not start_iso or not end_iso:
        return []
    start_flat = start_iso.replace("-", "")
    end_flat = end_iso.replace("-", "")
    rows = mkt_conn.execute(
        """
        SELECT date, close, high, low
        FROM price_kline
        WHERE code = ? AND freq='daily' AND adjust='qfq'
          AND REPLACE(date,'-','') BETWEEN ? AND ?
        ORDER BY date
        """,
        (stock_code, start_flat, end_flat),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows if r[1] is not None]


def _as_of_date(mkt_conn: sqlite3.Connection) -> str:
    """最近一个有行情的交易日（YYYY-MM-DD）。"""
    row = mkt_conn.execute(
        "SELECT MAX(date) FROM price_kline WHERE freq='daily' AND adjust='qfq'"
    ).fetchone()
    if not row or not row[0]:
        return datetime.now().strftime("%Y-%m-%d")
    d = row[0]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if "-" not in d else d


def _compute_follow_metrics(
    entry_price: Optional[float], series: list[tuple[str, float, float, float]]
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
    """跟投者 PnL + 最大回撤 + 最新 eval 价 + 最新 eval 日期."""
    if not entry_price or entry_price <= 0 or not series:
        return None, None, None, None
    eval_date = series[-1][0]
    eval_price = series[-1][1]
    pnl = (eval_price / entry_price - 1.0) * 100.0

    peak = entry_price
    max_dd = 0.0
    for _d, close, high, low in series:
        p_hi = high if high is not None else close
        p_lo = low if low is not None else close
        if p_hi is not None and p_hi > peak:
            peak = p_hi
        trough = p_lo if p_lo is not None else close
        if peak > 0:
            dd = (trough / peak - 1.0) * 100.0
            if dd < max_dd:
                max_dd = dd
    return pnl, max_dd, eval_price, eval_date


def _compute_inst_pnl(entry_cost: Optional[float], exit_cost: Optional[float]) -> Optional[float]:
    if entry_cost is None or exit_cost is None or entry_cost <= 0:
        return None
    return (exit_cost / entry_cost - 1.0) * 100.0


def backfill_chain_alpha(
    conn: sqlite3.Connection,
    mkt_conn: sqlite3.Connection,
    *,
    as_of_date: Optional[str] = None,
    batch_commit: int = 500,
) -> dict:
    """回填 fact_chain_alpha_truth。幂等（UPSERT）。

    Returns stats dict with counts + coverage.
    """
    as_of = as_of_date or _as_of_date(mkt_conn)
    logger.info("[SEF] chain α 回填开始，as_of=%s", as_of)

    chains = _fetch_chains(conn)
    total = len(chains)
    closed_cnt = open_cnt = 0
    kline_missing = 0
    now = datetime.utcnow().isoformat(timespec="seconds")

    stock_cache: dict[tuple[str, str, str], list] = {}

    def get_series(code: str, s: str, e: str):
        key = (code, s, e)
        if key not in stock_cache:
            stock_cache[key] = _fetch_kline_series(mkt_conn, code, s, e)
        return stock_cache[key]

    written = 0
    for i, ch in enumerate(chains):
        status = ch["chain_status"]
        raw_end = ch["chain_end_date"] if status == "closed" else as_of
        start_d = to_iso(ch["chain_start_date"])
        eval_d = to_iso(raw_end) if raw_end else None
        if not start_d:
            continue
        if not eval_d:
            # closed 链缺失 end_date，退回 as_of
            eval_d = to_iso(as_of)

        series = get_series(ch["stock_code"], start_d, eval_d)
        if not series:
            kline_missing += 1
            follow_pnl = follow_dd = eval_price = None
            entry_price = ch["entry_follow_price"]
            real_eval_date = eval_d
        else:
            entry_price = ch["entry_follow_price"]
            if entry_price is None:
                entry_price = series[0][1]
            follow_pnl, follow_dd, eval_price, real_eval_date = _compute_follow_metrics(
                entry_price, series
            )

        inst_pnl = _compute_inst_pnl(ch["entry_inst_cost"], ch["exit_inst_cost"])

        chain_days = ch["chain_days"]
        if chain_days is None and start_d and eval_d:
            try:
                chain_days = (
                    datetime.strptime(eval_d[:10], "%Y-%m-%d")
                    - datetime.strptime(start_d[:10], "%Y-%m-%d")
                ).days
            except ValueError:
                chain_days = None

        conn.execute(
            """
            INSERT INTO fact_chain_alpha_truth (
                institution_id, stock_code, research_chain_id,
                entry_date, exit_date, eval_date, status,
                entry_price, eval_price,
                entry_inst_cost, exit_inst_cost,
                chain_inst_pnl, chain_follow_pnl, chain_follow_max_dd,
                chain_days, industry_l1, industry_l2, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(institution_id, stock_code, research_chain_id) DO UPDATE SET
                entry_date = excluded.entry_date,
                exit_date = excluded.exit_date,
                eval_date = excluded.eval_date,
                status = excluded.status,
                entry_price = excluded.entry_price,
                eval_price = excluded.eval_price,
                entry_inst_cost = excluded.entry_inst_cost,
                exit_inst_cost = excluded.exit_inst_cost,
                chain_inst_pnl = excluded.chain_inst_pnl,
                chain_follow_pnl = excluded.chain_follow_pnl,
                chain_follow_max_dd = excluded.chain_follow_max_dd,
                chain_days = excluded.chain_days,
                industry_l1 = excluded.industry_l1,
                industry_l2 = excluded.industry_l2,
                updated_at = excluded.updated_at
            """,
            (
                ch["institution_id"],
                ch["stock_code"],
                ch["chain_id"],
                start_d,
                to_iso(ch["chain_end_date"]),
                real_eval_date if series else eval_d,
                status,
                entry_price,
                eval_price,
                ch["entry_inst_cost"],
                ch["exit_inst_cost"],
                inst_pnl,
                follow_pnl,
                follow_dd,
                chain_days,
                ch["industry_l1"],
                ch["industry_l2"],
                now,
            ),
        )
        written += 1
        if status == "closed":
            closed_cnt += 1
        else:
            open_cnt += 1

        if written % batch_commit == 0:
            conn.commit()
            logger.info("[SEF] chain α 进度 %d/%d", written, total)

    conn.commit()

    stats = {
        "total_chains": total,
        "written": written,
        "closed": closed_cnt,
        "open": open_cnt,
        "kline_missing": kline_missing,
        "kline_coverage_pct": (
            round((total - kline_missing) / total * 100, 2) if total else None
        ),
        "as_of_date": as_of,
    }
    logger.info("[SEF] chain α 回填完成：%s", stats)
    return stats


def link_events_to_chains(conn: sqlite3.Connection) -> int:
    """把 fact_institution_event.chain_id 填上对应 research_holding_chains.chain_id.

    对每条事件，通过 (institution_id, stock_code, notice_date between start..end) 匹配。
    """
    conn.execute(
        """
        UPDATE fact_institution_event
        SET chain_id = (
            SELECT chain_id FROM research_holding_chains rhc
            WHERE rhc.institution_id = fact_institution_event.institution_id
              AND rhc.stock_code = fact_institution_event.stock_code
              AND rhc.chain_start_date <= COALESCE(fact_institution_event.notice_date,
                                                    fact_institution_event.report_date)
              AND (rhc.chain_end_date IS NULL OR rhc.chain_end_date >=
                   COALESCE(fact_institution_event.notice_date,
                            fact_institution_event.report_date))
            ORDER BY rhc.chain_start_date DESC
            LIMIT 1
        )
        WHERE chain_id IS NULL
        """
    )
    updated = conn.total_changes
    conn.commit()
    logger.info("[SEF] 事件→chain 关联更新 %d 行", updated)
    return updated


def refresh_event_pnl_snapshot(conn: sqlite3.Connection) -> int:
    """把 fact_chain_alpha_truth 聚合值同步到每条事件（follow_pnl_to_eval / eval_status 等）。"""
    conn.execute(
        """
        UPDATE fact_institution_event AS e
        SET follow_pnl_to_eval = (
                SELECT t.chain_follow_pnl FROM fact_chain_alpha_truth t
                WHERE t.institution_id = e.institution_id
                  AND t.stock_code = e.stock_code
                  AND t.research_chain_id = e.chain_id
            ),
            follow_maxdd_to_eval = (
                SELECT t.chain_follow_max_dd FROM fact_chain_alpha_truth t
                WHERE t.institution_id = e.institution_id
                  AND t.stock_code = e.stock_code
                  AND t.research_chain_id = e.chain_id
            ),
            inst_pnl_to_eval = (
                SELECT t.chain_inst_pnl FROM fact_chain_alpha_truth t
                WHERE t.institution_id = e.institution_id
                  AND t.stock_code = e.stock_code
                  AND t.research_chain_id = e.chain_id
            ),
            eval_status = (
                SELECT t.status FROM fact_chain_alpha_truth t
                WHERE t.institution_id = e.institution_id
                  AND t.stock_code = e.stock_code
                  AND t.research_chain_id = e.chain_id
            )
        WHERE e.chain_id IS NOT NULL
        """
    )
    n = conn.total_changes
    conn.commit()
    logger.info("[SEF] 事件 PnL 快照刷新 %d 行", n)
    return n
