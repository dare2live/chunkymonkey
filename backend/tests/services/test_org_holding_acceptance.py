"""E0 tracer: org_holding land→validate→accept (fixture/memory; no mass fetch)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    authorize_nonconforming_direct_write,
    disclosure_inventory,
    refuse_accepted_publication_claim,
)
from services.data_sources.formal_execution import (
    FormalExecutionHandoffError,
    propagate_disclosure_execution_contract,
)
from services.data_sources.org_holding_acceptance import (
    OrgHoldingAcceptanceError,
    OrgHoldingLandingBatch,
    accept_org_holding_batch,
    land_org_holding_batch,
    publish_accepted_org_holding_partition,
)
from services.data_sources.org_holding_contract import load_org_holding_contract
from services.data_sources.org_holding_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    SOURCE,
)
from services.duck_adapter import connect

# Q1 2026 report → statutory available_date 2026-04-30
PARTITION = "20260430"
REPORT = "20260331"
OBSERVED = datetime(2026, 4, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)


def _row(**overrides):
    base = {
        "report_date": REPORT,
        "available_date": PARTITION,
        "stock_code": "600519",
        "holder_code": "10010626",
        "fund_derivecode": "",
        "holder_name": "香港中央结算有限公司",
        "org_type_name": "QFII",
        "total_shares": 1.2e8,
        "free_shares_ratio": 7.12,
    }
    base.update(overrides)
    return base


def _batch(batch_id: str, rows, *, available_at=OBSERVED, observed_at=OBSERVED):
    return OrgHoldingLandingBatch(
        batch_id=batch_id,
        partition_value=PARTITION,
        observed_at=observed_at,
        available_at=available_at,
        rows=rows,
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": PARTITION},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def test_inventory_declares_org_holding_formal_writers_strangler() -> None:
    inventory = {item["domain"]: item for item in disclosure_inventory()}
    item = inventory["org_holding"]
    assert item["landing_writer"] is not None
    assert item["canonical_writer"] is not None
    assert item["runtime_state"] == "formal_default_legacy_mirror"
    assert item["conformity"] == "NONCONFORMING"
    permit = authorize_nonconforming_direct_write(
        "org_holding",
        conformity="NONCONFORMING",
        allow_test_escape=True,
    )
    assert permit.publication == "nonconforming_direct_write"
    with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
        refuse_accepted_publication_claim("org_holding", "DatasetSnapshot")


def test_land_without_execution_handoff_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    with pytest.raises(OrgHoldingAcceptanceError, match="execution_handoff"):
        land_org_holding_batch(conn, _batch("org_holding:no-handoff", [_row()]), contract)


def test_missing_available_at_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    with pytest.raises(OrgHoldingAcceptanceError, match="available_at"):
        land_org_holding_batch(
            conn,
            _batch("org_holding:missing-avail", [_row()], available_at=None),
            handed,
            handoff=handed,
        )


def test_forged_available_at_before_available_date_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    forged = datetime(2026, 4, 29, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    batch = _batch(
        "org_holding:forged", [_row()], available_at=forged, observed_at=forged
    )
    land_org_holding_batch(conn, batch, handed, handoff=handed)
    outcome = accept_org_holding_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "FORGED_AVAILABLE_AT"


def test_forged_available_date_vs_disclosure_deadline_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    # Land under partition 20260430 but row claims mismatched deadline.
    bad = _row(available_date=PARTITION, report_date="20260630")  # deadline would be 0831
    batch = _batch("org_holding:bad-deadline", [bad])
    land_org_holding_batch(conn, batch, handed, handoff=handed)
    outcome = accept_org_holding_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "FORGED_AVAILABLE_DATE"


def test_publish_land_accept_roundtrip_fixture(conn) -> None:
    contract = load_org_holding_contract()
    rows = [
        _row(holder_code="10010626", holder_name="香港中央结算有限公司"),
        _row(holder_code="10020001", holder_name="某公募基金"),
    ]
    outcome = publish_accepted_org_holding_partition(
        conn,
        _batch("org_holding:ok", rows),
        contract,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 2
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


def test_disclosure_handoff_rejects_wrong_contract_type() -> None:
    from services.data_sources.holders_top10_contract import load_holders_top10_contract

    contract = load_holders_top10_contract()
    with pytest.raises(
        FormalExecutionHandoffError, match="OrgHoldingContract|mismatched"
    ):
        propagate_disclosure_execution_contract("org_holding", contract)
