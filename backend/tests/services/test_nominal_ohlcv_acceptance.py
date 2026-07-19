"""A3 residual: nominal OHLCV + ST land→accept→reader adversarial tests."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.nominal_ohlcv_contract import load_nominal_ohlcv_contract
from services.data_sources.nominal_ohlcv_reader import (
    NominalOhlcvTruthUnavailable,
    load_accepted_nominal_ohlcv_membership_from_conn,
)
from services.data_sources.nominal_ohlcv_runtime import (
    publish_accepted_nominal_ohlcv_partition,
)
from services.data_sources.nominal_ohlcv_schema import DATASET_ID, SCHEMA_HASH
from services.data_sources.observation_population import (
    NOMINAL_KLINE_DATASET_ID,
    AcceptedPartitionRef,
    resolve_traded_on_observation_date,
)
from services.data_sources.security_day_partition import SecurityDayLandingBatch
from services.data_sources.stock_st_contract import load_stock_st_contract
from services.data_sources.stock_st_reader import (
    load_accepted_stock_st_membership_from_conn,
)
from services.data_sources.stock_st_runtime import publish_accepted_stock_st_partition
from services.data_sources.stock_st_schema import DATASET_ID as ST_DATASET_ID
from services.duck_adapter import connect
from services.universe import load_universe_policy


_DAILY = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "domain_samples" / "daily.json").read_text(
        encoding="utf-8"
    )
)
_ST = json.loads(
    (
        Path(__file__).parents[1] / "fixtures" / "domain_samples" / "stock_st.json"
    ).read_text(encoding="utf-8")
)
PARTITION = "20230103"
ST_PARTITION = "20220104"
OBSERVED = datetime(2023, 1, 3, 18, 5, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)
ST_OBSERVED = datetime(2022, 1, 4, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)
OHLCV_ON_ST_DAY = datetime(
    2022, 1, 4, 18, 5, tzinfo=ZoneInfo("Asia/Shanghai")
).astimezone(timezone.utc)
# Decision time must be >= accepted_at (wall-clock at publish).  Historical
# partition dates remain the event grain; visibility is acceptance/PIT time.
DECISION = datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def _daily_rows(partition: str, *, include_bj: bool = True) -> list[dict]:
    rows = [dict(row) for row in _DAILY["rows"]]
    for row in rows:
        row["trade_date"] = partition
    if include_bj:
        sample = dict(rows[0])
        sample["ts_code"] = "830001.BJ"
        rows.append(sample)
    return rows


def _st_rows() -> list[dict]:
    rows = [dict(row) for row in _ST["rows"]]
    for row in rows:
        row["trade_date"] = ST_PARTITION
    rows[0]["ts_code"] = "000001.SZ"
    return rows


def _as_ref(part) -> AcceptedPartitionRef:
    return AcceptedPartitionRef(
        dataset_id=part.dataset_id,
        partition_value=part.partition_value,
        batch_id=part.batch_id,
        contract_hash=part.contract_hash,
        config_hash=part.config_hash,
        content_hash=part.content_hash,
        row_count=part.row_count,
        available_at=part.available_at,
        accepted_at=part.accepted_at,
    )


def test_contract_factory_binds_schema_hash() -> None:
    contract = load_nominal_ohlcv_contract()
    assert contract.dataset_id == DATASET_ID
    assert contract.schema_hash == SCHEMA_HASH
    assert contract.availability.payload() == {
        "axis": "trading_day",
        "rule": "same_day_at",
        "at": "18:00",
    }


def test_publish_accepts_partition_and_reader_returns_membership(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    rows = _daily_rows(PARTITION)
    outcome = publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            batch_id="daily-batch-1",
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=rows,
            request={"api": "daily", "trade_date": PARTITION},
        ),
        contract,
        bootstrap=True,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == len(rows)

    part = load_accepted_nominal_ohlcv_membership_from_conn(
        conn, date(2023, 1, 3), DECISION
    )
    assert part.dataset_id == NOMINAL_KLINE_DATASET_ID
    assert "000001.SZ" in part.ts_codes
    assert "830001.BJ" in part.ts_codes


def test_premature_publication_is_rejected(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    early = datetime(2023, 1, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    outcome = publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            batch_id="daily-early",
            partition_value=PARTITION,
            observed_at=early,
            available_at=early,
            rows=_daily_rows(PARTITION, include_bj=False),
            request={"api": "daily", "trade_date": PARTITION},
        ),
        contract,
        bootstrap=True,
    )
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "PREMATURE_PUBLICATION"


def test_kill_point_after_canonical_delete_rolls_back(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            batch_id="daily-seed",
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=_daily_rows(PARTITION, include_bj=False)[:2],
            request={"api": "daily", "trade_date": PARTITION},
        ),
        contract,
        bootstrap=True,
    )
    from services.data_sources.nominal_ohlcv_acceptance import (
        accept_nominal_ohlcv_batch,
        land_nominal_ohlcv_batch,
    )

    land_nominal_ohlcv_batch(
        conn,
        SecurityDayLandingBatch(
            batch_id="daily-kill",
            partition_value=PARTITION,
            observed_at=OBSERVED.replace(minute=10),
            available_at=OBSERVED.replace(minute=10),
            rows=_daily_rows(PARTITION, include_bj=False),
            request={"api": "daily", "trade_date": PARTITION},
        ),
        contract,
    )

    def boom(step: str) -> None:
        if step == "after_canonical_delete":
            raise RuntimeError("kill-point")

    with pytest.raises(RuntimeError, match="kill-point"):
        accept_nominal_ohlcv_batch(conn, "daily-kill", contract, after_step=boom)

    status = conn.execute(
        "SELECT status FROM ingest_batch WHERE batch_id = ?",
        ["daily-kill"],
    ).fetchone()[0]
    assert status == "LANDED"
    pointer = conn.execute(
        "SELECT batch_id FROM accepted_partition WHERE dataset_id = ? AND partition_value = ?",
        [DATASET_ID, PARTITION],
    ).fetchone()
    assert pointer[0] == "daily-seed"


def test_reader_fail_closed_without_partition(conn) -> None:
    from services.data_sources.nominal_ohlcv_acceptance import (
        ensure_nominal_ohlcv_acceptance_schema,
    )

    ensure_nominal_ohlcv_acceptance_schema(conn)
    with pytest.raises(NominalOhlcvTruthUnavailable) as caught:
        load_accepted_nominal_ohlcv_membership_from_conn(
            conn, date(2023, 1, 3), DECISION
        )
    assert caught.value.status == "NOT_EVALUATED"
    assert "no_accepted_partition" in caught.value.reason


def test_stock_st_and_ohlcv_resolver_end_to_end(conn) -> None:
    ohlcv_contract = load_nominal_ohlcv_contract()
    st_contract = load_stock_st_contract()
    assert st_contract.dataset_id == ST_DATASET_ID

    ohlcv_outcome = publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            batch_id="ohlcv-st-day",
            partition_value=ST_PARTITION,
            observed_at=OHLCV_ON_ST_DAY,
            available_at=OHLCV_ON_ST_DAY,
            rows=_daily_rows(ST_PARTITION, include_bj=True),
            request={"api": "daily", "trade_date": ST_PARTITION},
        ),
        ohlcv_contract,
        bootstrap=True,
    )
    assert ohlcv_outcome.status == "ACCEPTED"

    st_outcome = publish_accepted_stock_st_partition(
        conn,
        SecurityDayLandingBatch(
            batch_id="st-batch-1",
            partition_value=ST_PARTITION,
            observed_at=ST_OBSERVED,
            available_at=ST_OBSERVED,
            rows=_st_rows(),
            request={"api": "stock_st", "trade_date": ST_PARTITION},
        ),
        st_contract,
        bootstrap=True,
    )
    assert st_outcome.status == "ACCEPTED"

    policy = load_universe_policy()
    day = date(2022, 1, 4)
    decision = DECISION

    def calendar_loader(*_):
        evidence = type(
            "E",
            (),
            {
                "generation_id": "cal-1",
                "content_hash": "d" * 64,
                "usable_at": decision.replace(hour=0),
            },
        )()

        class Truth:
            def is_open(self, value):
                return True

        truth = Truth()
        truth.evidence = evidence
        return truth

    def kline_loader(observation_date, decision_time, _policy):
        part = load_accepted_nominal_ohlcv_membership_from_conn(
            conn, observation_date, decision_time
        )
        return _as_ref(part), part.ts_codes

    def st_loader(observation_date, decision_time, _policy):
        part = load_accepted_stock_st_membership_from_conn(
            conn, observation_date, decision_time
        )
        return _as_ref(part), part.ts_codes

    membership = resolve_traded_on_observation_date(
        day,
        decision,
        policy,
        calendar_loader=calendar_loader,
        nominal_kline_loader=kline_loader,
        st_membership_loader=st_loader,
    )
    assert "000001.SZ" not in membership.ts_codes
    assert "830001.BJ" not in membership.ts_codes
    assert membership.excluded_st_count >= 1
    assert membership.excluded_board_count >= 1
    assert any(code.endswith((".SH", ".SZ")) for code in membership.ts_codes)
