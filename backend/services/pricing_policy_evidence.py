"""Evidence collectors for pricing-label readiness gates."""
from __future__ import annotations

import json
from typing import Any

from services.pricing_policy_model import PricingLabelPolicy


def table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        return row[index]


def table_columns(conn: Any, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    return {
        str(row_value(row, "column_name", 0))
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def count_rows(conn: Any, table_name: str) -> int:
    if not table_exists(conn, table_name):
        return 0
    row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table_name}"').fetchone()
    return int(row_value(row, "n", 0) or 0) if row else 0


def required_follow_labels(policy: PricingLabelPolicy) -> list[str]:
    follow = policy.definition_sections.get("follow_return_label") or {}
    labels = follow.get("horizon_candidate_labels") or []
    return [str(label) for label in labels]


def missing_labels_by_table(conn: Any, feature_tables: list[str], labels: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for table_name in feature_tables:
        columns = table_columns(conn, table_name)
        if not columns:
            out[table_name] = labels[:]
            continue
        missing = [label for label in labels if label not in columns]
        if missing:
            out[table_name] = missing
    return out


def event_return_evidence(conn: Any, policy: PricingLabelPolicy) -> dict[str, Any]:
    table_name = "fact_institution_event"
    evidence: dict[str, Any] = {"table_exists": table_exists(conn, table_name)}
    if not evidence["table_exists"]:
        evidence.update({"event_rows_with_notice": 0, "stale_rows": 0})
        return evidence
    columns = table_columns(conn, table_name)
    if not {"notice_date", "calc_version", "calc_ref_price_mode"} <= columns:
        evidence.update({"event_rows_with_notice": 0, "stale_rows": 0, "missing_columns": True})
        return evidence
    event_row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM fact_institution_event
         WHERE notice_date IS NOT NULL AND notice_date != ''
        """
    ).fetchone()
    stale_row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM fact_institution_event
         WHERE notice_date IS NOT NULL AND notice_date != ''
           AND (
               COALESCE(calc_version, '') != ?
            OR COALESCE(calc_ref_price_mode, '') != ?
           )
        """,
        (policy.event_calc_version, policy.follow_entry_ref_price_mode),
    ).fetchone()
    evidence.update(
        {
            "event_rows_with_notice": int(row_value(event_row, "n", 0) or 0),
            "stale_rows": int(row_value(stale_row, "n", 0) or 0),
        }
    )
    if {"price_entry", "price_entry_status"} <= columns:
        mature_missing_row = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM fact_institution_event
             WHERE notice_date IS NOT NULL AND notice_date != ''
               AND COALESCE(calc_version, '') = ?
               AND (price_entry IS NULL OR price_entry = 0)
               AND COALESCE(price_entry_status, '') NOT IN ('future_signal_waiting')
            """,
            (policy.event_calc_version,),
        ).fetchone()
        future_unpriced_row = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM fact_institution_event
             WHERE notice_date IS NOT NULL AND notice_date != ''
               AND COALESCE(calc_version, '') = ?
               AND (price_entry IS NULL OR price_entry = 0)
               AND COALESCE(price_entry_status, '') = 'future_signal_waiting'
            """,
            (policy.event_calc_version,),
        ).fetchone()
        evidence["mature_missing_price_entry_rows"] = int(row_value(mature_missing_row, "n", 0) or 0)
        evidence["future_unpriced_rows"] = int(row_value(future_unpriced_row, "n", 0) or 0)
    return evidence


def existing_artifact_evidence(conn: Any, table_name: str, policy_hash: str) -> dict[str, Any]:
    evidence = {"table_exists": table_exists(conn, table_name), "rows": 0, "missing_policy_hash": False, "stale_rows": 0}
    if not evidence["table_exists"]:
        return evidence
    evidence["rows"] = count_rows(conn, table_name)
    if evidence["rows"] == 0:
        return evidence
    columns = table_columns(conn, table_name)
    if "pricing_policy_hash" not in columns:
        evidence["missing_policy_hash"] = True
        evidence["stale_rows"] = evidence["rows"]
        return evidence
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM "{table_name}"
         WHERE COALESCE(pricing_policy_hash, '') != ?
        """,
        (policy_hash,),
    ).fetchone()
    evidence["stale_rows"] = int(row_value(row, "n", 0) or 0) if row else 0
    return evidence


def latest_follow_label_build(
    conn: Any,
    table_name: str,
    *,
    policy: PricingLabelPolicy,
    required_labels: list[str],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "table_exists": table_exists(conn, "mart_follow_return_label_build"),
        "build_exists": False,
        "policy_hash_match": False,
        "event_calc_version_match": False,
        "missing_labels_in_build": required_labels[:],
        "zero_non_null_labels": [],
        "run_id": None,
        "built_at": None,
    }
    if not evidence["table_exists"]:
        return evidence
    row = conn.execute(
        """
        SELECT *
          FROM mart_follow_return_label_build
         WHERE feature_table = ?
         ORDER BY built_at DESC
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    if not row:
        return evidence
    row_policy_hash = row_value(row, "policy_hash", 3)
    row_event_calc_version = row_value(row, "event_calc_version", 4)
    row_count = int(row_value(row, "row_count", 9) or 0)
    evidence.update(
        {
            "build_exists": True,
            "run_id": row_value(row, "run_id", 0),
            "built_at": row_value(row, "built_at", 14),
            "policy_hash": row_policy_hash,
            "event_calc_version": row_event_calc_version,
            "row_count": row_count,
            "policy_hash_match": row_policy_hash == policy.policy_hash(),
            "event_calc_version_match": row_event_calc_version == policy.event_calc_version,
        }
    )
    try:
        labels = json.loads(row_value(row, "labels_json", 8) or "[]")
    except Exception:
        labels = []
    try:
        non_null = json.loads(row_value(row, "label_non_null_json", 10) or "{}")
    except Exception:
        non_null = {}
    evidence["missing_labels_in_build"] = [label for label in required_labels if label not in labels]
    evidence["zero_non_null_labels"] = [
        label for label in required_labels
        if row_count > 0 and int(non_null.get(label) or 0) <= 0
    ]
    evidence["label_non_null"] = non_null
    return evidence


