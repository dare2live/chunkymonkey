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
    capture_and_publish_authorized_nominal_ohlcv_partition,
    publish_accepted_nominal_ohlcv_partition,
    runtime_surface as ohlcv_runtime_surface,
)
from services.data_sources.nominal_ohlcv_schema import (
    CONTRACT_VERSION,
    DATASET_ID,
    ENRICHMENT_FIELDS,
    NON_NULL_NUMERIC_FIELDS,
    NUMERIC_FIELDS,
    PROVIDER_FIELDS,
    SCHEMA_CONTRACT,
    SCHEMA_HASH,
    SCHEMA_VERSION,
)
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
from services.data_sources.stock_st_runtime import (
    capture_and_publish_authorized_stock_st_partition,
    publish_accepted_stock_st_partition,
    runtime_surface as st_runtime_surface,
)
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
DECISION = datetime(2027, 12, 31, 20, 0, tzinfo=timezone.utc)


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


def test_identity_promotion_bumped_both_versions() -> None:
    """改 canonical 形状必须同时抬 schema 与 contract 版本, 否则下游分不清两份数据。

    照 holders_top10 同名测试的形态。钉死的是**声明值**不是测量值: 它的作用正是逼
    "改形状的那个人"同时改这里, 从而意识到要抬版本 —— 改了形状忘了抬版本, 这条会红。
    """

    assert SCHEMA_VERSION == "2"
    assert CONTRACT_VERSION == "2"


def test_non_null_numeric_is_derived_from_payload_not_a_second_list() -> None:
    """non_null 清单必须由 payload 的 nullable 现算, 不许手写第二份。

    手写会让"schema 说可空"与"non_null 清单"两处各抄一份, 改一处漏一处就永久不等
    且没有任何东西会红 (holders_top10 的 _HASH_FIELDS 注释记的正是这个教训)。
    本条断言的是**派生关系**, 不是某个具体名单 —— 将来再改哪列可空都不用动它。
    """

    by_name = {str(f["name"]): f for f in SCHEMA_CONTRACT["fields"]}
    expected = tuple(n for n in NUMERIC_FIELDS if not bool(by_name[n]["nullable"]))
    assert NON_NULL_NUMERIC_FIELDS == expected
    # 且它必须真的比 NUMERIC_FIELDS 短 —— 等长说明没有任何一列可空, 那 v2 白改了
    assert set(NON_NULL_NUMERIC_FIELDS) < set(NUMERIC_FIELDS)


def test_v2_null_semantics_are_honest_for_the_three_derived_columns() -> None:
    """pre_close/change/pct_chg 可空, 且 null 语义不许退回 forbidden。

    v1 声明 forbidden 的代价实测过: 一行取不到 pre_close -> NULL_NUMERIC ->
    **整个交易日的分区被 REJECTED**, 那是缺失放大不是缺失传播 (红线 3)。
    """

    by_name = {str(f["name"]): f for f in SCHEMA_CONTRACT["fields"]}
    for name in ("pre_close", "change", "pct_chg"):
        assert by_name[name]["nullable"] is True, name
        assert by_name[name]["null_semantics"] != "forbidden", name
        assert "never_zero_fill" in str(by_name[name]["null_semantics"]), name


def test_enrichment_column_stays_out_of_provider_fields() -> None:
    """pre_close_origin 必须在 schema 里、但**不在** PROVIDER_FIELDS 里。

    canonical_content_hash 只按 provider_fields 算 —— 实测同一批行带不带本列算出的
    content_hash 逐位相同。一旦有人把它挪进 PROVIDER_FIELDS, 859 万行的 content_hash
    会全部改变, 两份冻结快照与 accepted_partition 指针同时失配, 而那是静默发生的。
    """

    declared = {str(f["name"]) for f in SCHEMA_CONTRACT["fields"]}
    assert ENRICHMENT_FIELDS == ("pre_close_origin",)
    assert set(ENRICHMENT_FIELDS) <= declared
    assert not (set(ENRICHMENT_FIELDS) & set(PROVIDER_FIELDS))


