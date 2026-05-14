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


def check_event_table_enumeration(conn) -> list[CheckResult]:
    """Section 1: every catalog entry must exist with primary_ts column."""
    out: list[CheckResult] = []
    existing = {
        r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }
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
        cols = {c[0] for c in conn.execute(f"DESCRIBE {t}").fetchall()}
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
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
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
    for spec in EVENT_TABLES:
        t = spec["table"]
        try:
            n_total = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception as e:
            out.append(CheckResult(
                section="2. Timestamp non-null rate",
                name=t,
                status="WARN",
                detail=f"{t}: cannot read row count: {e}",
            ))
            continue
        if n_total == 0:
            out.append(CheckResult(
                section="2. Timestamp non-null rate",
                name=t,
                status="WARN",
                detail=f"{t}: 0 rows (empty table)",
            ))
            continue
        for kind, col in (("primary", spec["primary_ts"]), ("secondary", spec["secondary_ts"])):
            if not col:
                continue
            try:
                n_null = conn.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL OR CAST({col} AS VARCHAR) = ''"
                ).fetchone()[0]
            except Exception as e:
                out.append(CheckResult(
                    section="2. Timestamp non-null rate",
                    name=f"{t}.{col}",
                    status="WARN",
                    detail=f"{t}.{col}: check failed: {e}",
                ))
                continue
            non_null_rate = 1.0 - (n_null / n_total)
            row_label = f"{t}.{col}[{kind}]"
            if kind == "primary":
                # Gate: critical tables must meet 99.5%; non-critical → WARN
                if non_null_rate >= NON_NULL_THRESHOLD:
                    status = "PASS"
                elif spec["critical"]:
                    status = "FAIL"
                else:
                    status = "WARN"
                detail = f"{row_label}: non-null {non_null_rate*100:.3f}% ({n_total-n_null}/{n_total}); threshold {NON_NULL_THRESHOLD*100:.1f}%"
            else:
                # Secondary: descriptive only
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
    return out


def check_pit_lag_distribution(conn) -> list[CheckResult]:
    """Section 3: PIT lag = primary_ts - secondary_ts.

    Reports min/median/max/p95 lag in days. Negative lag (notice BEFORE effective
    date) is structurally OK for plans (announce date < start date). Strongly
    negative lag on lhb/institution (notice WAY before trade) ⇒ back-fill smell.
    """
    out: list[CheckResult] = []
    for spec in EVENT_TABLES:
        t = spec["table"]
        p, s = spec["primary_ts"], spec["secondary_ts"]
        if not p or not s:
            continue
        try:
            p_sql = _norm_date_sql(p)
            s_sql = _norm_date_sql(s)
            row = conn.execute(f"""
                SELECT
                  COUNT(*) AS n_total,
                  COUNT(({p_sql}) - ({s_sql})) AS n_with_lag,
                  MIN(({p_sql}) - ({s_sql})) AS lag_min,
                  MAX(({p_sql}) - ({s_sql})) AS lag_max,
                  AVG(({p_sql}) - ({s_sql})) AS lag_mean,
                  MEDIAN(({p_sql}) - ({s_sql})) AS lag_median,
                  QUANTILE_CONT(({p_sql}) - ({s_sql}), 0.95) AS lag_p95
                FROM {t}
                WHERE {p} IS NOT NULL AND {s} IS NOT NULL
            """).fetchone()
            n_total, n_lag, lag_min, lag_max, lag_mean, lag_median, lag_p95 = row
            if n_lag == 0:
                out.append(CheckResult(
                    section="3. PIT lag distribution",
                    name=f"{t}({p}-{s})",
                    status="WARN",
                    detail=f"{t}: no rows where both {p} and {s} parsable as DATE",
                    rows=n_total or 0,
                ))
                continue
            # Sanity: bizarre negative tails on non-plan tables = back-fill smell
            unusual = False
            if t not in {"fact_shareholder_plan"} and lag_min is not None and lag_min < -365:
                unusual = True
            status = "PASS"
            extra_note = ""
            if unusual:
                status = "WARN"
                extra_note = " (unusual negative tail: primary earlier than secondary by >1y)"
            out.append(CheckResult(
                section="3. PIT lag distribution",
                name=f"{t}({p}-{s})",
                status=status,
                detail=(
                    f"{t}: n={n_lag} lag(days) min={lag_min} median={lag_median} "
                    f"mean={lag_mean:.2f} p95={lag_p95} max={lag_max}{extra_note}"
                    if lag_mean is not None else
                    f"{t}: n={n_lag} lag(days) min={lag_min} median={lag_median} max={lag_max}{extra_note}"
                ),
                rows=n_lag,
                extras={
                    "lag_min": str(lag_min), "lag_max": str(lag_max),
                    "lag_median": str(lag_median), "lag_p95": str(lag_p95),
                    "lag_mean": float(lag_mean) if lag_mean is not None else None,
                },
            ))
        except Exception as e:
            out.append(CheckResult(
                section="3. PIT lag distribution",
                name=f"{t}({p}-{s})",
                status="WARN",
                detail=f"{t}: lag computation failed: {e}",
            ))
    return out


