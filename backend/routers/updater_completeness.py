"""Data completeness calibration helpers for updater runs."""

from __future__ import annotations

from typing import Callable

from services.industry import summarize_industry_coverage
from services.market_db import get_market_conn


_DATA_COMPLETENESS_TABLES = {
    "build_profiles": "mart_institution_profile",
    "build_industry_stat": "mart_institution_industry_stat",
    "build_trends": "mart_stock_trend",
}


def calibrate_data_completeness(
    conn,
    step_id: str,
    *,
    is_blocking_upstream_state: Callable,
    kline_relation: str,
    logger,
) -> None:
    """
    Calibrate downstream mart data_completeness from actual coverage.

    A skipped updater step can mean "already fresh", so completeness is based on
    upstream blocking state plus measured returns/industry coverage.
    """

    table = _DATA_COMPLETENESS_TABLES.get(step_id)
    if table is None:
        return

    calc_returns_missing = is_blocking_upstream_state(conn, "calc_returns")
    sync_industry_missing = is_blocking_upstream_state(conn, "sync_industry")

    returns_partial = calc_returns_missing
    industry_partial = sync_industry_missing
    if not returns_partial:
        try:
            mkt_conn = get_market_conn()
            try:
                latest_market_date = mkt_conn.execute(
                    f"SELECT MAX(date) FROM {kline_relation} WHERE freq='daily' AND adjust='qfq'"
                ).fetchone()[0]
            finally:
                mkt_conn.close()
            total_events = conn.execute(
                """
                SELECT COUNT(*)
                FROM fact_institution_event
                WHERE notice_date IS NOT NULL AND notice_date != ''
                  AND tradable_date IS NOT NULL AND tradable_date != ''
                  AND (? IS NOT NULL AND tradable_date <= ?)
                """,
                (latest_market_date, latest_market_date),
            ).fetchone()[0]
            events_with_gain = conn.execute(
                """
                SELECT COUNT(*)
                FROM fact_institution_event
                WHERE return_to_now IS NOT NULL
                  AND tradable_date IS NOT NULL AND tradable_date != ''
                  AND (? IS NOT NULL AND tradable_date <= ?)
                """,
                (latest_market_date, latest_market_date),
            ).fetchone()[0]
            if total_events > 0 and events_with_gain / total_events < 0.5:
                returns_partial = True
                logger.info(
                    f"[data_completeness] 收益覆盖率 {events_with_gain}/{total_events} = "
                    f"{events_with_gain / total_events:.0%} < 50% → partial"
                )
        except Exception as e:
            logger.warning(f"[data_completeness] 收益覆盖率检测异常: {e}")
    if not industry_partial:
        try:
            coverage = summarize_industry_coverage(
                conn,
                "SELECT DISTINCT stock_code FROM inst_holdings WHERE stock_code IS NOT NULL",
            )
            total_holdings = coverage["total_codes"]
            with_industry = coverage["complete_codes"]
            if total_holdings > 0 and with_industry / total_holdings < 0.8:
                industry_partial = True
                logger.info(
                    f"[data_completeness] 行业覆盖率 {with_industry}/{total_holdings} = "
                    f"{with_industry / total_holdings:.0%} < 80% → partial"
                )
        except Exception as e:
            logger.warning(f"[data_completeness] 行业覆盖率检测异常: {e}")

    partial_by_step = {
        "build_profiles": returns_partial,
        "build_industry_stat": returns_partial or industry_partial,
        "build_trends": returns_partial or industry_partial,
    }
    completeness = "partial" if partial_by_step[step_id] else "complete"
    try:
        conn.execute(f"UPDATE {table} SET data_completeness = ?", (completeness,))
        conn.commit()
        if completeness == "partial":
            logger.info(f"[data_completeness] {table} → partial")
    except Exception as e:
        logger.warning(f"[data_completeness] 更新 {table} 完整度标记失败: {e}")
