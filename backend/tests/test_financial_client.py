import sqlite3
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Optional
from unittest import mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import financial_client


async def _async_zero(*args, **kwargs):
    return 0


def _build_raw_record(stock_code: str, report_date: str, *, ingested_at: Optional[str] = None) -> dict:
    record = {column: None for column in financial_client.RAW_FINANCIAL_COLUMNS}
    record.update({
        "stock_code": stock_code,
        "report_date": report_date,
        "report_type": "latest_snapshot",
        "source_file": "test",
        "ingested_at": ingested_at or datetime.now().isoformat(),
    })
    return record


def _fake_optional_modules() -> dict[str, object]:
    return {
        "services.capital_client": types.SimpleNamespace(sync_capital_behavior_data=_async_zero),
        "services.financial_indicator_client": types.SimpleNamespace(sync_financial_indicator_data=_async_zero),
        "services.quality_feature_engine": types.SimpleNamespace(build_quality_features=lambda conn: 0),
        "services.stock_archetype_engine": types.SimpleNamespace(build_stock_archetypes=lambda conn: 0),
    }


class _FakeAkFrame:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.empty = not self._rows

    def to_dict(self, orient):
        assert orient == "records"
        return list(self._rows)


def _fake_balance_frame(report_date: str = "2025-09-30"):
    return _FakeAkFrame([
        {
            "报告日": report_date,
            "公告日期": "2025-10-30",
            "类型": "合并报表",
            "是否审计": "是",
            "资产总计": "100",
            "负债合计": "40",
            "归属于母公司股东权益合计": "60",
            "流动资产合计": "50",
            "流动负债合计": "20",
            "实收资本(或股本)": "10",
            "合同负债": "5",
            "存货": "3",
            "未分配利润": "9",
        }
    ])


def _fake_income_frame(report_date: str = "2025-09-30"):
    return _FakeAkFrame([
        {
            "报告日": report_date,
            "公告日期": "2025-10-30",
            "类型": "合并报表",
            "是否审计": "是",
            "营业总收入": "88",
            "营业成本": "33",
            "营业利润": "20",
            "归属于母公司所有者的净利润": "18",
            "基本每股收益": "1.2",
        }
    ])


def _fake_cashflow_frame(report_date: str = "2025-09-30"):
    return _FakeAkFrame([
        {
            "报告日": report_date,
            "公告日期": "2025-10-30",
            "类型": "合并报表",
            "是否审计": "是",
            "经营活动产生的现金流量净额": "12",
        }
    ])


def _create_history_candidate_tables(conn):
    conn.execute("CREATE TABLE dim_active_a_stock (stock_code TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE excluded_stocks (stock_code TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE mart_current_relationship (stock_code TEXT)")
    conn.execute("CREATE TABLE mart_stock_trend (stock_code TEXT PRIMARY KEY)")


def test_resolve_history_candidate_limit_caps_large_backlogs_to_safe_batch():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    financial_client.ensure_tables(conn)
    try:
        conn.execute("CREATE TABLE mart_stock_trend (stock_code TEXT PRIMARY KEY)")
        stock_codes = [f"{index:06d}" for index in range(1, 1201)]
        conn.executemany(
            "INSERT INTO mart_stock_trend (stock_code) VALUES (?)",
            [(code,) for code in stock_codes],
        )
        conn.commit()

        limit = financial_client._resolve_history_candidate_limit(conn, stock_codes)

        assert limit == financial_client.FIN_HISTORY_BATCH_SIZE
    finally:
        conn.close()


def test_resolve_history_candidate_limit_keeps_small_explicit_batches():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    financial_client.ensure_tables(conn)
    try:
        limit = financial_client._resolve_history_candidate_limit(conn, ["000001", "000002", "000003"])
        assert limit == 3
    finally:
        conn.close()


