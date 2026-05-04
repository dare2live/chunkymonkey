#!/usr/bin/env python3
"""Build semantic dictionary rows and quarterly auto features from TDX gpcw."""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402

logger = logging.getLogger("tdx_gpcw_auto_features")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


AUTO_FEATURE_SET_ID = "tdx_gpcw_auto_v1"


DDL = """
CREATE TABLE IF NOT EXISTS dim_tdx_gpcw_field_semantic (
    field_key TEXT PRIMARY KEY,
    zh_name TEXT,
    db_column TEXT,
    field_index INTEGER,
    unit TEXT,
    field_family TEXT,
    semantic_role TEXT,
    value_type TEXT,
    scale_rule TEXT,
    pit_date_field TEXT,
    candidate_priority TEXT,
    exclude_reason TEXT,
    source_profile_run_id TEXT,
    mapped_status TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS fact_tdx_gpcw_auto_feature_quarterly (
    feature_set_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    available_date TEXT NOT NULL,
    field_key TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_family TEXT,
    transform TEXT NOT NULL,
    feature_value DOUBLE,
    source_value DOUBLE,
    coverage_group TEXT,
    built_at TEXT,
    PRIMARY KEY (feature_set_id, stock_code, report_date, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_tdx_gpcw_auto_quarterly_feature
    ON fact_tdx_gpcw_auto_feature_quarterly(feature_set_id, feature_name);
CREATE INDEX IF NOT EXISTS idx_tdx_gpcw_auto_quarterly_asof
    ON fact_tdx_gpcw_auto_feature_quarterly(feature_set_id, stock_code, available_date);
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _latest_profile_run(conn: Any) -> str:
    row = conn.execute(
        """
        SELECT profile_run_id
        FROM mart_tdx_gpcw_field_profile
        GROUP BY profile_run_id
        ORDER BY MAX(profiled_at) DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("mart_tdx_gpcw_field_profile has no profile runs")
    return str(row[0])


def _value_type(zh_name: str, unit: str | None, db_column: str | None) -> str:
    text = f"{zh_name} {unit or ''} {db_column or ''}".lower()
    if "日期" in zh_name or text.endswith("_date") or "announce_date" in text:
        return "date"
    if "%" in zh_name or "率" in zh_name or "ratio" in text or "roe" in text:
        return "ratio"
    if "机构数" in zh_name or "人数" in zh_name or "户" in zh_name or "count" in text:
        return "count"
    if "股" in zh_name or "shares" in text:
        return "shares"
    if "每股" in zh_name or "eps" in text or "per_share" in text:
        return "per_share"
    if "flag" in text:
        return "flag"
    return "amount"


def _scale_rule(zh_name: str, unit: str | None, value_type: str) -> str:
    text = f"{zh_name} {unit or ''}"
    if "%" in text or value_type == "ratio":
        return "pct_to_decimal"
    if "万股" in text:
        return "wan_share_to_share"
    if "万元" in text:
        return "wan_to_yuan"
    return "raw"


def _semantic_role(field_family: str, value_type: str, zh_name: str) -> str:
    if value_type == "date":
        return "pit_date"
    if field_family == "forecast_express":
        return "forecast_or_express_signal"
    if field_family == "ownership":
        return "ownership_or_chip_signal"
    if field_family == "fundamental_quality":
        return "quality_balance_cashflow_signal"
    if field_family == "profit_growth":
        return "profit_growth_signal"
    if "现金流" in zh_name:
        return "cashflow_quality_signal"
    return f"{field_family}_signal"


def _pit_date_field(field_family: str, db_column: str | None, zh_name: str) -> str:
    if db_column == "forecast_announce_date" or "预告" in zh_name:
        return "forecast_announce_date"
    if "快报" in zh_name:
        return "report_announce_date"
    if field_family == "forecast_express":
        return "forecast_announce_date"
    return "report_announce_date"


