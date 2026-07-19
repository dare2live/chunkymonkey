"""E0 tracer: stk_holdertrade land→validate→accept (fixture/memory; no mass fetch)."""
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
from services.data_sources.stk_holdertrade_acceptance import (
    StkHoldertradeAcceptanceError,
    StkHoldertradeLandingBatch,
    accept_stk_holdertrade_batch,
    land_stk_holdertrade_batch,
    publish_accepted_stk_holdertrade_partition,
)
from services.data_sources.stk_holdertrade_contract import load_stk_holdertrade_contract
from services.data_sources.stk_holdertrade_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    SOURCE,
)
from services.duck_adapter import connect

PARTITION = "20190102"
OBSERVED = datetime(2019, 1, 2, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)


def _row(**overrides):
    base = {
        "ts_code": "300010.SZ",
        "ann_date": PARTITION,
        "holder_name": "窦昕",
        "holder_type": "P",
        "in_de": "IN",
        "change_vol": 10076031,
        "change_ratio": 1.5963,
        "after_share": 10076031,
        "after_ratio": 1.5963,
        "avg_price": 14.77,
        "total_share": 10076031,
    }
    base.update(overrides)
    return base


def _batch(batch_id: str, rows, *, available_at=OBSERVED, observed_at=OBSERVED):
    return StkHoldertradeLandingBatch(
        batch_id=batch_id,
        partition_value=PARTITION,
        observed_at=observed_at,
        available_at=available_at,
        rows=rows,
        request={"api": "stk_holdertrade", "ann_date": PARTITION},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def test_inventory_declares_stk_holdertrade_formal_writers_strangler() -> None:
    inventory = {item["domain"]: item for item in disclosure_inventory()}
    item = inventory["stk_holdertrade"]
    assert item["landing_writer"] is not None
    assert item["canonical_writer"] is not None
    assert item["runtime_state"] == "formal_default_legacy_mirror"
    assert item["conformity"] == "NONCONFORMING"
    permit = authorize_nonconforming_direct_write(
        "stk_holdertrade",
        conformity="NONCONFORMING",
        allow_test_escape=True,
    )
    assert permit.publication == "nonconforming_direct_write"
    with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
        refuse_accepted_publication_claim("stk_holdertrade", "DatasetSnapshot")


def test_land_without_execution_handoff_fails_closed(conn) -> None:
    contract = load_stk_holdertrade_contract()
    with pytest.raises(StkHoldertradeAcceptanceError, match="execution_handoff"):
        land_stk_holdertrade_batch(
            conn, _batch("stk_holdertrade:no-handoff", [_row()]), contract
        )


def test_missing_available_at_fails_closed(conn) -> None:
    contract = load_stk_holdertrade_contract()
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    with pytest.raises(StkHoldertradeAcceptanceError, match="available_at"):
        land_stk_holdertrade_batch(
            conn,
            _batch("stk_holdertrade:missing-avail", [_row()], available_at=None),
            handed,
            handoff=handed,
        )


def test_forged_available_at_before_ann_date_fails_closed(conn) -> None:
    contract = load_stk_holdertrade_contract()
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    forged = datetime(2019, 1, 1, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    batch = _batch(
        "stk_holdertrade:forged", [_row()], available_at=forged, observed_at=forged
    )
    land_stk_holdertrade_batch(conn, batch, handed, handoff=handed)
    outcome = accept_stk_holdertrade_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "FORGED_AVAILABLE_AT"


def test_missing_ann_date_on_row_fails_closed(conn) -> None:
    contract = load_stk_holdertrade_contract()
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    batch = _batch("stk_holdertrade:null-ann", [_row(ann_date=None)])
    land_stk_holdertrade_batch(conn, batch, handed, handoff=handed)
    outcome = accept_stk_holdertrade_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "MISSING_ANN_DATE"


def test_duplicate_grain_fails_closed(conn) -> None:
    """Registry grain dedups same-direction multiples; formal path rejects dupes."""

    contract = load_stk_holdertrade_contract()
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    rows = [_row(change_vol=1), _row(change_vol=2)]  # same grain
    batch = _batch("stk_holdertrade:dup", rows)
    land_stk_holdertrade_batch(conn, batch, handed, handoff=handed)
    outcome = accept_stk_holdertrade_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "DUPLICATE_GRAIN"


def test_publish_land_accept_roundtrip_fixture(conn) -> None:
    contract = load_stk_holdertrade_contract()
    rows = [
        _row(holder_name="窦昕", in_de="IN"),
        _row(holder_name="窦昕", in_de="DE", change_vol=5000),
        _row(holder_name="其他股东", in_de="IN", change_vol=9000),
    ]
    outcome = publish_accepted_stk_holdertrade_partition(
        conn,
        _batch("stk_holdertrade:ok", rows),
        contract,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 3
    landed = conn.execute(
        f"SELECT COUNT(*) FROM {LANDING_TABLE} WHERE batch_id = ?",
        [outcome.batch_id],
    ).fetchone()[0]
    assert landed == 3
    canonical = conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0]
    assert canonical == 3
    pointer = conn.execute(
        f"""
        SELECT dataset_id, partition_value, row_count
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
        """,
        [DATASET_ID],
    ).fetchone()
    assert tuple(pointer) == (DATASET_ID, PARTITION, 3)
    status = conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [outcome.batch_id],
    ).fetchone()[0]
    assert status == "ACCEPTED"


def test_disclosure_handoff_rejects_wrong_contract_type() -> None:
    from services.data_sources.holders_top10_contract import load_holders_top10_contract

    contract = load_holders_top10_contract()
    with pytest.raises(
        FormalExecutionHandoffError, match="StkHoldertradeContract|mismatched"
    ):
        propagate_disclosure_execution_contract("stk_holdertrade", contract)
