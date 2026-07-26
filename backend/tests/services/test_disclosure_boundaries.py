"""E0 adversarial tests: disclosure domain strangler + fail-closed formal claims."""
from __future__ import annotations

import pytest

from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    attest_disclosure_research_surface,
    authorize_legacy_mirror_write,
    authorize_nonconforming_direct_write,
    disclosure_boundary,
    disclosure_domains,
    disclosure_inventory,
    refuse_accepted_publication_claim,
    refuse_formal_disclosure_write_without_accepted_path,
)


def test_inventory_declares_three_disclosure_domains() -> None:
    domains = set(disclosure_domains())
    assert domains == {"holders_top10", "org_holding", "stk_holdertrade"}
    inventory = {item["domain"]: item for item in disclosure_inventory()}
    assert inventory["holders_top10"]["adapter"] == "miaoxiang"
    assert inventory["holders_top10"]["target_table"] == "canonical_top10_float_holders_period"
    assert inventory["holders_top10"]["availability_axis"] == "notice_date"
    assert inventory["holders_top10"]["landing_writer"] is not None
    assert inventory["holders_top10"]["canonical_writer"] is not None
    assert inventory["holders_top10"]["runtime_state"] == "formal_only"
    assert inventory["org_holding"]["adapter"] == "miaoxiang"
    assert inventory["org_holding"]["target_table"] == "raw_org_holding_aif10"
    assert inventory["org_holding"]["availability_axis"] == "available_date"
    assert inventory["org_holding"]["landing_writer"] is not None
    assert inventory["org_holding"]["canonical_writer"] is not None
    assert inventory["org_holding"]["runtime_state"] == "formal_only"
    assert inventory["stk_holdertrade"]["adapter"] == "tushare"
    assert inventory["stk_holdertrade"]["target_table"] == "raw_tushare_stk_holdertrade"
    assert inventory["stk_holdertrade"]["availability_axis"] == "ann_date"
    assert inventory["stk_holdertrade"]["landing_writer"] is not None
    assert inventory["stk_holdertrade"]["canonical_writer"] is not None
    assert inventory["stk_holdertrade"]["runtime_state"] == "formal_only"
    for item in inventory.values():
        assert item["conformity"] == "NONCONFORMING"
        assert item["population_kind"] == "raw_evidence"
        assert item["formal_write"] == "forbidden"
        assert item["runtime_state"] == "formal_only"
        assert item["legacy_mirror_default"] is False
        assert item["landing_writer"] is not None
        assert item["canonical_writer"] is not None


def test_nonconforming_direct_write_requires_test_escape() -> None:
    # Holders compat retired — escape hatch gone.
    with pytest.raises(DisclosureBoundaryError, match="holders_compat_retired"):
        authorize_nonconforming_direct_write(
            "holders_top10", conformity="NONCONFORMING", allow_test_escape=True
        )
    # Org still has test escape.
    with pytest.raises(
        DisclosureBoundaryError, match="naked_nonconforming_escape_retired"
    ):
        authorize_nonconforming_direct_write(
            "org_holding", conformity="NONCONFORMING"
        )
    permit = authorize_nonconforming_direct_write(
        "org_holding",
        conformity="NONCONFORMING",
        allow_test_escape=True,
    )
    assert permit.domain == "org_holding"
    assert permit.conformity == "NONCONFORMING"
    assert permit.publication == "nonconforming_direct_write"


def test_legacy_mirror_write_requires_test_escape() -> None:
    with pytest.raises(DisclosureBoundaryError, match="holders_compat_retired"):
        authorize_legacy_mirror_write("holders_top10", allow_test_escape=True)
    with pytest.raises(
        DisclosureBoundaryError, match="legacy_mirror_retired_from_default"
    ):
        authorize_legacy_mirror_write("org_holding")
    permit = authorize_legacy_mirror_write(
        "org_holding", allow_test_escape=True
    )
    assert permit.publication == "legacy_mirror_of_formal_accept"


def test_formal_conformity_claim_fails_closed_without_accepted_path() -> None:
    with pytest.raises(DisclosureBoundaryError, match="holders_compat_retired"):
        authorize_nonconforming_direct_write("holders_top10", conformity="ACCEPTED")
    with pytest.raises(
        DisclosureBoundaryError, match="formal_write_without_accepted_path"
    ):
        authorize_nonconforming_direct_write("org_holding", conformity="formal")
    with pytest.raises(
        DisclosureBoundaryError, match="formal_write_without_accepted_path"
    ):
        authorize_nonconforming_direct_write("stk_holdertrade", conformity="landing")


def test_accepted_publication_claims_fail_closed() -> None:
    # Path claims allowed; DatasetSnapshot blocked until cutover_allowed.
    for domain in ("holders_top10", "org_holding", "stk_holdertrade"):
        for claim in ("accepted", "canonical", "landing", "accepted_partition"):
            refuse_accepted_publication_claim(domain, claim)
        with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
            refuse_accepted_publication_claim(domain, "DatasetSnapshot")
        with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
            refuse_formal_disclosure_write_without_accepted_path(
                domain, publication_claim="DatasetSnapshot"
            )
        refuse_accepted_publication_claim(
            domain, "DatasetSnapshot", cutover_allowed=True
        )


def test_research_attestation_is_nonconforming_never_ready() -> None:
    report = attest_disclosure_research_surface()
    assert report.overall_status == "NONCONFORMING"
    assert report.cutover_allowed is False
    by_domain = {item.domain: item for item in report.domains}
    assert set(by_domain) == {"holders_top10", "org_holding", "stk_holdertrade"}
    for item in report.domains:
        assert item.status == "NONCONFORMING"
        assert item.population_kind == "raw_evidence"
        assert (
            "legacy" in item.reason
            or "formal_only" in item.reason
            or "cutover" in item.reason
        )
    assert "formal_only" in " ".join(report.notes)
    payload = report.as_dict()
    assert payload["overall_status"] == "NONCONFORMING"
    assert payload["cutover_allowed"] is False
    assert payload["e0_phase"] == "in_progress"


def test_unknown_domain_fails_closed() -> None:
    assert disclosure_boundary("daily") is None
    with pytest.raises(DisclosureBoundaryError, match="unknown_disclosure_domain"):
        authorize_nonconforming_direct_write(
            "daily", conformity="NONCONFORMING", allow_test_escape=True
        )
    with pytest.raises(DisclosureBoundaryError, match="unknown_disclosure_domain"):
        refuse_accepted_publication_claim("daily", "accepted")
