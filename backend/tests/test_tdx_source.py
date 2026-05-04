import sys
import asyncio
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import services.akshare_client as akshare_client
import services.financial_client as financial_client
import services.tdx_affair_client as tdx_affair_client
import services.tdx_source as tdx_source
from scripts.profile_tdx_gpcw_fields import profile_tdx_gpcw_fields


def test_iter_tdx_servers_prefers_custom_and_deduplicates(monkeypatch):
    monkeypatch.setenv("CM_TDX_SERVERS", "1.1.1.1:7709,2.2.2.2:7709,1.1.1.1:7709")
    monkeypatch.setattr(
        tdx_source,
        "_load_hq_hosts",
        lambda: (("2.2.2.2", 7709), ("3.3.3.3", 7709)),
    )

    servers = tdx_source.iter_tdx_servers()

    assert servers == (("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709))


def test_call_tdx_quotes_with_retry_reuses_pooled_client(monkeypatch):
    factory_calls = []

    class FakeClient:
        def __init__(self, server):
            self.server = server

        def close(self):
            return None

        def quotes(self, symbol):
            return {"server": self.server, "symbol": tuple(symbol)}

    class FakeQuotes:
        @staticmethod
        def factory(*, server, **_kwargs):
            factory_calls.append(server)
            return FakeClient(server)

    monkeypatch.setattr(tdx_source, "get_tdx_quotes_class", lambda: FakeQuotes)
    monkeypatch.setattr(tdx_source, "iter_tdx_servers", lambda: (("1.1.1.1", 7709),))

    tdx_source.reset_tdx_quotes_pool()
    try:
        first, first_source = tdx_source.call_tdx_quotes_with_retry(
            lambda client: client.quotes(["000001"]),
            action_name="quotes",
        )
        second, second_source = tdx_source.call_tdx_quotes_with_retry(
            lambda client: client.quotes(["600036"]),
            action_name="quotes",
        )
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert factory_calls == [("1.1.1.1", 7709)]
    assert first_source == "tdxhub_1.1.1.1:7709"
    assert second_source == "tdxhub_1.1.1.1:7709"
    assert first["symbol"] == ("000001",)
    assert second["symbol"] == ("600036",)


def test_call_tdx_quotes_with_retry_collects_attempts(monkeypatch):
    class FakeClient:
        def __init__(self, server):
            self.server = server

        def close(self):
            return None

        def quotes(self, symbol):
            if self.server == ("1.1.1.1", 7709):
                raise ValueError("empty")
            return {"symbol": tuple(symbol)}

    class FakeQuotes:
        @staticmethod
        def factory(*, server, **_kwargs):
            return FakeClient(server)

    monkeypatch.setattr(tdx_source, "get_tdx_quotes_class", lambda: FakeQuotes)
    monkeypatch.setattr(tdx_source, "iter_tdx_servers", lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709)))

    tdx_source.reset_tdx_quotes_pool()
    try:
        result, source, attempts = tdx_source.call_tdx_quotes_with_retry(
            lambda client: client.quotes(["000001"]),
            action_name="quotes",
            collect_attempts=True,
        )
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert source == "tdxhub_2.2.2.2:7709"
    assert result["symbol"] == ("000001",)
    assert attempts[0]["server"] == ("1.1.1.1", 7709)
    assert attempts[0]["error_type"] == "empty"
    assert attempts[1]["server"] == ("2.2.2.2", 7709)
    assert attempts[1]["ok"] is True


def test_call_tdx_quotes_with_retry_rotates_start_server(monkeypatch):
    monkeypatch.setattr(
        tdx_source,
        "iter_tdx_servers",
        lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)),
    )

    tdx_source.reset_tdx_quotes_pool()
    try:
        first = tdx_source._iter_tdx_servers_for_request()
        second = tdx_source._iter_tdx_servers_for_request()
        third = tdx_source._iter_tdx_servers_for_request()
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert first[0] == ("1.1.1.1", 7709)
    assert second[0] == ("2.2.2.2", 7709)
    assert third[0] == ("3.3.3.3", 7709)


