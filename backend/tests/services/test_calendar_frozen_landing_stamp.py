"""冻结落地戳 vs 活契约 — 日历真相读边界 (2026-09-02 活体故障的回归锁).

故障: 2c4af4a08 把 source/api 移出 config_hash 后, accepted_partition / canonical_* 跟着当前契约
重打了戳, 而 ingest_batch 的 contract_hash/config_hash/source_name 是落地时刻的证据封印
(payload_hash 从它们派生), 有意停在旧值。calendar_reader / calendar_acceptance 仍把
"批次戳 == 指针戳 / == 现算契约戳 / source_name == contract.source" 当不变量断言, 于是活库上
open_calendar_truth() 永久 BLOCKED, 连带 resolve_traded_on_observation_date /
evaluate_observation_population_readiness 一起断; 而 calendar_builder 不走 reader, 所以
dim_trading_calendar 照常生成 —— 故障是静默的。现有 test_calendar_reader.py 全绿是因为它
land+accept 都用同一份契约, 两侧天然相等, 覆盖不到"重打之后"这个状态。

本文件自带 fixture (临时 DuckDB 文件 + 真 land/accept 路径), 不读 data/*.duckdb, 不断言宿主状态。
正向用例在 2026-09-02 修复前是红的 (fields=['config_hash','contract_hash','source_name'] /
fields=['source_name']); 负向控制证明修复没有顺手放松仍该守的东西:
指针 vs 活契约 / 封印自洽 / 声明身份 (contract_version, writer_id)。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from services.data_sources import calendar_acceptance, calendar_landing, calendar_reader
from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.calendar_acceptance import (
    CalendarFragmentCapture,
    CalendarLandingBatch,
    accept_calendar_batch,
    land_calendar_batch,
)
from services.data_sources.calendar_contract import calendar_contract_for_spec
from services.data_sources.calendar_reader import (
    CalendarTruthUnavailable,
    open_calendar_truth,
)
from services.data_sources.calendar_schema import (
    DATASET_ID,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    ensure_calendar_acceptance_schema,
)
from services.data_sources.stamp_checks import _recompute_payload_hash_calendar
from services.data_sources.stamp_types import DOMAIN_BY_DATASET_ID
from services.data_sources.sync_runner import domain_spec, load_registry
from services.duck_adapter import connect

UTC = timezone.utc
START = date(1990, 12, 19)
OBSERVED_AT = datetime(1990, 12, 19, 8, 0, tzinfo=UTC)
FIRST_ACCEPTED = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
DECISION = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
BATCH_ID = "frozen-stamp-generation-1990"

STALE_CONTRACT_HASH = "1" * 64
STALE_CONFIG_HASH = "2" * 64
STALE_SOURCE = "retired_vendor"

_STAMP_ROW_FIELDS = (
    "batch_id",
    "contract_version",
    "contract_hash",
    "config_hash",
    "writer_id",
    "source_name",
    "observed_at",
    "payload_hash",
)


def _contract_and_registry():
    registry = load_registry()
    return calendar_contract_for_spec(domain_spec(registry, "trade_cal")), registry


def _real_provider_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous_open: date | None = None
    current = START
    while current <= date(1990, 12, 31):
        is_open = int(current.weekday() < 5)
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": is_open,
                "pretrade_date": previous_open.strftime("%Y%m%d") if previous_open else None,
            }
        )
        if is_open:
            previous_open = current
        current += timedelta(days=1)
    return rows


def _land_and_accept(path: Path, monkeypatch) -> None:
    contract, _registry = _contract_and_registry()
    conn = connect(str(path))
    try:
        ensure_calendar_acceptance_schema(conn)
        landed_at = FIRST_ACCEPTED - timedelta(minutes=30)
        monkeypatch.setattr(calendar_landing, "_now_utc", lambda: landed_at)
        monkeypatch.setattr(calendar_acceptance, "_now_utc", lambda: FIRST_ACCEPTED)
        land_calendar_batch(
            conn,
            CalendarLandingBatch(
                batch_id=BATCH_ID,
                observed_at=OBSERVED_AT,
                fragments=(
                    CalendarFragmentCapture(
                        fragment_ordinal=0,
                        request=contract.request_for_page(OBSERVED_AT, 0),
                        rows=_real_provider_rows(),
                        outcome="completed",
                        completed_at=OBSERVED_AT,
                    ),
                ),
            ),
            contract,
        )
        outcome = accept_calendar_batch(conn, BATCH_ID, contract)
        assert outcome.status == "ACCEPTED"
        assert outcome.row_count == 13
    finally:
        conn.close()


def _patch_live(monkeypatch, path: Path) -> None:
    _contract, registry = _contract_and_registry()
    monkeypatch.setattr(
        calendar_reader, "_load_live_registry_snapshot", lambda: deepcopy(registry)
    )
    monkeypatch.setattr(
        calendar_reader,
        "_open_live_tushare_raw_readonly",
        lambda: connect(str(path), read_only=True),
    )


def _stamp_row(conn) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT {', '.join(_STAMP_ROW_FIELDS)} FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [BATCH_ID],
    ).fetchone()
    assert row is not None
    return dict(zip(_STAMP_ROW_FIELDS, row, strict=True))


def _reseal(conn) -> str:
    """用产线同一套公式 (stamp_checks 的 calendar 家族) 按批次自己的戳重算封印。"""
    domain = DOMAIN_BY_DATASET_ID[DATASET_ID]
    assert domain.calendar_landing_table == LANDING_TABLE
    assert domain.calendar_fragment_table == FRAGMENT_TABLE
    recomputed, error = _recompute_payload_hash_calendar(conn, domain, _stamp_row(conn))
    assert error is None, error
    assert recomputed is not None
    return recomputed


def _make_stamps_stale(path: Path, *, hashes: bool = True, source: bool = True, reseal: bool = True) -> None:
    conn = connect(str(path))
    try:
        if hashes:
            conn.execute(
                f"UPDATE {INGEST_BATCH_TABLE} SET contract_hash = ?, config_hash = ? WHERE batch_id = ?",
                [STALE_CONTRACT_HASH, STALE_CONFIG_HASH, BATCH_ID],
            )
        if source:
            conn.execute(
                f"UPDATE {INGEST_BATCH_TABLE} SET source_name = ? WHERE batch_id = ?",
                [STALE_SOURCE, BATCH_ID],
            )
        if reseal:
            conn.execute(
                f"UPDATE {INGEST_BATCH_TABLE} SET payload_hash = ? WHERE batch_id = ?",
                [_reseal(conn), BATCH_ID],
            )
    finally:
        conn.close()


def _blocked_reason(path: Path, monkeypatch) -> str:
    _patch_live(monkeypatch, path)
    with pytest.raises(CalendarTruthUnavailable) as excinfo:
        open_calendar_truth(DECISION)
    assert excinfo.value.status == "BLOCKED"
    return excinfo.value.reason


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "calendar.duckdb"
    _land_and_accept(path, monkeypatch)
    return path


def test_reseal_recipe_reproduces_production_seal(db_path) -> None:
    conn = connect(str(db_path), read_only=True)
    try:
        assert _reseal(conn) == _stamp_row(conn)["payload_hash"]
    finally:
        conn.close()


def test_baseline_generation_is_readable(db_path, monkeypatch) -> None:
    _patch_live(monkeypatch, db_path)
    truth = open_calendar_truth(DECISION)
    assert truth.evidence.batch_id == BATCH_ID
    assert truth.is_open(START) is True


def test_stale_batch_stamps_do_not_block_calendar_truth(db_path, monkeypatch) -> None:
    """生产态复刻: 指针/canonical 已重打为现算契约, ingest_batch 仍是旧算法值 + 旧 source。"""
    contract, _registry = _contract_and_registry()
    _make_stamps_stale(db_path)
    _patch_live(monkeypatch, db_path)

    truth = open_calendar_truth(DECISION)

    assert truth.evidence.batch_id == BATCH_ID
    # 证据信封报告的是指针 (活契约) 的戳, 不是批次的冻结戳
    assert truth.evidence.contract_hash == contract.contract_hash
    assert truth.evidence.config_hash == contract.config_hash
    assert truth.is_open(START) is True
    assert truth.previous_open(date(1990, 12, 24)) == date(1990, 12, 21)
    conn = connect(str(db_path), read_only=True)
    try:
        frozen = _stamp_row(conn)
        assert frozen["contract_hash"] == STALE_CONTRACT_HASH
        assert frozen["source_name"] == STALE_SOURCE
    finally:
        conn.close()


def test_source_switch_alone_does_not_block_calendar_truth(db_path, monkeypatch) -> None:
    """业主点名的 fixture: 只换 source_name (哈希不动) —— 换根水管不改数据身份。"""
    _make_stamps_stale(db_path, hashes=False, source=True)
    _patch_live(monkeypatch, db_path)

    truth = open_calendar_truth(DECISION)

    assert truth.evidence.batch_id == BATCH_ID
    assert truth.open_dates(START, date(1990, 12, 31)) == tuple(
        START + timedelta(days=i) for i in range(13) if (START + timedelta(days=i)).weekday() < 5
    )


def test_pointer_stamp_drift_is_still_blocking(db_path, monkeypatch) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            f"UPDATE {ACCEPTED_TABLE} SET config_hash = ? WHERE batch_id = ?",
            ["0" * 64, BATCH_ID],
        )
    finally:
        conn.close()
    reason = _blocked_reason(db_path, monkeypatch)
    assert reason == "accepted_calendar_generation_does_not_match_live_contract"


def test_batch_seal_is_still_enforced_when_stamps_change_without_reseal(db_path, monkeypatch) -> None:
    """修复后的控制: 冻结戳被动了却没重算封印 → 封印自洽检查必须抓住 (不是靠与活契约比)。"""
    _make_stamps_stale(db_path, reseal=False)
    reason = _blocked_reason(db_path, monkeypatch)
    assert reason.startswith("accepted_calendar_landing_invalid: BATCH_EVIDENCE_MISMATCH")


@pytest.mark.parametrize(
    ("column", "value", "field"),
    [
        ("contract_version", "99", "contract_version"),
        ("writer_id", "someone_else", "writer_id"),
    ],
)
def test_declared_identity_is_still_compared(db_path, monkeypatch, column, value, field) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            f"UPDATE {INGEST_BATCH_TABLE} SET {column} = ? WHERE batch_id = ?",
            [value, BATCH_ID],
        )
    finally:
        conn.close()
    reason = _blocked_reason(db_path, monkeypatch)
    assert reason == f"accepted_calendar_pointer_batch_mismatch fields=['{field}']"