def latest_follow_label_quality(
    conn: Any,
    table_name: str,
    *,
    run_id: str | None,
    policy: PricingLabelPolicy,
    required_labels: list[str],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "table_exists": table_exists(conn, "mart_follow_return_label_quality"),
        "quality_exists": False,
        "run_id": run_id,
        "missing_labels_in_quality": required_labels[:],
        "policy_hash_mismatch_labels": [],
        "event_calc_version_mismatch_labels": [],
        "mature_null_labels": [],
        "missing_signal_kline_labels": [],
        "missing_entry_price_labels": [],
        "missing_exit_price_labels": [],
        "unclassified_null_labels": [],
        "labels": {},
    }
    if not evidence["table_exists"] or not run_id:
        return evidence
    rows = conn.execute(
        """
        SELECT label_name,
               horizon_days,
               policy_hash,
               event_calc_version,
               row_count,
               non_null_count,
               null_count,
               immature_null_count,
               mature_null_count,
               missing_signal_kline_count,
               missing_entry_price_count,
               missing_exit_price_count,
               unclassified_null_count,
               global_market_max_date,
               built_at
          FROM mart_follow_return_label_quality
         WHERE feature_table = ?
           AND run_id = ?
        """,
        (table_name, run_id),
    ).fetchall()
    if not rows:
        return evidence
    evidence["quality_exists"] = True
    seen: set[str] = set()
    for row in rows:
        label = str(row_value(row, "label_name", 0))
        seen.add(label)
        detail = {
            "horizon_days": int(row_value(row, "horizon_days", 1) or 0),
            "policy_hash": row_value(row, "policy_hash", 2),
            "event_calc_version": row_value(row, "event_calc_version", 3),
            "row_count": int(row_value(row, "row_count", 4) or 0),
            "non_null_count": int(row_value(row, "non_null_count", 5) or 0),
            "null_count": int(row_value(row, "null_count", 6) or 0),
            "immature_null_count": int(row_value(row, "immature_null_count", 7) or 0),
            "mature_null_count": int(row_value(row, "mature_null_count", 8) or 0),
            "missing_signal_kline_count": int(row_value(row, "missing_signal_kline_count", 9) or 0),
            "missing_entry_price_count": int(row_value(row, "missing_entry_price_count", 10) or 0),
            "missing_exit_price_count": int(row_value(row, "missing_exit_price_count", 11) or 0),
            "unclassified_null_count": int(row_value(row, "unclassified_null_count", 12) or 0),
            "global_market_max_date": row_value(row, "global_market_max_date", 13),
            "built_at": row_value(row, "built_at", 14),
        }
        evidence["labels"][label] = detail
        if detail["policy_hash"] != policy.policy_hash():
            evidence["policy_hash_mismatch_labels"].append(label)
        if detail["event_calc_version"] != policy.event_calc_version:
            evidence["event_calc_version_mismatch_labels"].append(label)
        if detail["mature_null_count"] > 0:
            evidence["mature_null_labels"].append(label)
        if detail["missing_signal_kline_count"] > 0:
            evidence["missing_signal_kline_labels"].append(label)
        if detail["missing_entry_price_count"] > 0:
            evidence["missing_entry_price_labels"].append(label)
        if detail["missing_exit_price_count"] > 0:
            evidence["missing_exit_price_labels"].append(label)
        if detail["unclassified_null_count"] > 0:
            evidence["unclassified_null_labels"].append(label)
    evidence["missing_labels_in_quality"] = [label for label in required_labels if label not in seen]
    return evidence