def test_call_tdx_quotes_with_retry_deprioritizes_recent_timeout_server(monkeypatch):
    class FakeClient:
        def __init__(self, server):
            self.server = server

        def close(self):
            return None

    class FakeQuotes:
        @staticmethod
        def factory(*, server, **_kwargs):
            return FakeClient(server)

    monkeypatch.setattr(tdx_source, "get_tdx_quotes_class", lambda: FakeQuotes)
    monkeypatch.setattr(
        tdx_source,
        "iter_tdx_servers",
        lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709)),
    )

    def operation(client):
        if client.server == ("1.1.1.1", 7709):
            raise TimeoutError("timed out")
        return {"server": client.server}

    tdx_source.reset_tdx_quotes_pool()
    try:
        first, first_source, first_attempts = tdx_source.call_tdx_quotes_with_retry(
            operation,
            action_name="quotes",
            collect_attempts=True,
        )
        second, second_source, second_attempts = tdx_source.call_tdx_quotes_with_retry(
            operation,
            action_name="quotes",
            collect_attempts=True,
        )
        third, third_source, third_attempts = tdx_source.call_tdx_quotes_with_retry(
            operation,
            action_name="quotes",
            collect_attempts=True,
        )
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert first_source == "tdxhub_2.2.2.2:7709"
    assert first_attempts[0]["server"] == ("1.1.1.1", 7709)
    assert first_attempts[0]["error_type"] == "TimeoutError"
    assert second_source == "tdxhub_2.2.2.2:7709"
    assert third_source == "tdxhub_2.2.2.2:7709"
    assert second_attempts[0]["server"] == ("2.2.2.2", 7709)
    assert third_attempts[0]["server"] == ("2.2.2.2", 7709)
    assert first["server"] == ("2.2.2.2", 7709)
    assert second["server"] == ("2.2.2.2", 7709)
    assert third["server"] == ("2.2.2.2", 7709)


def test_fetch_latest_snapshot_batch_uses_shared_quotes_pool(monkeypatch):
    result_frame = pd.DataFrame([{"updated_date": "2026-04-13", "zongzichan": 100.0}])

    mocked_call = mock.Mock(return_value=({"000001": result_frame.iloc[0].to_dict()}, "tdxhub_1.1.1.1:7709"))
    monkeypatch.setattr(financial_client, "call_tdx_quotes_with_retry", mocked_call)

    result = financial_client._fetch_latest_snapshot_batch(["000001"])

    assert list(result.keys()) == ["000001"]
    assert mocked_call.call_args.kwargs["action_name"] == "finance[1]"


def test_fetch_latest_snapshot_batch_returns_empty_when_pool_fails(monkeypatch):
    monkeypatch.setattr(
        financial_client,
        "call_tdx_quotes_with_retry",
        mock.Mock(side_effect=RuntimeError("all servers failed")),
    )

    assert financial_client._fetch_latest_snapshot_batch(["000001"]) == {}


def test_fetch_etf_list_tdxhub_uses_shared_quotes_pool(monkeypatch):
    mocked_call = mock.Mock(
        return_value=([
            {"code": "159695", "name": "通信ETF", "market": "sz", "asset_type": "etf"},
            {"code": "512010", "name": "医药ETF", "market": "sh", "asset_type": "etf"},
        ], "tdxhub_1.1.1.1:7709")
    )
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)
    akshare_client._clear_tdxhub_unavailable()

    result = asyncio.run(akshare_client._fetch_etf_list_tdxhub())

    assert [row["code"] for row in result] == ["159695", "512010"]
    assert mocked_call.call_args.kwargs["action_name"] == "stocks[etf-list]"


def test_fetch_index_kline_uses_shared_quotes_pool(monkeypatch):
    frame = pd.DataFrame(
        [
            {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "vol": 12345.0, "amount": 67890.0},
            {"open": 10.2, "high": 10.6, "low": 10.0, "close": 10.4, "vol": 22345.0, "amount": 77890.0},
        ],
        index=pd.to_datetime(["2026-04-10", "2026-04-13"]),
    )
    mocked_call = mock.Mock(return_value=(frame, "tdxhub_1.1.1.1:7709"))
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)

    result_df, source = asyncio.run(akshare_client.fetch_index_kline("000001", "20260410", "20260413"))

    assert source == "tdxhub_index"
    assert list(result_df["date"]) == ["2026-04-10", "2026-04-13"]
    assert list(result_df["volume"]) == [12345.0, 22345.0]
    assert mocked_call.call_args.kwargs["action_name"] == "index_bars[000001]"