def test_select_history_candidates_skips_recent_history_cooldown():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    financial_client.ensure_tables(conn)
    try:
        _create_history_candidate_tables(conn)
        conn.execute("INSERT INTO dim_active_a_stock (stock_code) VALUES ('000001')")
        conn.execute("INSERT INTO mart_stock_trend (stock_code) VALUES ('000001')")
        conn.executemany(
            "INSERT INTO raw_gpcw_financial (stock_code, report_date, ingested_at) VALUES (?, ?, ?)",
            [
                ("000001", "2025-09-30", "2026-04-17T10:00:00"),
                ("000001", "2025-12-31", "2026-04-17T10:00:00"),
            ],
        )
        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO financial_sync_state (
                stock_code, history_rows, last_report_date, last_history_at,
                history_status, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", 2, "2025-12-31", now, "failed", "failed", now),
        )
        conn.commit()

        candidates = financial_client._select_history_candidates(
            conn,
            stock_codes=["000001"],
            limit=1,
        )

        assert candidates == []
    finally:
        conn.close()


def test_snapshot_state_update_preserves_history_phase_status():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    financial_client.ensure_tables(conn)
    try:
        financial_client._apply_history_backfill(
            conn,
            ["000001"],
            [],
            {"000001": {"status": "failed", "error": "history_boom"}},
            "2026-04-17T14:30:00",
        )
        financial_client._upsert_snapshot_state(
            conn,
            "000001",
            "2026-04-17T14:31:00",
            snapshot_at="2026-04-17T14:31:00",
            status="ok",
        )
        conn.commit()

        state = conn.execute(
            """
            SELECT status, error, history_status, history_error,
                   snapshot_status, snapshot_error
            FROM financial_sync_state
            WHERE stock_code = ?
            """,
            ("000001",),
        ).fetchone()

        assert state["status"] == "ok"
        assert state["error"] is None
        assert state["history_status"] == "failed"
        assert state["history_error"] == "history_boom"
        assert state["snapshot_status"] == "ok"
        assert state["snapshot_error"] is None
    finally:
        conn.close()


def test_fetch_sina_history_batch_retries_transient_empty_response():
    call_state = {"count": 0}

    def fake_fetch(*, stock, symbol):
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if symbol == "资产负债表":
            return _fake_balance_frame()
        if symbol == "利润表":
            return _fake_income_frame()
        if symbol == "现金流量表":
            return _fake_cashflow_frame()
        raise AssertionError(f"unexpected symbol {symbol}")

    fake_ak = types.SimpleNamespace(stock_financial_report_sina=fake_fetch)

    with mock.patch.dict(sys.modules, {"akshare": fake_ak}, clear=False), mock.patch.object(
        financial_client,
        "FIN_HISTORY_SOURCE_RETRY_ATTEMPTS",
        2,
    ), mock.patch.object(financial_client.time, "sleep", return_value=None):
        records, states = financial_client._fetch_sina_history_batch(["000001"])

    assert len(records) == 1
    assert records[0]["stock_code"] == "000001"
    assert records[0]["report_date"] == "2025-09-30"
    assert states["000001"]["status"] == "ok"
    assert states["000001"]["history_rows"] == 1
    assert call_state["count"] == 4


def test_fetch_sina_history_batch_keeps_partial_rows_when_one_statement_fails():
    def fake_fetch(*, stock, symbol):
        if symbol == "资产负债表":
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if symbol == "利润表":
            return _fake_income_frame()
        if symbol == "现金流量表":
            return _fake_cashflow_frame()
        raise AssertionError(f"unexpected symbol {symbol}")

    fake_ak = types.SimpleNamespace(stock_financial_report_sina=fake_fetch)

    with mock.patch.dict(sys.modules, {"akshare": fake_ak}, clear=False), mock.patch.object(
        financial_client,
        "FIN_HISTORY_SOURCE_RETRY_ATTEMPTS",
        1,
    ), mock.patch.object(financial_client.time, "sleep", return_value=None):
        records, states = financial_client._fetch_sina_history_batch(["000001"])

    assert len(records) == 1
    assert records[0]["stock_code"] == "000001"
    assert records[0]["report_date"] == "2025-09-30"
    assert states["000001"]["status"] == "partial"
    assert "资产负债表" in states["000001"]["error"]
    assert states["000001"]["last_report_date"] == "2025-09-30"


