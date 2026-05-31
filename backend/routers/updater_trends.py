"""Stock trend mart runner for the updater pipeline."""

import gc
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from routers.updater_runtime import _run_blocking_db_task
from services.holdings import refresh_stock_latest_cache
from services.market_db import get_canonical_kline_qfq_relation

logger = logging.getLogger("cm-api")
KLINE_DAILY_QFQ_RELATION = get_canonical_kline_qfq_relation()


def _trend_str(vals) -> str:
    if len(vals) < 2:
        return "—"
    parts = []
    for idx, current in enumerate(vals[:-1]):
        previous = vals[idx + 1]
        if current > previous:
            parts.append("↑")
        elif current < previous:
            parts.append("↓")
        else:
            parts.append("→")
    return "".join(parts)


def _monthly_price_trend(monthly_closes) -> str:
    if len(monthly_closes) < 3:
        return "—"
    ups = sum(
        1
        for idx, current in enumerate(monthly_closes[:-1])
        if current and monthly_closes[idx + 1] and current > monthly_closes[idx + 1]
    )
    if ups >= 2:
        return "连涨"
    if ups == 0:
        return "连跌"
    return "震荡"


def _group_recent_periods(rows) -> dict:
    per_stock: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
    for row in rows:
        per_stock[row[0]].append((row[1], row[2] or 0, row[3] or 0))
    return per_stock


def _group_recent_closes(rows, limit: int) -> dict:
    per_stock: dict[str, list[float]] = defaultdict(list)
    for code, _trade_date, close in rows:
        closes = per_stock[code]
        if len(closes) < limit:
            closes.append(close)
    return per_stock


def _create_trend_code_temp_table(mkt_conn, codes) -> None:
    mkt_conn.execute("DROP TABLE IF EXISTS tmp_updater_trend_codes")
    mkt_conn.execute("CREATE TEMP TABLE tmp_updater_trend_codes (code TEXT)")
    if codes:
        mkt_conn.executemany(
            "INSERT INTO tmp_updater_trend_codes VALUES (?)",
            [(code,) for code in sorted(codes)],
        )


def _fetch_price_rows(mkt_conn, freq: str, *, min_date: str | None = None):
    relation = KLINE_DAILY_QFQ_RELATION if freq == "daily" else "price_kline"
    if min_date:
        return mkt_conn.execute(
            f"""
            SELECT k.code, k.date, k.close
            FROM {relation} k
            INNER JOIN tmp_updater_trend_codes c ON c.code = k.code
            WHERE k.freq=? AND k.adjust='qfq' AND k.date >= ?
            ORDER BY k.code, k.date DESC
            """,
            [freq, min_date],
        ).fetchall()
    return mkt_conn.execute(
        f"""
        SELECT k.code, k.date, k.close
        FROM {relation} k
        INNER JOIN tmp_updater_trend_codes c ON c.code = k.code
        WHERE k.freq=? AND k.adjust='qfq'
        ORDER BY k.code, k.date DESC
        """,
        [freq],
    ).fetchall()


def _delete_stale_stock_trend_rows(conn, expected_codes) -> None:
    conn.execute("DROP TABLE IF EXISTS tmp_expected_stock_trend_codes")
    conn.execute("CREATE TEMP TABLE tmp_expected_stock_trend_codes (stock_code TEXT)")
    if expected_codes:
        conn.executemany(
            "INSERT INTO tmp_expected_stock_trend_codes VALUES (?)",
            [(code,) for code in sorted(expected_codes)],
        )
    conn.execute("""
        DELETE FROM mart_stock_trend
        WHERE stock_code IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM tmp_expected_stock_trend_codes e
              WHERE e.stock_code = mart_stock_trend.stock_code
          )
    """)


