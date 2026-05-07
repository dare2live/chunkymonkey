"""Initial shareholder-plan event mart built from TDX/F10 latest-state rows."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from services.schema_versions import record_actual_version


MART_TABLE = "mart_shareholder_plan_initial_event"
SOURCE_TABLE = "fact_shareholder_plan_tdx_f10"

DDL = f"""
CREATE TABLE IF NOT EXISTS {MART_TABLE} (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    source_notice_date TEXT NOT NULL,
    source_available_date TEXT NOT NULL,
    source_date_quality TEXT NOT NULL,
    source_row_grain TEXT NOT NULL,
    subject TEXT,
    direction TEXT,
    start_date TEXT,
    end_date TEXT,
    target_shares_min BIGINT,
    target_shares BIGINT,
    target_ratio DOUBLE,
    target_amount_min BIGINT,
    target_amount_max BIGINT,
    trade_method TEXT,
    reason TEXT,
    first_announce_date TEXT,
    announce_date TEXT,
    latest_announce_date TEXT,
    latest_state_available_date TEXT,
    latest_progress TEXT,
    page_update_date TEXT,
    source TEXT,
    source_tier SMALLINT,
    raw_hash TEXT,
    row_seq INTEGER,
    built_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_initial_event_stock_date
    ON {MART_TABLE}(stock_code, source_available_date DESC);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_initial_event_direction
    ON {MART_TABLE}(direction);
"""


REQUIRED_SOURCE_COLUMNS = {
    "stock_code",
    "source_available_date",
    "source_notice_date",
    "latest_announce_date",
    "first_announce_date",
    "direction",
    "raw_hash",
    "row_seq",
}


def _execute_script(conn: Any, sql: str) -> None:
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: Any, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    rows = conn.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = ?
        """,
        (table,),
    ).fetchall()
    return {str(row["column_name"] if hasattr(row, "keys") else row[0]) for row in rows}


def _compact_date_expr(column_name: str) -> str:
    return (
        "substr("
        f"regexp_replace(COALESCE(CAST({column_name} AS VARCHAR), ''), '[^0-9]', '', 'g'), "
        "1, 8)"
    )


def _iso_date_expr(compact_expr: str) -> str:
    return (
        f"substr({compact_expr},1,4) || '-' || "
        f"substr({compact_expr},5,2) || '-' || "
        f"substr({compact_expr},7,2)"
    )


def _normalized_date_expr(column_name: str) -> str:
    compact = _compact_date_expr(column_name)
    return f"CASE WHEN length({compact}) = 8 THEN {_iso_date_expr(compact)} ELSE NULL END"


def ensure_shareholder_plan_initial_event_table(conn: Any) -> None:
    _execute_script(conn, DDL)