def check_recent_30d_sanity(conn) -> list[CheckResult]:
    """Section 4: events with primary_ts within last 30 days vs today.

    - primary_ts <= today (no future-dated leak)
    - primary_ts within [today-30, today] count > 0 for non-empty critical tables
      (else watermark stale — WARN only, since chunkymonkey sync can lag)
    """
    out: list[CheckResult] = []
    today = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
    for spec in EVENT_TABLES:
        t = spec["table"]
        p = spec["primary_ts"]
        if not p:
            continue
        try:
            p_sql = _norm_date_sql(p)
            row = conn.execute(f"""
                SELECT
                  COUNT(*) FILTER (WHERE ({p_sql}) > CURRENT_DATE)              AS n_future,
                  COUNT(*) FILTER (WHERE ({p_sql}) BETWEEN CURRENT_DATE - INTERVAL 30 DAY AND CURRENT_DATE) AS n_recent,
                  MAX({p_sql})                                                  AS max_ts,
                  MIN({p_sql})                                                  AS min_ts
                FROM {t}
                WHERE {p} IS NOT NULL
            """).fetchone()
            n_future, n_recent, max_ts, min_ts = row
            # 4a: future-dated events = FAIL on critical, WARN elsewhere
            if n_future > 0:
                status = "FAIL" if spec["critical"] else "WARN"
                out.append(CheckResult(
                    section="4. Recent-30d sanity",
                    name=f"{t}.{p}.future",
                    status=status,
                    detail=f"{t}: {n_future} rows with {p} > {today} (look-ahead leak risk)",
                    rows=int(n_future),
                    extras={"max_ts": str(max_ts), "today": str(today)},
                ))
            else:
                out.append(CheckResult(
                    section="4. Recent-30d sanity",
                    name=f"{t}.{p}.future",
                    status="PASS",
                    detail=f"{t}: 0 rows with {p} > {today} (max_ts={max_ts})",
                ))
            # 4b: recent-30d presence = WARN if stale (informational)
            recent_status = "PASS" if n_recent and n_recent > 0 else "WARN"
            out.append(CheckResult(
                section="4. Recent-30d sanity",
                name=f"{t}.{p}.recent_30d",
                status=recent_status,
                detail=f"{t}: {n_recent} rows in last 30 days (max_ts={max_ts}, min_ts={min_ts})",
                rows=int(n_recent or 0),
                extras={"max_ts": str(max_ts)},
            ))
        except Exception as e:
            out.append(CheckResult(
                section="4. Recent-30d sanity",
                name=f"{t}.{p}",
                status="WARN",
                detail=f"{t}: sanity check failed: {e}",
            ))
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
