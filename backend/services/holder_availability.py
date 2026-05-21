"""PIT availability helpers for top holder period facts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    except Exception:
        return set()
    columns: set[str] = set()
    for row in rows:
        if hasattr(row, "keys"):
            columns.add(str(row["name"]))
        else:
            columns.add(str(row[1]))
    return columns


def normalize_yyyymmdd(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return None


def regulatory_notice_date_for_report_date(report_date: Any) -> str | None:
    """Return conservative statutory disclosure deadline for a report period."""

    yyyymmdd = normalize_yyyymmdd(report_date)
    if not yyyymmdd:
        return None
    year = int(yyyymmdd[:4])
    mmdd = yyyymmdd[4:8]
    if mmdd == "1231":
        return f"{year + 1}0430"
    if mmdd == "0331":
        return f"{year}0430"
    if mmdd == "0630":
        return f"{year}0831"
    if mmdd == "0930":
        return f"{year}1031"
    dt = datetime.strptime(yyyymmdd, "%Y%m%d").date() + timedelta(days=90)
    return dt.strftime("%Y%m%d")


def _plus_days(yyyymmdd: str, days: int) -> str:
    dt = datetime.strptime(yyyymmdd, "%Y%m%d").date() + timedelta(days=days)
    return dt.strftime("%Y%m%d")


def _day_gap(start_yyyymmdd: str, end_yyyymmdd: str) -> int:
    start = datetime.strptime(start_yyyymmdd, "%Y%m%d").date()
    end = datetime.strptime(end_yyyymmdd, "%Y%m%d").date()
    return (end - start).days


def next_trading_day_after(conn, yyyymmdd: str) -> str | None:
    """Return the next trading day strictly after yyyymmdd, falling back to calendar day."""

    target = normalize_yyyymmdd(yyyymmdd)
    if not target:
        return None
    if conn is None:
        return _plus_days(target, 1)
    try:
        bounds = conn.execute(
            """
            SELECT MIN(REPLACE(CAST(trade_date AS VARCHAR), '-', '')) AS min_date,
                   MAX(REPLACE(CAST(trade_date AS VARCHAR), '-', '')) AS max_date
            FROM dim_trading_calendar
            WHERE is_trading = 1
            """
        ).fetchone()
        min_date = bounds["min_date"] if hasattr(bounds, "keys") else bounds[0]
        max_date = bounds["max_date"] if hasattr(bounds, "keys") else bounds[1]
        if not min_date or not max_date or target >= max_date:
            return _plus_days(target, 1)
        if target < min_date and _day_gap(target, min_date) > 10:
            return _plus_days(target, 1)
        row = conn.execute(
            """
            SELECT REPLACE(CAST(trade_date AS VARCHAR), '-', '') AS trade_date
            FROM dim_trading_calendar
            WHERE is_trading = 1
              AND REPLACE(CAST(trade_date AS VARCHAR), '-', '') > ?
            ORDER BY REPLACE(CAST(trade_date AS VARCHAR), '-', '')
            LIMIT 1
            """,
            (target,),
        ).fetchone()
    except Exception:
        return _plus_days(target, 1)
    if not row:
        return _plus_days(target, 1)
    return row["trade_date"] if hasattr(row, "keys") else row[0]


def derive_holder_availability_dates(
    conn,
    *,
    report_date: Any,
    notice_date: Any = None,
    effective_date: Any = None,
    page_update_date: Any = None,
    fetched_at: Any = None,
) -> tuple[str | None, str | None, str | None]:
    """Return notice, effective, and source for holder period availability."""

    normalized_notice = normalize_yyyymmdd(notice_date)
    source = "source_notice" if normalized_notice else None
    regulatory_deadline = regulatory_notice_date_for_report_date(report_date)
    if normalized_notice is None:
        normalized_page_update = normalize_yyyymmdd(page_update_date)
        normalized_report = normalize_yyyymmdd(report_date)
        if normalized_page_update and (
            normalized_report is None or normalized_page_update >= normalized_report
        ):
            normalized_notice = normalized_page_update
            source = "page_update_date"
    if normalized_notice is None:
        normalized_fetched = normalize_yyyymmdd(fetched_at)
        normalized_report = normalize_yyyymmdd(report_date)
        today = datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: fetched_at <= 物理 today 健康检查
        if (
            normalized_fetched
            and normalized_report
            and normalized_fetched >= normalized_report
            and normalized_fetched <= today
            and regulatory_deadline
            and regulatory_deadline > today
        ):
            normalized_notice = normalized_fetched
            source = "fetched_at_observed"
    if normalized_notice is None:
        normalized_notice = regulatory_deadline
        source = "regulatory_deadline" if normalized_notice else None
    normalized_effective = normalize_yyyymmdd(effective_date)
    if normalized_notice and normalized_effective is None:
        normalized_effective = next_trading_day_after(conn, normalized_notice)
    return normalized_notice, normalized_effective, source


def enrich_holder_rows_with_availability(conn, rows: Iterable[dict]) -> list[dict]:
    enriched = []
    for row in rows:
        item = dict(row)
        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date=item.get("report_date"),
            notice_date=item.get("notice_date"),
            effective_date=item.get("effective_date"),
            page_update_date=item.get("page_update_date"),
            fetched_at=item.get("fetched_at"),
        )
        item["notice_date"] = notice
        item["effective_date"] = effective
        item["availability_source"] = source
        enriched.append(item)
    return enriched


def backfill_holder_period_availability(conn) -> dict:
    """Backfill missing PIT availability dates on fact_top10_holder_period."""

    return backfill_holder_period_availability_rows(conn, overwrite_regulatory=False)


def backfill_future_holder_period_page_update_availability(conn) -> dict:
    """Upgrade future regulatory fallback dates to observed TDX F10 page-update dates."""

    columns = _table_columns(conn, "fact_top10_holder_period")
    required = {"notice_date", "effective_date", "availability_source", "page_update_date", "report_date"}
    if not required <= columns:
        return {
            "status": "missing_columns",
            "updated_rows": 0,
            "missing_columns": sorted(required - columns),
        }

    page_norm = (
        "substr(REPLACE(REPLACE(REPLACE(CAST(page_update_date AS VARCHAR), '-', ''), '/', ''), '.', ''), 1, 8)"
    )
    report_norm = (
        "substr(REPLACE(REPLACE(REPLACE(CAST(report_date AS VARCHAR), '-', ''), '/', ''), '.', ''), 1, 8)"
    )
    notice_norm = (
        "substr(REPLACE(REPLACE(REPLACE(CAST(notice_date AS VARCHAR), '-', ''), '/', ''), '.', ''), 1, 8)"
    )
    page_iso = (
        f"substr({page_norm},1,4) || '-' || substr({page_norm},5,2) || '-' || substr({page_norm},7,2)"
    )
    notice_iso = (
        f"substr({notice_norm},1,4) || '-' || substr({notice_norm},5,2) || '-' || substr({notice_norm},7,2)"
    )
    candidate_where = f"""
        availability_source = 'regulatory_deadline'
        AND page_update_date IS NOT NULL
        AND CAST(page_update_date AS VARCHAR) != ''
        AND length({page_norm}) = 8
        AND length({report_norm}) = 8
        AND {page_norm} >= {report_norm}
        AND TRY_CAST({page_iso} AS DATE) <= CURRENT_DATE
        AND TRY_CAST({notice_iso} AS DATE) > CURRENT_DATE
    """
    before = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_top10_holder_period WHERE {candidate_where}"
    ).fetchone()
    before_count = int((before["n"] if hasattr(before, "keys") else before[0]) or 0)
    if before_count == 0:
        return {"status": "ok", "updated_rows": 0, "remaining_candidate_rows": 0}

    page_dates = [
        row["page_date"] if hasattr(row, "keys") else row[0]
        for row in conn.execute(
            f"""
            SELECT DISTINCT {page_norm} AS page_date
              FROM fact_top10_holder_period
             WHERE {candidate_where}
             ORDER BY page_date
            """
        ).fetchall()
    ]
    update_rows = []
    for page_date in page_dates:
        effective_date = next_trading_day_after(conn, page_date)
        update_rows.append((page_date, effective_date, page_date))
    if update_rows:
        conn.executemany(
            f"""
            UPDATE fact_top10_holder_period
               SET notice_date = ?,
                   effective_date = ?,
                   availability_source = 'page_update_date'
             WHERE {candidate_where}
               AND {page_norm} = ?
            """,
            update_rows,
        )
    conn.commit()
    after = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_top10_holder_period WHERE {candidate_where}"
    ).fetchone()
    after_count = int((after["n"] if hasattr(after, "keys") else after[0]) or 0)
    return {
        "status": "ok",
        "updated_rows": max(before_count - after_count, 0),
        "remaining_candidate_rows": after_count,
    }


def backfill_future_holder_period_fetched_at_availability(conn) -> dict:
    """Upgrade future regulatory fallback dates to observed raw fetch dates.

    This is deliberately limited to rows whose regulatory fallback is still in
    the future. For older historical periods, a current scrape timestamp is not
    a historical disclosure date and must not replace the statutory fallback.
    """

    columns = _table_columns(conn, "fact_top10_holder_period")
    required = {"notice_date", "effective_date", "availability_source", "fetched_at", "report_date"}
    if not required <= columns:
        return {
            "status": "missing_columns",
            "updated_rows": 0,
            "missing_columns": sorted(required - columns),
        }

    fetched_norm = (
        "substr(REPLACE(REPLACE(REPLACE(REPLACE(CAST(fetched_at AS VARCHAR), '-', ''), '/', ''), '.', ''), 'T', ''), 1, 8)"
    )
    report_norm = (
        "substr(REPLACE(REPLACE(REPLACE(CAST(report_date AS VARCHAR), '-', ''), '/', ''), '.', ''), 1, 8)"
    )
    notice_norm = (
        "substr(REPLACE(REPLACE(REPLACE(CAST(notice_date AS VARCHAR), '-', ''), '/', ''), '.', ''), 1, 8)"
    )
    fetched_iso = (
        f"substr({fetched_norm},1,4) || '-' || substr({fetched_norm},5,2) || '-' || substr({fetched_norm},7,2)"
    )
    notice_iso = (
        f"substr({notice_norm},1,4) || '-' || substr({notice_norm},5,2) || '-' || substr({notice_norm},7,2)"
    )
    candidate_where = f"""
        availability_source = 'regulatory_deadline'
        AND fetched_at IS NOT NULL
        AND CAST(fetched_at AS VARCHAR) != ''
        AND length({fetched_norm}) = 8
        AND length({report_norm}) = 8
        AND {fetched_norm} >= {report_norm}
        AND TRY_CAST({fetched_iso} AS DATE) <= CURRENT_DATE
        AND TRY_CAST({notice_iso} AS DATE) > CURRENT_DATE
    """
    before = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_top10_holder_period WHERE {candidate_where}"
    ).fetchone()
    before_count = int((before["n"] if hasattr(before, "keys") else before[0]) or 0)
    if before_count == 0:
        return {"status": "ok", "updated_rows": 0, "remaining_candidate_rows": 0}

    fetched_dates = [
        row["fetched_date"] if hasattr(row, "keys") else row[0]
        for row in conn.execute(
            f"""
            SELECT DISTINCT {fetched_norm} AS fetched_date
              FROM fact_top10_holder_period
             WHERE {candidate_where}
             ORDER BY fetched_date
            """
        ).fetchall()
    ]
    update_rows = []
    for fetched_date in fetched_dates:
        effective_date = next_trading_day_after(conn, fetched_date)
        update_rows.append((fetched_date, effective_date, fetched_date))
    if update_rows:
        conn.executemany(
            f"""
            UPDATE fact_top10_holder_period
               SET notice_date = ?,
                   effective_date = ?,
                   availability_source = 'fetched_at_observed'
             WHERE {candidate_where}
               AND {fetched_norm} = ?
            """,
            update_rows,
        )
    conn.commit()
    after = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_top10_holder_period WHERE {candidate_where}"
    ).fetchone()
    after_count = int((after["n"] if hasattr(after, "keys") else after[0]) or 0)
    return {
        "status": "ok",
        "updated_rows": max(before_count - after_count, 0),
        "remaining_candidate_rows": after_count,
    }


def backfill_inst_holdings_notice_dates(conn) -> dict:
    """Backfill inst_holdings PIT availability fields from canonical TDX holder facts."""

    try:
        before = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM inst_holdings
             WHERE notice_date IS NULL OR notice_date = ''
            """
        ).fetchone()
    except Exception:
        return {"updated_rows": 0, "remaining_missing_rows": 0, "status": "missing_inst_holdings"}
    before_missing = int((before["n"] if hasattr(before, "keys") else before[0]) or 0)

    inst_columns = _table_columns(conn, "inst_holdings")
    holder_columns = _table_columns(conn, "fact_top10_holder_period")
    has_holder_source = "availability_source" in holder_columns
    has_notice_source = "notice_date_source" in inst_columns
    has_source_notice_date = "source_notice_date" in inst_columns
    has_availability_deadline = "availability_deadline" in inst_columns

    source_expr = (
        "COALESCE(NULLIF(availability_source, ''), 'unknown')"
        if has_holder_source
        else "'unknown'"
    )
    source_sort_expr = (
        """
        CASE
            WHEN availability_source = 'source_notice' THEN 0
            WHEN availability_source = 'page_update_date' THEN 1
            WHEN availability_source = 'fetched_at_observed' THEN 2
            WHEN availability_source = 'regulatory_deadline' THEN 3
            ELSE 4
        END
        """
        if has_holder_source
        else "2"
    )
    source_notice_expr = (
        "CASE WHEN availability_source = 'source_notice' THEN notice_date ELSE NULL END"
        if has_holder_source
        else "NULL"
    )
    deadline_expr = (
        "CASE WHEN availability_source = 'regulatory_deadline' THEN notice_date ELSE NULL END"
        if has_holder_source
        else "NULL"
    )

    set_clauses = ["notice_date = src.notice_date"]
    update_conditions = ["h.notice_date IS DISTINCT FROM src.notice_date"]
    if has_notice_source:
        set_clauses.append("notice_date_source = src.notice_date_source")
        update_conditions.append("h.notice_date_source IS DISTINCT FROM src.notice_date_source")
    if has_source_notice_date:
        set_clauses.append("source_notice_date = src.source_notice_date")
        update_conditions.append("h.source_notice_date IS DISTINCT FROM src.source_notice_date")
    if has_availability_deadline:
        set_clauses.append(
            "availability_deadline = COALESCE(src.availability_deadline, h.availability_deadline)"
        )
        update_conditions.append(
            "src.availability_deadline IS NOT NULL "
            "AND h.availability_deadline IS DISTINCT FROM src.availability_deadline"
        )

    conn.execute(
        f"""
        UPDATE inst_holdings AS h
           SET {", ".join(set_clauses)}
          FROM (
                SELECT stock_code, report_date, holder_name, notice_date,
                       notice_date_source, source_notice_date, availability_deadline
                  FROM (
                        SELECT stock_code, report_date, holder_name, notice_date,
                               {source_expr} AS notice_date_source,
                               {source_notice_expr} AS source_notice_date,
                               {deadline_expr} AS availability_deadline,
                               ROW_NUMBER() OVER (
                                   PARTITION BY stock_code, report_date, holder_name
                                   ORDER BY {source_sort_expr}, notice_date DESC
                               ) AS rn
                          FROM fact_top10_holder_period
                         WHERE notice_date IS NOT NULL AND notice_date != ''
                           AND holder_set = 'free'
                           AND NOT COALESCE(is_secondary_class, FALSE)
                           AND NOT COALESCE(is_exit_row, FALSE)
                       )
                 WHERE rn = 1
               ) AS src
         WHERE h.stock_code = src.stock_code
           AND h.report_date = src.report_date
           AND h.holder_name = src.holder_name
           AND ({" OR ".join(update_conditions)})
        """
    )
    after = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM inst_holdings
         WHERE notice_date IS NULL OR notice_date = ''
        """
    ).fetchone()
    after_missing = int((after["n"] if hasattr(after, "keys") else after[0]) or 0)
    return {
        "updated_rows": max(before_missing - after_missing, 0),
        "remaining_missing_rows": after_missing,
        "status": "ok",
    }


def backfill_institution_event_notice_sources(conn) -> dict:
    """Backfill event notice-source lineage without regenerating event returns."""

    event_columns = _table_columns(conn, "fact_institution_event")
    required_event_columns = {
        "notice_date_source",
        "source_notice_date",
        "availability_deadline",
    }
    if not event_columns:
        return {"status": "missing_fact_institution_event", "updated_rows": 0}
    if not required_event_columns <= event_columns:
        return {
            "status": "missing_event_source_columns",
            "updated_rows": 0,
            "missing_columns": sorted(required_event_columns - event_columns),
        }

    holder_columns = _table_columns(conn, "fact_top10_holder_period")
    if not holder_columns:
        return {"status": "missing_fact_top10_holder_period", "updated_rows": 0}
    has_holder_source = "availability_source" in holder_columns
    source_expr = (
        "COALESCE(NULLIF(availability_source, ''), 'unknown')"
        if has_holder_source
        else "'unknown'"
    )
    source_sort_expr = (
        """
        CASE
            WHEN availability_source = 'source_notice' THEN 0
            WHEN availability_source = 'page_update_date' THEN 1
            WHEN availability_source = 'fetched_at_observed' THEN 2
            WHEN availability_source = 'regulatory_deadline' THEN 3
            ELSE 4
        END
        """
        if has_holder_source
        else "2"
    )
    source_notice_expr = (
        "CASE WHEN availability_source = 'source_notice' THEN notice_date ELSE NULL END"
        if has_holder_source
        else "NULL"
    )
    deadline_expr = (
        "CASE WHEN availability_source = 'regulatory_deadline' THEN notice_date ELSE NULL END"
        if has_holder_source
        else "NULL"
    )
    missing_where = """
        notice_date_source IS NULL OR notice_date_source = ''
        OR (
            notice_date_source = 'source_notice'
            AND (source_notice_date IS NULL OR source_notice_date = '')
        )
        OR (
            notice_date_source = 'regulatory_deadline'
            AND (availability_deadline IS NULL OR availability_deadline = '')
        )
    """
    event_missing_where = """
        e.notice_date_source IS NULL OR e.notice_date_source = ''
        OR (
            e.notice_date_source = 'source_notice'
            AND (e.source_notice_date IS NULL OR e.source_notice_date = '')
        )
        OR (
            e.notice_date_source = 'regulatory_deadline'
            AND (e.availability_deadline IS NULL OR e.availability_deadline = '')
        )
    """
    event_rank_expr = """
        CASE
            WHEN e.notice_date_source = 'source_notice' THEN 0
            WHEN e.notice_date_source = 'page_update_date' THEN 1
            WHEN e.notice_date_source = 'fetched_at_observed' THEN 2
            WHEN e.notice_date_source = 'regulatory_deadline' THEN 3
            ELSE 4
        END
    """
    before = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_institution_event WHERE {missing_where}"
    ).fetchone()
    before_missing = int((before["n"] if hasattr(before, "keys") else before[0]) or 0)
    event_indexes = (
        "idx_event_type",
        "idx_event_date",
        "idx_event_notice",
        "idx_event_notice_source",
        "idx_fie_stock",
        "idx_fie_holder",
        "idx_fie_notice_source",
    )
    try:
        conn.execute(";\n".join(f"DROP INDEX IF EXISTS {index_name}" for index_name in event_indexes))
    except Exception:  # rule-compliance: ok evidence=duckdb-drop-index-idempotent
        pass

    holder_source_cte = f"""
        SELECT stock_code, report_date, holder_name, notice_date,
               notice_date_source, source_notice_date, availability_deadline,
               notice_rank
          FROM (
                SELECT stock_code, report_date, holder_name, notice_date,
                       {source_expr} AS notice_date_source,
                       {source_notice_expr} AS source_notice_date,
                       {deadline_expr} AS availability_deadline,
                       {source_sort_expr} AS notice_rank,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code, report_date, holder_name
                           ORDER BY {source_sort_expr}, notice_date DESC
                       ) AS rn
                  FROM fact_top10_holder_period
                 WHERE notice_date IS NOT NULL AND notice_date != ''
                   AND holder_set = 'free'
                   AND NOT COALESCE(is_secondary_class, FALSE)
                   AND NOT COALESCE(is_exit_row, FALSE)
               )
         WHERE rn = 1
    """
    period_source_cte = f"""
        SELECT stock_code, report_date, notice_date, notice_date_source,
               source_notice_date, availability_deadline, notice_rank
          FROM (
                SELECT stock_code, report_date, notice_date,
                       {source_expr} AS notice_date_source,
                       {source_notice_expr} AS source_notice_date,
                       {deadline_expr} AS availability_deadline,
                       {source_sort_expr} AS notice_rank,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code, report_date
                           ORDER BY {source_sort_expr}, notice_date DESC
                       ) AS rn
                  FROM fact_top10_holder_period
                 WHERE stock_code IS NOT NULL
                   AND notice_date IS NOT NULL AND notice_date != ''
                   AND holder_set = 'free'
                   AND NOT COALESCE(is_secondary_class, FALSE)
                   AND NOT COALESCE(is_exit_row, FALSE)
               )
         WHERE rn = 1
    """

    conn.execute(
        f"""
        UPDATE fact_institution_event AS e
           SET notice_date = src.notice_date,
               notice_date_source = src.notice_date_source,
               source_notice_date = src.source_notice_date,
               availability_deadline = COALESCE(src.availability_deadline, e.availability_deadline)
          FROM ({holder_source_cte}) AS src
         WHERE e.stock_code = src.stock_code
           AND e.report_date = src.report_date
           AND e.holder_name = src.holder_name
           AND (
               {event_missing_where}
               OR src.notice_rank < {event_rank_expr}
               OR e.notice_date IS DISTINCT FROM src.notice_date
           )
        """
    )
    conn.execute(
        f"""
        UPDATE fact_institution_event AS e
           SET notice_date = src.notice_date,
               notice_date_source = src.notice_date_source,
               source_notice_date = src.source_notice_date,
               availability_deadline = COALESCE(src.availability_deadline, e.availability_deadline)
          FROM ({period_source_cte}) AS src
         WHERE e.stock_code = src.stock_code
           AND e.report_date = src.report_date
           AND (
               {event_missing_where}
               OR src.notice_rank < {event_rank_expr}
               OR e.notice_date IS DISTINCT FROM src.notice_date
           )
        """
    )
    conn.commit()
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_event_type ON fact_institution_event(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_event_date ON fact_institution_event(report_date)",
        "CREATE INDEX IF NOT EXISTS idx_event_notice ON fact_institution_event(notice_date)",
        "CREATE INDEX IF NOT EXISTS idx_fie_stock ON fact_institution_event(stock_code, report_date DESC)",
        "CREATE INDEX IF NOT EXISTS idx_fie_holder ON fact_institution_event(holder_name, report_date DESC)",
        "CREATE INDEX IF NOT EXISTS idx_fie_notice_source ON fact_institution_event(notice_date_source)",
    ):
        try:
            conn.execute(sql)
        except Exception:
            pass
    conn.commit()

    after = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_institution_event WHERE {missing_where}"
    ).fetchone()
    after_missing = int((after["n"] if hasattr(after, "keys") else after[0]) or 0)
    future_by_source = conn.execute(
        """
        WITH norm AS (
            SELECT COALESCE(NULLIF(notice_date_source, ''), 'unknown') AS notice_date_source,
                   TRY_CAST(
                       CASE
                           WHEN length(CAST(notice_date AS VARCHAR)) = 8
                                AND instr(CAST(notice_date AS VARCHAR), '-') = 0
                           THEN substr(CAST(notice_date AS VARCHAR),1,4) || '-' ||
                                substr(CAST(notice_date AS VARCHAR),5,2) || '-' ||
                                substr(CAST(notice_date AS VARCHAR),7,2)
                           ELSE CAST(notice_date AS VARCHAR)
                       END AS DATE
                   ) AS notice_dt
              FROM fact_institution_event
             WHERE notice_date IS NOT NULL AND notice_date != ''
        )
        SELECT notice_date_source, COUNT(*) AS rows
          FROM norm
         WHERE notice_dt > CURRENT_DATE
         GROUP BY notice_date_source
         ORDER BY rows DESC
        """
    ).fetchall()
    return {
        "status": "ok",
        "updated_rows": max(before_missing - after_missing, 0),
        "remaining_missing_source_rows": after_missing,
        "future_notice_by_source": [dict(row) for row in future_by_source],
    }


def backfill_holder_period_availability_rows(conn, *, overwrite_regulatory: bool = False) -> dict:
    where = """
        report_date IS NOT NULL
        AND (
            notice_date IS NULL OR notice_date = ''
            OR effective_date IS NULL OR effective_date = ''
        )
    """
    if overwrite_regulatory:
        where = """
            report_date IS NOT NULL
            AND (
                notice_date IS NULL OR notice_date = ''
                OR effective_date IS NULL OR effective_date = ''
                OR availability_source = 'regulatory_deadline'
            )
        """
    rows = conn.execute(
        f"""
        SELECT DISTINCT report_date
        FROM fact_top10_holder_period
        WHERE {where}
        ORDER BY report_date
        """
    ).fetchall()
    updates = 0
    for row in rows:
        report_date = row["report_date"] if hasattr(row, "keys") else row[0]
        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date=report_date,
        )
        if not notice:
            continue
        conn.execute(
            f"""
            UPDATE fact_top10_holder_period
            SET notice_date = {('?' if overwrite_regulatory else "COALESCE(NULLIF(notice_date, ''), ?)")},
                effective_date = {('?' if overwrite_regulatory else "COALESCE(NULLIF(effective_date, ''), ?)")}
            WHERE report_date = ?
              AND (
                  notice_date IS NULL OR notice_date = ''
                  OR effective_date IS NULL OR effective_date = ''
                  { "OR availability_source = 'regulatory_deadline'" if overwrite_regulatory else "" }
              )
            """,
            (notice, effective, report_date),
        )
        updates += 1
        _backfill_availability_source(conn, report_date=report_date, source=source)
    remaining = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM fact_top10_holder_period
        WHERE notice_date IS NULL OR notice_date = ''
           OR effective_date IS NULL OR effective_date = ''
        """
    ).fetchone()
    return {
        "updated_report_dates": updates,
        "remaining_missing_rows": int((remaining["n"] if hasattr(remaining, "keys") else remaining[0]) or 0),
    }


def _backfill_availability_source(conn, *, report_date: str, source: str | None) -> None:
    if not source:
        return
    try:
        conn.execute(
            """
            UPDATE fact_top10_holder_period
            SET availability_source = COALESCE(NULLIF(availability_source, ''), ?)
            WHERE report_date = ?
            """,
            (source, report_date),
        )
    except Exception:
        return