def test_fetch_daily_tdxhub_with_diagnostics_uses_shared_quotes_pool(monkeypatch):
    frame = pd.DataFrame(
        [
            {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 12345.0, "amount": 67890.0, "date": "2026-04-10"},
        ]
    )
    attempts = [{"server": ("1.1.1.1", 7709), "ok": True, "rows": 1, "elapsed_sec": 0.01}]
    mocked_call = mock.Mock(return_value=(frame, "tdxhub_1.1.1.1:7709", attempts))
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)
    akshare_client._clear_tdxhub_unavailable()

    result_df, source, diagnostics = asyncio.run(
        akshare_client._fetch_daily_tdxhub_with_diagnostics("159695", "20260410", "20260410")
    )

    assert source == "tdxhub"
    assert list(result_df["date"]) == ["2026-04-10"]
    assert diagnostics["ok"] is True
    assert diagnostics["attempts"] == attempts
    assert mocked_call.call_args.kwargs["action_name"] == "bars[159695]"
    assert mocked_call.call_args.kwargs["collect_attempts"] is True


def test_fetch_daily_tdxhub_with_diagnostics_marks_timeout_heavy_success_as_degraded(monkeypatch):
    frame = pd.DataFrame(
        [
            {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 12345.0, "amount": 67890.0, "date": "2026-04-10"},
        ]
    )
    attempts = [
        {"server": ("1.1.1.1", 7709), "ok": False, "error_type": "timeout", "error": "timed out", "elapsed_sec": 5.0},
        {"server": ("2.2.2.2", 7709), "ok": False, "error_type": "timeout", "error": "timed out", "elapsed_sec": 5.0},
        {"server": ("3.3.3.3", 7709), "ok": True, "rows": 1, "elapsed_sec": 0.4},
    ]
    mocked_call = mock.Mock(return_value=(frame, "tdxhub_3.3.3.3:7709", attempts))
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)
    akshare_client._clear_tdxhub_unavailable()

    try:
        result_df, source, diagnostics = asyncio.run(
            akshare_client._fetch_daily_tdxhub_with_diagnostics("159695", "20260410", "20260410")
        )
        state = akshare_client._get_tdxhub_unavailable_state()
    finally:
        akshare_client._clear_tdxhub_unavailable()

    assert source == "tdxhub"
    assert list(result_df["date"]) == ["2026-04-10"]
    assert diagnostics["ok"] is True
    assert diagnostics["timeout_failures"] == 2
    assert diagnostics["fallback_recommended"] is True
    assert state["summary"] == "tdxhub timeout x2，切换 fallback"
    assert state["until"] > 0


