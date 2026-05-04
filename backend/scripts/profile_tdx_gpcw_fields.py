#!/usr/bin/env python3
"""Profile raw TDX gpcw wide payload fields for candidate screening."""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402

logger = logging.getLogger("tdx_gpcw_field_profile")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_field_profile (
    profile_run_id TEXT NOT NULL,
    field_key TEXT NOT NULL,
    zh_name TEXT,
    field_family TEXT,
    non_null_rows INTEGER,
    coverage_pct DOUBLE,
    stock_coverage DOUBLE,
    quarter_coverage DOUBLE,
    mean_value DOUBLE,
    std_value DOUBLE,
    min_value DOUBLE,
    max_value DOUBLE,
    p01 DOUBLE,
    p50 DOUBLE,
    p99 DOUBLE,
    zero_rate_pct DOUBLE,
    is_constant BOOLEAN,
    model_candidate BOOLEAN,
    rejection_reason TEXT,
    profiled_at TEXT,
    PRIMARY KEY (profile_run_id, field_key)
);
CREATE INDEX IF NOT EXISTS idx_tdx_gpcw_field_profile_candidate
    ON mart_tdx_gpcw_field_profile(profile_run_id, model_candidate);
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
        return
    for stmt in DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


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


def _load_field_meta(conn: Any) -> dict[str, dict[str, Any]]:
    try:
        rows = _rows_as_dicts(
            conn.execute(
                """
                SELECT field_key, zh_name, db_column, field_family, model_candidate
                FROM dim_tdx_gpcw_field
                """
            )
        )
    except Exception:
        return {}
    meta: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = {
            "field_key": row.get("field_key") or row.get("zh_name"),
            "zh_name": row.get("zh_name"),
            "field_family": row.get("field_family") or "other",
            "dict_model_candidate": bool(row.get("model_candidate")),
        }
        if row.get("zh_name"):
            meta[str(row["zh_name"])] = entry
        if row.get("db_column"):
            meta[str(row["db_column"])] = entry
    return meta


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _percent(numerator: int, denominator: int) -> float:
    return float(numerator / denominator * 100.0) if denominator else 0.0


def _quantile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    weight = pos - lo
    return float(sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight)


def _candidate_decision(
    *,
    field_name: str,
    field_family: str,
    coverage_pct: float,
    is_constant: bool,
    zero_rate_pct: float,
    numeric_count: int,
    non_null_rows: int,
    dict_model_candidate: bool,
    min_coverage: float,
) -> tuple[bool, str | None]:
    if field_name.startswith("col"):
        return False, "unnamed_col"
    if non_null_rows == 0:
        return False, "empty"
    if numeric_count == 0:
        return False, "non_numeric"
    if is_constant:
        return False, "constant"
    if zero_rate_pct >= 99.9:
        return False, "all_zero"
    if coverage_pct < min_coverage and not dict_model_candidate:
        return False, "low_coverage"
    if field_family == "other" and not dict_model_candidate:
        return False, "unprioritized_family"
    return True, None


