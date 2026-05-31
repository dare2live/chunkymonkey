"""Institution profile mart runner for the updater pipeline."""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from routers.updater_runtime import _run_blocking_db_task
from services.holdings import refresh_stock_latest_cache
from services.pricing_policy import load_pricing_label_policy
from services.schema_versions import record_actual_version

logger = logging.getLogger("cm-api")


def _median(sorted_vals: list) -> Optional[float]:
    """严格 median: 偶数样本取中间两数均值，奇数取中间。"""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2


def _median_from_unsorted(values) -> Optional[float]:
    return _median(sorted(values))


def _parse_notice_date(s: Optional[str]):
    """fact_institution_event.notice_date 兼容 YYYYMMDD 和 YYYY-MM-DD。"""
    if not s:
        return None
    raw = str(s).strip()
    if not raw:
        return None
    try:
        if len(raw) >= 8 and raw[:8].isdigit():
            return datetime.strptime(raw[:8], "%Y%m%d")
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _followability_hint(safe_cnt, safe_wr30, eff30, high_cnt, high_wr30):
    """根据可跟统计给出简短提示。"""
    safe_cnt = safe_cnt or 0
    high_cnt = high_cnt or 0
    if safe_cnt < 5:
        return "样本偏少"
    if eff30 is not None and eff30 >= 80 and (safe_wr30 or 0) >= 60:
        return "可跟性强"
    if high_cnt >= 5 and safe_wr30 is not None and high_wr30 is not None and high_wr30 + 10 < safe_wr30:
        return "不宜追高"
    if eff30 is not None and eff30 >= 50 and (safe_wr30 or 0) >= 50:
        return "可跟性中等"
    return "信号损耗较大"


def _row_value(row, key, default=None):
    if row is None:
        return default
    try:
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


def _ensure_profile_pricing_columns(conn) -> None:
    try:
        conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN pricing_policy_id TEXT")
    except Exception:  # rule-compliance: ok evidence=duckdb-alter-column-idempotent
        pass
    try:
        conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN pricing_policy_hash TEXT")
    except Exception:  # rule-compliance: ok evidence=duckdb-alter-column-idempotent
        pass


def _build_inst_summaries(conn) -> dict:
    rows = conn.execute("""
        SELECT h.institution_id,
               COUNT(*) as stock_count,
               SUM(h.hold_market_cap) as total_cap,
               MAX(h.notice_date) as latest_notice
        FROM inst_holdings h
        INNER JOIN (
            SELECT stock_code, max_rd
            FROM _cache_stock_latest_rd
        ) lat ON h.stock_code = lat.stock_code AND h.report_date = lat.max_rd
        GROUP BY h.institution_id
    """).fetchall()
    return {row["institution_id"]: dict(row) for row in rows}


def _build_inst_stats_map(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id,
               COUNT(*) as total_events,
               COUNT(DISTINCT stock_code) as total_stocks,
               COUNT(DISTINCT report_date) as total_periods
        FROM fact_institution_event
        GROUP BY institution_id
    """).fetchall()
    return {
        row["institution_id"]: {
            "total_events": row["total_events"],
            "total_stocks": row["total_stocks"],
            "total_periods": row["total_periods"],
        }
        for row in rows
    }


def _build_returns_map(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id,
               AVG(gain_10d) AS avg10,
               AVG(gain_30d) AS avg30,
               AVG(gain_60d) AS avg60,
               AVG(gain_120d) AS avg120
        FROM fact_institution_event
        WHERE gain_30d IS NOT NULL
        GROUP BY institution_id
    """).fetchall()
    return {row["institution_id"]: row for row in rows}


