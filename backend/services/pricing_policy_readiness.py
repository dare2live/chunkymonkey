"""Pricing-label data-readiness gate writer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from services.pricing_policy_evidence import (
    count_rows,
    event_return_evidence,
    existing_artifact_evidence,
    latest_follow_label_build,
    latest_follow_label_quality,
    missing_labels_by_table,
    required_follow_labels,
    table_columns,
)
from services.pricing_policy_model import PricingLabelPolicy, load_pricing_label_policy
from services.pricing_policy_records import ensure_pricing_policy_table, record_pricing_label_policy

UTC = timezone.utc


def _append_follow_label_blockers(
    blockers: list[str],
    *,
    table_name: str,
    build_evidence: dict[str, Any],
    quality_evidence: dict[str, Any],
) -> None:
    if not build_evidence["table_exists"] or not build_evidence["build_exists"]:
        blockers.append(f"{table_name}_follow_label_build_missing")
        return
    if not build_evidence["policy_hash_match"]:
        blockers.append(f"{table_name}_follow_label_build_policy_hash_mismatch")
    if not build_evidence["event_calc_version_match"]:
        blockers.append(f"{table_name}_follow_label_build_calc_version_mismatch")
    if build_evidence["missing_labels_in_build"]:
        blockers.append(f"{table_name}_follow_label_build_missing_required_labels")
    if build_evidence["zero_non_null_labels"]:
        blockers.append(f"{table_name}_follow_label_build_zero_non_null_labels")

    if not quality_evidence["table_exists"] or not quality_evidence["quality_exists"]:
        blockers.append(f"{table_name}_follow_label_quality_missing")
        return
    if quality_evidence["missing_labels_in_quality"]:
        blockers.append(f"{table_name}_follow_label_quality_missing_required_labels")
    if quality_evidence["policy_hash_mismatch_labels"]:
        blockers.append(f"{table_name}_follow_label_quality_policy_hash_mismatch")
    if quality_evidence["event_calc_version_mismatch_labels"]:
        blockers.append(f"{table_name}_follow_label_quality_calc_version_mismatch")
    if quality_evidence["mature_null_labels"]:
        blockers.append(f"{table_name}_follow_label_quality_mature_nulls")
    if quality_evidence["missing_signal_kline_labels"]:
        blockers.append(f"{table_name}_follow_label_quality_missing_signal_kline")
    if quality_evidence["missing_entry_price_labels"]:
        blockers.append(f"{table_name}_follow_label_quality_missing_entry_price")
    if quality_evidence["missing_exit_price_labels"]:
        blockers.append(f"{table_name}_follow_label_quality_missing_exit_price")
    if quality_evidence["unclassified_null_labels"]:
        blockers.append(f"{table_name}_follow_label_quality_unclassified_nulls")


def _append_event_return_findings(blockers: list[str], warnings: list[str], evidence: dict[str, Any]) -> None:
    if evidence.get("missing_columns"):
        blockers.append("event_return_calc_columns_missing")
    if int(evidence.get("stale_rows") or 0) > 0:
        blockers.append("event_returns_stale_for_pricing_policy")
    if int(evidence.get("mature_missing_price_entry_rows") or 0) > 0:
        blockers.append("event_returns_mature_missing_price_entry")
    if int(evidence.get("event_rows_with_notice") or 0) == 0:
        warnings.append("no_institution_event_rows_with_notice_in_current_db")


def _append_artifact_findings(
    conn: Any,
    blockers: list[str],
    warnings: list[str],
    evidence: dict[str, Any],
    *,
    policy: PricingLabelPolicy,
    gate_scope: str,
) -> None:
    artifact_blocking_by_scope = {
        "fact_institution_follow_backtest": True,
        "mart_institution_profile": True,
        "mart_multidim_model": gate_scope in {"champion_gate", "promotion_gate", "full_research", "production"},
    }
    for table_name, is_blocking in artifact_blocking_by_scope.items():
        artifact = existing_artifact_evidence(conn, table_name, policy.policy_hash())
        evidence["artifacts"][table_name] = artifact
        if artifact["rows"] and artifact["missing_policy_hash"]:
            message = f"{table_name}_missing_pricing_policy_hash"
            (blockers if is_blocking else warnings).append(message)
        elif artifact["stale_rows"]:
            message = f"{table_name}_stale_for_pricing_policy"
            (blockers if is_blocking else warnings).append(message)


def record_pricing_label_data_readiness_gate(
    conn: Any,
    *,
    policy: PricingLabelPolicy | None = None,
    gate_run_id: str | None = None,
    gate_scope: str = "model_training",
    feature_tables: list[str] | None = None,
) -> dict[str, Any]:
    policy = policy or load_pricing_label_policy()
    feature_tables = feature_tables or ["fact_feature_panel", "fact_feature_panel_candidate"]
    ensure_pricing_policy_table(conn)
    record_pricing_label_policy(conn, policy)
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    gate_run_id = gate_run_id or f"pricing_label_data_readiness_{gate_scope}_{built_at.replace(':', '').replace('-', '')}"
    required_labels = required_follow_labels(policy)
    blockers = policy.training_blockers(scope=gate_scope)
    warnings = policy.training_warnings()
    evidence: dict[str, Any] = {
        "definition_gate_blockers": blockers[:],
        "feature_tables": {},
        "event_returns": event_return_evidence(conn, policy),
        "artifacts": {},
    }

    missing_labels = missing_labels_by_table(conn, feature_tables, required_labels)
    for table_name in feature_tables:
        columns = table_columns(conn, table_name)
        evidence["feature_tables"][table_name] = {
            "exists": bool(columns),
            "rows": count_rows(conn, table_name),
            "missing_required_follow_labels": missing_labels.get(table_name, []),
        }
    if missing_labels:
        blockers.append("follow_return_labels_missing")

    for table_name in feature_tables:
        if missing_labels.get(table_name):
            continue
        build_evidence = latest_follow_label_build(conn, table_name, policy=policy, required_labels=required_labels)
        quality_evidence = latest_follow_label_quality(
            conn,
            table_name,
            run_id=build_evidence.get("run_id"),
            policy=policy,
            required_labels=required_labels,
        )
        evidence["feature_tables"][table_name]["follow_label_build"] = build_evidence
        evidence["feature_tables"][table_name]["follow_label_quality"] = quality_evidence
        _append_follow_label_blockers(
            blockers,
            table_name=table_name,
            build_evidence=build_evidence,
            quality_evidence=quality_evidence,
        )

    _append_event_return_findings(blockers, warnings, evidence["event_returns"])
    _append_artifact_findings(conn, blockers, warnings, evidence, policy=policy, gate_scope=gate_scope)

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    gate_status = "pass" if not blockers else "blocked"
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pricing_label_data_readiness_gate (
            gate_run_id, policy_id, policy_hash, gate_scope, gate_status,
            feature_tables_json, required_labels_json, blockers_json,
            warnings_json, evidence_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gate_run_id,
            policy.policy_id,
            policy.policy_hash(),
            gate_scope,
            gate_status,
            json.dumps(feature_tables, ensure_ascii=False, sort_keys=True),
            json.dumps(required_labels, ensure_ascii=False, sort_keys=True),
            json.dumps(blockers, ensure_ascii=False, sort_keys=True),
            json.dumps(warnings, ensure_ascii=False, sort_keys=True),
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    return {
        "gate_run_id": gate_run_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash(),
        "gate_scope": gate_scope,
        "gate_status": gate_status,
        "required_labels": required_labels,
        "blockers": blockers,
        "warnings": warnings,
        "evidence": evidence,
    }