def profile_tdx_gpcw_fields(
    conn: Any,
    *,
    profile_run_id: str | None = None,
    min_coverage: float = 30.0,
) -> dict[str, Any]:
    ensure_tables(conn)
    profile_run_id = profile_run_id or f"tdx_gpcw_profile_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    profiled_at = datetime.utcnow().isoformat(timespec="seconds")

    rows = _rows_as_dicts(
        conn.execute(
            """
            SELECT stock_code, report_date, field_values_json
            FROM raw_tdx_gpcw_wide
            """
        )
    )
    total_rows = len(rows)
    all_stocks = {str(r.get("stock_code")) for r in rows if r.get("stock_code")}
    all_quarters = {str(r.get("report_date")) for r in rows if r.get("report_date")}
    meta_by_payload_key = _load_field_meta(conn)

    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "non_null_rows": 0,
            "stocks": set(),
            "quarters": set(),
            "values": [],
            "zero_count": 0,
            "text_non_numeric": 0,
            "payload_keys": set(),
            "meta": None,
        }
    )

    for row in rows:
        stock = str(row.get("stock_code") or "")
        quarter = str(row.get("report_date") or "")
        try:
            payload = json.loads(row.get("field_values_json") or "{}")
        except json.JSONDecodeError:
            continue
        for raw_key, value in payload.items():
            if value is None:
                continue
            key = str(raw_key)
            meta = meta_by_payload_key.get(
                key,
                {
                    "field_key": key,
                    "zh_name": key,
                    "field_family": "other",
                    "dict_model_candidate": False,
                },
            )
            canonical_key = str(meta.get("field_key") or key)
            item = stats[canonical_key]
            item["payload_keys"].add(key)
            if item["meta"] is None:
                item["meta"] = meta
            item["non_null_rows"] += 1
            if stock:
                item["stocks"].add(stock)
            if quarter:
                item["quarters"].add(quarter)
            numeric = _numeric(value)
            if numeric is None:
                item["text_non_numeric"] += 1
                continue
            item["values"].append(numeric)
            if numeric == 0:
                item["zero_count"] += 1

    out_rows = []
    selected = []
    rejected = []
    for field_key, item in stats.items():
        meta = item["meta"] or {
            "field_key": field_key,
            "zh_name": field_key,
            "field_family": "other",
            "dict_model_candidate": False,
        }
        values = sorted(item["values"])
        non_null_rows = int(item["non_null_rows"])
        numeric_count = len(values)
        coverage_pct = _percent(non_null_rows, total_rows)
        zero_rate_pct = _percent(int(item["zero_count"]), non_null_rows)
        is_constant = bool(values and values[0] == values[-1])
        mean_value = None
        std_value = None
        if values:
            mean_value = float(sum(values) / len(values))
            if len(values) > 1:
                variance = sum((v - mean_value) ** 2 for v in values) / (len(values) - 1)
                std_value = float(math.sqrt(variance))
            else:
                std_value = 0.0
        model_candidate, rejection_reason = _candidate_decision(
            field_name=str(meta.get("zh_name") or field_key),
            field_family=str(meta.get("field_family") or "other"),
            coverage_pct=coverage_pct,
            is_constant=is_constant,
            zero_rate_pct=zero_rate_pct,
            numeric_count=numeric_count,
            non_null_rows=non_null_rows,
            dict_model_candidate=bool(meta.get("dict_model_candidate")),
            min_coverage=min_coverage,
        )
        field_key = str(meta.get("field_key") or field_key)
        row_tuple = (
            profile_run_id,
            field_key,
            meta.get("zh_name") or field_key,
            meta.get("field_family") or "other",
            non_null_rows,
            coverage_pct,
            _percent(len(item["stocks"]), len(all_stocks)),
            _percent(len(item["quarters"]), len(all_quarters)),
            mean_value,
            std_value,
            float(values[0]) if values else None,
            float(values[-1]) if values else None,
            _quantile(values, 0.01),
            _quantile(values, 0.50),
            _quantile(values, 0.99),
            zero_rate_pct,
            is_constant,
            model_candidate,
            rejection_reason,
            profiled_at,
        )
        out_rows.append(row_tuple)
        (selected if model_candidate else rejected).append(field_key)

    if out_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_field_profile
            (profile_run_id, field_key, zh_name, field_family, non_null_rows,
             coverage_pct, stock_coverage, quarter_coverage, mean_value,
             std_value, min_value, max_value, p01, p50, p99, zero_rate_pct,
             is_constant, model_candidate, rejection_reason, profiled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            out_rows,
        )
    conn.commit()
    return {
        "profile_run_id": profile_run_id,
        "profiled_at": profiled_at,
        "total_rows": total_rows,
        "field_count": len(out_rows),
        "model_candidate_count": len(selected),
        "rejected_count": len(rejected),
        "selected_fields": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-run-id", default=None)
    parser.add_argument("--min-coverage", type=float, default=30.0)
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = profile_tdx_gpcw_fields(
            conn,
            profile_run_id=args.profile_run_id,
            min_coverage=args.min_coverage,
        )
        logger.info("gpcw field profile: %s", result)
        return 0 if result["field_count"] > 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
