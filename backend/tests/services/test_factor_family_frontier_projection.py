"""K3 factor-family frontier projection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.factor_family_frontier_projection import (
    assert_defer_reasons_honest,
    projection_violations,
    project_family_frontiers,
)


def test_inventory_defer_reasons_present() -> None:
    assert assert_defer_reasons_honest() == []


def test_projection_without_db_is_blocked_not_live_green() -> None:
    payload = project_family_frontiers(smartmoney_conn=None, raw_conn=None)
    families = {r["family_id"]: r for r in payload["families"]}
    assert "org_disclosure_period" in families
    assert families["org_disclosure_period"]["stack_eligibility"] == "defer"
    assert families["org_disclosure_period"]["inventory_defer_reason"]
    assert families["org_disclosure_period"]["live_status"] == "UNVERIFIED"
    assert families["formula_single"]["live_status"] == "BLOCKED_DECLARED"
    assert payload["verdict"] == "BLOCKED"
    assert projection_violations(payload)


class _Conn:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def execute(self, sql: str, _params=None):
        class _Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        if "raw_tushare_moneyflow" in sql:
            assert self.kind == "raw"
            return _Result(("20260724",))
        if "fact_stock_moneyflow_daily" in sql:
            assert self.kind == "smartmoney"
            return _Result(("20260723",))
        if "LIKE '%margin%'" in sql:
            assert self.kind == "raw"
            return _Result((1828, "20260724"))
        if "dataset_id = ?" in sql:
            assert self.kind == "org_holding"
            return _Result((22, "20260430"))
        raise AssertionError(sql)


def test_projection_uses_correct_db_owners_and_passes_live_contract() -> None:
    payload = project_family_frontiers(
        smartmoney_conn=_Conn("smartmoney"),
        raw_conn=_Conn("raw"),
        org_holding_conn=_Conn("org_holding"),
    )
    assert payload["verdict"] == "PASS"
    assert projection_violations(payload) == []
    families = {r["family_id"]: r for r in payload["families"]}
    vendor = families["vendor_flow_proxy"]["live_detail"]
    assert vendor["raw_tip"] == "20260724"
    assert vendor["fact_tip"] == "20260723"
    assert vendor["margin_external_aggregate"]["accepted_partition_count"] == 1828


def test_projection_validation_rejects_stale_error_and_missing_family() -> None:
    payload = project_family_frontiers(
        smartmoney_conn=_Conn("smartmoney"),
        raw_conn=_Conn("raw"),
        org_holding_conn=_Conn("org_holding"),
    )
    payload["projected_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    payload["families"][0]["live_detail"]["raw_tip_error"] = "boom"
    payload["families"] = payload["families"][:-1]
    violations = projection_violations(payload, max_age_seconds=60)
    assert any("freshness" in item for item in violations)
    assert any("contains error" in item for item in violations)
    assert any("family set drift" in item for item in violations)
