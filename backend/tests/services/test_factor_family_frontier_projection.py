"""K3 factor-family frontier projection."""
from __future__ import annotations

from services.factor_family_frontier_projection import (
    assert_defer_reasons_honest,
    project_family_frontiers,
)


def test_inventory_defer_reasons_present() -> None:
    assert assert_defer_reasons_honest() == []


def test_projection_without_db_is_unverified_or_declared() -> None:
    payload = project_family_frontiers(smartmoney_conn=None, raw_conn=None)
    families = {r["family_id"]: r for r in payload["families"]}
    assert "org_disclosure_period" in families
    assert families["org_disclosure_period"]["stack_eligibility"] == "defer"
    assert families["org_disclosure_period"]["inventory_defer_reason"]
    assert families["org_disclosure_period"]["live_status"] in {
        "UNVERIFIED",
        "PROJECTED",
    }
    assert families["formula_single"]["live_status"] == "BLOCKED_DECLARED"
