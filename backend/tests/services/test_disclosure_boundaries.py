"""E0 adversarial tests: disclosure domain strangler + fail-closed formal claims."""
from __future__ import annotations

import pytest

from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    attest_disclosure_research_surface,
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
    assert inventory["holders_top10"]["target_table"] == "fact_top10_holder_period"
    assert inventory["holders_top10"]["availability_axis"] == "notice_date"
    assert inventory["org_holding"]["adapter"] == "miaoxiang"
    assert inventory["org_holding"]["target_table"] == "raw_org_holding_aif10"
    assert inventory["org_holding"]["availability_axis"] == "available_date"
    assert inventory["stk_holdertrade"]["adapter"] == "tushare"
    assert inventory["stk_holdertrade"]["target_table"] == "raw_tushare_stk_holdertrade"
    assert inventory["stk_holdertrade"]["availability_axis"] == "ann_date"
    for item in inventory.values():
        assert item["conformity"] == "NONCONFORMING"
        assert item["population_kind"] == "raw_evidence"
        assert item["landing_writer"] is None
        assert item["canonical_writer"] is None
        assert item["formal_write"] == "forbidden"
        assert item["runtime_state"] == "direct_write_strangler"


def test_nonconforming_direct_write_is_authorized_with_explicit_label() -> None:
    permit = authorize_nonconforming_direct_write(
        "holders_top10", conformity="NONCONFORMING"
    )
    assert permit.domain == "holders_top10"
    assert permit.conformity == "NONCONFORMING"
    assert permit.target_table == "fact_top10_holder_period"
    assert permit.publication == "nonconforming_direct_write"


def test_formal_conformity_claim_fails_closed_without_accepted_path() -> None:
    with pytest.raises(
        DisclosureBoundaryError, match="formal_write_without_accepted_path"
    ):
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
    for claim in (
        "accepted",
        "canonical",
        "landing",
        "DatasetSnapshot",
        "accepted_partition",
    ):
        with pytest.raises(
            DisclosureBoundaryError, match="accepted_claim_without_formal_path"
        ):
            refuse_accepted_publication_claim("holders_top10", claim)
        with pytest.raises(
            DisclosureBoundaryError, match="accepted_claim_without_formal_path"
        ):
            refuse_formal_disclosure_write_without_accepted_path(
                "org_holding", publication_claim=claim
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
        assert "landing" in item.reason or "accepted" in item.reason
    payload = report.as_dict()
    assert payload["overall_status"] == "NONCONFORMING"
    assert payload["cutover_allowed"] is False
    assert payload["e0_phase"] == "in_progress"


def test_unknown_domain_fails_closed() -> None:
    assert disclosure_boundary("daily") is None
    with pytest.raises(DisclosureBoundaryError, match="unknown_disclosure_domain"):
        authorize_nonconforming_direct_write("daily", conformity="NONCONFORMING")
    with pytest.raises(DisclosureBoundaryError, match="unknown_disclosure_domain"):
        refuse_accepted_publication_claim("daily", "accepted")