def test_provider_nan_normalizes_before_landing() -> None:
    from services.data_sources.nominal_ohlcv_schema import DOMAIN as OHLCV_DOMAIN
    from services.data_sources.security_day_capture import (
        project_security_day_provider_row,
    )

    row = dict(_daily_rows(PARTITION, include_bj=False)[0])
    row["change"] = float("nan")
    projected = project_security_day_provider_row(OHLCV_DOMAIN, row)
    assert projected["change"] is None


def test_capture_and_publish_authorized_paths_from_fetch(conn) -> None:
    ohlcv_contract = load_nominal_ohlcv_contract()
    st_contract = load_stock_st_contract()
    daily_rows = _daily_rows(PARTITION, include_bj=False)
    st_rows = _st_rows()

    def fetch_daily(request):
        assert request["trade_date"] == PARTITION
        return daily_rows

    def fetch_st(request):
        assert request["trade_date"] == ST_PARTITION
        return st_rows

    ohlcv_outcome = capture_and_publish_authorized_nominal_ohlcv_partition(
        conn,
        ohlcv_contract,
        trade_date=PARTITION,
        fetch_rows=fetch_daily,
        observed_at=OBSERVED,
        bootstrap=True,
    )
    assert ohlcv_outcome.status == "ACCEPTED"
    assert ohlcv_outcome.row_count == len(daily_rows)
    assert ohlcv_outcome.batch_id.startswith(f"daily:{PARTITION}:")

    st_outcome = capture_and_publish_authorized_stock_st_partition(
        conn,
        st_contract,
        trade_date=ST_PARTITION,
        fetch_rows=fetch_st,
        observed_at=ST_OBSERVED,
        bootstrap=True,
    )
    assert st_outcome.status == "ACCEPTED"
    assert st_outcome.row_count == len(st_rows)
    assert st_outcome.batch_id.startswith(f"stock_st:{ST_PARTITION}:")
    assert ohlcv_runtime_surface()["provider_sync"] == "authorized_manual_generation"
    assert st_runtime_surface()["provider_sync"] == "authorized_manual_generation"


def test_publish_accepts_partition_and_reader_returns_membership(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    rows = _daily_rows(PARTITION)
    outcome = publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            source=contract.source,
            contract_version=contract.contract_version,
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
            source=contract.source,
            contract_version=contract.contract_version,
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


def test_early_capture_stamps_contractual_available_at_and_accepts(conn) -> None:
    """Manual intraday fetch may land/accept; consumers still see available_at=18:00."""

    from services.data_sources.availability import publication_cutoff
    from services.data_sources.nominal_ohlcv_schema import DOMAIN
    from services.data_sources.security_day_capture import (
        build_security_day_landing_batch,
    )

    early = datetime(2023, 1, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    cutoff = publication_cutoff(
        DOMAIN.availability_policy,
        partition_value=PARTITION,
        trading_day_values=(PARTITION,),
    ).astimezone(timezone.utc)
    batch = build_security_day_landing_batch(
        DOMAIN,
        trade_date=PARTITION,
        rows=_daily_rows(PARTITION, include_bj=False),
        observed_at=early,
        batch_id="daily-early-contractual",
    )
    assert batch.observed_at == early
    assert batch.available_at == cutoff
    outcome = publish_accepted_nominal_ohlcv_partition(
        conn,
        batch,
        load_nominal_ohlcv_contract(),
        bootstrap=True,
    )
    assert outcome.status == "ACCEPTED"


def test_kill_point_after_canonical_delete_rolls_back(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            source=contract.source,
            contract_version=contract.contract_version,
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
            source=contract.source,
            contract_version=contract.contract_version,
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
            source=ohlcv_contract.source,
            contract_version=ohlcv_contract.contract_version,
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
            source=st_contract.source,
            contract_version=st_contract.contract_version,
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
        return _as_ref(part), part.ts_codes, part.active_ts_codes

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
    # 000001.SZ is in stock_st fixture but remains 沪深A whitelist member.
    assert "000001.SZ" in membership.ts_codes
    assert "830001.BJ" not in membership.ts_codes
    assert membership.st_member_count >= 1
    assert membership.excluded_board_count >= 1
    assert any(code.endswith((".SH", ".SZ")) for code in membership.ts_codes)
