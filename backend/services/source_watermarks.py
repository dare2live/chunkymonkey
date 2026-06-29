"""Source-domain watermarks for data freshness and fallback visibility.

时间戳口径警示 (2026-06-12 复查乌龙教训): 本模块所有时间戳列
(first_seen_at/last_seen_at/resolved_at/last_success_at/updated_at) 存
`datetime.now(timezone.utc)` — 人读必须 +8h 转北京时, 直接当本地时间读
会把晚间事件误判成上午 (曾因此虚构出不存在的"record 静默失效"事故)。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
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

CREATE TABLE IF NOT EXISTS mart_data_source_failure_queue (
    failure_id TEXT PRIMARY KEY,
    data_domain TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_tier SMALLINT,
    stock_code TEXT,
    error_type TEXT,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    first_seen_at TIMESTAMP,
    last_seen_at TIMESTAMP,
    retry_after TIMESTAMP,
    occurrence_count INTEGER DEFAULT 1,
    resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_source_failure_open
    ON mart_data_source_failure_queue(status, data_domain, source_name);
"""


DOMAIN_SPECS = [
    # kline_daily 2026-06-28 repoint tushare canonical (price_kline_tdxhub U6前已物删 + akshare 源退役):
    #   单一 tier1 = tushare 前复权物化表 price_kline_qfq_tushare (M2 clean build, serving 真相源)。
    {
        "data_domain": "kline_daily",
        "source_name": "tushare",
        "source_tier": 1,
        "table": "market.price_kline_qfq_tushare",
        "date_col": "date",
        "parser_version": "tushare_qfq_daily",
    },
    {
        "data_domain": "holders_top10_float",
        "source_name": "miaoxiang",  # 2026-06-28: holder 主源=东财妙想 aif10 (fact_top10_holder_period source='miaoxiang'); 旧标 tdxhub_holders 退役
        "source_tier": 1,
        "table": "fact_top10_holder_period",
        "date_col": "report_date",
        "raw_hash_col": "raw_hash",
        "parser_version_col": None,
    },
    # financial_gpcw_8q watermark 条目已删 2026-06-27 (通达信全删 gpcw物删; 财务新鲜度走 tushare sync:* 域)
    # xdxr watermark 域已删 2026-06-28 (xdxr sync acquire 已移除, 复权走 tushare adj_factor; price_xdxr=tdxhub 残留表无 live sync)
    {
        # 2026-06-23 全项目单一供应商=东财迁移 (Stage②): serving 行业真相源 = dim_stock_dc_industry
        #   (东财行业=申万对齐同套桶, daily_update Step 2.96c build_dc_industry_view 每日刷新)。
        #   深史2025前PIT走 v_sw_industry_pit (申万深PIT兜底, 选A)。
        "data_domain": "industry_dc",
        "source_name": "tushare_dc",
        "source_tier": 1,
        "table": "dim_stock_dc_industry",
        "date_col": "updated_at",
        "parser_version": "dc",
    },
    # stock_blocks 域已删 (2026-06-23): 原指通达信 dim_stock_tdx_industry, 源物删; 行业新鲜度由上方 industry_dc 域 (东财 dim_stock_dc_industry) 跟踪。
    # lhb_daily 域已删 2026-06-29 (批2b: LHB 切 tushare top_list/top_inst, 新鲜度由 sync:top_list/sync:top_inst 自动域覆盖)
    {
        "data_domain": "institution_survey",
        "source_name": "tushare",
        "source_tier": 1,
        "table": "raw_tushare_stk_surv",
        "date_col": "surv_date",
        "parser_version": "tushare",  # 2026-06-28 批2: 切 tushare stk_surv 唯一 (aif10+akshare 退役)
    },
    {
        "data_domain": "qfii_holding_quarterly",
        "source_name": "aif10_qfii",
        "source_tier": 2,
        "table": "raw_qfii_holding_quarterly",
        "date_col": "report_date",
        "parser_version": "aif10_or_akshare",
    },
    # northbound_holding 域已删 2026-06-28 (akshare 源退役 + legacy_hsgt 表 ABSENT; 个股北向 tushare ~2025-07 停披露)
    # stock_fund_flow_rank_snapshot 域已删 2026-06-28 (akshare 源退役 + mart_stock_fund_flow_rank_snapshot_daily 表 ABSENT)
]


def ensure_source_watermark_schema(conn) -> None:
    conn.executescript(DDL)


def _table_parts(table: str) -> tuple[str | None, str]:
    parts = table.split(".", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, table)


def _quote_table(table: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in table.split("."))


def _where_clause(spec: dict[str, Any], key: str = "where") -> str:
    clause = str(spec.get(key) or "").strip()
    return f" WHERE {clause}" if clause else ""


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


