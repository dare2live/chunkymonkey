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
    assert item["runtime_state"] == "formal_only"
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


def test_available_date_before_report_period_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    bad = _row(available_date=PARTITION, report_date="20260630")
    batch = _batch("org_holding:before-report", [bad])
    land_org_holding_batch(conn, batch, handed, handoff=handed)
    outcome = accept_org_holding_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "AVAILABLE_BEFORE_REPORT"


def test_announcement_before_statutory_deadline_accepts(conn) -> None:
    """Q1 filed 4/15: known-at is 20260415, not 20260430."""
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    early = "20260415"
    observed = datetime(2026, 4, 15, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    batch = OrgHoldingLandingBatch(
        batch_id="org_holding:early",
        partition_value=early,
        observed_at=observed,
        available_at=observed,
        rows=[_row(available_date=early)],
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": early},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )
    outcome = publish_accepted_org_holding_partition(conn, batch, handed)
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 1
    stored = conn.execute(
        f"SELECT available_date FROM {CANONICAL_TABLE}"
    ).fetchone()[0]
    assert str(stored).replace("-", "")[:8] == early


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


def test_accept_merge_preserves_sibling_report_date_in_shared_partition(conn) -> None:
    """2018-12-31 and 2019-03-31 share available_date=20190430 — merge by report_date."""
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    shared = "20190430"
    observed = datetime(2019, 4, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    q1 = _row(
        report_date="20190331",
        available_date=shared,
        holder_code="10010626",
    )
    batch_q1 = OrgHoldingLandingBatch(
        batch_id="org_holding:q1",
        partition_value=shared,
        observed_at=observed,
        available_at=observed,
        rows=[q1],
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": shared},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )
    out1 = publish_accepted_org_holding_partition(conn, batch_q1, handed)
    assert out1.status == "ACCEPTED"
    annual = _row(
        report_date="20181231",
        available_date=shared,
        holder_code="10020001",
        holder_name="年报机构",
    )
    batch_annual = OrgHoldingLandingBatch(
        batch_id="org_holding:annual",
        partition_value=shared,
        observed_at=observed,
        available_at=observed,
        rows=[annual],
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": shared},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )
    out2 = publish_accepted_org_holding_partition(conn, batch_annual, handed)
    assert out2.status == "ACCEPTED"
    rows = conn.execute(
        f"SELECT report_date, holder_code FROM {CANONICAL_TABLE} ORDER BY 1, 2"
    ).fetchall()
    assert len(rows) == 2
    assert {str(r[0]) for r in rows} == {"20181231", "20190331"}
    # Class-A fix: accepted pointer must describe the merged partition, not last batch.
    pointer = conn.execute(
        f"""
        SELECT row_count, content_hash
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [DATASET_ID, shared],
    ).fetchone()
    assert pointer is not None
    assert int(pointer[0]) == 2
    batch_q1_hash = conn.execute(
        f"SELECT canonical_hash, canonical_row_count FROM {INGEST_BATCH_TABLE} "
        f"WHERE batch_id = ?",
        ["org_holding:q1"],
    ).fetchone()
    batch_annual_hash = conn.execute(
        f"SELECT canonical_hash, canonical_row_count FROM {INGEST_BATCH_TABLE} "
        f"WHERE batch_id = ?",
        ["org_holding:annual"],
    ).fetchone()
    assert int(batch_q1_hash[1]) == 1
    assert int(batch_annual_hash[1]) == 1
    assert str(pointer[1]) != str(batch_annual_hash[0])
    assert str(pointer[1]) != str(batch_q1_hash[0])

    # Retrying an older accepted batch remains idempotent after a sibling
    # advances the partition-scoped pointer. The outcome is batch-scoped.
    retried = accept_org_holding_batch(conn, batch_q1.batch_id, handed, handoff=handed)
    assert retried.status == "ACCEPTED"
    assert retried.row_count == 1
    assert retried.content_hash == str(batch_q1_hash[0])


def test_disclosure_handoff_rejects_wrong_contract_type() -> None:
    from services.data_sources.holders_top10_contract import load_holders_top10_contract

    contract = load_holders_top10_contract()
    with pytest.raises(
        FormalExecutionHandoffError, match="OrgHoldingContract|mismatched"
    ):
        propagate_disclosure_execution_contract("org_holding", contract)
