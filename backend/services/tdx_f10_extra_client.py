"""TDX F10 Format B extra landing.

This module parses already-captured ``raw_tdx_f10_holder_research`` rows.  It
does not fetch live F10 data; daily fetching stays in ``ingest_holders_tdxhub``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services.tdx_source import ensure_workspace_tdxhub_path

logger = logging.getLogger("cm-api")


def _ensure_tdxhub_import_path() -> None:
    ensure_workspace_tdxhub_path()
    import tdxhub.holders  # noqa: F401


_ensure_tdxhub_import_path()

from tdxhub.holders import (  # noqa: E402
    detect_f10_format,
    parse_common_major_holder_stocks_format_b,
    parse_controlling_shareholder,
    parse_fund_holdings_format_b,
    parse_holder_count_history_format_b,
    parse_shareholder_plans_format_b,
    parse_shareholder_trades_format_b,
)


DDL = """
CREATE TABLE IF NOT EXISTS raw_tdx_f10_holder_count_history (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT,
    report_date TEXT,
    report_date_text TEXT,
    holder_count_text TEXT,
    holder_count BIGINT,
    holder_count_change_text TEXT,
    holder_count_change BIGINT,
    holder_count_change_pct_text TEXT,
    holder_count_change_pct DOUBLE,
    avg_float_shares_text TEXT,
    avg_float_shares BIGINT,
    avg_float_shares_change_pct_text TEXT,
    avg_float_shares_change_pct DOUBLE,
    close_price_text TEXT,
    close_price DOUBLE,
    page_update_date TEXT,
    source_notice_date TEXT,
    source_available_date TEXT,
    source_date_quality TEXT,
    source TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    fetched_at TEXT,
    row_seq INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (stock_code, raw_hash, row_seq)
);
CREATE INDEX IF NOT EXISTS idx_raw_holder_count_stock_date
    ON raw_tdx_f10_holder_count_history(stock_code, report_date DESC);

CREATE TABLE IF NOT EXISTS fact_holder_count_period (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT,
    report_date TEXT NOT NULL,
    holder_count BIGINT,
    holder_count_change BIGINT,
    holder_count_change_pct DOUBLE,
    avg_float_shares BIGINT,
    avg_float_shares_change_pct DOUBLE,
    close_price DOUBLE,
    page_update_date TEXT,
    source TEXT NOT NULL,
    source_tier SMALLINT NOT NULL DEFAULT 1,
    raw_hash TEXT,
    fetched_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (stock_code, report_date, source)
);
CREATE INDEX IF NOT EXISTS idx_fact_holder_count_stock_date
    ON fact_holder_count_period(stock_code, report_date DESC);

CREATE TABLE IF NOT EXISTS fact_shareholder_trade_tdx_b (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT,
    change_period_text TEXT,
    change_start_date TEXT,
    change_end_date TEXT,
    change_date TEXT,
    holder_name TEXT,
    holder_name_norm TEXT,
    shares_change_text TEXT,
    shares_change BIGINT,
    average_price_text TEXT,
    average_price DOUBLE,
    shares_after_text TEXT,
    shares_after BIGINT,
    change_method TEXT,
    page_update_date TEXT,
    source_notice_date TEXT,
    source_available_date TEXT,
    source_date_quality TEXT,
    source TEXT NOT NULL,
    source_tier SMALLINT NOT NULL DEFAULT 1,
    raw_hash TEXT,
    fetched_at TEXT,
    trade_seq INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (stock_code, raw_hash, trade_seq)
);
CREATE INDEX IF NOT EXISTS idx_trade_b_stock_date
    ON fact_shareholder_trade_tdx_b(stock_code, change_date DESC);
CREATE INDEX IF NOT EXISTS idx_trade_b_holder
    ON fact_shareholder_trade_tdx_b(holder_name_norm);

CREATE TABLE IF NOT EXISTS fact_shareholder_plan_tdx_f10 (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT,
    announce_date TEXT,
    latest_announce_date TEXT,
    first_announce_date TEXT,
    source_notice_date TEXT,
    source_available_date TEXT,
    source_date_quality TEXT,
    subject TEXT,
    direction TEXT,
    progress TEXT,
    start_date TEXT,
    end_date TEXT,
    target_shares_min_text TEXT,
    target_shares_min BIGINT,
    target_shares_text TEXT,
    target_shares BIGINT,
    target_ratio_text TEXT,
    target_ratio DOUBLE,
    target_amount_min_text TEXT,
    target_amount_min BIGINT,
    target_amount_max_text TEXT,
    target_amount_max BIGINT,
    trade_method TEXT,
    reason TEXT,
    narrative TEXT,
    page_update_date TEXT,
    source TEXT NOT NULL,
    source_tier SMALLINT NOT NULL DEFAULT 1,
    raw_hash TEXT,
    fetched_at TEXT,
    row_seq INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (stock_code, raw_hash, row_seq)
);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_stock_notice
    ON fact_shareholder_plan_tdx_f10(stock_code, source_available_date DESC);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_subject
    ON fact_shareholder_plan_tdx_f10(subject);

CREATE TABLE IF NOT EXISTS fact_common_major_holder_stock (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT,
    report_date TEXT,
    report_date_text TEXT,
    major_holder_name TEXT NOT NULL,
    peer_stock_code TEXT NOT NULL,
    peer_stock_name TEXT,
    shares_text TEXT,
    shares BIGINT,
    hold_ratio_text TEXT,
    hold_ratio DOUBLE,
    change_text TEXT,
    change_shares BIGINT,
    net_profit_parent_text TEXT,
    net_profit_parent DOUBLE,
    net_profit_deducted_text TEXT,
    net_profit_deducted DOUBLE,
    page_update_date TEXT,
    source_notice_date TEXT,
    source_available_date TEXT,
    source_date_quality TEXT,
    source TEXT NOT NULL,
    source_tier SMALLINT NOT NULL DEFAULT 1,
    raw_hash TEXT,
    fetched_at TEXT,
    row_seq INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (stock_code, major_holder_name, peer_stock_code, row_seq)
);
CREATE INDEX IF NOT EXISTS idx_common_holder_name
    ON fact_common_major_holder_stock(major_holder_name);
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS report_date_text TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS hold_ratio_text TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS change_shares BIGINT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS net_profit_parent_text TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS net_profit_deducted_text TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS source_notice_date TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS source_available_date TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS source_date_quality TEXT;

