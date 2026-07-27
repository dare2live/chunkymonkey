"""K3: factor-family frontier projection (continuity → honest DEFER reasons).

Read-only projection of live tips for defer/blocked families. Does not mutate
inventory YAML; writes an evidence artifact that RX preflight can cite.

Authority: analysis/factor_family_governance_toplevel_20260724.md (K3).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.factor_family_inventory import (
    DEFAULT_INVENTORY,
    FactorFamilyInventory,
    load_inventory,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PROJECTION_PATH = (
    REPO / "data" / "lineage" / "factor_family_frontier_projection.json"
)


@dataclass(frozen=True)
class FamilyFrontierRow:
    family_id: str
    stack_eligibility: str
    inventory_defer_reason: str | None
    inventory_blocked_reason: str | None
    live_status: str
    live_detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "stack_eligibility": self.stack_eligibility,
            "inventory_defer_reason": self.inventory_defer_reason,
            "inventory_blocked_reason": self.inventory_blocked_reason,
            "live_status": self.live_status,
            "live_detail": dict(self.live_detail),
        }


def _compact(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _query_org_frontier(conn) -> dict[str, Any]:
    from services.data_sources.org_holding_schema import DATASET_ID

    row = conn.execute(
        """
        SELECT COUNT(*) AS n,
               MAX(replace(CAST(partition_value AS VARCHAR), '-', '')) AS tip
          FROM accepted_partition
         WHERE dataset_id = ?
        """,
        [DATASET_ID],
    ).fetchone()
    n = int(row[0] or 0) if row else 0
    tip = _compact(row[1]) if row else ""
    return {
        "dataset_id": DATASET_ID,
        "accepted_partition_count": n,
        "accepted_tip": tip or None,
        "defer_policy": "period_gap_bounded_n1_not_mass",
    }


def _query_margin_frontier(conn) -> dict[str, Any]:
    # Margin is external_aggregate; tip from accepted if present.
    row = conn.execute(
        """
        SELECT COUNT(*) AS n,
               MAX(replace(CAST(partition_value AS VARCHAR), '-', '')) AS tip
          FROM accepted_partition
         WHERE dataset_id LIKE '%margin%'
        """
    ).fetchone()
    n = int(row[0] or 0) if row else 0
    tip = _compact(row[1]) if row else ""
    return {
        "accepted_partition_count": n,
        "accepted_tip": tip or None,
        "defer_policy": "external_aggregate_product_trust_gated",
    }


def _query_tip(conn, table: str) -> str | None:
    row = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
    return _compact(row[0]) if row and row[0] else None


def _has_error(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) == "error"
            or str(key).endswith("_error")
            or _has_error(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_error(item) for item in value)
    return False


def projection_violations(
    payload: Mapping[str, Any],
    *,
    max_age_seconds: int = 86_400,
    now: datetime | None = None,
    inventory_path: Path | None = None,
) -> list[str]:
    """Validate live K3 evidence; structural inventory checks are separate."""

    violations: list[str] = []
    projected_raw = str(payload.get("projected_at") or "")
    try:
        projected_at = datetime.fromisoformat(projected_raw.replace("Z", "+00:00"))
        if projected_at.tzinfo is None:
            raise ValueError("timezone missing")
        age = ((now or datetime.now(timezone.utc)) - projected_at).total_seconds()
        if age < -300 or age > max_age_seconds:
            violations.append(
                f"projection freshness invalid age_seconds={int(age)} max={max_age_seconds}"
            )
    except ValueError as exc:
        violations.append(f"projection projected_at invalid: {exc}")

    inv_path = inventory_path or DEFAULT_INVENTORY
    expected_hash = hashlib.sha256(inv_path.read_bytes()).hexdigest()
    if str(payload.get("inventory_sha256") or "") != expected_hash:
        violations.append("projection inventory_sha256 drift")

    inv = load_inventory(inv_path)
    expected = {
        family_id: str(spec.get("stack_eligibility") or "")
        for family_id, spec in inv.families.items()
        if isinstance(spec, dict)
        and str(spec.get("stack_eligibility") or "") in {"defer", "blocked"}
    }
    rows = payload.get("families")
    if not isinstance(rows, list):
        return [*violations, "projection families must be a list"]
    by_id: dict[str, Mapping[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            violations.append("projection family row must be a mapping")
            continue
        family_id = str(raw.get("family_id") or "")
        if not family_id or family_id in by_id:
            violations.append(f"projection duplicate/blank family_id={family_id!r}")
            continue
        by_id[family_id] = raw
    if set(by_id) != set(expected):
        violations.append(
            f"projection family set drift missing={sorted(set(expected)-set(by_id))} "
            f"extra={sorted(set(by_id)-set(expected))}"
        )
    for family_id, eligibility in expected.items():
        row = by_id.get(family_id)
        if row is None:
            continue
        status = str(row.get("live_status") or "")
        detail = row.get("live_detail") or {}
        if eligibility == "defer" and status != "PROJECTED":
            violations.append(f"{family_id}: live_status={status or 'missing'}")
        if eligibility == "blocked" and status != "BLOCKED_DECLARED":
            violations.append(f"{family_id}: blocked live_status={status or 'missing'}")
        if _has_error(detail):
            violations.append(f"{family_id}: live_detail contains error")
        if family_id == "org_disclosure_period":
            if int((detail or {}).get("accepted_partition_count") or 0) <= 0:
                violations.append(f"{family_id}: accepted_partition_count=0")
        if family_id == "vendor_flow_proxy":
            if not (detail or {}).get("raw_tip"):
                violations.append(f"{family_id}: raw_tip missing")
            if not (detail or {}).get("fact_tip"):
                violations.append(f"{family_id}: fact_tip missing")
            margin = (detail or {}).get("margin_external_aggregate") or {}
            if int(margin.get("accepted_partition_count") or 0) <= 0:
                violations.append(
                    f"{family_id}: margin accepted_partition_count=0"
                )
    return violations


def project_family_frontiers(
    *,
    smartmoney_conn=None,
    raw_conn=None,
    inventory_path: Path | None = None,
) -> dict[str, Any]:
    """Project defer/blocked family frontiers; missing DB → UNVERIFIED rows."""
    inv = load_inventory(inventory_path or DEFAULT_INVENTORY)
    rows: list[FamilyFrontierRow] = []

    for family_id, spec in inv.families.items():
        if not isinstance(spec, dict):
            continue
        se = str(spec.get("stack_eligibility") or "")
        if se not in {"defer", "blocked"}:
            continue
        defer_reason = (
            str(spec.get("defer_reason")) if spec.get("defer_reason") else None
        )
        blocked_reason = (
            str(spec.get("blocked_reason")) if spec.get("blocked_reason") else None
        )
        live_status = "UNVERIFIED"
        live_detail: dict[str, Any] = {}

        try:
            if family_id == "org_disclosure_period":
                if smartmoney_conn is None:
                    live_detail = {"error": "smartmoney_conn_missing"}
                else:
                    live_detail = _query_org_frontier(smartmoney_conn)
                    live_status = "PROJECTED"
            elif family_id == "vendor_flow_proxy":
                if smartmoney_conn is None or raw_conn is None:
                    live_detail = {"error": "conn_missing"}
                else:
                    live_detail = {
                        "defer_policy": "type_b_fact_publish_may_lag_raw",
                        "raw_tip": _query_tip(raw_conn, "raw_tushare_moneyflow"),
                        "fact_tip": _query_tip(
                            smartmoney_conn, "fact_stock_moneyflow_daily"
                        ),
                    }
                    live_status = (
                        "PROJECTED"
                        if live_detail["raw_tip"] and live_detail["fact_tip"]
                        else "UNVERIFIED"
                    )
            elif family_id == "formula_single":
                live_status = "BLOCKED_DECLARED"
                live_detail = {"blocked_reason": blocked_reason}
            else:
                live_status = "NO_LIVE_PROBE"
                live_detail = {"note": "inventory_defer_only"}
        except Exception as exc:  # noqa: BLE001 — fail closed to UNVERIFIED
            live_status = "UNVERIFIED"
            live_detail = {"error": str(exc)[:300]}

        # Margin honesty: attach as sibling note under vendor_flow when probed.
        if family_id == "vendor_flow_proxy" and raw_conn is not None:
            try:
                live_detail["margin_external_aggregate"] = _query_margin_frontier(
                    raw_conn
                )
            except Exception as exc:  # noqa: BLE001
                live_detail["margin_probe_error"] = str(exc)[:200]

        if se == "defer" and not defer_reason:
            live_status = "INVENTORY_INVALID"
            live_detail["error"] = "defer_family_missing_defer_reason"

        rows.append(
            FamilyFrontierRow(
                family_id=family_id,
                stack_eligibility=se,
                inventory_defer_reason=defer_reason,
                inventory_blocked_reason=blocked_reason,
                live_status=live_status,
                live_detail=live_detail,
            )
        )

    inv_path = inventory_path or DEFAULT_INVENTORY
    payload = {
        "projected_at": datetime.now(timezone.utc).isoformat(),
        "inventory_path": str(inv_path),
        "inventory_sha256": hashlib.sha256(inv_path.read_bytes()).hexdigest(),
        "families": [r.as_dict() for r in rows],
        "notes": [
            "k3_frontier_projection",
            "does_not_mutate_inventory_yaml",
            "rx_still_requires_owner_schedule",
        ],
    }
    violations = projection_violations(payload, inventory_path=inv_path)
    payload["verdict"] = "PASS" if not violations else "BLOCKED"
    payload["violations"] = violations
    return payload


def write_frontier_projection(
    payload: dict[str, Any] | None = None,
    *,
    path: Path | str | None = None,
    smartmoney_conn=None,
    raw_conn=None,
) -> Path:
    target = Path(path) if path is not None else DEFAULT_PROJECTION_PATH
    body = payload or project_family_frontiers(
        smartmoney_conn=smartmoney_conn, raw_conn=raw_conn
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def assert_defer_reasons_honest(
    inv_path: Path | None = None,
    inv: FactorFamilyInventory | None = None,
) -> list[str]:
    """Structural: every defer family has a non-empty defer_reason (K3 inventory)."""
    loaded = inv or load_inventory(inv_path or DEFAULT_INVENTORY)
    viol: list[str] = []
    for family_id, spec in loaded.families.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("stack_eligibility") != "defer":
            continue
        if not str(spec.get("defer_reason") or "").strip():
            viol.append(f"{family_id}: defer without defer_reason")
    return viol


__all__ = [
    "DEFAULT_PROJECTION_PATH",
    "FamilyFrontierRow",
    "assert_defer_reasons_honest",
    "projection_violations",
    "project_family_frontiers",
    "write_frontier_projection",
]
