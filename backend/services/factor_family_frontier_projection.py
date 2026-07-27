"""K3: factor-family frontier projection (continuity → honest DEFER reasons).

Read-only projection of live tips for defer/blocked families. Does not mutate
inventory YAML; writes an evidence artifact that RX preflight can cite.

Authority: analysis/factor_family_governance_toplevel_20260724.md (K3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _query_moneyflow_type_b(conn) -> dict[str, Any]:
    # Best-effort: raw tip vs fact tip when tables exist.
    detail: dict[str, Any] = {"defer_policy": "type_b_fact_publish_may_lag_raw"}
    try:
        raw_tip = conn.execute(
            """
            SELECT MAX(trade_date) FROM raw_tushare_moneyflow
            """
        ).fetchone()
        detail["raw_tip"] = _compact(raw_tip[0]) if raw_tip and raw_tip[0] else None
    except Exception as exc:  # noqa: BLE001
        detail["raw_tip_error"] = str(exc)[:200]
    try:
        fact_tip = conn.execute(
            """
            SELECT MAX(trade_date) FROM fact_stock_moneyflow_daily
            """
        ).fetchone()
        detail["fact_tip"] = (
            _compact(fact_tip[0]) if fact_tip and fact_tip[0] else None
        )
    except Exception as exc:  # noqa: BLE001
        detail["fact_tip_error"] = str(exc)[:200]
    return detail


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
                if smartmoney_conn is None and raw_conn is None:
                    live_detail = {"error": "conn_missing"}
                else:
                    live_detail = {}
                    if raw_conn is not None:
                        live_detail.update(_query_moneyflow_type_b(raw_conn))
                    if smartmoney_conn is not None:
                        fact_only = _query_moneyflow_type_b(smartmoney_conn)
                        if fact_only.get("fact_tip") is not None:
                            live_detail["fact_tip"] = fact_only.get("fact_tip")
                        if fact_only.get("fact_tip_error"):
                            live_detail["fact_tip_error"] = fact_only.get(
                                "fact_tip_error"
                            )
                    live_status = "PROJECTED"
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
        if family_id == "vendor_flow_proxy" and smartmoney_conn is not None:
            try:
                live_detail["margin_external_aggregate"] = _query_margin_frontier(
                    smartmoney_conn
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

    return {
        "projected_at": datetime.now(timezone.utc).isoformat(),
        "inventory_path": str(inventory_path or DEFAULT_INVENTORY),
        "families": [r.as_dict() for r in rows],
        "notes": [
            "k3_frontier_projection",
            "does_not_mutate_inventory_yaml",
            "rx_still_requires_owner_schedule",
        ],
    }


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
    "project_family_frontiers",
    "write_frontier_projection",
]