CREATE TABLE IF NOT EXISTS fact_fund_holding_tdx_f10 (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT,
    report_date TEXT,
    report_date_text TEXT,
    fund_name TEXT NOT NULL,
    shares_text TEXT,
    shares BIGINT,
    float_a_ratio_text TEXT,
    float_a_ratio DOUBLE,
    market_value_text TEXT,
    market_value DOUBLE,
    page_update_date TEXT,
    source_notice_date TEXT,
    source_available_date TEXT,
    source_date_quality TEXT,
    source TEXT NOT NULL,
    source_tier SMALLINT NOT NULL DEFAULT 1,
    raw_hash TEXT,
    fetched_at TEXT,
    row_seq INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (stock_code, fund_name, report_date, row_seq)
);
CREATE INDEX IF NOT EXISTS idx_fund_holding_name
    ON fact_fund_holding_tdx_f10(fund_name);
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS report_date_text TEXT;
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS float_a_ratio_text TEXT;
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS market_value_text TEXT;
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS source_notice_date TEXT;
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS source_available_date TEXT;
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS source_date_quality TEXT;

CREATE TABLE IF NOT EXISTS raw_tdx_f10_extra_parse_status (
    stock_code TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    parsed_at TEXT,
    holder_count_rows INTEGER DEFAULT 0,
    trade_b_rows INTEGER DEFAULT 0,
    control_rows INTEGER DEFAULT 0,
    common_major_holder_rows INTEGER DEFAULT 0,
    fund_holding_rows INTEGER DEFAULT 0,
    fund_holding_rejected_rows INTEGER DEFAULT 0,
    shareholder_plan_rows INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    status_reason TEXT,
    parser_version TEXT,
    error TEXT,
    PRIMARY KEY (stock_code, raw_hash)
);
CREATE INDEX IF NOT EXISTS idx_f10_extra_status_status
    ON raw_tdx_f10_extra_parse_status(status);
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS fund_holding_rejected_rows INTEGER DEFAULT 0;
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS shareholder_plan_rows INTEGER DEFAULT 0;
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS status_reason TEXT;
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS parser_version TEXT;

CREATE TABLE IF NOT EXISTS fact_controlling_shareholder (
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT,
    primary_label TEXT,
    primary_name TEXT,
    primary_ratio DOUBLE,
    primary_raw TEXT,
    actual_name TEXT,
    actual_ratio DOUBLE,
    actual_raw TEXT,
    page_update_date TEXT,
    source TEXT NOT NULL,
    source_tier SMALLINT NOT NULL,
    raw_hash TEXT,
    fetched_at TEXT,
    control_chain_text TEXT,
    source_notice_date TEXT,
    source_available_date TEXT,
    source_date_quality TEXT,
    PRIMARY KEY (stock_code, source)
);
ALTER TABLE fact_controlling_shareholder ADD COLUMN control_chain_text TEXT;
ALTER TABLE fact_controlling_shareholder ADD COLUMN IF NOT EXISTS source_notice_date TEXT;
ALTER TABLE fact_controlling_shareholder ADD COLUMN IF NOT EXISTS source_available_date TEXT;
ALTER TABLE fact_controlling_shareholder ADD COLUMN IF NOT EXISTS source_date_quality TEXT;
ALTER TABLE fact_holder_count_period ADD COLUMN IF NOT EXISTS source_notice_date TEXT;
ALTER TABLE fact_holder_count_period ADD COLUMN IF NOT EXISTS source_available_date TEXT;
ALTER TABLE fact_holder_count_period ADD COLUMN IF NOT EXISTS source_date_quality TEXT;
ALTER TABLE fact_shareholder_trade_tdx_b ADD COLUMN IF NOT EXISTS source_notice_date TEXT;
ALTER TABLE fact_shareholder_trade_tdx_b ADD COLUMN IF NOT EXISTS source_available_date TEXT;
ALTER TABLE fact_shareholder_trade_tdx_b ADD COLUMN IF NOT EXISTS source_date_quality TEXT;

