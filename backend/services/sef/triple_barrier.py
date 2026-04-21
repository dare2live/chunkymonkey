"""Triple Barrier Method (Lopez de Prado 2018, Ch.3).

对每条链从 entry_date 起：
- upper barrier = entry_price * (1 + k_up × ATR%)
- lower barrier = entry_price * (1 - k_dn × ATR%)
- time barrier  = entry_date + time_horizon_days

三者哪个先触发决定 label: 'upper' / 'lower' / 'time'.
ATR 在 entry 前 14 天计算（不含 entry 日本身，避免前视）。

默认参数（可调）:
- k_up = 2.0 (止盈)
- k_dn = 1.0 (止损)
- time_horizon_days = 120
- atr_window = 14
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.triple_barrier")


def _compute_atr_pct(
    mkt_conn: sqlite3.Connection,
    stock_code: str,
    entry_date: str,
    window: int = 14,
) -> Optional[float]:
    """Entry 日之前 window 天的平均真实振幅（%）。

    使用 entry 前最多 window+5 个交易日的 (high-low)/close 作为简化 ATR%。
    返回 None 表示样本不足或价格缺失。
    """
    entry_iso = to_iso(entry_date)
    if not entry_iso:
        return None
    start = (datetime.strptime(entry_iso, "%Y-%m-%d") - timedelta(days=window * 2 + 10)).strftime(
        "%Y-%m-%d"
    )
    rows = mkt_conn.execute(
        """
        SELECT date, high, low, close FROM price_kline
        WHERE code=? AND freq='daily' AND adjust='qfq'
          AND REPLACE(date,'-','') >= REPLACE(?,'-','')
          AND REPLACE(date,'-','') < REPLACE(?,'-','')
        ORDER BY date DESC LIMIT ?
        """,
        (stock_code, start, entry_iso, window),
    ).fetchall()
    if len(rows) < max(5, window // 2):
        return None
    total = 0.0
    cnt = 0
    for r in rows:
        hi, lo, cl = r[1], r[2], r[3]
        if hi is None or lo is None or cl is None or cl <= 0:
            continue
        total += (hi - lo) / cl
        cnt += 1
    if cnt == 0:
        return None
    return total / cnt


def _fetch_forward_series(
    mkt_conn: sqlite3.Connection,
    stock_code: str,
    entry_date: str,
    horizon_days: int,
) -> list[tuple[str, float, float, float]]:
    entry_iso = to_iso(entry_date)
    if not entry_iso:
        return []
    end = (datetime.strptime(entry_iso, "%Y-%m-%d") + timedelta(days=horizon_days + 5)).strftime(
        "%Y-%m-%d"
    )
    rows = mkt_conn.execute(
        """
        SELECT date, close, high, low FROM price_kline
        WHERE code=? AND freq='daily' AND adjust='qfq'
          AND REPLACE(date,'-','') >= REPLACE(?,'-','')
          AND REPLACE(date,'-','') <= REPLACE(?,'-','')
        ORDER BY date
        """,
        (stock_code, entry_iso, end),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _label_chain(
    entry_price: float,
    series: list[tuple[str, float, float, float]],
    k_up: float,
    k_dn: float,
    atr_pct: float,
    horizon_days: int,
) -> dict:
    upper = entry_price * (1.0 + k_up * atr_pct)
    lower = entry_price * (1.0 - k_dn * atr_pct)
    first_iso = to_iso(series[0][0]) if series else None
    entry_dt = datetime.strptime(first_iso, "%Y-%m-%d") if first_iso else None
    deadline = entry_dt + timedelta(days=horizon_days) if entry_dt else None
    last_date = series[-1][0] if series else None

    for d, _close, hi, lo in series:
        d_iso = to_iso(d)
        if not d_iso:
            continue
        dt = datetime.strptime(d_iso, "%Y-%m-%d")
        if deadline and dt > deadline:
            break
        if hi is not None and hi >= upper:
            return {
                "upper": 1,
                "lower": 0,
                "time": 0,
                "label": "upper",
                "trigger_date": d,
                "upper_level": upper,
                "lower_level": lower,
            }
        if lo is not None and lo <= lower:
            return {
                "upper": 0,
                "lower": 1,
                "time": 0,
                "label": "lower",
                "trigger_date": d,
                "upper_level": upper,
                "lower_level": lower,
            }

    return {
        "upper": 0,
        "lower": 0,
        "time": 1,
        "label": "time",
        "trigger_date": last_date,
        "upper_level": upper,
        "lower_level": lower,
    }


def apply_triple_barrier(
    conn: sqlite3.Connection,
    mkt_conn: sqlite3.Connection,
    *,
    k_up: float = 2.0,
    k_dn: float = 1.0,
    horizon_days: int = 120,
    atr_window: int = 14,
    batch_commit: int = 500,
) -> dict:
    """为 fact_chain_alpha_truth 所有行生成 Triple Barrier label。"""
    rows = conn.execute(
        "SELECT chain_id, stock_code, entry_date, entry_price FROM fact_chain_alpha_truth"
    ).fetchall()
    logger.info("[SEF] Triple Barrier 开始，%d 链", len(rows))

    stats = {
        "total": len(rows),
        "labeled": 0,
        "upper": 0,
        "lower": 0,
        "time": 0,
        "skipped_no_price": 0,
        "skipped_no_atr": 0,
    }

    for i, r in enumerate(rows):
        chain_id = r[0]
        code = r[1]
        entry_d = r[2]
        entry_p = r[3]
        if entry_p is None or entry_p <= 0 or not entry_d:
            stats["skipped_no_price"] += 1
            continue
        atr = _compute_atr_pct(mkt_conn, code, entry_d, window=atr_window)
        if atr is None or atr <= 0:
            stats["skipped_no_atr"] += 1
            continue
        series = _fetch_forward_series(mkt_conn, code, entry_d, horizon_days)
        if not series:
            stats["skipped_no_price"] += 1
            continue
        label = _label_chain(entry_p, series, k_up, k_dn, atr, horizon_days)
        conn.execute(
            """
            UPDATE fact_chain_alpha_truth
            SET tb_upper_hit=?, tb_lower_hit=?, tb_time_hit=?, tb_label=?,
                tb_upper_level=?, tb_lower_level=?, tb_time_horizon_days=?,
                tb_trigger_date=?
            WHERE chain_id=?
            """,
            (
                label["upper"],
                label["lower"],
                label["time"],
                label["label"],
                label["upper_level"],
                label["lower_level"],
                horizon_days,
                label["trigger_date"],
                chain_id,
            ),
        )
        stats["labeled"] += 1
        stats[label["label"]] += 1
        if stats["labeled"] % batch_commit == 0:
            conn.commit()
            logger.info("[SEF] Triple Barrier 进度 %d/%d", stats["labeled"], stats["total"])

    conn.commit()
    # 分布
    if stats["labeled"] > 0:
        stats["upper_pct"] = round(stats["upper"] / stats["labeled"] * 100, 2)
        stats["lower_pct"] = round(stats["lower"] / stats["labeled"] * 100, 2)
        stats["time_pct"] = round(stats["time"] / stats["labeled"] * 100, 2)
    logger.info("[SEF] Triple Barrier 完成：%s", stats)
    return stats
