"""``restamp_nominal_ohlcv_contract.py`` — v2 stale-plan + ``--to-v1`` rollback.

断言7 (刀1 spec 修订6, 因为原断言依赖生产库、worktree 做不了): 用 fixture 建一个
v2 形状的临时 DuckDB, ``plan()`` 得到 ``pointer_stale=canonical_stale=0``；
``plan_to_v1()`` 算出的三个目标常量 (schema/config/contract hash) 与
``nominal_ohlcv_contract_versions.yaml`` 里烤好的 v1 值完全一致；插入一行 NULL
(pre_close_origin=unknown, pre_close IS NULL —— 一个合法的 v2 行) 之后
``plan_to_v1()`` 变为不可执行, ``execute_to_v1()`` 拒绝执行并报错提到"必须先删除
含 NULL 的分区"。

全部用内存 DuckDB (``services.duck_adapter.connect(':memory:')``), 不打开
``data/*.duckdb`` (project rule: 测试不 mock 掉 calendar/universe/population 门,
但这里根本不涉及它们 —— 这是纯 land→accept + restamp 的机械测试)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.restamp_nominal_ohlcv_contract import (
    RestampMismatchError,
    execute_to_v1,
    format_plan_to_v1,
    plan,
    plan_to_v1,
)
from services.data_sources.nominal_ohlcv_contract import load_nominal_ohlcv_contract
from services.data_sources.nominal_ohlcv_contract_versions import load_rollback_target
from services.data_sources.nominal_ohlcv_runtime import (
    publish_accepted_nominal_ohlcv_partition,
)
from services.data_sources.security_day_partition import SecurityDayLandingBatch
from services.duck_adapter import connect

_DAILY = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "domain_samples" / "daily.json").read_text(
        encoding="utf-8"
    )
)
_PARTITION = "20230103"
_OBSERVED = datetime(2023, 1, 3, 18, 5, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def _clean_rows(partition: str) -> list[dict]:
    rows = [dict(row) for row in _DAILY["rows"]]
    for row in rows:
        row["trade_date"] = partition
    return rows


def _publish_clean_partition(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            source=contract.source,
            contract_version=contract.contract_version,
            batch_id="restamp-fixture-clean",
            partition_value=_PARTITION,
            observed_at=_OBSERVED,
            available_at=_OBSERVED,
            rows=_clean_rows(_PARTITION),
            request={"api": "daily", "trade_date": _PARTITION},
        ),
        contract,
        bootstrap=True,
    )


def _publish_unknown_null_partition(conn) -> None:
    """一个合法的 v2 行: kind=unknown, pre_close IS NULL —— 触发回退窗口关闭。"""

    contract = load_nominal_ohlcv_contract()
    other_partition = "20230104"
    other_observed = datetime(
        2023, 1, 4, 18, 5, tzinfo=ZoneInfo("Asia/Shanghai")
    ).astimezone(timezone.utc)
    rows = _clean_rows(other_partition)
    rows[0]["pre_close"] = None
    rows[0]["change"] = None
    rows[0]["pct_chg"] = None
    rows[0]["pre_close_origin"] = "unknown_reference_unavailable"
    outcome = publish_accepted_nominal_ohlcv_partition(
        conn,
        SecurityDayLandingBatch(
            source=contract.source,
            contract_version=contract.contract_version,
            batch_id="restamp-fixture-with-null",
            partition_value=other_partition,
            observed_at=other_observed,
            available_at=other_observed,
            rows=rows,
            request={"api": "daily", "trade_date": other_partition},
        ),
        contract,
    )
    assert outcome.status == "ACCEPTED", outcome.rejection_code


# ---------------------------------------------------------------------------
# plan() — v2 stale counts
# ---------------------------------------------------------------------------


def test_plan_stale_is_zero_after_fresh_v2_publish(conn) -> None:
    _publish_clean_partition(conn)
    p = plan(conn)
    assert p.pointer_stale == 0
    assert p.canonical_stale == 0
    assert p.pointer_rows == 1
    assert p.canonical_rows == len(_DAILY["rows"])


# ---------------------------------------------------------------------------
# plan_to_v1() — target constants correct, before any NULL row exists
# ---------------------------------------------------------------------------


def test_plan_to_v1_dry_run_prints_correct_target_constants(conn) -> None:
    _publish_clean_partition(conn)
    rp = plan_to_v1(conn)
    target = load_rollback_target("1")
    assert rp.target_schema_hash == target.schema_hash
    assert rp.target_config_hash == target.config_hash
    assert rp.target_contract_hash == target.contract_hash
    assert rp.target_schema_hash.startswith("fd84a583")
    assert rp.target_config_hash.startswith("21d86185")
    assert rp.target_contract_hash.startswith("a25c126e")
    assert rp.executable is True
    assert rp.null_row_count == 0

    rendered = format_plan_to_v1(rp)
    assert rp.target_schema_hash in rendered
    assert rp.target_config_hash in rendered
    assert rp.target_contract_hash in rendered
    assert "无法执行" not in rendered


# ---------------------------------------------------------------------------
# NULL row closes the rollback window
# ---------------------------------------------------------------------------


def test_to_v1_rejects_once_a_null_row_exists(conn) -> None:
    _publish_clean_partition(conn)
    _publish_unknown_null_partition(conn)

    rp = plan_to_v1(conn)
    assert rp.null_row_count >= 1
    assert rp.executable is False

    rendered = format_plan_to_v1(rp)
    assert "必须先删除含 NULL 的分区" in rendered

    with pytest.raises(RestampMismatchError, match="必须先删除含 NULL 的分区"):
        execute_to_v1(conn, rp)