def test_sync_gpcw_files_uses_shared_affair_loader(monkeypatch, tmp_path):
    conn = duck_mem()
    try:
        class FakeAffair:
            @staticmethod
            def files():
                return [{"filename": "gpcw20260331.zip", "filesize": 120000}]

            @staticmethod
            def parse(*, downdir, filename, columns):
                assert Path(downdir) == tmp_path
                assert filename == "gpcw20260331.zip"
                assert columns is None
                return pd.DataFrame(
                    [
                        {
                            "report_date": 20260331.0,
                            "基本每股收益": 1.23,
                            "合同负债(万元)": 11.0,
                            "预收款项": 99.0,
                            "其中：营业成本": 45.6,
                            "col328": 7.8,
                        },
                        {
                            "report_date": 20260331.0,
                            "基本每股收益": 2.34,
                            "预收款项": 22.0,
                        },
                    ],
                    index=["000001", "000002"],
                )

        monkeypatch.setattr(tdx_affair_client, "get_tdx_affair_class", lambda: FakeAffair)

        result = tdx_affair_client.sync_gpcw_files(conn, quarters=1, downdir=str(tmp_path))
        rows = conn.execute(
            """
            SELECT stock_code, report_date, eps, contract_liabilities, operating_cost, operating_cost_single_quarter
            FROM raw_gpcw_detail
            ORDER BY stock_code
            """
        ).fetchall()
        wide_rows = conn.execute(
            "SELECT stock_code, report_date, field_values_json FROM raw_tdx_gpcw_wide ORDER BY stock_code"
        ).fetchall()
        field_row = conn.execute(
            "SELECT db_column, model_candidate FROM dim_tdx_gpcw_field WHERE zh_name = '合同负债(万元)'"
        ).fetchone()

        assert result["files_synced"] == 1
        assert result["rows_upserted"] == 2
        assert result["wide_rows_upserted"] == 2
        assert len(rows) == 2
        assert len(wide_rows) == 2
        assert "合同负债(万元)" in wide_rows[0]["field_values_json"]
        assert field_row["db_column"] == "contract_liabilities"
        assert field_row["model_candidate"] is True
        # DuckDB REAL = FLOAT32, 与 sqlite3 REAL=DOUBLE 不同, 故用近似比较.
        assert rows[0]["stock_code"] == "000001"
        assert rows[0]["report_date"] == "2026-03-31"
        assert rows[0]["eps"] == pytest.approx(1.23, rel=1e-5)
        assert rows[0]["contract_liabilities"] == pytest.approx(11.0)
        assert rows[0]["operating_cost"] == pytest.approx(45.6, rel=1e-5)
        assert rows[0]["operating_cost_single_quarter"] == pytest.approx(7.8, rel=1e-5)
        assert rows[1]["stock_code"] == "000002"
        assert rows[1]["report_date"] == "2026-03-31"
        assert rows[1]["eps"] == pytest.approx(2.34, rel=1e-5)
        assert rows[1]["contract_liabilities"] == pytest.approx(22.0)
        assert rows[1]["operating_cost"] is None
        assert rows[1]["operating_cost_single_quarter"] is None
    finally:
        conn.close()


def test_ensure_table_adds_missing_gpcw_columns():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE raw_gpcw_detail (
                stock_code TEXT NOT NULL,
                report_date TEXT NOT NULL,
                eps REAL,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (stock_code, report_date)
            )
            """
        )

        tdx_affair_client._ensure_table(conn)

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(raw_gpcw_detail)").fetchall()
        }

        assert "contract_liabilities" in columns
        assert "operating_cost" in columns
        assert "operating_cost_single_quarter" in columns
    finally:
        conn.close()


def test_profile_tdx_gpcw_fields_records_candidate_and_rejections():
    conn = duck_mem()
    try:
        tdx_affair_client._ensure_table(conn)
        conn.executemany(
            """
            INSERT INTO raw_tdx_gpcw_wide
            (stock_code, report_date, source_file, field_values_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    "000001",
                    "2026-03-31",
                    "gpcw20260331.zip",
                    '{"合同负债(万元)": 11.0, "营业收入": 100.0, "col999": 1.0}',
                ),
                (
                    "000002",
                    "2026-03-31",
                    "gpcw20260331.zip",
                    '{"合同负债(万元)": 22.0, "营业收入": 200.0, "col999": 1.0}',
                ),
            ],
        )

        result = profile_tdx_gpcw_fields(conn, profile_run_id="test_profile")
        rows = conn.execute(
            """
            SELECT zh_name, coverage_pct, p50, model_candidate, rejection_reason
            FROM mart_tdx_gpcw_field_profile
            WHERE profile_run_id = 'test_profile'
            ORDER BY zh_name
            """
        ).fetchall()
        by_name = {r["zh_name"]: r for r in rows}

        assert result["field_count"] == 3
        assert by_name["合同负债(万元)"]["coverage_pct"] == 100.0
        assert by_name["合同负债(万元)"]["p50"] == 16.5
        assert by_name["合同负债(万元)"]["model_candidate"] is True
        assert by_name["col999"]["model_candidate"] is False
        assert by_name["col999"]["rejection_reason"] == "unnamed_col"
    finally:
        conn.close()
