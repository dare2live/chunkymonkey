"""Phase E smoke: DatasetSnapshot gate + research surface_status only.

Does NOT run institution_follow ablation / B0–B4 search.
"""
from __future__ import annotations

import json
from pathlib import Path

from routers import institution_profile as inst_router
from services.data_sources.disclosure_boundaries import (
    attest_disclosure_research_surface,
)
from services.data_sources.disclosure_dataset_snapshot import (
    DISCLOSURE_SNAPSHOT_RELPATH,
    default_snapshot_path,
)
from services.data_sources.disclosure_research_read import (
    build_disclosure_read_policy,
)
from services.data_sources.disclosure_shadow_compare import (
    DisclosureDomainShadowReport,
    DisclosureShadowCompareReport,
)


def _match_domain(name: str, partition: str) -> DisclosureDomainShadowReport:
    return DisclosureDomainShadowReport(
        domain=name,
        partition=partition,
        status="MATCH",
        legacy_row_count=2,
        canonical_row_count=2,
        compared_fields=("stock_code",),
        rows_match=True,
        mismatch_count=0,
        sample_mismatches=(),
        issues=("phase_e_smoke",),
    )


def test_phase_e_smoke_dataset_snapshot_gate_and_surface_status() -> None:
    root = Path(__file__).resolve().parents[3]
    snap_path = root / DISCLOSURE_SNAPSHOT_RELPATH
    assert snap_path == default_snapshot_path()
    assert snap_path.is_file(), f"missing canary DatasetSnapshot at {snap_path}"
    payload = json.loads(snap_path.read_text(encoding="utf-8"))
    assert payload.get("cutover_allowed") is True
    assert payload.get("scope") in {
        "canary_accepted_partitions",
        "bounded_accepted_partitions",
    }
    assert payload.get("phase_e_ablation") in {
        "blocked_canary_scope_only",
        "bounded_scope_wf_paper_still_blocked",
        "bounded_scope_measured_b0_short_window",
    }
    domains = payload.get("domains") or {}
    assert set(domains) >= {
        "holders_top10",
        "org_holding",
        "stk_holdertrade",
    }
    if payload.get("scope") == "bounded_accepted_partitions":
        for name in ("holders_top10", "org_holding", "stk_holdertrade"):
            date_set = domains[name].get("date_set") or []
            assert len(date_set) >= 2, f"{name} needs bounded date_set"
            assert domains[name].get("accepted")

    shadow = DisclosureShadowCompareReport(
        overall_status="MATCH",
        cutover_allowed=True,
        domains=(
            _match_domain("holders_top10", "20260717"),
            _match_domain("org_holding", "20190430"),
            _match_domain("stk_holdertrade", "20260706"),
        ),
        notes=("phase_e_smoke",),
    )
    policy = build_disclosure_read_policy(shadow)
    assert policy.cutover_allowed is True
    assert policy.feature_store_field_status
    report = attest_disclosure_research_surface(policy)
    assert report.cutover_allowed is True
    assert report.e0_phase == "gate_closed_canary"
    assert "phase_e_smoke_eligible_ablation_still_blocked" in report.notes

    envelope = {
        "status": "ok",
        "surface_status": inst_router.SURFACE_STATUS,
        "disclosure_conformity": report.as_dict(),
        "disclosure_read_policy": policy.as_dict(),
        "cutover_allowed": True,
    }
    assert envelope["surface_status"] == "tier3_research_evidence_only"
    assert envelope["cutover_allowed"] is True
    # Full B0→B4 ablation remains blocked; bounded scope unlocks measured B0 only.
    ablation = payload["phase_e_ablation"]
    assert ablation != "eligible_full_b0_b4"
    assert (
        "ablation" in ablation
        or "blocked" in ablation
        or ablation == "bounded_scope_measured_b0_short_window"
    )


def test_phase_e_checkpoint_verdict_artifacts_reject_no_gain() -> None:
    """Committed Phase E ladder artifacts: all reject / claimable=false."""

    root = Path(__file__).resolve().parents[3]
    manifest_path = root / "data/lineage/phase_e_experiment_verdicts/manifest.json"
    assert manifest_path.is_file(), f"missing Phase E verdict manifest at {manifest_path}"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("overall", {}).get("status") == "measured_reject_no_gain"
    assert manifest.get("overall", {}).get("any_claimable") is False
    assert manifest.get("overall", {}).get("strategy_release") is False
    snap_path = root / DISCLOSURE_SNAPSHOT_RELPATH
    import hashlib

    expected_hash = hashlib.sha256(snap_path.read_bytes()).hexdigest()
    assert manifest.get("snapshot_hash") == expected_hash
    blocks = manifest.get("blocks") or {}
    for name in ("b0", "b1", "b2", "b4"):
        rel = blocks.get(name)
        assert rel, f"manifest missing block {name}"
        payload = json.loads((root / rel).read_text(encoding="utf-8"))
        assert payload.get("verdict") == "reject"
        assert payload.get("claimable") is False
        assert payload.get("strategy_release") is False
        assert payload.get("snapshot_hash") == expected_hash
    # B2 short-window accept was withdrawn — holdout lift gate records unmet.
    b2 = json.loads((root / blocks["b2"]).read_text(encoding="utf-8"))
    assert b2.get("reason") == "holdout_lift_vs_b0_unmet"