CREATE TABLE IF NOT EXISTS mart_tdx_f10_capability_matrix (
    module_id TEXT PRIMARY KEY,
    module_name TEXT NOT NULL,
    endpoint TEXT,
    parser TEXT,
    raw_table TEXT,
    fact_table TEXT,
    raw_text_available BOOLEAN,
    parsed_table_available BOOLEAN,
    coverage_stock_count INTEGER,
    row_count INTEGER,
    latest_page_update_date TEXT,
    latest_fetched_at TEXT,
    parser_version TEXT,
    pit_risk TEXT,
    source_date_field TEXT,
    availability_date_field TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    built_at TEXT
);
"""


FUND_HOLDING_REJECTION_KEYWORDS = (
    "本公司力求但不保证",
    "真实性、准确性",
    "投资者使用前请自行予以核实",
    "不作为投资决策的依据",
    "投资有风险",
    "中国证监会指定上市公司信息披露媒体",
)

PARSER_VERSION = "tdx_f10_extra_v2"


F10_CAPABILITIES = [
    {
        "module_id": "raw_holder_research",
        "module_name": "股东研究原文",
        "endpoint": "tdxhub.holders",
        "parser": "raw_capture",
        "raw_table": "raw_tdx_f10_holder_research",
        "fact_table": None,
        "pit_risk": "critical",
        "source_date_field": "page_update_date",
        "availability_date_field": "fetched_at",
        "notes": "append-only raw F10 text; source notice extraction depends on downstream parser",
    },
    {
        "module_id": "holder_count_history",
        "module_name": "股东人数历史",
        "endpoint": "tdxhub.holders",
        "parser": "parse_holder_count_history_format_b",
        "raw_table": "raw_tdx_f10_holder_research",
        "fact_table": "fact_holder_count_period",
        "pit_risk": "high",
        "source_date_field": "source_notice_date",
        "availability_date_field": "source_available_date",
        "notes": "uses explicit source date quality; page update is a conservative fallback until true notice date is parsed",
    },
    {
        "module_id": "shareholder_trade_b",
        "module_name": "股东增减持",
        "endpoint": "tdxhub.holders",
        "parser": "parse_shareholder_trades_format_b",
        "raw_table": "raw_tdx_f10_holder_research",
        "fact_table": "fact_shareholder_trade_tdx_b",
        "pit_risk": "high",
        "source_date_field": "source_notice_date",
        "availability_date_field": "source_available_date",
        "notes": "event date is parsed separately; source availability uses conservative page update fallback",
    },
    {
        "module_id": "shareholder_plan_tdx_f10",
        "module_name": "股东增减持计划",
        "endpoint": "tdxhub.holders",
        "parser": "parse_shareholder_plans_format_b",
        "raw_table": "raw_tdx_f10_holder_research",
        "fact_table": "fact_shareholder_plan_tdx_f10",
        "pit_risk": "high",
        "source_date_field": "source_notice_date",
        "availability_date_field": "source_available_date",
        "notes": "Format B section 2; uses parsed latest/first announcement dates where present",
    },
    {
        "module_id": "controlling_shareholder",
        "module_name": "控股股东与实控人",
        "endpoint": "tdxhub.holders",
        "parser": "parse_controlling_shareholder",
        "raw_table": "raw_tdx_f10_holder_research",
        "fact_table": "fact_controlling_shareholder",
        "pit_risk": "high",
        "source_date_field": "source_notice_date",
        "availability_date_field": "source_available_date",
        "notes": "profile-like F10 content; source availability is explicitly quality-tagged",
    },
    {
        "module_id": "common_major_holder_stock",
        "module_name": "同大股东持股",
        "endpoint": "tdxhub.holders",
        "parser": "parse_common_major_holder_stocks_format_b",
        "raw_table": "raw_tdx_f10_holder_research",
        "fact_table": "fact_common_major_holder_stock",
        "pit_risk": "high",
        "source_date_field": "source_notice_date",
        "availability_date_field": "source_available_date",
        "notes": "relationship feature candidate; report_date is event period and source availability is quality-tagged",
    },
    {
        "module_id": "fund_holding_tdx_f10",
        "module_name": "基金持股明细",
        "endpoint": "tdxhub.holders",
        "parser": "parse_fund_holdings_format_b",
        "raw_table": "raw_tdx_f10_holder_research",
        "fact_table": "fact_fund_holding_tdx_f10",
        "pit_risk": "high",
        "source_date_field": "source_notice_date",
        "availability_date_field": "source_available_date",
        "notes": "section 7 fund rows; report_date is event period and source availability is quality-tagged",
    },
]


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate" in msg:
                continue
            raise


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _fetched_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _derive_conservative_f10_source_dates(rec: dict[str, Any]) -> tuple[str | None, str | None, str]:
    page_update = rec.get("page_update_date")
    if page_update:
        value = str(page_update)
        return value, value, "page_update_date_conservative_fallback"
    fetched = _fetched_date(rec.get("fetched_at"))
    if fetched:
        return None, fetched, "fetched_at_conservative_fallback"
    return None, None, "missing_source_date"


def _table_columns(conn: Any, table: str) -> set[str]:
    try:
        return {str(row[0]) for row in conn.execute(f"DESCRIBE {table}").fetchall()}
    except Exception:
        return set()


def _table_metrics(conn: Any, table: str | None) -> dict[str, Any]:
    if not table or not _table_exists(conn, table):
        return {
            "exists": False,
            "row_count": 0,
            "coverage_stock_count": 0,
            "latest_page_update_date": None,
            "latest_fetched_at": None,
        }
    cols = _table_columns(conn, table)
    stock_expr = "COUNT(DISTINCT stock_code)" if "stock_code" in cols else "0"
    page_expr = "MAX(page_update_date)" if "page_update_date" in cols else "NULL"
    fetched_expr = "MAX(fetched_at)" if "fetched_at" in cols else "NULL"
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS row_count,
               {stock_expr} AS coverage_stock_count,
               {page_expr} AS latest_page_update_date,
               {fetched_expr} AS latest_fetched_at
          FROM {table}
        """
    ).fetchone()
    return {
        "exists": True,
        "row_count": int(row["row_count"] or 0),
        "coverage_stock_count": int(row["coverage_stock_count"] or 0),
        "latest_page_update_date": str(row["latest_page_update_date"]) if row["latest_page_update_date"] else None,
        "latest_fetched_at": str(row["latest_fetched_at"]) if row["latest_fetched_at"] else None,
    }