def _failure_id(data_domain: str, source_name: str, stock_code: str | None = None, error_type: str | None = None) -> str:
    return _stable_hash(data_domain, source_name, stock_code or "", error_type or "generic")


def _fetch_failure_queue_rows(conn) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """
        SELECT failure_id, data_domain, source_name, source_tier, stock_code,
               error_type, last_error, status, first_seen_at, last_seen_at,
               retry_after, occurrence_count, resolved_at
          FROM mart_data_source_failure_queue
         ORDER BY first_seen_at, last_seen_at, failure_id
        """
    )
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


def _rewrite_failure_queue_rows(conn, rows: list[dict[str, Any]]) -> None:
    # This queue is tiny and ledger-like; full rewrite is safer than in-place
    # PK mutation on DuckDB when the index has seen prior failure churn.
    conn.execute("DROP TABLE IF EXISTS mart_data_source_failure_queue")
    ensure_source_watermark_schema(conn)
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO mart_data_source_failure_queue (
            failure_id, data_domain, source_name, source_tier, stock_code,
            error_type, last_error, status, first_seen_at, last_seen_at,
            retry_after, occurrence_count, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.get("failure_id"),
                row.get("data_domain"),
                row.get("source_name"),
                row.get("source_tier"),
                row.get("stock_code"),
                row.get("error_type"),
                row.get("last_error"),
                row.get("status") or "open",
                row.get("first_seen_at"),
                row.get("last_seen_at"),
                row.get("retry_after"),
                int(row.get("occurrence_count") or 0),
                row.get("resolved_at"),
            )
            for row in rows
        ],
    )


def record_source_failure(
    conn,
    *,
    data_domain: str,
    source_name: str,
    source_tier: int | None = None,
    stock_code: str | None = None,
    error_type: str = "source_failure",
    last_error: str | None = None,
    retry_after: str | None = None,
    commit: bool = False,
) -> str:
    ensure_source_watermark_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    failure_id = _failure_id(data_domain, source_name, stock_code, error_type)
    last_error_text = (last_error or "")[:1000]
    rows = _fetch_failure_queue_rows(conn)
    first_seen_at = now
    occurrence_count = 1
    replaced = False
    updated_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("failure_id") != failure_id:
            updated_rows.append(row)
            continue
        replaced = True
        try:
            first_seen_at = str(row.get("first_seen_at") or now)
        except Exception:
            first_seen_at = now
        try:
            occurrence_count = int(row.get("occurrence_count") or 0) + 1
        except Exception:
            occurrence_count = 2
        updated_rows.append(
            {
                **row,
                "data_domain": data_domain,
                "source_name": source_name,
                "source_tier": source_tier,
                "stock_code": stock_code,
                "error_type": error_type,
                "last_error": last_error_text,
                "status": "open",
                "first_seen_at": first_seen_at,
                "last_seen_at": now,
                "retry_after": retry_after,
                "occurrence_count": occurrence_count,
                "resolved_at": None,
            }
        )
    if not replaced:
        updated_rows.append(
            {
                "failure_id": failure_id,
                "data_domain": data_domain,
                "source_name": source_name,
                "source_tier": source_tier,
                "stock_code": stock_code,
                "error_type": error_type,
                "last_error": last_error_text,
                "status": "open",
                "first_seen_at": first_seen_at,
                "last_seen_at": now,
                "retry_after": retry_after,
                "occurrence_count": occurrence_count,
                "resolved_at": None,
            }
        )
    _rewrite_failure_queue_rows(conn, updated_rows)
    if commit:
        conn.commit()
    return failure_id


def resolve_source_failures(
    conn,
    *,
    data_domain: str,
    source_name: str,
    stock_code: str | None = None,
    commit: bool = False,
) -> int:
    ensure_source_watermark_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    rows = _fetch_failure_queue_rows(conn)
    resolved = 0
    updated_rows: list[dict[str, Any]] = []
    for row in rows:
        matches = (
            row.get("data_domain") == data_domain
            and row.get("source_name") == source_name
            and row.get("status") == "open"
            and (stock_code is None or row.get("stock_code") == stock_code)
        )
        if matches:
            resolved += 1
            updated_rows.append(
                {
                    **row,
                    "status": "resolved",
                    "resolved_at": now,
                    "last_seen_at": now,
                }
            )
        else:
            updated_rows.append(row)
    _rewrite_failure_queue_rows(conn, updated_rows)
    if commit:
        conn.commit()
    return resolved