@pytest.mark.asyncio
async def test_sync_financial_data_skips_recent_snapshot_successes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    financial_client.ensure_tables(conn)
    try:
        financial_client._upsert_raw_financial(conn, _build_raw_record("000001", "2025-12-31"))
        financial_client._update_snapshot_state(conn, ["000001"], datetime.now().isoformat())
        conn.commit()

        with mock.patch.object(financial_client, "_select_history_candidates", return_value=[]), mock.patch.object(
            financial_client,
            "_fetch_latest_snapshot_batch",
            side_effect=AssertionError("recent snapshot should be skipped"),
        ), mock.patch.dict(sys.modules, _fake_optional_modules(), clear=False):
            total = await financial_client.sync_financial_data(conn, stock_codes=["000001"])

        assert total == 0
        state = conn.execute(
            "SELECT status, last_snapshot_at FROM financial_sync_state WHERE stock_code = ?",
            ("000001",),
        ).fetchone()
        assert state["status"] == "ok"
        assert state["last_snapshot_at"] is not None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sync_financial_data_marks_missing_report_date_as_failed():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    financial_client.ensure_tables(conn)
    try:
        snapshot_payload = {
            "000001": {
                "updated_date": None,
                "zongzichan": "100",
                "jingzichan": "50",
            },
            "000002": {
                "updated_date": "2026-04-13",
                "zongzichan": "200",
                "jingzichan": "120",
            },
        }

        with mock.patch.object(financial_client, "_select_history_candidates", return_value=[]), mock.patch.object(
            financial_client,
            "_fetch_latest_snapshot_batch",
            return_value=snapshot_payload,
        ), mock.patch.dict(sys.modules, _fake_optional_modules(), clear=False):
            total = await financial_client.sync_financial_data(conn, stock_codes=["000001", "000002"])

        assert total == 1

        rows = conn.execute(
            "SELECT stock_code, report_date FROM raw_gpcw_financial ORDER BY stock_code, report_date"
        ).fetchall()
        assert [(row["stock_code"], row["report_date"]) for row in rows] == [("000002", "2025-12-31")]

        states = conn.execute(
            "SELECT stock_code, status, error, last_snapshot_at FROM financial_sync_state ORDER BY stock_code"
        ).fetchall()
        state_map = {row["stock_code"]: dict(row) for row in states}
        assert state_map["000001"]["status"] == "failed"
        assert state_map["000001"]["error"] == "missing_snapshot_report_date"
        assert state_map["000001"]["last_snapshot_at"] is None
        assert state_map["000002"]["status"] == "ok"
        assert state_map["000002"]["error"] is None
        assert state_map["000002"]["last_snapshot_at"] is not None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sync_financial_data_reports_progress_snapshots():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    financial_client.ensure_tables(conn)
    progress = []
    try:
        financial_client._upsert_raw_financial(conn, _build_raw_record("000002", "2025-12-31"))
        conn.commit()

        history_records = [_build_raw_record("000001", "2025-12-31")]
        snapshot_payload = {
            "000002": {
                "updated_date": "2026-04-13",
                "zongzichan": "200",
                "jingzichan": "120",
            },
        }

        with mock.patch.object(financial_client, "_select_history_candidates", return_value=["000001"]), mock.patch.object(
            financial_client,
            "_fetch_sina_history_batch",
            return_value=(history_records, {"000001": {"status": "ok", "history_rows": 1, "last_report_date": "2025-12-31"}}),
        ), mock.patch.object(
            financial_client,
            "_select_snapshot_candidates",
            return_value=(["000002"], 1),
        ), mock.patch.object(
            financial_client,
            "_fetch_latest_snapshot_batch",
            return_value=snapshot_payload,
        ), mock.patch.dict(sys.modules, _fake_optional_modules(), clear=False):
            total = await financial_client.sync_financial_data(
                conn,
                stock_codes=["000001", "000002"],
                progress_callback=progress.append,
            )

        assert total == 2
        assert progress
        assert any(item["history_backfill"]["status"] == "running" for item in progress)
        assert any(item["snapshot_sync"]["status"] == "running" for item in progress)
        final = progress[-1]
        assert final["summary"]["status"] == "completed"
        assert final["summary"]["records"] == 2
        assert final["history_backfill"]["rows"] == 1
        assert final["snapshot_sync"]["rows"] == 1
        assert final["snapshot_sync"]["skipped_recent"] == 1
    finally:
        conn.close()