def build_tdx_f10_capability_matrix(conn: Any) -> dict[str, Any]:
    """Persist a capability matrix for currently supported TDX F10 modules."""

    from services.schema_versions import record_actual_version

    ensure_tables(conn)
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for cap in F10_CAPABILITIES:
        raw_metrics = _table_metrics(conn, cap.get("raw_table"))
        fact_metrics = _table_metrics(conn, cap.get("fact_table"))
        parsed = bool(fact_metrics["exists"])
        effective = fact_metrics if parsed else raw_metrics
        status = "ready" if parsed and fact_metrics["row_count"] > 0 else (
            "raw_only" if raw_metrics["row_count"] > 0 else "missing_raw"
        )
        rows.append(
            (
                cap["module_id"],
                cap["module_name"],
                cap.get("endpoint"),
                cap.get("parser"),
                cap.get("raw_table"),
                cap.get("fact_table"),
                bool(raw_metrics["exists"] and raw_metrics["row_count"] > 0),
                parsed,
                int(effective["coverage_stock_count"] or 0),
                int(effective["row_count"] or 0),
                effective["latest_page_update_date"],
                effective["latest_fetched_at"],
                PARSER_VERSION,
                cap.get("pit_risk"),
                cap.get("source_date_field"),
                cap.get("availability_date_field"),
                status,
                cap.get("notes"),
                built_at,
            )
        )
    conn.execute("DELETE FROM mart_tdx_f10_capability_matrix")
    conn.executemany(
        """
        INSERT INTO mart_tdx_f10_capability_matrix
        (module_id, module_name, endpoint, parser, raw_table, fact_table,
         raw_text_available, parsed_table_available, coverage_stock_count,
         row_count, latest_page_update_date, latest_fetched_at, parser_version,
         pit_risk, source_date_field, availability_date_field, status, notes,
         built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    record_actual_version(conn, "mart_tdx_f10_capability_matrix")
    conn.commit()
    return {
        "capability_rows": len(rows),
        "ready_rows": sum(1 for row in rows if row[16] == "ready"),
        "raw_only_rows": sum(1 for row in rows if row[16] == "raw_only"),
        "built_at": built_at,
    }


def _rows_as_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    desc = getattr(cursor, "description", None) or []
    cols = [d[0] for d in desc]
    out: list[dict[str, Any]] = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
        else:
            out.append(dict(zip(cols, row)))
    return out


def _table_exists(conn: Any, table: str) -> bool:
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            LIMIT 1
            """,
            (table,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _load_alias_map(conn: Any) -> dict[str, str]:
    if not _table_exists(conn, "dim_holder_alias"):
        return {}
    try:
        rows = conn.execute("SELECT alias, canonical_name FROM dim_holder_alias").fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    return value


def _records_from_payload(payload: Any, fallback: dict[str, Any]) -> list[dict[str, Any]]:
    if payload is None:
        return []

    empty = getattr(payload, "empty", None)
    if empty is not None:
        try:
            if bool(empty):
                return []
        except Exception:
            pass

    raw_rows = None
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            raw_rows = to_dict("records")
        except TypeError:
            raw_rows = None
    if raw_rows is None:
        if isinstance(payload, dict):
            raw_rows = [payload]
        elif isinstance(payload, (str, bytes)):
            raw_rows = []
        else:
            try:
                raw_rows = list(payload)
            except TypeError:
                raw_rows = []

    records: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_rows, 1):
        if not isinstance(raw, dict):
            if hasattr(raw, "_asdict"):
                raw = raw._asdict()
            else:
                try:
                    raw = dict(raw)
                except Exception:
                    continue
        rec = {k: _clean(v) for k, v in raw.items()}
        for key in ("stock_code", "stock_name", "market"):
            if not rec.get(key):
                rec[key] = fallback.get(key)
        # The landing table's raw_hash is the provenance key used by chunky.
        # tdxhub also computes a hash from the text; keep chunky authoritative.
        rec["raw_hash"] = fallback.get("raw_hash") or rec.get("raw_hash")
        if not rec.get("fetched_at"):
            rec["fetched_at"] = fallback.get("fetched_at")
        rec["row_seq"] = idx
        rec["trade_seq"] = idx
        records.append(rec)
    return records


def _dedupe_records_by_key(records: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    """Keep the last record for each key tuple.

    DuckDB ``INSERT OR REPLACE`` has been brittle on these F10 lanes when a
    batch carries repeated keys or replays an already-written key.  We keep
    the last record for each key tuple before an atomic upsert.
    """

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for rec in records:
        key = tuple(rec.get(field) for field in key_fields)
        deduped[key] = rec
    return list(deduped.values())


def _upsert_rows_on_conflict(
    conn: Any,
    *,
    table: str,
    cols: list[str],
    key_fields: list[str],
    records: list[dict[str, Any]],
) -> int:
    if not records:
        return 0
    deduped = _dedupe_records_by_key(records, key_fields)
    update_cols = [c for c in cols if c not in key_fields]
    update_clause = ", ".join(f"{col} = excluded.{col}" for col in update_cols)
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))}) "
        f"ON CONFLICT({', '.join(key_fields)}) DO UPDATE SET {update_clause}"
    )
    for rec in deduped:
        conn.execute(sql, tuple(rec.get(c) for c in cols))
    return len(deduped)


def _insert_rows_ignore_conflict(
    conn: Any,
    *,
    table: str,
    cols: list[str],
    key_fields: list[str],
    records: list[dict[str, Any]],
) -> int:
    if not records:
        return 0
    deduped = _dedupe_records_by_key(records, key_fields)
    sql = (
        f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})"
    )
    for rec in deduped:
        conn.execute(sql, tuple(rec.get(c) for c in cols))
    return len(deduped)