def _build_win_rate_map(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id,
               100.0 * SUM(CASE WHEN gain_30d > 0 THEN 1 ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN gain_30d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr30,
               100.0 * SUM(CASE WHEN gain_60d > 0 THEN 1 ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr60,
               100.0 * SUM(CASE WHEN gain_90d > 0 THEN 1 ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN gain_90d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr90,
               100.0 * SUM(CASE WHEN gain_120d > 0 THEN 1 ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN gain_120d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr120,
               100.0 * SUM(CASE WHEN COALESCE(gain_30d, 0) > 0 OR COALESCE(gain_60d, 0) > 0
                                      OR COALESCE(gain_90d, 0) > 0 OR COALESCE(gain_120d, 0) > 0
                                 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS total_wr
        FROM fact_institution_event
        GROUP BY institution_id
    """).fetchall()
    return {row["institution_id"]: row for row in rows}


def _build_drawdown_medians(conn, *, buy_only: bool = False) -> dict:
    where = "gain_30d IS NOT NULL"
    if buy_only:
        where += " AND event_type IN ('new_entry', 'increase')"
    rows = conn.execute(f"""
        SELECT institution_id, max_drawdown_30d, max_drawdown_60d
        FROM fact_institution_event
        WHERE {where}
    """).fetchall()
    dd30_by_inst: dict[str, list] = defaultdict(list)
    dd60_by_inst: dict[str, list] = defaultdict(list)
    for row in rows:
        inst_id = row["institution_id"]
        if row["max_drawdown_30d"] is not None:
            dd30_by_inst[inst_id].append(row["max_drawdown_30d"])
        if row["max_drawdown_60d"] is not None:
            dd60_by_inst[inst_id].append(row["max_drawdown_60d"])
    inst_ids = set(dd30_by_inst) | set(dd60_by_inst)
    return {
        inst_id: (
            _median_from_unsorted(dd30_by_inst.get(inst_id, [])),
            _median_from_unsorted(dd60_by_inst.get(inst_id, [])),
        )
        for inst_id in inst_ids
    }


def _build_buy_stats_map(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id,
               COUNT(*) as cnt,
               AVG(gain_30d) as avg30,
               AVG(gain_60d) as avg60,
               AVG(gain_120d) as avg120,
               SUM(CASE WHEN gain_30d > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as wr30,
               SUM(CASE WHEN gain_60d > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as wr60,
               SUM(CASE WHEN gain_120d > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as wr120
        FROM fact_institution_event
        WHERE event_type IN ('new_entry', 'increase')
          AND gain_30d IS NOT NULL
        GROUP BY institution_id
    """).fetchall()
    return {row["institution_id"]: row for row in rows}


def _build_exit_map(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id,
               COUNT(*) AS cnt,
               AVG(gain_30d) AS post_avg30,
               AVG(gain_60d) AS post_avg60,
               AVG(gain_120d) AS post_avg120,
               100.0 * SUM(CASE WHEN gain_30d <= 0 THEN 1 ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN gain_30d IS NOT NULL THEN 1 ELSE 0 END), 0) AS avoid30,
               100.0 * SUM(CASE WHEN gain_60d <= 0 THEN 1 ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0) AS avoid60,
               100.0 * SUM(CASE WHEN gain_120d <= 0 THEN 1 ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN gain_120d IS NOT NULL THEN 1 ELSE 0 END), 0) AS avoid120
        FROM fact_institution_event
        WHERE event_type IN ('decrease', 'exit')
        GROUP BY institution_id
    """).fetchall()
    return {row["institution_id"]: row for row in rows}


def _build_follow_stats_map(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id,
               AVG(CASE WHEN premium_pct IS NOT NULL THEN premium_pct END) as avg_premium,
               COUNT(CASE WHEN premium_pct <= 5 THEN 1 END) as safe_cnt,
               AVG(CASE WHEN premium_pct <= 5 THEN gain_30d END) as safe_avg30,
               AVG(CASE WHEN premium_pct <= 5 THEN max_drawdown_30d END) as safe_dd30,
               COALESCE(
                   SUM(CASE WHEN premium_pct <= 5 AND gain_30d > 0 THEN 1 ELSE 0 END) * 100.0 /
                   NULLIF(SUM(CASE WHEN premium_pct <= 5 THEN 1 ELSE 0 END), 0), 0) as safe_wr30,
               COUNT(CASE WHEN premium_bucket = 'discount' THEN 1 END) as discount_cnt,
               COALESCE(
                   SUM(CASE WHEN premium_bucket = 'discount' AND gain_30d > 0 THEN 1 ELSE 0 END) * 100.0 /
                   NULLIF(SUM(CASE WHEN premium_bucket = 'discount' THEN 1 ELSE 0 END), 0), 0) as discount_wr30,
               COUNT(CASE WHEN premium_bucket = 'near_cost' THEN 1 END) as near_cnt,
               COALESCE(
                   SUM(CASE WHEN premium_bucket = 'near_cost' AND gain_30d > 0 THEN 1 ELSE 0 END) * 100.0 /
                   NULLIF(SUM(CASE WHEN premium_bucket = 'near_cost' THEN 1 ELSE 0 END), 0), 0) as near_wr30,
               COUNT(CASE WHEN premium_bucket = 'premium' THEN 1 END) as premium_cnt,
               COALESCE(
                   SUM(CASE WHEN premium_bucket = 'premium' AND gain_30d > 0 THEN 1 ELSE 0 END) * 100.0 /
                   NULLIF(SUM(CASE WHEN premium_bucket = 'premium' THEN 1 ELSE 0 END), 0), 0) as premium_wr30,
               COUNT(CASE WHEN premium_bucket = 'high_premium' THEN 1 END) as high_cnt,
               COALESCE(
                   SUM(CASE WHEN premium_bucket = 'high_premium' AND gain_30d > 0 THEN 1 ELSE 0 END) * 100.0 /
                   NULLIF(SUM(CASE WHEN premium_bucket = 'high_premium' THEN 1 ELSE 0 END), 0), 0) as high_wr30
        FROM fact_institution_event
        WHERE event_type IN ('new_entry', 'increase')
          AND gain_30d IS NOT NULL
        GROUP BY institution_id
    """).fetchall()
    return {row["institution_id"]: row for row in rows}


def _build_holding_median_days(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id, stock_code, event_type, notice_date
        FROM fact_institution_event
        WHERE notice_date IS NOT NULL AND notice_date != ''
        ORDER BY institution_id, stock_code, report_date
    """).fetchall()
    entries: dict[tuple[str, str], str] = {}
    closed_periods: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        inst_id = row["institution_id"]
        stock_code = row["stock_code"]
        key = (inst_id, stock_code)
        if row["event_type"] == "new_entry":
            entries[key] = row["notice_date"]
        elif row["event_type"] == "exit" and key in entries:
            entry_date = _parse_notice_date(entries[key])
            exit_date = _parse_notice_date(row["notice_date"])
            if entry_date and exit_date:
                days = (exit_date - entry_date).days
                if days > 0:
                    closed_periods[inst_id].append(days)
            entries.pop(key, None)
    return {
        inst_id: int(median)
        for inst_id, periods in closed_periods.items()
        if (median := _median_from_unsorted(periods)) is not None
    }


def _build_current_avg_held_map(conn) -> dict:
    rows = conn.execute("""
        SELECT institution_id, AVG(current_held_days) AS avg_held_days
        FROM mart_current_relationship
        WHERE current_held_days IS NOT NULL
        GROUP BY institution_id
    """).fetchall()
    return {row["institution_id"]: int(row["avg_held_days"]) for row in rows if row["avg_held_days"]}


def _build_recent_event_map(conn) -> dict:
    rows = conn.execute("""
        SELECT e.institution_id,
               COUNT(CASE WHEN e.event_type = 'new_entry' THEN 1 END) AS recent_new_entry_count,
               COUNT(CASE WHEN e.event_type = 'increase' THEN 1 END) AS recent_increase_count,
               COUNT(CASE WHEN e.event_type = 'exit' THEN 1 END) AS recent_exit_count
        FROM fact_institution_event e
        INNER JOIN mart_current_relationship m
            ON e.institution_id = m.institution_id AND e.stock_code = m.stock_code
            AND e.report_date = m.report_date
        GROUP BY e.institution_id
    """).fetchall()
    return {row["institution_id"]: row for row in rows}


def _build_profile_rows(conn, institutions, pricing_policy, now: str, should_stop=None) -> list[tuple]:
    inst_summaries = _build_inst_summaries(conn)
    inst_stats_map = _build_inst_stats_map(conn)
    returns_map = _build_returns_map(conn)
    win_rate_map = _build_win_rate_map(conn)
    drawdown_medians = _build_drawdown_medians(conn)
    buy_stats_map = _build_buy_stats_map(conn)
    buy_drawdown_medians = _build_drawdown_medians(conn, buy_only=True)
    exit_map = _build_exit_map(conn)
    follow_stats_map = _build_follow_stats_map(conn)
    holding_median_days = _build_holding_median_days(conn)
    current_avg_held_map = _build_current_avg_held_map(conn)
    recent_event_map = _build_recent_event_map(conn)
    empty_stats = {"total_events": 0, "total_stocks": 0, "total_periods": 0}

    rows = []
    for inst in institutions:
        if should_stop is not None:
            should_stop()
        inst_id = inst["id"]
        stats = inst_stats_map.get(inst_id, empty_stats)
        returns = returns_map.get(inst_id)
        win_rates = win_rate_map.get(inst_id)
        buy_stats = buy_stats_map.get(inst_id)
        exit_row = exit_map.get(inst_id)
        follow_stats = follow_stats_map.get(inst_id)
        median_dd30, median_dd60 = drawdown_medians.get(inst_id, (None, None))
        buy_median_dd30, buy_median_dd60 = buy_drawdown_medians.get(inst_id, (None, None))

        buy_avg30 = _row_value(buy_stats, "avg30")
        safe_avg30 = _row_value(follow_stats, "safe_avg30")
        signal_transfer_eff = None
        if buy_avg30 is not None and buy_avg30 > 0 and safe_avg30 is not None:
            signal_transfer_eff = round(safe_avg30 / buy_avg30 * 100, 2)
        safe_wr30 = _row_value(follow_stats, "safe_wr30", 0)
        high_wr30 = _row_value(follow_stats, "high_wr30", 0)

        follow_hint = _followability_hint(
            _row_value(follow_stats, "safe_cnt", 0),
            safe_wr30,
            signal_transfer_eff,
            _row_value(follow_stats, "high_cnt", 0),
            high_wr30,
        )

        summary = inst_summaries.get(inst_id, {})
        recent = recent_event_map.get(inst_id)
        rows.append((
            inst_id, inst["name"], inst["display_name"], inst["type"],
            stats["total_events"], stats["total_stocks"], stats["total_periods"],
            _row_value(returns, "avg10"), _row_value(returns, "avg30"),
            _row_value(returns, "avg60"), _row_value(returns, "avg120"),
            _row_value(win_rates, "wr30"), _row_value(win_rates, "wr60"),
            _row_value(win_rates, "wr90"), _row_value(win_rates, "wr120"),
            _row_value(win_rates, "total_wr"), median_dd30, median_dd60,
            summary.get("stock_count", 0), summary.get("total_cap"), summary.get("latest_notice"),
            _row_value(recent, "recent_new_entry_count", 0),
            _row_value(recent, "recent_increase_count", 0),
            _row_value(recent, "recent_exit_count", 0),
            _row_value(buy_stats, "cnt", 0),
            _row_value(buy_stats, "avg30"),
            _row_value(buy_stats, "avg60"),
            _row_value(buy_stats, "avg120"),
            _row_value(buy_stats, "wr30"),
            _row_value(buy_stats, "wr60"),
            _row_value(buy_stats, "wr120"),
            buy_median_dd30, buy_median_dd60,
            _row_value(follow_stats, "avg_premium"),
            _row_value(follow_stats, "safe_cnt", 0),
            safe_wr30,
            _row_value(follow_stats, "safe_avg30"),
            _row_value(follow_stats, "safe_dd30"),
            _row_value(follow_stats, "discount_cnt", 0),
            _row_value(follow_stats, "discount_wr30", 0),
            _row_value(follow_stats, "near_cnt", 0),
            _row_value(follow_stats, "near_wr30", 0),
            _row_value(follow_stats, "premium_cnt", 0),
            _row_value(follow_stats, "premium_wr30", 0),
            _row_value(follow_stats, "high_cnt", 0),
            high_wr30,
            signal_transfer_eff,
            follow_hint,
            holding_median_days.get(inst_id),
            current_avg_held_map.get(inst_id),
            _row_value(exit_row, "cnt", 0),
            _row_value(exit_row, "post_avg30"),
            _row_value(exit_row, "post_avg60"),
            _row_value(exit_row, "post_avg120"),
            _row_value(exit_row, "avoid30"),
            _row_value(exit_row, "avoid60"),
            _row_value(exit_row, "avoid120"),
            pricing_policy.policy_id,
            pricing_policy.policy_hash(),
            now,
        ))
    return rows


def _step_build_profiles_sync(conn, should_stop=None) -> int:
    """计算机构画像 mart_institution_profile."""
    refresh_stock_latest_cache(conn)
    pricing_policy = load_pricing_label_policy()
    _ensure_profile_pricing_columns(conn)
    now = datetime.now().isoformat()
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM mart_institution_profile")
        institutions = conn.execute(
            "SELECT id, name, display_name, type FROM inst_institutions WHERE enabled = 1 AND blacklisted = 0 AND merged_into IS NULL"
        ).fetchall()
        rows = _build_profile_rows(conn, institutions, pricing_policy, now, should_stop=should_stop)
        if rows:
            conn.executemany("""
                INSERT OR REPLACE INTO mart_institution_profile
                (institution_id, institution_name, display_name, inst_type,
                 total_events, total_stocks, total_periods,
                 avg_gain_10d, avg_gain_30d, avg_gain_60d, avg_gain_120d,
                 win_rate_30d, win_rate_60d, win_rate_90d, win_rate_120d, total_win_rate,
                 median_max_drawdown_30d, median_max_drawdown_60d,
                 current_stock_count, current_total_cap, latest_notice_date,
                 recent_new_entry_count, recent_increase_count, recent_exit_count,
                 buy_event_count, buy_avg_gain_30d, buy_avg_gain_60d, buy_avg_gain_120d,
                 buy_win_rate_30d, buy_win_rate_60d, buy_win_rate_120d,
                 buy_median_max_drawdown_30d, buy_median_max_drawdown_60d,
                 avg_premium_pct, safe_follow_event_count, safe_follow_win_rate_30d,
                 safe_follow_avg_gain_30d, safe_follow_avg_drawdown_30d,
                 premium_discount_event_count, premium_discount_win_rate_30d,
                 premium_near_cost_event_count, premium_near_cost_win_rate_30d,
                 premium_premium_event_count, premium_premium_win_rate_30d,
                 premium_high_event_count, premium_high_win_rate_30d,
                 signal_transfer_efficiency_30d, followability_hint,
                 historical_median_holding_days, current_avg_held_days,
                 exit_event_count, exit_post_avg_gain_30d, exit_post_avg_gain_60d, exit_post_avg_gain_120d,
                 exit_avoid_loss_rate_30d, exit_avoid_loss_rate_60d, exit_avoid_loss_rate_120d,
                 pricing_policy_id, pricing_policy_hash,
                 updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
        record_actual_version(conn, "mart_institution_profile")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"[画像] 完成: {len(rows)} 个机构")
    return len(rows)


async def _step_build_profiles(conn, should_stop=None) -> int:
    """计算机构画像 mart_institution_profile."""
    return await _run_blocking_db_task(
        lambda worker_conn: _step_build_profiles_sync(worker_conn, should_stop=should_stop)
    )