def list_source_failures(conn, *, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
    ensure_source_watermark_schema(conn)
    rows = conn.execute(
        """
        SELECT failure_id, data_domain, source_name, source_tier, stock_code,
               error_type, last_error, status, first_seen_at, last_seen_at,
               retry_after, occurrence_count, resolved_at
          FROM mart_data_source_failure_queue
         WHERE status = ?
         ORDER BY last_seen_at DESC
         LIMIT ?
        """,
        (status, max(1, min(int(limit), 1000))),
    ).fetchall()
    return [dict(row) for row in rows]


def derive_watermark(conn, spec: dict[str, Any]) -> dict[str, Any]:
    table = spec["table"]
    now = datetime.now(timezone.utc).isoformat()
    if not _table_exists(conn, table):
        return {
            **spec,
            "last_success_at": None,
            "last_data_date": None,
            "last_raw_hash": None,
            "row_count": 0,
            "fallback_active": False,
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
          {_where_clause(spec)}
        """
    ).fetchone()
    row_count = int(row["row_count"] or 0)
    last_data_date = row["last_data_date"]
    last_raw_hash = row["last_raw_hash"] or _stable_hash(table, row_count, last_data_date)
    fallback_active = _derive_fallback_active(
        conn,
        spec,
        row_count=row_count,
        last_data_date=last_data_date,
    )
    return {
        **spec,
        "last_success_at": now if row_count > 0 else None,
        "last_data_date": last_data_date,
        "last_raw_hash": last_raw_hash,
        "row_count": row_count,
        "fallback_active": fallback_active,
        "fallback_reason": spec.get("fallback_reason"),
        "consecutive_failures": 0 if row_count > 0 else 1,
        "updated_at": now,
    }


def _derive_fallback_active(
    conn,
    spec: dict[str, Any],
    *,
    row_count: int,
    last_data_date: str | None,
) -> bool:
    """Return whether this source is actively filling a primary-source gap."""

    if row_count <= 0 or int(spec.get("source_tier") or 0) <= 1:
        return False
    if spec.get("fallback_mode") != "fills_primary_gap":
        return False

    primary_table = spec.get("primary_table")
    primary_date_col = spec.get("primary_date_col") or spec.get("date_col")
    if not primary_table or not primary_date_col or not last_data_date:
        return False
    if not _table_exists(conn, primary_table):
        return True
    gap_key_cols = list(spec.get("gap_key_cols") or [])
    if gap_key_cols:
        fallback_cols = _columns(conn, table=spec["table"])
        primary_cols = _columns(conn, primary_table)
        if all(col in fallback_cols and col in primary_cols for col in gap_key_cols):
            select_cols = ", ".join(f'"{col}"' for col in gap_key_cols)
            join_clause = " AND ".join(f"p.\"{col}\" = f.\"{col}\"" for col in gap_key_cols)
            missing_probe = conn.execute(
                f"""
                WITH fallback_rows AS (
                    SELECT {select_cols}
                      FROM {_quote_table(spec["table"])}
                      {_where_clause(spec)}
                ),
                primary_rows AS (
                    SELECT {select_cols}
                      FROM {_quote_table(primary_table)}
                      {_where_clause(spec, "primary_where")}
                )
                SELECT 1
                  FROM fallback_rows f
                  LEFT JOIN primary_rows p ON {join_clause}
                 WHERE p."{gap_key_cols[0]}" IS NULL
                 LIMIT 1
                """
            ).fetchone()
            return missing_probe is not None
    primary_cols = _columns(conn, primary_table)
    if primary_date_col not in primary_cols:
        return True
    row = conn.execute(
        f"""
        SELECT CAST(MAX("{primary_date_col}") AS TEXT) AS last_data_date
          FROM {_quote_table(primary_table)}
          {_where_clause(spec, "primary_where")}
        """
    ).fetchone()
    primary_last = row["last_data_date"] if row else None
    if primary_last is None:
        return True
    return str(last_data_date) > str(primary_last)


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
            item.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        ),
    )


def refresh_known_source_watermarks(conn) -> list[dict[str, Any]]:
    ensure_source_watermark_schema(conn)
    _attach_external_dbs(conn)
    items = [derive_watermark(conn, spec) for spec in DOMAIN_SPECS]
    for item in items:
        upsert_watermark(conn, item)
        if int(item.get("consecutive_failures") or 0) > 0:
            record_source_failure(
                conn,
                data_domain=item["data_domain"],
                source_name=item["source_name"],
                source_tier=int(item["source_tier"]),
                error_type="watermark_failure",
                last_error=item.get("fallback_reason") or "no rows or table missing",
            )
        else:
            resolve_source_failures(
                conn,
                data_domain=item["data_domain"],
                source_name=item["source_name"],
            )
    try:
        from services.schema_versions import record_actual_version

        record_actual_version(conn, "mart_data_source_watermark")
        record_actual_version(conn, "mart_data_source_failure_queue")
    except Exception:
        pass
    conn.commit()
    return items