def _select_raw_rows(
    conn: Any,
    *,
    stock_codes: list[str] | None,
    limit: int,
    only_new: bool,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if stock_codes:
        placeholders = ",".join(["?"] * len(stock_codes))
        where.append(f"stock_code IN ({placeholders})")
        params.extend(stock_codes)
    if only_new:
        where.append(
            """
            NOT EXISTS (
                SELECT 1 FROM raw_tdx_f10_extra_parse_status s
                WHERE s.stock_code = r.stock_code
                  AND s.raw_hash = r.raw_hash
                  AND s.status IN (
                      'completed',
                      'completed_with_rejections',
                      'skipped_non_format_b',
                      'skipped_no_extra_section'
                  )
            )
            """
        )
    sql = f"""
        SELECT stock_code, stock_name, market, raw_text, raw_hash, fetched_at, f10_format
        FROM raw_tdx_f10_holder_research r
        WHERE {' AND '.join(where)}
        ORDER BY fetched_at DESC, stock_code
    """
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return _rows_as_dicts(conn.execute(sql, params))


def _select_plan_raw_rows(
    conn: Any,
    *,
    stock_codes: list[str] | None,
    limit: int,
    only_missing: bool,
) -> list[dict[str, Any]]:
    where = [
        "raw_text LIKE '%【2.股东增减持计划】%'",
        "raw_text LIKE '%最新公告日期%'",
    ]
    params: list[Any] = []
    if stock_codes:
        placeholders = ",".join(["?"] * len(stock_codes))
        where.append(f"stock_code IN ({placeholders})")
        params.extend(stock_codes)
    if only_missing and _table_exists(conn, "fact_shareholder_plan_tdx_f10"):
        where.append(
            """
            NOT EXISTS (
                SELECT 1
                  FROM fact_shareholder_plan_tdx_f10 p
                 WHERE p.stock_code = r.stock_code
                   AND p.raw_hash = r.raw_hash
            )
            """
        )
    sql = f"""
        SELECT stock_code, stock_name, market, raw_text, raw_hash, fetched_at, f10_format
        FROM raw_tdx_f10_holder_research r
        WHERE {' AND '.join(where)}
        ORDER BY fetched_at DESC, stock_code
    """
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return _rows_as_dicts(conn.execute(sql, params))


def _update_parse_status_plan_rows(
    conn: Any,
    fallback: dict[str, Any],
    *,
    shareholder_plan_rows: int,
) -> None:
    if not fallback.get("stock_code") or not fallback.get("raw_hash"):
        return
    exists = conn.execute(
        """
        SELECT 1
          FROM raw_tdx_f10_extra_parse_status
         WHERE stock_code = ? AND raw_hash = ?
         LIMIT 1
        """,
        (fallback.get("stock_code"), fallback.get("raw_hash")),
    ).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE raw_tdx_f10_extra_parse_status
               SET shareholder_plan_rows = ?,
                   parsed_at = ?
             WHERE stock_code = ? AND raw_hash = ?
            """,
            (
                int(shareholder_plan_rows or 0),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                fallback.get("stock_code"),
                fallback.get("raw_hash"),
            ),
        )
        return
    _upsert_parse_status(
        conn,
        fallback,
        status="completed" if shareholder_plan_rows else "skipped_no_extra_section",
        status_reason=(
            "shareholder_plan_backfill"
            if shareholder_plan_rows
            else "no shareholder plan rows"
        ),
        shareholder_plan_rows=shareholder_plan_rows,
    )


def backfill_tdx_f10_shareholder_plans(
    conn: Any,
    *,
    stock_codes: list[str] | None = None,
    limit: int = 0,
    only_missing: bool = True,
) -> dict[str, Any]:
    """Backfill only Format B shareholder-plan rows with parsed announce dates."""

    ensure_tables(conn)
    raw_rows = _select_plan_raw_rows(
        conn,
        stock_codes=stock_codes,
        limit=limit,
        only_missing=only_missing,
    )
    stats: dict[str, Any] = {
        "raw_rows": len(raw_rows),
        "shareholder_plan_rows": 0,
        "skipped_non_format_b": 0,
        "skipped_no_plan_rows": 0,
        "errors": [],
    }
    for row in raw_rows:
        fallback = {
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "market": row.get("market"),
            "raw_hash": row.get("raw_hash"),
            "fetched_at": str(row.get("fetched_at")) if row.get("fetched_at") is not None else None,
        }
        try:
            text = row.get("raw_text") or ""
            if detect_f10_format(text) != "b":
                stats["skipped_non_format_b"] += 1
                _update_parse_status_plan_rows(conn, fallback, shareholder_plan_rows=0)
                continue
            plan_records = _records_from_payload(
                parse_shareholder_plans_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            row_plan = _insert_shareholder_plan_rows(conn, plan_records)
            _update_parse_status_plan_rows(conn, fallback, shareholder_plan_rows=row_plan)
            stats["shareholder_plan_rows"] += row_plan
            if row_plan == 0:
                stats["skipped_no_plan_rows"] += 1
        except Exception as exc:
            ident = f"{row.get('stock_code')}/{row.get('raw_hash')}"
            err = f"{ident}: {type(exc).__name__}: {exc}"
            logger.warning("[tdx-f10-plan-backfill] parse failed for %s: %s", ident, exc)
            stats["errors"].append(err)
    try:
        conn.commit()
    except Exception:
        pass
    stats["status"] = "partial" if stats["errors"] else "completed"
    try:
        stats["capability_matrix"] = build_tdx_f10_capability_matrix(conn)
    except Exception as exc:
        stats["capability_matrix_error"] = f"{type(exc).__name__}: {exc}"
    return stats


def _insert_holder_count_rows(conn: Any, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    cols = [
        "stock_code", "stock_name", "market", "report_date", "report_date_text",
        "holder_count_text", "holder_count", "holder_count_change_text",
        "holder_count_change", "holder_count_change_pct_text",
        "holder_count_change_pct", "avg_float_shares_text", "avg_float_shares",
        "avg_float_shares_change_pct_text", "avg_float_shares_change_pct",
        "close_price_text", "close_price", "page_update_date", "source",
        "raw_hash", "fetched_at", "row_seq",
    ]
    inserted_raw = _insert_rows_ignore_conflict(
        conn,
        table="raw_tdx_f10_holder_count_history",
        cols=cols,
        key_fields=["stock_code", "raw_hash", "row_seq"],
        records=records,
    )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fact_cols = [
        "stock_code", "stock_name", "market", "report_date", "holder_count",
        "holder_count_change", "holder_count_change_pct", "avg_float_shares",
        "avg_float_shares_change_pct", "close_price", "page_update_date",
        "source_notice_date", "source_available_date", "source_date_quality",
        "source", "source_tier", "raw_hash", "fetched_at", "updated_at",
    ]
    fact_rows = []
    for rec in records:
        if not rec.get("report_date"):
            continue
        fact = dict(rec)
        notice, available, quality = _derive_conservative_f10_source_dates(fact)
        fact["source_notice_date"] = notice
        fact["source_available_date"] = available
        fact["source_date_quality"] = quality
        fact["source_tier"] = 1
        fact["updated_at"] = now
        fact_rows.append(tuple(fact.get(c) for c in fact_cols))
    if fact_rows:
        _upsert_rows_on_conflict(
            conn,
            table="fact_holder_count_period",
            cols=fact_cols,
            key_fields=["stock_code", "report_date", "source"],
            records=[dict(zip(fact_cols, row)) for row in fact_rows],
        )
    return inserted_raw


def _insert_trade_b_rows(
    conn: Any, records: list[dict[str, Any]], alias_map: dict[str, str]
) -> int:
    if not records:
        return 0
    cols = [
        "stock_code", "stock_name", "market", "change_period_text",
        "change_start_date", "change_end_date", "change_date", "holder_name",
        "holder_name_norm", "shares_change_text", "shares_change",
        "average_price_text", "average_price", "shares_after_text",
        "shares_after", "change_method", "page_update_date",
        "source_notice_date", "source_available_date", "source_date_quality",
        "source",
        "source_tier", "raw_hash", "fetched_at", "trade_seq",
    ]
    rows = []
    for rec in records:
        rec["holder_name_norm"] = alias_map.get(rec.get("holder_name"), rec.get("holder_name"))
        notice, available, quality = _derive_conservative_f10_source_dates(rec)
        rec["source_notice_date"] = notice
        rec["source_available_date"] = available
        rec["source_date_quality"] = quality
        rec["source_tier"] = 1
        rows.append(tuple(rec.get(c) for c in cols))
    _upsert_rows_on_conflict(
        conn,
        table="fact_shareholder_trade_tdx_b",
        cols=cols,
        key_fields=["stock_code", "raw_hash", "trade_seq"],
        records=[dict(zip(cols, row)) for row in rows],
    )
    return len(records)


def _derive_plan_source_dates(rec: dict[str, Any]) -> tuple[str | None, str | None, str]:
    latest = rec.get("latest_announce_date") or rec.get("announce_date")
    first = rec.get("first_announce_date")
    page_update = rec.get("page_update_date")
    if latest:
        return latest, latest, "parsed_latest_announce_date"
    if first:
        return first, first, "parsed_first_announce_date"
    if page_update:
        return page_update, page_update, "page_update_date_fallback"
    return None, None, "missing_source_date"


def _insert_shareholder_plan_rows(conn: Any, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    cols = [
        "stock_code", "stock_name", "market", "announce_date",
        "latest_announce_date", "first_announce_date", "source_notice_date",
        "source_available_date", "source_date_quality", "subject", "direction",
        "progress", "start_date", "end_date", "target_shares_min_text",
        "target_shares_min", "target_shares_text", "target_shares",
        "target_ratio_text", "target_ratio", "target_amount_min_text",
        "target_amount_min", "target_amount_max_text", "target_amount_max",
        "trade_method", "reason", "narrative", "page_update_date", "source",
        "source_tier", "raw_hash", "fetched_at", "row_seq",
    ]
    rows = []
    for rec in records:
        notice, available, quality = _derive_plan_source_dates(rec)
        rec["source_notice_date"] = notice
        rec["source_available_date"] = available
        rec["source_date_quality"] = quality
        rec["source_tier"] = 1
        rows.append(tuple(rec.get(c) for c in cols))
    _upsert_rows_on_conflict(
        conn,
        table="fact_shareholder_plan_tdx_f10",
        cols=cols,
        key_fields=["stock_code", "raw_hash", "row_seq"],
        records=[dict(zip(cols, row)) for row in rows],
    )
    return len(records)


def _insert_common_major_holder_rows(conn: Any, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    cols = [
        "stock_code", "stock_name", "market", "report_date", "report_date_text",
        "major_holder_name", "peer_stock_code", "peer_stock_name", "shares_text",
        "shares", "hold_ratio_text", "hold_ratio", "change_text", "change_shares",
        "net_profit_parent_text", "net_profit_parent", "net_profit_deducted_text",
        "net_profit_deducted", "page_update_date",
        "source_notice_date", "source_available_date", "source_date_quality",
        "source", "source_tier",
        "raw_hash", "fetched_at", "row_seq",
    ]
    rows = []
    for rec in records:
        notice, available, quality = _derive_conservative_f10_source_dates(rec)
        rec["source_notice_date"] = notice
        rec["source_available_date"] = available
        rec["source_date_quality"] = quality
        rec["source_tier"] = 1
        rows.append(tuple(rec.get(c) for c in cols))
    _upsert_rows_on_conflict(
        conn,
        table="fact_common_major_holder_stock",
        cols=cols,
        key_fields=["stock_code", "major_holder_name", "peer_stock_code", "row_seq"],
        records=[dict(zip(cols, row)) for row in rows],
    )
    return len(records)


def _is_invalid_fund_holding(rec: dict[str, Any]) -> bool:
    text = "".join(
        str(rec.get(field) or "")
        for field in ("fund_name", "shares_text", "float_a_ratio_text", "market_value_text")
    ).replace(" ", "")
    if any(keyword in text for keyword in FUND_HOLDING_REJECTION_KEYWORDS):
        return True
    if not rec.get("fund_name"):
        return True
    if rec.get("shares") is None or rec.get("market_value") is None:
        return True
    return False


def _insert_fund_holding_rows(conn: Any, records: list[dict[str, Any]]) -> tuple[int, int]:
    if not records:
        return 0, 0
    cols = [
        "stock_code", "stock_name", "market", "report_date", "report_date_text",
        "fund_name", "shares_text", "shares", "float_a_ratio_text",
        "float_a_ratio", "market_value_text", "market_value",
        "page_update_date",
        "source_notice_date", "source_available_date", "source_date_quality",
        "source", "source_tier", "raw_hash", "fetched_at",
        "row_seq",
    ]
    rows = []
    rejected = 0
    for rec in records:
        if _is_invalid_fund_holding(rec):
            rejected += 1
            continue
        notice, available, quality = _derive_conservative_f10_source_dates(rec)
        rec["source_notice_date"] = notice
        rec["source_available_date"] = available
        rec["source_date_quality"] = quality
        rec["source_tier"] = 1
        rows.append(tuple(rec.get(c) for c in cols))
    if rows:
        _upsert_rows_on_conflict(
            conn,
            table="fact_fund_holding_tdx_f10",
            cols=cols,
            key_fields=["stock_code", "fund_name", "report_date", "row_seq"],
            records=[dict(zip(cols, row)) for row in rows],
        )
    return len(rows), rejected


def _upsert_control(conn: Any, rec: dict[str, Any] | None, fallback: dict[str, Any]) -> int:
    if not rec:
        return 0
    row = {
        "stock_code": rec.get("stock_code") or fallback.get("stock_code"),
        "stock_name": rec.get("stock_name") or fallback.get("stock_name"),
        "market": rec.get("market") or fallback.get("market"),
        "primary_label": rec.get("primary_shareholder_label"),
        "primary_name": rec.get("primary_shareholder_name"),
        "primary_ratio": rec.get("primary_shareholder_ratio"),
        "primary_raw": rec.get("primary_shareholder_raw"),
        "actual_name": rec.get("actual_controller_name"),
        "actual_ratio": rec.get("actual_controller_ratio"),
        "actual_raw": rec.get("actual_controller_raw"),
        "page_update_date": rec.get("page_update_date"),
        "source": rec.get("source") or "tdx_f10",
        "source_tier": 1,
        "raw_hash": fallback.get("raw_hash") or rec.get("raw_hash"),
        "fetched_at": rec.get("fetched_at") or fallback.get("fetched_at"),
        "control_chain_text": rec.get("control_chain_text"),
    }
    notice, available, quality = _derive_conservative_f10_source_dates(row)
    row["source_notice_date"] = notice
    row["source_available_date"] = available
    row["source_date_quality"] = quality
    if not row["stock_code"] or not row["source"]:
        return 0
    cols = list(row.keys())
    _upsert_rows_on_conflict(
        conn,
        table="fact_controlling_shareholder",
        cols=cols,
        key_fields=["stock_code", "source"],
        records=[row],
    )
    return 1


F10_SOURCE_DATE_BACKFILL_TABLES = (
    "fact_holder_count_period",
    "fact_shareholder_trade_tdx_b",
    "fact_common_major_holder_stock",
    "fact_fund_holding_tdx_f10",
    "fact_controlling_shareholder",
)


def backfill_tdx_f10_source_dates(conn: Any) -> dict[str, Any]:
    """Backfill explicit PIT source-date columns for parsed TDX F10 fact tables.

    This does not claim to recover true exchange announcement timestamps. It
    makes the current conservative fallback explicit so downstream PIT audits
    can distinguish parsed notice dates from page-update/fetched-at fallback.
    """

    ensure_tables(conn)
    out: dict[str, Any] = {"tables": {}, "updated_rows": 0}
    for table in F10_SOURCE_DATE_BACKFILL_TABLES:
        if not _table_exists(conn, table):
            out["tables"][table] = {"exists": False, "updated_rows": 0, "remaining_missing": 0}
            continue
        cols = _table_columns(conn, table)
        required = {"source_notice_date", "source_available_date", "source_date_quality"}
        if not required.issubset(cols):
            out["tables"][table] = {
                "exists": True,
                "updated_rows": 0,
                "remaining_missing": None,
                "error": "missing_source_date_columns",
            }
            continue
        before = conn.execute(
            f"""
            SELECT COUNT(*) AS n
              FROM {table}
             WHERE source_available_date IS NULL
                OR source_available_date = ''
                OR source_date_quality IS NULL
                OR source_date_quality = ''
            """
        ).fetchone()["n"]
        conn.execute(
            f"""
            UPDATE {table}
               SET source_notice_date = COALESCE(NULLIF(source_notice_date, ''), NULLIF(page_update_date, '')),
                   source_available_date = COALESCE(
                       NULLIF(source_available_date, ''),
                       NULLIF(page_update_date, ''),
                       NULLIF(SUBSTR(CAST(fetched_at AS VARCHAR), 1, 10), '')
                   ),
                   source_date_quality = COALESCE(
                       NULLIF(source_date_quality, ''),
                       CASE
                           WHEN NULLIF(page_update_date, '') IS NOT NULL
                               THEN 'page_update_date_conservative_fallback'
                           WHEN NULLIF(CAST(fetched_at AS VARCHAR), '') IS NOT NULL
                               THEN 'fetched_at_conservative_fallback'
                           ELSE 'missing_source_date'
                       END
                   )
             WHERE source_available_date IS NULL
                OR source_available_date = ''
                OR source_date_quality IS NULL
                OR source_date_quality = ''
            """
        )
        after = conn.execute(
            f"""
            SELECT COUNT(*) AS n
              FROM {table}
             WHERE source_available_date IS NULL
                OR source_available_date = ''
                OR source_date_quality IS NULL
                OR source_date_quality = ''
            """
        ).fetchone()["n"]
        updated = int(before or 0) - int(after or 0)
        out["tables"][table] = {
            "exists": True,
            "updated_rows": updated,
            "remaining_missing": int(after or 0),
        }
        out["updated_rows"] += updated
    try:
        conn.commit()
    except Exception:
        pass
    return out


def _upsert_parse_status(
    conn: Any,
    fallback: dict[str, Any],
    *,
    status: str,
    holder_count_rows: int = 0,
    trade_b_rows: int = 0,
    control_rows: int = 0,
    common_major_holder_rows: int = 0,
    fund_holding_rows: int = 0,
    fund_holding_rejected_rows: int = 0,
    shareholder_plan_rows: int = 0,
    status_reason: str | None = None,
    error: str | None = None,
) -> None:
    if not fallback.get("stock_code") or not fallback.get("raw_hash"):
        return
    row = {
        "stock_code": fallback.get("stock_code"),
        "raw_hash": fallback.get("raw_hash"),
        "parsed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "holder_count_rows": holder_count_rows,
        "trade_b_rows": trade_b_rows,
        "control_rows": control_rows,
        "common_major_holder_rows": common_major_holder_rows,
        "fund_holding_rows": fund_holding_rows,
        "fund_holding_rejected_rows": fund_holding_rejected_rows,
        "shareholder_plan_rows": shareholder_plan_rows,
        "status": status,
        "status_reason": status_reason,
        "parser_version": PARSER_VERSION,
        "error": error,
    }
    cols = list(row.keys())
    _upsert_rows_on_conflict(
        conn,
        table="raw_tdx_f10_extra_parse_status",
        cols=cols,
        key_fields=["stock_code", "raw_hash"],
        records=[row],
    )


def sync_tdx_f10_extra_facts(
    conn: Any,
    *,
    stock_codes: list[str] | None = None,
    limit: int = 0,
    only_new: bool = True,
) -> dict[str, Any]:
    """Parse Format B extras from existing raw F10 text and upsert fact tables."""

    ensure_tables(conn)
    raw_rows = _select_raw_rows(
        conn,
        stock_codes=stock_codes,
        limit=limit,
        only_new=only_new,
    )
    alias_map = _load_alias_map(conn)

    stats: dict[str, Any] = {
        "raw_rows": len(raw_rows),
        "holder_count_rows": 0,
        "trade_b_rows": 0,
        "shareholder_plan_rows": 0,
        "control_rows": 0,
        "common_major_holder_rows": 0,
        "fund_holding_rows": 0,
        "fund_holding_rejected_rows": 0,
        "skipped_non_format_b": 0,
        "skipped_no_extra_section": 0,
        "errors": [],
    }
    for row in raw_rows:
        fallback = {
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "market": row.get("market"),
            "raw_hash": row.get("raw_hash"),
            "fetched_at": str(row.get("fetched_at")) if row.get("fetched_at") is not None else None,
        }
        try:
            text = row.get("raw_text") or ""
            if detect_f10_format(text) != "b":
                _upsert_parse_status(
                    conn,
                    fallback,
                    status="skipped_non_format_b",
                    status_reason="raw text is not TDX Format B",
                )
                stats["skipped_non_format_b"] += 1
                continue
            holder_records = _records_from_payload(
                parse_holder_count_history_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            trade_records = _records_from_payload(
                parse_shareholder_trades_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            plan_records = _records_from_payload(
                parse_shareholder_plans_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            common_records = _records_from_payload(
                parse_common_major_holder_stocks_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            fund_records = _records_from_payload(
                parse_fund_holdings_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            ctrl = parse_controlling_shareholder(
                text,
                symbol=fallback["stock_code"] or "",
                stock_name=fallback["stock_name"] or "",
            )
            row_holder_count = _insert_holder_count_rows(conn, holder_records)
            row_trade_b = _insert_trade_b_rows(conn, trade_records, alias_map)
            row_plan = _insert_shareholder_plan_rows(conn, plan_records)
            row_control = _upsert_control(conn, ctrl, fallback)
            row_common = _insert_common_major_holder_rows(conn, common_records)
            row_fund, row_fund_rejected = _insert_fund_holding_rows(conn, fund_records)
            row_status = "completed"
            if row_fund_rejected:
                row_status = "completed_with_rejections"
            if (
                row_holder_count == 0
                and row_trade_b == 0
                and row_plan == 0
                and row_control == 0
                and row_common == 0
                and row_fund == 0
                and row_fund_rejected == 0
            ):
                row_status = "skipped_no_extra_section"
            status_reason = "parsed_format_b"
            if row_status == "completed_with_rejections":
                status_reason = "fund_holding_rows_rejected"
            elif row_status == "skipped_no_extra_section":
                status_reason = "no supported extra section rows"
            _upsert_parse_status(
                conn,
                fallback,
                status=row_status,
                holder_count_rows=row_holder_count,
                trade_b_rows=row_trade_b,
                shareholder_plan_rows=row_plan,
                control_rows=row_control,
                common_major_holder_rows=row_common,
                fund_holding_rows=row_fund,
                fund_holding_rejected_rows=row_fund_rejected,
                status_reason=status_reason,
            )
            stats["holder_count_rows"] += row_holder_count
            stats["trade_b_rows"] += row_trade_b
            stats["shareholder_plan_rows"] += row_plan
            stats["control_rows"] += row_control
            stats["common_major_holder_rows"] += row_common
            stats["fund_holding_rows"] += row_fund
            stats["fund_holding_rejected_rows"] += row_fund_rejected
            if row_status == "skipped_no_extra_section":
                stats["skipped_no_extra_section"] += 1
        except Exception as exc:
            ident = f"{row.get('stock_code')}/{row.get('raw_hash')}"
            logger.warning("[tdx-f10-extra] parse failed for %s: %s", ident, exc)
            err = f"{ident}: {type(exc).__name__}: {exc}"
            stats["errors"].append(err)
            try:
                _upsert_parse_status(
                    conn,
                    fallback,
                    status="failed",
                    status_reason="exception",
                    error=err,
                )
            except Exception:
                pass

    try:
        conn.commit()
    except Exception:
        pass
    if stats["errors"]:
        stats["status"] = "partial"
    elif stats["fund_holding_rejected_rows"]:
        stats["status"] = "completed_with_rejections"
    else:
        stats["status"] = "completed"
    try:
        stats["capability_matrix"] = build_tdx_f10_capability_matrix(conn)
    except Exception as exc:
        stats["capability_matrix_error"] = f"{type(exc).__name__}: {exc}"
    return stats


__all__ = [
    "ensure_tables",
    "sync_tdx_f10_extra_facts",
    "build_tdx_f10_capability_matrix",
    "backfill_tdx_f10_shareholder_plans",
    "backfill_tdx_f10_source_dates",
]
