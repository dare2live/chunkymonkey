"""E0 tracer: holders_top10 land→validate→accept (fixture/memory; no mass fetch)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    attest_disclosure_research_surface,
    authorize_nonconforming_direct_write,
    disclosure_inventory,
    refuse_accepted_publication_claim,
)
from services.data_sources.formal_execution import (
    FormalExecutionHandoffError,
    propagate_disclosure_execution_contract,
)
from services.data_sources.holders_top10_acceptance import (
    HoldersTop10AcceptanceError,
    HoldersTop10LandingBatch,
    accept_holders_top10_batch,
    land_holders_top10_batch,
    publish_accepted_holders_top10_partition,
)
from services.data_sources.holders_top10_contract import load_holders_top10_contract
from services.data_sources.holders_top10_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
)
from services.duck_adapter import connect

PARTITION = "20260429"
OBSERVED = datetime(2026, 4, 29, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)


def _row(**overrides):
    base = {
        "stock_code": "600519",
        "report_date": "20260331",
        "holder_set": "free",
        "holder_rank": 1,
        "row_seq": 1,
        "holder_name": "香港中央结算有限公司",
        "hold_ratio_float": 7.12,
        "notice_date": PARTITION,
        "is_exit_row": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def test_inventory_declares_holders_formal_writers_strangler() -> None:
    inventory = {item["domain"]: item for item in disclosure_inventory()}
    holders = inventory["holders_top10"]
    assert holders["landing_writer"] is not None
    assert holders["canonical_writer"] is not None
    assert holders["runtime_state"] == "formal_only"
    assert holders["conformity"] == "NONCONFORMING"
    # Naked legacy direct write is test-escape only.
    permit = authorize_nonconforming_direct_write(
        "holders_top10",
        conformity="NONCONFORMING",
        allow_test_escape=True,
    )
    assert permit.publication == "nonconforming_direct_write"
    # DatasetSnapshot freeze remains blocked without cutover_allowed.
    with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
        refuse_accepted_publication_claim("holders_top10", "DatasetSnapshot")
    report = attest_disclosure_research_surface()
    assert report.overall_status == "NONCONFORMING"
    assert report.cutover_allowed is False


def test_land_without_execution_handoff_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:test",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        rows=[_row()],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    with pytest.raises(HoldersTop10AcceptanceError, match="execution_handoff"):
        land_holders_top10_batch(conn, batch, contract)


def test_missing_available_at_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:missing-avail",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=None,  # type: ignore[arg-type]
        rows=[_row()],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    with pytest.raises(HoldersTop10AcceptanceError, match="available_at"):
        land_holders_top10_batch(conn, batch, handed, handoff=handed)


def test_forged_available_at_before_notice_date_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    forged = datetime(2026, 4, 28, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:forged",
        partition_value=PARTITION,
        observed_at=forged,
        available_at=forged,
        rows=[_row()],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    outcome = accept_holders_top10_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "FORGED_AVAILABLE_AT"


def test_missing_notice_date_on_row_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:null-notice",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        rows=[_row(notice_date=None)],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    outcome = accept_holders_top10_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "MISSING_NOTICE_DATE"


def test_publish_land_accept_roundtrip_fixture(conn) -> None:
    contract = load_holders_top10_contract()
    rows = [
        _row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    outcome = publish_accepted_holders_top10_partition(
        conn,
        HoldersTop10LandingBatch(
            batch_id=f"holders_top10:{PARTITION}:ok",
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=rows,
            request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
        ),
        contract,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 2
    assert outcome.batch_id.startswith("holders_top10:")
    landed = conn.execute(
        f"SELECT COUNT(*) FROM {LANDING_TABLE} WHERE batch_id = ?",
        [outcome.batch_id],
    ).fetchone()[0]
    assert landed == 2
    canonical = conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0]
    assert canonical == 2
    pointer = conn.execute(
        f"""
        SELECT dataset_id, partition_value, row_count
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
        """,
        [DATASET_ID],
    ).fetchone()
    assert tuple(pointer) == (DATASET_ID, PARTITION, 2)
    status = conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [outcome.batch_id],
    ).fetchone()[0]
    assert status == "ACCEPTED"


def test_disclosure_handoff_rejects_wrong_contract_for_other_domain() -> None:
    contract = load_holders_top10_contract()
    with pytest.raises(
        FormalExecutionHandoffError, match="OrgHoldingContract|mismatched"
    ):
        propagate_disclosure_execution_contract("org_holding", contract)
    with pytest.raises(
        FormalExecutionHandoffError, match="no disclosure execution consumer"
    ):
        propagate_disclosure_execution_contract("not_a_disclosure_domain", contract)
