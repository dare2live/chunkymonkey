#!/usr/bin/env python3
"""PIT/ASOF audit for TDX gpcw auto quarterly features."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from scripts.build_tdx_gpcw_auto_features import AUTO_FEATURE_SET_ID  # noqa: E402

logger = logging.getLogger("tdx_gpcw_auto_pit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_pit_audit (
    audit_run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    checked_rows INTEGER,
    violation_rows INTEGER,
    status TEXT NOT NULL,
    notes TEXT,
    built_at TEXT,
    PRIMARY KEY (audit_run_id, feature_set_id, feature_name)
);
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def validate_tdx_gpcw_auto_pit(
    conn: Any,
    *,
    feature_set_id: str = AUTO_FEATURE_SET_ID,
    audit_run_id: str = "pit_gpcw_auto",
) -> dict[str, Any]:
    ensure_tables(conn)
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT feature_name,
               COUNT(*) AS checked_rows,
               SUM(CASE
                   WHEN available_date IS NULL THEN 1
                   WHEN report_date IS NULL THEN 1
                   WHEN available_date < report_date THEN 1
                   ELSE 0
               END) AS violation_rows
        FROM fact_tdx_gpcw_auto_feature_quarterly
        WHERE feature_set_id = ?
        GROUP BY feature_name
        ORDER BY feature_name
        """,
        (feature_set_id,),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no auto quarterly features for feature_set_id={feature_set_id}")
    out = []
    for row in rows:
        violations = int(row["violation_rows"] or 0)
        out.append(
            (
                audit_run_id,
                feature_set_id,
                row["feature_name"],
                int(row["checked_rows"] or 0),
                violations,
                "passed" if violations == 0 else "failed",
                "available_date is present and not before report_date",
                built_at,
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_tdx_gpcw_auto_pit_audit
        (audit_run_id, feature_set_id, feature_name, checked_rows,
         violation_rows, status, notes, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        out,
    )
    conn.commit()
    total_violations = sum(row[4] for row in out)
    return {
        "audit_run_id": audit_run_id,
        "feature_set_id": feature_set_id,
        "features": len(out),
        "violation_rows": total_violations,
        "status": "passed" if total_violations == 0 else "failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=AUTO_FEATURE_SET_ID)
    parser.add_argument("--audit-run-id", default="pit_gpcw_auto")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = validate_tdx_gpcw_auto_pit(
            conn,
            feature_set_id=args.feature_set_id,
            audit_run_id=args.audit_run_id,
        )
        logger.info("tdx gpcw auto pit audit: %s", result)
        return 0 if result["violation_rows"] == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