def refresh_semantic_dictionary(conn: Any, profile_run_id: str | None = None) -> dict[str, Any]:
    ensure_tables(conn)
    profile_run_id = profile_run_id or _latest_profile_run(conn)
    updated_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT d.field_key, d.zh_name, d.db_column, d.field_index, d.unit,
               COALESCE(p.field_family, d.field_family, 'other') AS field_family,
               COALESCE(p.model_candidate, d.model_candidate, FALSE) AS model_candidate,
               p.rejection_reason
        FROM dim_tdx_gpcw_field d
        LEFT JOIN mart_tdx_gpcw_field_profile p
          ON p.field_key = d.field_key AND p.profile_run_id = ?
        ORDER BY d.field_index NULLS LAST, d.field_key
        """,
        (profile_run_id,),
    ).fetchall()
    out = []
    for row in rows:
        field_key = str(row["field_key"])
        zh_name = str(row["zh_name"] or field_key)
        db_column = row["db_column"]
        field_family = str(row["field_family"] or "other")
        value_type = _value_type(zh_name, row["unit"], db_column)
        exclude_reason = row["rejection_reason"]
        mapped_status = "mapped"
        if zh_name.startswith("col") or field_key.startswith("col"):
            mapped_status = "exclude"
            exclude_reason = exclude_reason or "unnamed_col"
        elif not db_column:
            mapped_status = "exclude"
            exclude_reason = exclude_reason or "no_db_column"
        elif not bool(row["model_candidate"]):
            mapped_status = "profile_rejected"
        candidate_priority = "p1" if bool(row["model_candidate"]) and mapped_status == "mapped" else "exclude"
        if field_family in {"forecast_express", "ownership", "fundamental_quality", "profit_growth"} and candidate_priority != "exclude":
            candidate_priority = "p0"
        out.append(
            (
                field_key,
                zh_name,
                db_column,
                row["field_index"],
                row["unit"],
                field_family,
                _semantic_role(field_family, value_type, zh_name),
                value_type,
                _scale_rule(zh_name, row["unit"], value_type),
                _pit_date_field(field_family, db_column, zh_name),
                candidate_priority,
                exclude_reason,
                profile_run_id,
                mapped_status,
                updated_at,
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO dim_tdx_gpcw_field_semantic
        (field_key, zh_name, db_column, field_index, unit, field_family,
         semantic_role, value_type, scale_rule, pit_date_field,
         candidate_priority, exclude_reason, source_profile_run_id,
         mapped_status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        out,
    )
    conn.commit()
    return {"profile_run_id": profile_run_id, "semantic_rows": len(out), "updated_at": updated_at}


def _safe_feature_token(name: str) -> str:
    token = re.sub(r"[^0-9A-Za-z_]+", "_", name.strip().lower())
    token = re.sub(r"_+", "_", token).strip("_")
    if not token:
        token = "field"
    if token[0].isdigit():
        token = f"f_{token}"
    return token[:48]


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _available_date_sql(date_expr: str) -> str:
    six = f"lpad(CAST(CAST({date_expr} AS BIGINT) AS VARCHAR), 6, '0')"
    parsed = (
        f"CASE WHEN {date_expr} IS NOT NULL AND CAST({date_expr} AS DOUBLE) > 0 THEN "
        f"(CASE WHEN CAST(substr({six}, 1, 2) AS INTEGER) >= 90 THEN '19' ELSE '20' END || "
        f"substr({six}, 1, 2) || '-' || substr({six}, 3, 2) || '-' || substr({six}, 5, 2)) "
        "ELSE NULL END"
    )
    conservative = """
    CASE
      WHEN substr(report_date, 6, 2) = '03' THEN substr(report_date, 1, 4) || '-04-30'
      WHEN substr(report_date, 6, 2) = '06' THEN substr(report_date, 1, 4) || '-08-31'
      WHEN substr(report_date, 6, 2) = '09' THEN substr(report_date, 1, 4) || '-10-31'
      WHEN substr(report_date, 6, 2) = '12' THEN CAST(CAST(substr(report_date, 1, 4) AS INTEGER) + 1 AS VARCHAR) || '-04-30'
      ELSE report_date
    END
    """
    return f"COALESCE({parsed}, {conservative})"


REQUIRED_BASE_COLUMNS = {
    "operating_cashflow",
    "net_profit",
    "contract_liabilities",
    "revenue",
    "accounts_receivable",
    "inventory",
    "forecast_profit_yoy_low",
    "forecast_profit_yoy_high",
    "holder_count",
}


def _feature_rows(conn: Any, profile_run_id: str, max_base_fields: int) -> list[dict[str, Any]]:
    raw_cols = {row[0] for row in conn.execute("DESCRIBE raw_gpcw_detail").fetchall()}
    rows = conn.execute(
        """
        SELECT s.field_key, s.zh_name, s.db_column, s.field_family, s.value_type,
               s.pit_date_field, p.coverage_pct
        FROM dim_tdx_gpcw_field_semantic s
        JOIN mart_tdx_gpcw_field_profile p
          ON p.field_key = s.field_key AND p.profile_run_id = ?
        WHERE s.candidate_priority IN ('p0', 'p1')
          AND s.mapped_status = 'mapped'
          AND p.model_candidate
        ORDER BY
          CASE s.field_family
            WHEN 'forecast_express' THEN 0
            WHEN 'ownership' THEN 1
            WHEN 'fundamental_quality' THEN 2
            WHEN 'profit_growth' THEN 3
            ELSE 4
          END,
          p.coverage_pct DESC,
          s.field_index
        """,
        (profile_run_id,),
    ).fetchall()
    selected: list[dict[str, Any]] = []
    seen_cols: set[str] = set()
    rows_by_col = {row["db_column"]: row for row in rows if row["db_column"] in raw_cols}
    for row in rows:
        col = row["db_column"]
        if col not in raw_cols or col in seen_cols:
            continue
        if col in {"report_announce_date", "forecast_announce_date", "ingested_at"}:
            continue
        selected.append({k: row[k] for k in row.keys()})
        seen_cols.add(col)
        if len(selected) >= max_base_fields:
            break
    for col in sorted(REQUIRED_BASE_COLUMNS):
        if col in seen_cols or col not in rows_by_col:
            continue
        row = rows_by_col[col]
        selected.append({k: row[k] for k in row.keys()})
        seen_cols.add(col)
    return selected


def build_tdx_gpcw_auto_features(
    conn: Any,
    *,
    feature_set_id: str = AUTO_FEATURE_SET_ID,
    profile_run_id: str | None = None,
    max_base_fields: int = 80,
) -> dict[str, Any]:
    ensure_tables(conn)
    semantic = refresh_semantic_dictionary(conn, profile_run_id)
    profile_run_id = semantic["profile_run_id"]
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    fields = _feature_rows(conn, profile_run_id, max_base_fields)
    if not fields:
        raise RuntimeError("no mapped model-candidate gpcw fields available for auto features")

    conn.execute("SET preserve_insertion_order=false")
    conn.execute("SET threads=2")
    conn.execute("DELETE FROM fact_tdx_gpcw_auto_feature_quarterly WHERE feature_set_id = ?", (feature_set_id,))
    generated: list[str] = []
    selected_cols = list(dict.fromkeys(str(field["db_column"]) for field in fields))
    temp_cols = []
    for col in selected_cols:
        ident = _quote_ident(col)
        temp_cols.append(f"{ident}")
        temp_cols.append(f"LAG({ident}) OVER (PARTITION BY stock_code ORDER BY report_date) AS {_quote_ident(col + '__lag1')}")
        temp_cols.append(f"LAG({ident}, 4) OVER (PARTITION BY stock_code ORDER BY report_date) AS {_quote_ident(col + '__lag4')}")
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE tmp_gpcw_auto_enriched AS
        SELECT stock_code, report_date, report_announce_date, forecast_announce_date,
               {', '.join(temp_cols)}
        FROM raw_gpcw_detail
        """
    )

    select_sql: list[str] = []

    def add_select(
        *,
        field_key: str,
        family: str,
        feature_name: str,
        transform: str,
        value_expr: str,
        source_expr: str,
        date_col: str,
        coverage_group: str,
    ) -> None:
        available_date = _available_date_sql(date_col)
        select_sql.append(
            f"""
            SELECT {_sql_literal(feature_set_id)} AS feature_set_id,
                   stock_code,
                   report_date,
                   greatest({available_date}, report_date) AS available_date,
                   {_sql_literal(field_key)} AS field_key,
                   {_sql_literal(feature_name)} AS feature_name,
                   {_sql_literal(family)} AS feature_family,
                   {_sql_literal(transform)} AS transform,
                   CAST({value_expr} AS DOUBLE) AS feature_value,
                   CAST({source_expr} AS DOUBLE) AS source_value,
                   {_sql_literal(coverage_group)} AS coverage_group,
                   {_sql_literal(built_at)} AS built_at
            FROM tmp_gpcw_auto_enriched
            WHERE {source_expr} IS NOT NULL
            """
        )

    for field in fields:
        base_name = f"auto_{_safe_feature_token(str(field['db_column']))}"
        col = _quote_ident(str(field["db_column"]))
        lag1 = _quote_ident(str(field["db_column"]) + "__lag1")
        lag4 = _quote_ident(str(field["db_column"]) + "__lag4")
        date_col = "forecast_announce_date" if field["pit_date_field"] == "forecast_announce_date" else "report_announce_date"
        transforms = [
            (f"{base_name}_level", "level", col),
            (f"{base_name}_qoq", "qoq", f"{col} / NULLIF({lag1}, 0) - 1"),
            (f"{base_name}_yoy", "yoy", f"{col} / NULLIF({lag4}, 0) - 1"),
        ]
        if field["field_family"] in {"ownership", "forecast_express"}:
            transforms.append((f"{base_name}_event_nonzero", "event_nonzero", f"CASE WHEN {col} <> 0 THEN 1.0 ELSE 0.0 END"))
        for feature_name, transform, expr in transforms:
            add_select(
                field_key=str(field["field_key"]),
                family=str(field["field_family"]),
                feature_name=feature_name,
                transform=transform,
                value_expr=expr,
                source_expr=col,
                date_col=date_col,
                coverage_group="core_profile_candidate",
            )
            generated.append(feature_name)

    special_sql = [
        ("ocf_to_profit_tdx", "f234", "fundamental_quality", "ratio_to_profit", "operating_cashflow / NULLIF(net_profit, 0)", "operating_cashflow"),
        ("contract_liabilities_to_revenue", "f434", "fundamental_quality", "ratio_to_revenue", "contract_liabilities / NULLIF(revenue, 0)", "contract_liabilities"),
        ("receivables_to_revenue", "f011", "fundamental_quality", "ratio_to_revenue", "accounts_receivable / NULLIF(revenue, 0)", "accounts_receivable"),
        ("inventory_to_revenue", "f017", "fundamental_quality", "ratio_to_revenue", "inventory / NULLIF(revenue, 0)", "inventory"),
        ("forecast_profit_yoy_mid", "f285", "forecast_express", "forecast_mid", "(forecast_profit_yoy_low + forecast_profit_yoy_high) / 2.0", "forecast_profit_yoy_low"),
        ("forecast_range_width", "f286", "forecast_express", "forecast_range_width", "forecast_profit_yoy_high - forecast_profit_yoy_low", "forecast_profit_yoy_high"),
        ("inverse_holder_count_change_pct_tdx", "f242", "ownership", "inverse_qoq", "-1.0 * (holder_count / NULLIF(LAG(holder_count) OVER (PARTITION BY stock_code ORDER BY report_date), 0) - 1)", "holder_count"),
    ]
    for feature_name, field_key, family, transform, expr, source_col in special_sql:
        expr = expr.replace("LAG(holder_count) OVER (PARTITION BY stock_code ORDER BY report_date)", _quote_ident("holder_count__lag1"))
        add_select(
            field_key=field_key,
            family=family,
            feature_name=feature_name,
            transform=transform,
            value_expr=expr,
            source_expr=_quote_ident(source_col),
            date_col="forecast_announce_date" if family == "forecast_express" else "report_announce_date",
            coverage_group="baseline_required",
        )
        generated.append(feature_name)

    chunk_size = 36
    for start in range(0, len(select_sql), chunk_size):
        chunk = select_sql[start:start + chunk_size]
        conn.execute(
            f"""
            INSERT OR REPLACE INTO fact_tdx_gpcw_auto_feature_quarterly
            (feature_set_id, stock_code, report_date, available_date, field_key,
             feature_name, feature_family, transform, feature_value, source_value,
             coverage_group, built_at)
            {' UNION ALL '.join(chunk)}
            """
        )

    conn.execute(
        """
        DELETE FROM fact_tdx_gpcw_auto_feature_quarterly
        WHERE feature_set_id = ?
          AND feature_name IN (
              SELECT feature_name
              FROM fact_tdx_gpcw_auto_feature_quarterly
              WHERE feature_set_id = ?
              GROUP BY feature_name
              HAVING COUNT(feature_value) = 0
                 OR COUNT(DISTINCT feature_value) <= 1
          )
        """,
        (feature_set_id, feature_set_id),
    )
    conn.commit()
    summary = conn.execute(
        """
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT feature_name) AS features,
               COUNT(DISTINCT stock_code) AS stocks,
               COUNT(DISTINCT report_date) AS quarters,
               MIN(available_date) AS min_available,
               MAX(available_date) AS max_available
        FROM fact_tdx_gpcw_auto_feature_quarterly
        WHERE feature_set_id = ?
        """,
        (feature_set_id,),
    ).fetchone()
    return {
        "feature_set_id": feature_set_id,
        "profile_run_id": profile_run_id,
        "semantic_rows": semantic["semantic_rows"],
        "generated_feature_attempts": len(set(generated)),
        "rows": summary["rows"],
        "features": summary["features"],
        "stocks": summary["stocks"],
        "quarters": summary["quarters"],
        "min_available": summary["min_available"],
        "max_available": summary["max_available"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=AUTO_FEATURE_SET_ID)
    parser.add_argument("--profile-run-id", default=None)
    parser.add_argument("--max-base-fields", type=int, default=60)
    args = parser.parse_args()
    conn = get_conn()
    try:
        result = build_tdx_gpcw_auto_features(
            conn,
            feature_set_id=args.feature_set_id,
            profile_run_id=args.profile_run_id,
            max_base_fields=args.max_base_fields,
        )
        logger.info("tdx gpcw auto features: %s", result)
        return 0 if result["features"] >= 150 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
