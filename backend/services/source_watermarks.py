"""Source-domain watermarks for data freshness and fallback visibility."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any


DDL = """
CREATE TABLE IF NOT EXISTS mart_data_source_watermark (
    data_domain          TEXT NOT NULL,
    source_name          TEXT NOT NULL,
    source_tier          SMALLINT NOT NULL,
    last_success_at      TIMESTAMP,
    last_data_date       TEXT,
    last_raw_hash        TEXT,
    next_check_at        TIMESTAMP,
    consecutive_failures INTEGER DEFAULT 0,
    fallback_active      BOOLEAN DEFAULT FALSE,
    fallback_reason      TEXT,
    row_count            BIGINT,
    parser_version       TEXT,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (data_domain, source_name, source_tier)
);
CREATE INDEX IF NOT EXISTS idx_source_watermark_domain
    ON mart_data_source_watermark(data_domain, source_tier);
"""


DOMAIN_SPECS = [
    {
        "data_domain": "kline_daily",
        "source_name": "tdxhub_quote",
        "source_tier": 1,
        "table": "market.price_kline_tdxhub",
        "date_col": "date",
        "parser_version": "tdxhub_qfq_daily",
    },
    {
        "data_domain": "kline_daily",
        "source_name": "akshare_multi_source",
        "source_tier": 3,
        "table": "market.price_kline",
        "date_col": "date",
        "parser_version": "akshare_fallback_daily",
        "fallback_reason": "fills dates not yet present in tdxhub qfq daily",
    },
    {
        "data_domain": "holders_top10_float",
        "source_name": "tdxhub_holders",
        "source_tier": 1,
        "table": "fact_top10_holder_period",
        "date_col": "report_date",
        "raw_hash_col": "raw_hash",
        "parser_version_col": None,
    },
    {
        "data_domain": "financial_gpcw_8q",
        "source_name": "tdxhub_gpcw",
        "source_tier": 1,
        "table": "raw_gpcw_detail",
        "date_col": "report_date",
        "parser_version": "tdxhub_gpcw",
    },
    {
        "data_domain": "xdxr",
        "source_name": "tdxhub_xdxr",
        "source_tier": 1,
        "table": "market.price_xdxr",
        "date_col": "date",
        "parser_version": "tdxhub_xdxr",
    },
    {
        "data_domain": "industry_sw",
        "source_name": "tdxhub_block",
        "source_tier": 1,
        "table": "dim_stock_tdx_industry",
        "date_col": "updated_at",
        "parser_version": "tdxhub_block",
    },
    {
        "data_domain": "stock_blocks",
        "source_name": "tdxhub_block",
        "source_tier": 1,
        "table": "dim_stock_tdx_industry",
        "date_col": "updated_at",
        "parser_version": "tdxhub_block",
    },
    {
        "data_domain": "lhb_daily",
        "source_name": "aif10_lhb",
        "source_tier": 2,
        "table": "raw_lhb_daily",
        "date_col": "trade_date",
        "parser_version": "aif10_or_akshare",
    },
    {
        "data_domain": "institution_survey",
        "source_name": "aif10_survey",
        "source_tier": 2,
        "table": "raw_institution_surveys",
        "date_col": "survey_date",
        "parser_version": "aif10_or_akshare",
    },
    {
        "data_domain": "qfii_holding_quarterly",
        "source_name": "aif10_qfii",
        "source_tier": 2,
        "table": "raw_qfii_holding_quarterly",
        "date_col": "report_date",
        "parser_version": "aif10_or_akshare",
    },
    {
        "data_domain": "northbound_holding",
        "source_name": "akshare_hsgt",
        "source_tier": 3,
        "table": "legacy_hsgt",
        "date_col": "date",
        "parser_version": "akshare",
        "fallback_reason": "no stable tdxhub/miaoxiang primary in current repo",
    },
]


def ensure_source_watermark_schema(conn) -> None:
    conn.executescript(DDL)


def _table_parts(table: str) -> tuple[str | None, str]:
    parts = table.split(".", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, table)


def _quote_table(table: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in table.split("."))


def _table_exists(conn, table: str) -> bool:
    schema, table_name = _table_parts(table)
    if schema:
        row = conn.execute(
            """
            SELECT 1 FROM information_schema.tables
             WHERE (table_schema = ? OR table_catalog = ?) AND table_name = ?
             LIMIT 1
            """,
            (schema, schema, table_name),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    return row is not None


def _columns(conn, table: str) -> set[str]:
    schema, table_name = _table_parts(table)
    if schema:
        return {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                 WHERE (table_schema = ? OR table_catalog = ?) AND table_name = ?
                """,
                (schema, schema, table_name),
            ).fetchall()
        }
    return {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table_name,),
        ).fetchall()
    }


