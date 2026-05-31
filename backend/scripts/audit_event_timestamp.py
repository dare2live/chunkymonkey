#!/usr/bin/env python3
"""P-1.4 event timestamp audit — PLAN_V3 v3.2 P-1 fourth gate.

Verifies event-table timestamps are not back-filled / fake. In a real-money
selector, an event at time t can only inform the t-decision if its timestamp
truly was observable at t. If `notice_date` is NULL or fictionally far before
the true announcement, training labels and PIT joins silently leak.

Sections:
1. Event tables enumeration (sanity: all listed tables exist)
2. Timestamp column non-null rate per (table, ts_col) — gate is ≥99.5%
3. PIT lag distribution: ts_col_a - ts_col_b (announcement vs effective date)
4. Sanity check: recent-30-day events with reasonable notice_date <= today

Exit 0 = PASS, 1 = FAIL.
PLAN_V3 §2 P-1 Go: event-timestamp non-null ≥99.5% on critical tables.

Usage:
    PYTHONPATH=backend python backend/scripts/audit_event_timestamp.py
    PYTHONPATH=backend python backend/scripts/audit_event_timestamp.py --json-out /tmp/event_ts_audit.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("audit_event_ts")

# ──────────────────────────────────────────────────────────────────────────────
# Event-table catalog
#   - primary_ts: column that MUST be non-null (the "when was this knowable" timestamp)
#   - secondary_ts: optional second timestamp for PIT-lag computation (notice vs effective)
#   - critical: True ⇒ non-null < 99.5% is FAIL; False ⇒ WARN
# Inferred from DESCRIBE on smartmoney.duckdb (chunkymonkey 2026-05-14).
# ──────────────────────────────────────────────────────────────────────────────
EVENT_TABLES: list[dict] = [
    {
        "table": "fact_institution_event",
        "primary_ts": "notice_date",
        "secondary_ts": "report_date",
        "critical": True,
    },
    {
        "table": "fact_lhb_event",
        "primary_ts": "trade_date",
        "secondary_ts": "built_at",
        "critical": True,
    },
    {
        "table": "raw_lhb_daily",
        "primary_ts": "trade_date",
        "secondary_ts": "ingested_at",
        "critical": True,
    },
    {
        "table": "raw_institution_surveys",
        "primary_ts": "notice_date",
        "secondary_ts": "survey_date",
        "critical": True,
    },
    {
        "table": "fact_holder_event",
        "primary_ts": "report_date",
        "secondary_ts": "created_at",
        "critical": False,
    },
    {
        "table": "fact_shareholder_plan",
        "primary_ts": "announce_date",
        "secondary_ts": "start_date",
        "critical": True,
    },
    {
        "table": "fact_shareholder_trade",
        "primary_ts": "change_date",
        "secondary_ts": "fetched_at",
        "critical": False,
    },
    {
        "table": "fact_jgdy_event",
        "primary_ts": "notice_date",
        "secondary_ts": "built_at",
        "critical": True,
    },
    {
        "table": "fact_dzjy_event",
        "primary_ts": "trade_date",
        "secondary_ts": "built_at",
        "critical": False,
    },
    {
        "table": "fact_executive_trade_event",
        "primary_ts": "notice_date",
        "secondary_ts": "built_at",
        "critical": True,
    },
]

NON_NULL_THRESHOLD = 0.995  # PLAN_V3 §2 P-1 gate


@dataclass
class CheckResult:
    section: str
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str
    rows: int = 0
    extras: dict = field(default_factory=dict)


def _norm_date_sql(col: str) -> str:
    """Return a SQL expression that parses YYYYMMDD or YYYY-MM-DD VARCHAR (or
    TIMESTAMP) into DATE.

    Tables store dates inconsistently (e.g. fact_institution_event.notice_date has
    '20260506' rows mixed with '2026-04-02' rows in report_date). TRY_CAST alone
    drops the YYYYMMDD form. We first stringify (covers TIMESTAMP columns), then
    replace dashes and slice into YYYY-MM-DD form before TRY_CAST.
    """
    s = f"CAST({col} AS VARCHAR)"
    return (
        f"TRY_CAST("
        f"  CASE WHEN length(REPLACE({s}, '-', '')) = 8 "
        f"       THEN substr(REPLACE({s}, '-', ''),1,4)||'-'||substr(REPLACE({s}, '-', ''),5,2)||'-'||substr(REPLACE({s}, '-', ''),7,2) "
        f"       ELSE {s} END "
        f"AS DATE)"
    )


def _event_table_names() -> list[str]:
    return [spec["table"] for spec in EVENT_TABLES]


def _existing_tables(conn) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }


def _load_columns_by_table(conn, table_names: list[str]) -> dict[str, set[str]]:
    names = sorted(set(table_names))
    if not names:
        return {}
    placeholders = ", ".join(["?"] * len(names))
    rows = conn.execute(
        f"""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'main'
          AND table_name IN ({placeholders})
        """,
        names,
    ).fetchall()
    columns_by_table: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        columns_by_table.setdefault(table_name, set()).add(column_name)
    return columns_by_table


def _event_inventory(conn) -> tuple[set[str], dict[str, set[str]]]:
    table_names = _event_table_names()
    return _existing_tables(conn), _load_columns_by_table(conn, table_names)


def _fetch_union_metrics(conn, metric_parts: list[str]) -> dict[str, dict]:
    if not metric_parts:
        return {}
    cursor = conn.execute(" UNION ALL ".join(metric_parts))
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    return {
        row[0]: {columns[i]: row[i] for i in range(1, len(columns))}
        for row in rows
    }


def _timestamp_metric_select(spec: dict, cols: set[str]) -> str:
    table = spec["table"]
    select_parts = [f"'{table}' AS table_name", "COUNT(*) AS n_total"]
    primary = spec["primary_ts"]
    secondary = spec["secondary_ts"]
    if primary and primary in cols:
        select_parts.append(
            f"COUNT(*) FILTER (WHERE {primary} IS NULL OR CAST({primary} AS VARCHAR) = '') AS primary_null"
        )
    if secondary and secondary in cols:
        select_parts.append(
            f"COUNT(*) FILTER (WHERE {secondary} IS NULL OR CAST({secondary} AS VARCHAR) = '') AS secondary_null"
        )
    return f"SELECT {', '.join(select_parts)} FROM {table}"


def _append_timestamp_rate_result(
    out: list[CheckResult],
    spec: dict,
    kind: str,
    col: str | None,
    table_metrics: dict[str, int],
    columns: set[str],
) -> None:
    if not col:
        return
    table = spec["table"]
    if col not in columns:
        out.append(CheckResult(
            section="2. Timestamp non-null rate",
            name=f"{table}.{col}",
            status="WARN",
            detail=f"{table}.{col}: check failed: column not found",
        ))
        return
    n_total = int(table_metrics.get("n_total", 0) or 0)
    n_null = int(table_metrics.get(f"{kind}_null", 0) or 0)
    non_null_rate = 1.0 - (n_null / n_total)
    row_label = f"{table}.{col}[{kind}]"
    if kind == "primary":
        if non_null_rate >= NON_NULL_THRESHOLD:
            status = "PASS"
        elif spec["critical"]:
            status = "FAIL"
        else:
            status = "WARN"
        detail = f"{row_label}: non-null {non_null_rate*100:.3f}% ({n_total-n_null}/{n_total}); threshold {NON_NULL_THRESHOLD*100:.1f}%"
    else:
        status = "PASS" if non_null_rate >= 0.50 else "WARN"
        detail = f"{row_label}: non-null {non_null_rate*100:.3f}% (descriptive only)"
    out.append(CheckResult(
        section="2. Timestamp non-null rate",
        name=row_label,
        status=status,
        detail=detail,
        rows=n_total,
        extras={
            "non_null_rate": round(non_null_rate, 6),
            "n_null": n_null,
            "critical": spec["critical"],
            "kind": kind,
        },
    ))


def _missing_columns(spec: dict, columns_by_table: dict[str, set[str]]) -> list[str]:
    cols = columns_by_table.get(spec["table"], set())
    return [col for col in (spec["primary_ts"], spec["secondary_ts"]) if col and col not in cols]


def _lag_metric_select(spec: dict) -> str:
    table = spec["table"]
    primary = spec["primary_ts"]
    secondary = spec["secondary_ts"]
    p_sql = _norm_date_sql(primary)
    s_sql = _norm_date_sql(secondary)
    return f"""
        SELECT
          '{table}' AS table_name,
          COUNT(*) AS n_total,
          COUNT(({p_sql}) - ({s_sql})) AS n_with_lag,
          MIN(({p_sql}) - ({s_sql})) AS lag_min,
          MAX(({p_sql}) - ({s_sql})) AS lag_max,
          AVG(({p_sql}) - ({s_sql})) AS lag_mean,
          MEDIAN(({p_sql}) - ({s_sql})) AS lag_median,
          QUANTILE_CONT(({p_sql}) - ({s_sql}), 0.95) AS lag_p95
        FROM {table}
        WHERE {primary} IS NOT NULL AND {secondary} IS NOT NULL
    """


def _append_lag_result(out: list[CheckResult], spec: dict, metric: dict) -> None:
    table = spec["table"]
    primary = spec["primary_ts"]
    secondary = spec["secondary_ts"]
    n_total = int(metric.get("n_total") or 0)
    n_lag = int(metric.get("n_with_lag") or 0)
    lag_min = metric.get("lag_min")
    lag_max = metric.get("lag_max")
    lag_mean = metric.get("lag_mean")
    lag_median = metric.get("lag_median")
    lag_p95 = metric.get("lag_p95")
    if n_lag == 0:
        out.append(CheckResult(
            section="3. PIT lag distribution",
            name=f"{table}({primary}-{secondary})",
            status="WARN",
            detail=f"{table}: no rows where both {primary} and {secondary} parsable as DATE",
            rows=n_total,
        ))
        return
    unusual = table not in {"fact_shareholder_plan"} and lag_min is not None and lag_min < -365
    status = "WARN" if unusual else "PASS"
    extra_note = " (unusual negative tail: primary earlier than secondary by >1y)" if unusual else ""
    out.append(CheckResult(
        section="3. PIT lag distribution",
        name=f"{table}({primary}-{secondary})",
        status=status,
        detail=(
            f"{table}: n={n_lag} lag(days) min={lag_min} median={lag_median} "
            f"mean={lag_mean:.2f} p95={lag_p95} max={lag_max}{extra_note}"
            if lag_mean is not None else
            f"{table}: n={n_lag} lag(days) min={lag_min} median={lag_median} max={lag_max}{extra_note}"
        ),
        rows=n_lag,
        extras={
            "lag_min": str(lag_min),
            "lag_max": str(lag_max),
            "lag_median": str(lag_median),
            "lag_p95": str(lag_p95),
            "lag_mean": float(lag_mean) if lag_mean is not None else None,
        },
    ))


def _recent_metric_select(spec: dict) -> str:
    table = spec["table"]
    primary = spec["primary_ts"]
    p_sql = _norm_date_sql(primary)
    return f"""
        SELECT
          '{table}' AS table_name,
          COUNT(*) FILTER (WHERE ({p_sql}) > CURRENT_DATE) AS n_future,
          COUNT(*) FILTER (WHERE ({p_sql}) BETWEEN CURRENT_DATE - INTERVAL 30 DAY AND CURRENT_DATE) AS n_recent,
          MAX({p_sql}) AS max_ts,
          MIN({p_sql}) AS min_ts
        FROM {table}
        WHERE {primary} IS NOT NULL
    """


def _append_recent_results(out: list[CheckResult], spec: dict, metric: dict, today) -> None:
    table = spec["table"]
    primary = spec["primary_ts"]
    n_future = int(metric.get("n_future") or 0)
    n_recent = int(metric.get("n_recent") or 0)
    max_ts = metric.get("max_ts")
    min_ts = metric.get("min_ts")
    if n_future > 0:
        status = "FAIL" if spec["critical"] else "WARN"
        out.append(CheckResult(
            section="4. Recent-30d sanity",
            name=f"{table}.{primary}.future",
            status=status,
            detail=f"{table}: {n_future} rows with {primary} > {today} (look-ahead leak risk)",
            rows=n_future,
            extras={"max_ts": str(max_ts), "today": str(today)},
        ))
    else:
        out.append(CheckResult(
            section="4. Recent-30d sanity",
            name=f"{table}.{primary}.future",
            status="PASS",
            detail=f"{table}: 0 rows with {primary} > {today} (max_ts={max_ts})",
        ))
    recent_status = "PASS" if n_recent > 0 else "WARN"
    out.append(CheckResult(
        section="4. Recent-30d sanity",
        name=f"{table}.{primary}.recent_30d",
        status=recent_status,
        detail=f"{table}: {n_recent} rows in last 30 days (max_ts={max_ts}, min_ts={min_ts})",
        rows=n_recent,
        extras={"max_ts": str(max_ts)},
    ))


def check_event_table_enumeration(conn) -> list[CheckResult]:
    """Section 1: every catalog entry must exist with primary_ts column."""
    out: list[CheckResult] = []
    existing, columns_by_table = _event_inventory(conn)
    count_by_table: dict[str, int] = {}
    present_tables = [table for table in _event_table_names() if table in existing]
    if present_tables:
        count_sql = " UNION ALL ".join(
            f"SELECT '{table}' AS table_name, COUNT(*) AS n FROM {table}"
            for table in present_tables
        )
        count_by_table = {table: int(n) for table, n in conn.execute(count_sql).fetchall()}
    for spec in EVENT_TABLES:
        t = spec["table"]
        if t not in existing:
            out.append(CheckResult(
                section="1. Event tables enumeration",
                name=t,
                status="FAIL" if spec["critical"] else "WARN",
                detail=f"{t}: table NOT FOUND in smartmoney.duckdb",
                extras={"critical": spec["critical"]},
            ))
            continue
        cols = columns_by_table.get(t, set())
        missing = [c for c in (spec["primary_ts"], spec["secondary_ts"]) if c and c not in cols]
        if missing:
            out.append(CheckResult(
                section="1. Event tables enumeration",
                name=t,
                status="FAIL" if spec["critical"] else "WARN",
                detail=f"{t}: missing column(s) {missing}",
                extras={"critical": spec["critical"], "missing": missing},
            ))
            continue
        n = count_by_table.get(t, 0)
        out.append(CheckResult(
            section="1. Event tables enumeration",
            name=t,
            status="PASS",
            detail=f"{t}: present, primary_ts={spec['primary_ts']}, secondary_ts={spec['secondary_ts']}, {n} rows",
            rows=n,
            extras={"critical": spec["critical"]},
        ))
    return out


def check_timestamp_non_null_rate(conn) -> list[CheckResult]:
    """Section 2: every event table's primary_ts must be ≥99.5% non-null.

    Secondary_ts only WARN, since it's used for PIT-lag descriptive stats.
    """
    out: list[CheckResult] = []
    existing, columns_by_table = _event_inventory(conn)

    metric_parts: list[str] = []
    for spec in EVENT_TABLES:
        t = spec["table"]
        cols = columns_by_table.get(t, set())
        if t not in existing:
            continue
        metric_parts.append(_timestamp_metric_select(spec, cols))
    metrics = {
        table: {key: int(value or 0) for key, value in values.items()}
        for table, values in _fetch_union_metrics(conn, metric_parts).items()
    }

    for spec in EVENT_TABLES:
        t = spec["table"]
        if t not in existing:
            out.append(CheckResult(
                section="2. Timestamp non-null rate",
                name=t,
                status="WARN",
                detail=f"{t}: cannot read row count: table not found",
            ))
            continue
        table_metrics = metrics.get(t, {})
        n_total = table_metrics.get("n_total", 0)
        if n_total == 0:
            out.append(CheckResult(
                section="2. Timestamp non-null rate",
                name=t,
                status="WARN",
                detail=f"{t}: 0 rows (empty table)",
            ))
            continue
        cols = columns_by_table.get(t, set())
        _append_timestamp_rate_result(out, spec, "primary", spec["primary_ts"], table_metrics, cols)
        _append_timestamp_rate_result(out, spec, "secondary", spec["secondary_ts"], table_metrics, cols)
    return out


def check_pit_lag_distribution(conn) -> list[CheckResult]:
    """Section 3: PIT lag = primary_ts - secondary_ts.

    Reports min/median/max/p95 lag in days. Negative lag (notice BEFORE effective
    date) is structurally OK for plans (announce date < start date). Strongly
    negative lag on lhb/institution (notice WAY before trade) ⇒ back-fill smell.
    """
    out: list[CheckResult] = []
    existing, columns_by_table = _event_inventory(conn)
    metric_parts: list[str] = []
    for spec in EVENT_TABLES:
        t = spec["table"]
        p, s = spec["primary_ts"], spec["secondary_ts"]
        if not p or not s:
            continue
        if t in existing and not _missing_columns(spec, columns_by_table):
            metric_parts.append(_lag_metric_select(spec))
    metrics = _fetch_union_metrics(conn, metric_parts)
    for spec in EVENT_TABLES:
        t = spec["table"]
        p, s = spec["primary_ts"], spec["secondary_ts"]
        if not p or not s:
            continue
        missing = _missing_columns(spec, columns_by_table)
        if t not in existing:
            out.append(CheckResult(
                section="3. PIT lag distribution",
                name=f"{t}({p}-{s})",
                status="WARN",
                detail=f"{t}: lag computation failed: table not found",
            ))
            continue
        if missing:
            out.append(CheckResult(
                section="3. PIT lag distribution",
                name=f"{t}({p}-{s})",
                status="WARN",
                detail=f"{t}: lag computation failed: missing column(s) {missing}",
            ))
            continue
        _append_lag_result(out, spec, metrics.get(t, {}))
    return out


def check_recent_30d_sanity(conn) -> list[CheckResult]:
    """Section 4: events with primary_ts within last 30 days vs today.

    - primary_ts <= today (no future-dated leak)
    - primary_ts within [today-30, today] count > 0 for non-empty critical tables
      (else watermark stale — WARN only, since chunkymonkey sync can lag)
    """
    out: list[CheckResult] = []
    today = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
    existing, columns_by_table = _event_inventory(conn)
    metric_parts: list[str] = []
    for spec in EVENT_TABLES:
        t = spec["table"]
        p = spec["primary_ts"]
        if not p:
            continue
        if t in existing and p in columns_by_table.get(t, set()):
            metric_parts.append(_recent_metric_select(spec))
    metrics = _fetch_union_metrics(conn, metric_parts)
    for spec in EVENT_TABLES:
        t = spec["table"]
        p = spec["primary_ts"]
        if not p:
            continue
        if t not in existing:
            out.append(CheckResult(
                section="4. Recent-30d sanity",
                name=f"{t}.{p}",
                status="WARN",
                detail=f"{t}: sanity check failed: table not found",
            ))
            continue
        if p not in columns_by_table.get(t, set()):
            out.append(CheckResult(
                section="4. Recent-30d sanity",
                name=f"{t}.{p}",
                status="WARN",
                detail=f"{t}: sanity check failed: missing column {p}",
            ))
            continue
        _append_recent_results(out, spec, metrics.get(t, {}), today)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="P-1.4 event timestamp audit")
    parser.add_argument("--json-out", type=Path, default=None, help="Write full JSON report to path")
    args = parser.parse_args()

    log.info("=== P-1.4 Event Timestamp Audit (PLAN_V3 v3.2) ===")
    # Rule 11 §11.4: read_only=True for concurrent safety with other P-1.* audits.
    conn = duck_connect(str(DB_PATH), read_only=True)
    try:
        results: list[CheckResult] = []
        results.extend(check_event_table_enumeration(conn))
        results.extend(check_timestamp_non_null_rate(conn))
        results.extend(check_pit_lag_distribution(conn))
        results.extend(check_recent_30d_sanity(conn))
    finally:
        conn.close()

    by_status = Counter(r.status for r in results)
    log.info("")
    log.info("=== Results ===")
    for r in results:
        log.info(f"  [{r.status:4s}] {r.section} :: {r.name} — {r.detail}")
    log.info("")
    log.info(f"SUMMARY: PASS={by_status['PASS']} WARN={by_status['WARN']} FAIL={by_status['FAIL']}")

    if args.json_out:
        payload = {
            "audit": "P-1.4 event timestamp",
            "summary": dict(by_status),
            "threshold_non_null": NON_NULL_THRESHOLD,
            "results": [asdict(r) for r in results],
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"JSON report → {args.json_out}")

    if by_status["FAIL"] > 0:
        log.error(f"P-1.4 FAIL: {by_status['FAIL']} hard violations — PLAN_V3 §6 串行 gate blocks P0")
        return 1
    log.info("P-1.4 PASS — event timestamp integrity OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