def _step_build_trends_sync(conn, should_stop=None) -> int:
    """计算股票趋势 mart_stock_trend.

    性能优化（审计性能诊断）：原 N+1 query 6× × 3285 股 ≈ 20k queries → 17s。
    重构为批量预聚合：一次性拉所有股票的 inst_holdings / latest_events / price_kline，
    in-memory 分组查询，目标耗时 < 3s。
    """
    try:
        conn.execute("SET preserve_insertion_order=false")
        conn.execute("SET threads=2")
    except Exception as exc:  # rule-compliance: ok evidence=optional-duckdb-session-pragmas
        logger.debug(f"[趋势] DuckDB session pragma skipped: {exc}")
    refresh_stock_latest_cache(conn)
    now = datetime.now().isoformat()
    mkt_conn = None
    try:
        # 股票列表骨架以 mart_current_relationship 为真相源，
        # 历史机构数/资金趋势再回看 inst_holdings 的近3期数据。
        stocks = conn.execute("""
            SELECT DISTINCT stock_code, stock_name
            FROM mart_current_relationship
            WHERE stock_code IS NOT NULL
        """).fetchall()
        logger.info(f"[趋势] 股票范围: {len(stocks)} 只")

        # 批量预聚合 1：每股近 3 期机构家数 + 合计持仓（取代 N+1 的 stock_periods + inst_counts/caps）。
        agg_rows = conn.execute("""
            SELECT stock_code, report_date,
                   COUNT(DISTINCT institution_id) AS n_inst,
                   SUM(hold_market_cap) AS total_cap
            FROM inst_holdings
            WHERE stock_code IS NOT NULL
            GROUP BY stock_code, report_date
            ORDER BY stock_code, report_date DESC
        """).fetchall()
        logger.info(f"[趋势] 持仓期数聚合: {len(agg_rows)} 行")
        per_stock_periods = _group_recent_periods(agg_rows)

        # 批量预聚合 2：每股最近 3 个事件（取代 fact_institution_event N+1）。
        ev_rows = conn.execute("""
            SELECT stock_code, event_type, holder_name, change_pct, report_date, notice_date
            FROM (
                SELECT stock_code, event_type, holder_name, change_pct, report_date, notice_date,
                       ROW_NUMBER() OVER (
                         PARTITION BY stock_code
                         ORDER BY report_date DESC, notice_date DESC
                       ) AS rn
                FROM fact_institution_event
            )
            WHERE rn <= 3
        """).fetchall()
        logger.info(f"[趋势] 最新事件聚合: {len(ev_rows)} 行")
        per_stock_events: dict[str, list] = defaultdict(list)
        for row in ev_rows:
            per_stock_events[row[0]].append(row)

        # 批量预聚合 3：每股最近 3 个月 K 线 + 最近 21 日 K 线。
        from services.market_db import get_market_conn as _get_mkt_conn

        mkt_conn = _get_mkt_conn()
        try:
            mkt_conn.execute("SET preserve_insertion_order=false")
            mkt_conn.execute("SET threads=2")
            mkt_conn.execute("SET memory_limit='2GB'")
        except Exception as exc:  # rule-compliance: ok evidence=optional-duckdb-session-pragmas
            logger.debug(f"[趋势] market DuckDB session pragma skipped: {exc}")
        trend_code_set = {stock["stock_code"] for stock in stocks if stock["stock_code"]}
        _create_trend_code_temp_table(mkt_conn, trend_code_set)

        monthly_rows = _fetch_price_rows(mkt_conn, "monthly")
        logger.info(f"[趋势] 月线读取: {len(monthly_rows)} 行")
        per_stock_monthly = _group_recent_closes(monthly_rows, 3)
        del monthly_rows

        # daily 限定近 45 天（21 交易日 + 缓冲），避免全表扫。
        cutoff_daily = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        daily_rows = _fetch_price_rows(mkt_conn, "daily", min_date=cutoff_daily)
        logger.info(f"[趋势] 日线读取: {len(daily_rows)} 行")
        mkt_conn.close()
        mkt_conn = None
        per_stock_daily = _group_recent_closes(daily_rows, 21)
        del daily_rows
        gc.collect()

        count = 0
        insert_batch = []
        for stock in stocks:
            if should_stop is not None:
                should_stop()
            code = stock["stock_code"]
            name = stock["stock_name"]

            # 机构增减趋势：近 3 期家数 + 合计持仓（从 per_stock_periods 取）。
            periods = per_stock_periods.get(code, [])[:3]
            inst_counts = ([period[1] for period in periods] + [0, 0, 0])[:3]
            inst_caps = ([period[2] for period in periods] + [0, 0, 0])[:3]

            inst_trend = _trend_str(inst_counts)
            cap_trend = _trend_str(inst_caps)

            # 最新事件（从 per_stock_events 取）。
            latest_ev = per_stock_events.get(code, [])
            latest_events_json = json.dumps(
                [{"inst": (event[2] or "")[:20], "type": event[1], "pct": event[3]} for event in latest_ev],
                ensure_ascii=False,
            ) if latest_ev else "[]"
            latest_rd = latest_ev[0][4] if latest_ev else None
            latest_nd = latest_ev[0][5] if latest_ev else None

            # 股价趋势（从预加载的 per_stock_monthly/daily 取）。
            monthly_closes = per_stock_monthly.get(code, [])
            daily_closes = per_stock_daily.get(code, [])

            price_1m = None
            price_20d = None
            if len(monthly_closes) >= 2 and monthly_closes[1] and monthly_closes[1] > 0:
                price_1m = (monthly_closes[0] - monthly_closes[1]) / monthly_closes[1] * 100

            if len(daily_closes) >= 21 and daily_closes[20] and daily_closes[20] > 0:
                price_20d = (daily_closes[0] - daily_closes[20]) / daily_closes[20] * 100

            insert_batch.append((
                code, name, inst_counts[0], inst_counts[1], inst_counts[2],
                inst_caps[0], inst_caps[1], inst_caps[2], inst_trend, cap_trend,
                latest_events_json, latest_rd, latest_nd,
                price_1m, price_20d, _monthly_price_trend(monthly_closes), now,
            ))
            count += 1

        logger.info(f"[趋势] 准备写入: {len(insert_batch)} 行")
        existing_codes = {
            row[0] for row in conn.execute("SELECT stock_code FROM mart_stock_trend").fetchall()
            if row[0]
        }
        expected_codes = {row[0] for row in insert_batch if row[0]}
        _delete_stale_stock_trend_rows(conn, expected_codes)

        update_sql = """
            UPDATE mart_stock_trend SET
                stock_name=?,
                inst_count_t0=?, inst_count_t1=?, inst_count_t2=?,
                inst_cap_t0=?, inst_cap_t1=?, inst_cap_t2=?,
                inst_trend=?, cap_trend=?,
                latest_events=?, latest_report_date=?, latest_notice_date=?,
                price_1m_pct=?, price_20d_pct=?, price_trend=?,
                updated_at=?
            WHERE stock_code=?
        """
        update_batch = [
            (
                row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                row[8], row[9], row[10], row[11], row[12], row[13],
                row[14], row[15], row[16], row[0],
            )
            for row in insert_batch
            if row[0] in existing_codes
        ]
        for idx in range(0, len(update_batch), 500):
            conn.executemany(update_sql, update_batch[idx:idx + 500])

        # 对新增股票补 insert；常规路径大多只有 update。
        insert_sql = """
            INSERT INTO mart_stock_trend
            (stock_code, stock_name, inst_count_t0, inst_count_t1, inst_count_t2,
             inst_cap_t0, inst_cap_t1, inst_cap_t2, inst_trend, cap_trend,
             latest_events, latest_report_date, latest_notice_date,
             price_1m_pct, price_20d_pct, price_trend, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        new_batch = [row for row in insert_batch if row[0] not in existing_codes]
        for idx in range(0, len(new_batch), 500):
            conn.executemany(insert_sql, new_batch[idx:idx + 500])

        conn.commit()
    except Exception:
        if mkt_conn is not None:
            mkt_conn.close()
        raise
    logger.info(f"[趋势] 完成: {count} 只股票")
    return count


async def _step_build_trends(conn, should_stop=None) -> int:
    """计算股票趋势 mart_stock_trend."""
    return await _run_blocking_db_task(
        lambda worker_conn: _step_build_trends_sync(worker_conn, should_stop=should_stop)
    )