def _attach_external_dbs(conn) -> None:
    try:
        existing = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'market' LIMIT 1"
        ).fetchone()
        if existing is not None:
            return
    except Exception:
        pass
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    market_db = data_dir / "market.duckdb"
    if market_db.exists():
        try:
            duck = conn.raw if hasattr(conn, "raw") else conn
            duck.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS market (READ_ONLY)")
        except Exception:
            pass


def _stable_hash(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def derive_watermark(conn, spec: dict[str, Any]) -> dict[str, Any]:
    table = spec["table"]
    now = datetime.utcnow().isoformat()
    if not _table_exists(conn, table):
        return {
            **spec,
            "last_success_at": None,
            "last_data_date": None,
            "last_raw_hash": None,
            "row_count": 0,
            "fallback_active": spec["source_tier"] > 1,
            "fallback_reason": spec.get("fallback_reason") or f"table_missing:{table}",
            "consecutive_failures": 1,
            "updated_at": now,
        }

    cols = _columns(conn, table)
    date_col = spec.get("date_col")
    raw_hash_col = spec.get("raw_hash_col")
    date_expr = f'"{date_col}"' if date_col in cols else "CAST(NULL AS VARCHAR)"
    hash_expr = f'"{raw_hash_col}"' if raw_hash_col and raw_hash_col in cols else "CAST(NULL AS VARCHAR)"
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS row_count,
            CAST(MAX({date_expr}) AS TEXT) AS last_data_date,
            CAST(MAX({hash_expr}) AS TEXT) AS last_raw_hash
          FROM {_quote_table(table)}
        """
    ).fetchone()
    row_count = int(row["row_count"] or 0)
    last_data_date = row["last_data_date"]
    last_raw_hash = row["last_raw_hash"] or _stable_hash(table, row_count, last_data_date)
    return {
        **spec,
        "last_success_at": now if row_count > 0 else None,
        "last_data_date": last_data_date,
        "last_raw_hash": last_raw_hash,
        "row_count": row_count,
        "fallback_active": spec["source_tier"] > 1 and row_count > 0,
        "fallback_reason": spec.get("fallback_reason"),
        "consecutive_failures": 0 if row_count > 0 else 1,
        "updated_at": now,
    }


def upsert_watermark(conn, item: dict[str, Any]) -> None:
    ensure_source_watermark_schema(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_data_source_watermark (
            data_domain, source_name, source_tier,
            last_success_at, last_data_date, last_raw_hash, next_check_at,
            consecutive_failures, fallback_active, fallback_reason,
            row_count, parser_version, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["data_domain"],
            item["source_name"],
            int(item["source_tier"]),
            item.get("last_success_at"),
            item.get("last_data_date"),
            item.get("last_raw_hash"),
            item.get("next_check_at"),
            int(item.get("consecutive_failures") or 0),
            bool(item.get("fallback_active")),
            item.get("fallback_reason"),
            int(item.get("row_count") or 0),
            item.get("parser_version")
            or item.get("parser_version_col")
            or "unknown",
            item.get("updated_at") or datetime.utcnow().isoformat(),
        ),
    )


def refresh_known_source_watermarks(conn) -> list[dict[str, Any]]:
    ensure_source_watermark_schema(conn)
    _attach_external_dbs(conn)
    items = [derive_watermark(conn, spec) for spec in DOMAIN_SPECS]
    for item in items:
        upsert_watermark(conn, item)
    conn.commit()
    return items
