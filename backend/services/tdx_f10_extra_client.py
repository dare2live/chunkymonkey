"""TDX F10 Format B extra landing.

This module parses already-captured ``raw_tdx_f10_holder_research`` rows.  It
does not fetch live F10 data; daily fetching stays in ``ingest_holders_tdxhub``.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("cm-api")


def _ensure_tdxhub_import_path() -> None:
    try:
        import tdxhub.holders  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    stock_root = Path(__file__).resolve().parents[3]
    local_tdxhub = stock_root / "tdxhub"
    if local_tdxhub.exists() and str(local_tdxhub) not in sys.path:
        sys.path.insert(0, str(local_tdxhub))


_ensure_tdxhub_import_path()

from tdxhub.holders import (  # noqa: E402
    detect_f10_format,
    parse_common_major_holder_stocks_format_b,
    parse_controlling_shareholder,
    parse_fund_holdings_format_b,
    parse_holder_count_history_format_b,
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
    status TEXT NOT NULL,
    status_reason TEXT,
    parser_version TEXT,
    error TEXT,
    PRIMARY KEY (stock_code, raw_hash)
);
CREATE INDEX IF NOT EXISTS idx_f10_extra_status_status
    ON raw_tdx_f10_extra_parse_status(status);
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS fund_holding_rejected_rows INTEGER DEFAULT 0;
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
    PRIMARY KEY (stock_code, source)
);
ALTER TABLE fact_controlling_shareholder ADD COLUMN control_chain_text TEXT;
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


def _records_from_df(df: Any, fallback: dict[str, Any]) -> list[dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    records: list[dict[str, Any]] = []
    for idx, raw in enumerate(df.to_dict("records"), 1):
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
    conn.executemany(
        f"INSERT OR REPLACE INTO raw_tdx_f10_holder_count_history ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        [tuple(rec.get(c) for c in cols) for rec in records],
    )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fact_cols = [
        "stock_code", "stock_name", "market", "report_date", "holder_count",
        "holder_count_change", "holder_count_change_pct", "avg_float_shares",
        "avg_float_shares_change_pct", "close_price", "page_update_date",
        "source", "source_tier", "raw_hash", "fetched_at", "updated_at",
    ]
    fact_rows = []
    for rec in records:
        if not rec.get("report_date"):
            continue
        fact = dict(rec)
        fact["source_tier"] = 1
        fact["updated_at"] = now
        fact_rows.append(tuple(fact.get(c) for c in fact_cols))
    if fact_rows:
        conn.executemany(
            f"INSERT OR REPLACE INTO fact_holder_count_period ({','.join(fact_cols)}) "
            f"VALUES ({','.join(['?'] * len(fact_cols))})",
            fact_rows,
        )
    return len(records)


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
        "shares_after", "change_method", "page_update_date", "source",
        "source_tier", "raw_hash", "fetched_at", "trade_seq",
    ]
    rows = []
    for rec in records:
        rec["holder_name_norm"] = alias_map.get(rec.get("holder_name"), rec.get("holder_name"))
        rec["source_tier"] = 1
        rows.append(tuple(rec.get(c) for c in cols))
    conn.executemany(
        f"INSERT OR REPLACE INTO fact_shareholder_trade_tdx_b ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        rows,
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
        "net_profit_deducted", "page_update_date", "source", "source_tier",
        "raw_hash", "fetched_at", "row_seq",
    ]
    rows = []
    for rec in records:
        rec["source_tier"] = 1
        rows.append(tuple(rec.get(c) for c in cols))
    conn.executemany(
        f"INSERT OR REPLACE INTO fact_common_major_holder_stock ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        rows,
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
        "page_update_date", "source", "source_tier", "raw_hash", "fetched_at",
        "row_seq",
    ]
    rows = []
    rejected = 0
    for rec in records:
        if _is_invalid_fund_holding(rec):
            rejected += 1
            continue
        rec["source_tier"] = 1
        rows.append(tuple(rec.get(c) for c in cols))
    if rows:
        conn.executemany(
            f"INSERT OR REPLACE INTO fact_fund_holding_tdx_f10 ({','.join(cols)}) "
            f"VALUES ({','.join(['?'] * len(cols))})",
            rows,
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
    if not row["stock_code"] or not row["source"]:
        return 0
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO fact_controlling_shareholder ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        tuple(row[c] for c in cols),
    )
    return 1


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
        "status": status,
        "status_reason": status_reason,
        "parser_version": PARSER_VERSION,
        "error": error,
    }
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO raw_tdx_f10_extra_parse_status ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        tuple(row[c] for c in cols),
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
            holder_records = _records_from_df(
                parse_holder_count_history_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            trade_records = _records_from_df(
                parse_shareholder_trades_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            common_records = _records_from_df(
                parse_common_major_holder_stocks_format_b(
                    text,
                    symbol=fallback["stock_code"] or "",
                    stock_name=fallback["stock_name"] or "",
                ),
                fallback,
            )
            fund_records = _records_from_df(
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
            row_control = _upsert_control(conn, ctrl, fallback)
            row_common = _insert_common_major_holder_rows(conn, common_records)
            row_fund, row_fund_rejected = _insert_fund_holding_rows(conn, fund_records)
            row_status = "completed"
            if row_fund_rejected:
                row_status = "completed_with_rejections"
            if (
                row_holder_count == 0
                and row_trade_b == 0
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
                control_rows=row_control,
                common_major_holder_rows=row_common,
                fund_holding_rows=row_fund,
                fund_holding_rejected_rows=row_fund_rejected,
                status_reason=status_reason,
            )
            stats["holder_count_rows"] += row_holder_count
            stats["trade_b_rows"] += row_trade_b
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
    return stats


__all__ = ["ensure_tables", "sync_tdx_f10_extra_facts"]
