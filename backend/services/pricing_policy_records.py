"""Pricing policy table and gate writers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from services.pricing_policy_model import PricingLabelPolicy, load_pricing_label_policy
from services.pricing_schema import PRICING_POLICY_DDL

UTC = timezone.utc
DDL = PRICING_POLICY_DDL


def ensure_pricing_policy_table(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
        return
    for stmt in DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def record_pricing_label_policy(conn: Any, policy: PricingLabelPolicy | None = None) -> dict[str, Any]:
    policy = policy or load_pricing_label_policy()
    ensure_pricing_policy_table(conn)
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    payload = policy.to_dict()
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pricing_label_policy (
            policy_id, version, policy_hash, event_calc_version,
            follow_entry_price_mode, follow_entry_ref_price_mode,
            transaction_cost_bps, policy_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy.policy_id,
            policy.version,
            policy.policy_hash(),
            policy.event_calc_version,
            policy.follow_entry_price_mode,
            policy.follow_entry_ref_price_mode,
            policy.transaction_cost_bps,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    return payload


def record_pricing_label_policy_gate(
    conn: Any,
    *,
    policy: PricingLabelPolicy | None = None,
    gate_run_id: str | None = None,
    gate_scope: str = "model_training",
) -> dict[str, Any]:
    policy = policy or load_pricing_label_policy()
    ensure_pricing_policy_table(conn)
    record_pricing_label_policy(conn, policy)
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    gate_run_id = gate_run_id or f"pricing_label_policy_gate_{gate_scope}_{built_at.replace(':', '').replace('-', '')}"
    blockers = policy.training_blockers(scope=gate_scope)
    warnings = policy.training_warnings()
    gate_status = "pass" if not blockers else "blocked"
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pricing_label_policy_gate (
            gate_run_id, policy_id, policy_hash, gate_scope, gate_status,
            blockers_json, warnings_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gate_run_id,
            policy.policy_id,
            policy.policy_hash(),
            gate_scope,
            gate_status,
            json.dumps(blockers, ensure_ascii=False, sort_keys=True),
            json.dumps(warnings, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    return {
        "gate_run_id": gate_run_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash(),
        "gate_scope": gate_scope,
        "gate_status": gate_status,
        "blockers": blockers,
        "warnings": warnings,
    }
