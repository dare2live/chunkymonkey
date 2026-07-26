"""Residual hygiene SLA — FOUNDATION F9 single compute point.

Measures Type-B raw→fact publish lag and declared ann-axis tip lag vs eligible_end.
Thresholds live in ``backend/config/residual_hygiene.yaml``. Continuity WARN /
UNTRUSTED cosmetics are out of scope.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO / "backend" / "config" / "residual_hygiene.yaml"


def _compact(value: Any) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def trading_lag_days(
    trading_days: list[str],
    from_day: str | None,
    to_day: str | None,
) -> int | None:
    """Count trading days strictly after ``from_day`` up to and including ``to_day``."""
    older = _compact(from_day)
    newer = _compact(to_day)
    if not older or not newer:
        return None
    if newer <= older:
        return 0
    return sum(1 for d in trading_days if older < d <= newer)


def classify_lag(
    lag: int | None,
    *,
    warn_trading_days: int,
    fail_trading_days: int,
) -> str:
    if lag is None:
        return "skip"
    if lag > int(fail_trading_days):
        return "fail"
    if lag > int(warn_trading_days):
        return "warn"
    return "pass"


def load_policy(path: Path | None = None) -> dict[str, Any]:
    cfg = path or DEFAULT_POLICY_PATH
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    if int(raw.get("version") or 0) != 1:
        raise ValueError(f"residual_hygiene.yaml unsupported version: {raw.get('version')!r}")
    return raw


def overall_status(findings: list[dict[str, Any]]) -> str:
    if any(f.get("status") == "fail" for f in findings):
        return "FAIL"
    if any(f.get("status") == "warn" for f in findings):
        return "WARN"
    return "PASS"


def _table_present(conn: Any, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [name],
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _max_date(conn: Any, table: str, date_column: str) -> str | None:
    if not _table_present(conn, table):
        return None
    try:
        row = conn.execute(
            f"""
            SELECT MAX(replace(CAST({date_column} AS VARCHAR), '-', ''))
              FROM {table}
             WHERE {date_column} IS NOT NULL
            """
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _compact(row[0] if row else None)


def measure_type_b_publish_lags(
    *,
    policy: dict[str, Any],
    trading_days: list[str],
    raw_conn: Any,
    fact_conn: Any,
) -> list[dict[str, Any]]:
    block = policy.get("type_b_publish_lag") or {}
    if not block.get("enabled", True):
        return []
    warn_n = int(block.get("warn_trading_days", 1))
    fail_n = int(block.get("fail_trading_days", 2))
    from services.type_b_fact_publish_catchup import type_b_publish_specs

    findings: list[dict[str, Any]] = []
    for spec in type_b_publish_specs():
        raw_max = _max_date(raw_conn, spec.raw_table, "trade_date")
        fact_max = _max_date(fact_conn, spec.fact_table, "trade_date")
        if raw_max is None:
            findings.append(
                {
                    "check": "type_b_publish_lag",
                    "domain": spec.domain,
                    "status": "skip",
                    "detail": "raw_empty_or_missing",
                    "raw_max": None,
                    "fact_max": fact_max,
                    "lag_trading_days": None,
                }
            )
            continue
        if fact_max is None:
            findings.append(
                {
                    "check": "type_b_publish_lag",
                    "domain": spec.domain,
                    "status": "fail",
                    "detail": "fact_empty_while_raw_present",
                    "raw_max": raw_max,
                    "fact_max": None,
                    "lag_trading_days": None,
                    "warn_trading_days": warn_n,
                    "fail_trading_days": fail_n,
                }
            )
            continue
        lag = trading_lag_days(trading_days, fact_max, raw_max)
        status = classify_lag(lag, warn_trading_days=warn_n, fail_trading_days=fail_n)
        findings.append(
            {
                "check": "type_b_publish_lag",
                "domain": spec.domain,
                "status": status,
                "detail": "raw_ahead_of_fact" if (lag or 0) > 0 else "fact_caught_up",
                "raw_max": raw_max,
                "fact_max": fact_max,
                "lag_trading_days": lag,
                "warn_trading_days": warn_n,
                "fail_trading_days": fail_n,
            }
        )
    return findings


def measure_ann_tip_lags(
    *,
    policy: dict[str, Any],
    trading_days: list[str],
    conn_for_alias: Any,
    registry: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    block = policy.get("ann_tip_lag") or {}
    if not block.get("enabled", True):
        return []
    warn_n = int(block.get("warn_trading_days", 5))
    fail_n = int(block.get("fail_trading_days", 15))
    from services.data_sources.sync_runner import eligible_end_date, load_registry

    reg = registry if registry is not None else load_registry()
    domains_cfg = list(block.get("domains") or [])
    findings: list[dict[str, Any]] = []
    for entry in domains_cfg:
        domain = str(entry.get("domain") or "")
        table = str(entry.get("table") or "")
        date_col = str(entry.get("date_column") or "ann_date")
        alias = str(entry.get("db_alias") or "tushare_raw")
        spec = dict((reg.get("domains") or {}).get(domain) or {})
        if not spec:
            findings.append(
                {
                    "check": "ann_tip_lag",
                    "domain": domain,
                    "status": "skip",
                    "detail": "domain_missing_from_registry",
                }
            )
            continue
        spec = {**spec, "domain": domain}
        eligibility = eligible_end_date(
            spec,
            now=now,
            trading_day_values=trading_days,
            trigger_mode="automatic",
        )
        eligible = _compact(eligibility.eligible_end)
        conn = conn_for_alias(alias)
        try:
            local_max = _max_date(conn, table, date_col)
        finally:
            conn.close()
        if eligible is None:
            findings.append(
                {
                    "check": "ann_tip_lag",
                    "domain": domain,
                    "status": "skip",
                    "detail": f"eligible_end_unavailable:{eligibility.reason}",
                    "local_max": local_max,
                    "eligible_end": None,
                }
            )
            continue
        if local_max is None:
            findings.append(
                {
                    "check": "ann_tip_lag",
                    "domain": domain,
                    "status": "fail",
                    "detail": "local_empty_while_eligible_present",
                    "local_max": None,
                    "eligible_end": eligible,
                    "eligible_reason": eligibility.reason,
                    "lag_trading_days": None,
                    "warn_trading_days": warn_n,
                    "fail_trading_days": fail_n,
                }
            )
            continue
        lag = trading_lag_days(trading_days, local_max, eligible)
        status = classify_lag(lag, warn_trading_days=warn_n, fail_trading_days=fail_n)
        findings.append(
            {
                "check": "ann_tip_lag",
                "domain": domain,
                "status": status,
                "detail": (
                    "tip_behind_eligible" if (lag or 0) > 0 else "tip_at_or_past_eligible"
                ),
                "local_max": local_max,
                "eligible_end": eligible,
                "eligible_reason": eligibility.reason,
                "lag_trading_days": lag,
                "warn_trading_days": warn_n,
                "fail_trading_days": fail_n,
            }
        )
    return findings


def evaluate_residual_hygiene(
    *,
    policy: dict[str, Any] | None = None,
    trading_days: list[str],
    raw_conn: Any | None = None,
    fact_conn: Any | None = None,
    conn_for_alias: Any | None = None,
    registry: dict[str, Any] | None = None,
    now: datetime | None = None,
    type_b_only: bool = False,
) -> dict[str, Any]:
    """Single compute point. Returns overall + findings."""
    pol = policy if policy is not None else load_policy()
    findings: list[dict[str, Any]] = []
    if raw_conn is not None and fact_conn is not None:
        findings.extend(
            measure_type_b_publish_lags(
                policy=pol,
                trading_days=trading_days,
                raw_conn=raw_conn,
                fact_conn=fact_conn,
            )
        )
    if not type_b_only and conn_for_alias is not None:
        findings.extend(
            measure_ann_tip_lags(
                policy=pol,
                trading_days=trading_days,
                conn_for_alias=conn_for_alias,
                registry=registry,
                now=now,
            )
        )
    overall = overall_status(findings)
    return {
        "policy_id": pol.get("policy_id"),
        "overall": overall,
        "findings": findings,
        "summary": {
            "fail": sum(1 for f in findings if f.get("status") == "fail"),
            "warn": sum(1 for f in findings if f.get("status") == "warn"),
            "pass": sum(1 for f in findings if f.get("status") == "pass"),
            "skip": sum(1 for f in findings if f.get("status") == "skip"),
        },
    }


def load_trading_days() -> list[str]:
    from services.data_access.resolver import connect_ro

    conn = connect_ro("reference")
    try:
        rows = conn.execute(
            "SELECT trade_date FROM dim_trading_calendar WHERE is_trading = 1 "
            "ORDER BY trade_date"
        ).fetchall()
    finally:
        conn.close()
    out: list[str] = []
    for (d,) in rows:
        c = _compact(d)
        if c:
            out.append(c)
    return out


def evaluate_type_b_after_catchup(
    *,
    trading_days: list[str] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Post type_b catchup evaluate using live DBs (read-only)."""
    from services.data_access import resolver
    from services.duck_adapter import connect as duck_connect

    days = trading_days if trading_days is not None else load_trading_days()
    raw_path = Path(resolver.db_path("tushare_raw"))
    sm_path = Path(resolver.db_path("smartmoney"))
    if not raw_path.is_file() or not sm_path.is_file():
        return {
            "policy_id": (policy or load_policy()).get("policy_id"),
            "overall": "PASS",
            "findings": [],
            "summary": {"fail": 0, "warn": 0, "pass": 0, "skip": 0},
            "status": "skipped",
            "reason": "db_missing",
        }
    raw_conn = duck_connect(str(raw_path), read_only=True)
    fact_conn = duck_connect(str(sm_path), read_only=True)
    try:
        return evaluate_residual_hygiene(
            policy=policy,
            trading_days=days,
            raw_conn=raw_conn,
            fact_conn=fact_conn,
            type_b_only=True,
        )
    finally:
        raw_conn.close()
        fact_conn.close()


__all__ = [
    "DEFAULT_POLICY_PATH",
    "classify_lag",
    "evaluate_residual_hygiene",
    "evaluate_type_b_after_catchup",
    "load_policy",
    "load_trading_days",
    "overall_status",
    "trading_lag_days",
]