def build_shareholder_plan_initial_event(conn: Any) -> dict[str, Any]:
    """Materialize initial plan-announcement events without latest progress leakage."""

    ensure_shareholder_plan_initial_event_table(conn)
    if not _table_exists(conn, SOURCE_TABLE):
        conn.execute(f"DELETE FROM {MART_TABLE}")
        conn.commit()
        record_actual_version(conn, MART_TABLE)
        return {
            "status": "missing_source",
            "source_rows": 0,
            "candidate_rows": 0,
            "inserted_rows": 0,
            "missing_source_notice_rows": 0,
            "future_source_notice_rows": 0,
            "duplicate_dropped_rows": 0,
        }

    source_columns = _table_columns(conn, SOURCE_TABLE)
    missing = sorted(REQUIRED_SOURCE_COLUMNS - source_columns)
    if missing:
        raise RuntimeError(f"{SOURCE_TABLE} missing required columns: {', '.join(missing)}")

    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    optional = {
        "stock_name": "stock_name" if "stock_name" in source_columns else "NULL",
        "subject": "subject" if "subject" in source_columns else "NULL",
        "start_date": "start_date" if "start_date" in source_columns else "NULL",
        "end_date": "end_date" if "end_date" in source_columns else "NULL",
        "target_shares_min": "target_shares_min" if "target_shares_min" in source_columns else "NULL",
        "target_shares": "target_shares" if "target_shares" in source_columns else "NULL",
        "target_ratio": "target_ratio" if "target_ratio" in source_columns else "NULL",
        "target_amount_min": "target_amount_min" if "target_amount_min" in source_columns else "NULL",
        "target_amount_max": "target_amount_max" if "target_amount_max" in source_columns else "NULL",
        "trade_method": "trade_method" if "trade_method" in source_columns else "NULL",
        "reason": "reason" if "reason" in source_columns else "NULL",
        "announce_date": "announce_date" if "announce_date" in source_columns else "NULL",
        "progress": "progress" if "progress" in source_columns else "NULL",
        "page_update_date": "page_update_date" if "page_update_date" in source_columns else "NULL",
        "source": "source" if "source" in source_columns else "NULL",
        "source_tier": "source_tier" if "source_tier" in source_columns else "NULL",
    }
    announce_date_expr = _normalized_date_expr("announce_date") if "announce_date" in source_columns else "NULL"
    first_date_expr = _normalized_date_expr("first_announce_date")
    latest_date_expr = _normalized_date_expr("latest_announce_date")
    source_notice_expr = _normalized_date_expr("source_notice_date")
    source_available_expr = _normalized_date_expr("source_available_date")

    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_initial_event_build")
    conn.execute(
        f"""
        CREATE TEMP TABLE tmp_shareholder_plan_initial_event_build AS
        WITH normalized AS (
            SELECT stock_code,
                   {optional["stock_name"]} AS stock_name,
                   {optional["subject"]} AS subject,
                   direction,
                   {optional["start_date"]} AS start_date,
                   {optional["end_date"]} AS end_date,
                   {optional["target_shares_min"]} AS target_shares_min,
                   {optional["target_shares"]} AS target_shares,
                   {optional["target_ratio"]} AS target_ratio,
                   {optional["target_amount_min"]} AS target_amount_min,
                   {optional["target_amount_max"]} AS target_amount_max,
                   {optional["trade_method"]} AS trade_method,
                   {optional["reason"]} AS reason,
                   {first_date_expr} AS first_announce_date,
                   {announce_date_expr} AS announce_date,
                   {latest_date_expr} AS latest_announce_date,
                   {source_notice_expr} AS current_source_notice_date,
                   {source_available_expr} AS latest_state_available_date,
                   {optional["progress"]} AS latest_progress,
                   {optional["page_update_date"]} AS page_update_date,
                   {optional["source"]} AS source,
                   {optional["source_tier"]} AS source_tier,
                   raw_hash,
                   row_seq
              FROM {SOURCE_TABLE}
        ),
        chosen AS (
            SELECT *,
                   CASE
                       WHEN first_announce_date IS NOT NULL THEN first_announce_date
                       WHEN announce_date IS NOT NULL THEN announce_date
                       WHEN latest_announce_date IS NOT NULL THEN latest_announce_date
                       ELSE current_source_notice_date
                   END AS source_notice_date,
                   CASE
                       WHEN first_announce_date IS NOT NULL THEN 'parsed_first_announce_date_initial_event'
                       WHEN announce_date IS NOT NULL THEN 'parsed_announce_date_initial_event'
                       WHEN latest_announce_date IS NOT NULL THEN 'parsed_latest_announce_date_initial_event_fallback'
                       WHEN current_source_notice_date IS NOT NULL THEN 'current_source_notice_initial_event_fallback'
                       ELSE 'missing_source_date'
                   END AS source_date_quality
              FROM normalized
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code, source_notice_date, direction,
                                    COALESCE(subject, ''), COALESCE(start_date, ''),
                                    COALESCE(end_date, ''),
                                    COALESCE(CAST(target_amount_min AS VARCHAR), ''),
                                    COALESCE(CAST(target_amount_max AS VARCHAR), ''),
                                    COALESCE(CAST(target_shares_min AS VARCHAR), ''),
                                    COALESCE(CAST(target_shares AS VARCHAR), '')
                       ORDER BY latest_announce_date DESC NULLS LAST,
                                latest_state_available_date DESC NULLS LAST,
                                raw_hash DESC NULLS LAST,
                                row_seq DESC NULLS LAST
                   ) AS rn
              FROM chosen
        )
        SELECT * FROM ranked
        """
    )

    metrics = conn.execute(
        """
        SELECT COUNT(*) AS source_rows,
               SUM(CASE WHEN source_notice_date IS NULL THEN 1 ELSE 0 END) AS missing_source_notice_rows,
               SUM(CASE WHEN TRY_CAST(source_notice_date AS DATE) > CURRENT_DATE THEN 1 ELSE 0 END)
                   AS future_source_notice_rows,
               SUM(CASE WHEN rn = 1
                         AND source_notice_date IS NOT NULL
                         AND COALESCE(TRY_CAST(source_notice_date AS DATE) <= CURRENT_DATE, FALSE)
                        THEN 1 ELSE 0 END) AS inserted_rows,
               SUM(CASE WHEN rn > 1 THEN 1 ELSE 0 END) AS duplicate_dropped_rows
          FROM tmp_shareholder_plan_initial_event_build
        """
    ).fetchone()

    conn.execute(f"DELETE FROM {MART_TABLE}")
    conn.execute(
        f"""
        INSERT INTO {MART_TABLE}
        (stock_code, stock_name, source_notice_date, source_available_date,
         source_date_quality, source_row_grain, subject, direction, start_date,
         end_date, target_shares_min, target_shares, target_ratio,
         target_amount_min, target_amount_max, trade_method, reason,
         first_announce_date, announce_date, latest_announce_date,
         latest_state_available_date, latest_progress, page_update_date,
         source, source_tier, raw_hash, row_seq, built_at)
        SELECT stock_code, stock_name, source_notice_date, source_notice_date,
               source_date_quality, 'initial_shareholder_plan_notice',
               subject, direction, start_date, end_date, target_shares_min,
               target_shares, target_ratio, target_amount_min, target_amount_max,
               trade_method, reason, first_announce_date, announce_date,
               latest_announce_date, latest_state_available_date, latest_progress,
               page_update_date, source, source_tier, raw_hash, row_seq, ?
          FROM tmp_shareholder_plan_initial_event_build
         WHERE rn = 1
           AND source_notice_date IS NOT NULL
           AND COALESCE(TRY_CAST(source_notice_date AS DATE) <= CURRENT_DATE, FALSE)
        """,
        (built_at,),
    )
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_initial_event_build")
    record_actual_version(conn, MART_TABLE)
    conn.commit()
    source_rows = int(metrics["source_rows"] or 0)
    inserted_rows = int(metrics["inserted_rows"] or 0)
    duplicate_dropped_rows = int(metrics["duplicate_dropped_rows"] or 0)
    return {
        "status": "completed",
        "source_rows": source_rows,
        "candidate_rows": source_rows - int(metrics["missing_source_notice_rows"] or 0),
        "inserted_rows": inserted_rows,
        "missing_source_notice_rows": int(metrics["missing_source_notice_rows"] or 0),
        "future_source_notice_rows": int(metrics["future_source_notice_rows"] or 0),
        "duplicate_dropped_rows": duplicate_dropped_rows,
        "built_at": built_at,
    }


__all__ = [
    "MART_TABLE",
    "SOURCE_TABLE",
    "build_shareholder_plan_initial_event",
    "ensure_shareholder_plan_initial_event_table",
]
