import sqlite3
import sys
import asyncio
from pathlib import Path
from unittest import mock

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.akshare_client as akshare_client
import services.financial_client as financial_client
import services.tdx_affair_client as tdx_affair_client
import services.tdx_source as tdx_source


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


def test_fetch_etf_list_mootdx_uses_shared_quotes_pool(monkeypatch):
    mocked_call = mock.Mock(
        return_value=([
            {"code": "159695", "name": "通信ETF", "market": "sz", "asset_type": "etf"},
            {"code": "512010", "name": "医药ETF", "market": "sh", "asset_type": "etf"},
        ], "tdxhub_1.1.1.1:7709")
    )
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)
    akshare_client._clear_mootdx_unavailable()

    result = asyncio.run(akshare_client._fetch_etf_list_mootdx())

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

    assert source == "mootdx_index"
    assert list(result_df["date"]) == ["2026-04-10", "2026-04-13"]
    assert list(result_df["volume"]) == [12345.0, 22345.0]
    assert mocked_call.call_args.kwargs["action_name"] == "index_bars[000001]"


def test_fetch_daily_mootdx_with_diagnostics_uses_shared_quotes_pool(monkeypatch):
    frame = pd.DataFrame(
        [
            {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 12345.0, "amount": 67890.0, "date": "2026-04-10"},
        ]
    )
    attempts = [{"server": ("1.1.1.1", 7709), "ok": True, "rows": 1, "elapsed_sec": 0.01}]
    mocked_call = mock.Mock(return_value=(frame, "tdxhub_1.1.1.1:7709", attempts))
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)
    akshare_client._clear_mootdx_unavailable()

    result_df, source, diagnostics = asyncio.run(
        akshare_client._fetch_daily_mootdx_with_diagnostics("159695", "20260410", "20260410")
    )

    assert source == "mootdx"
    assert list(result_df["date"]) == ["2026-04-10"]
    assert diagnostics["ok"] is True
    assert diagnostics["attempts"] == attempts
    assert mocked_call.call_args.kwargs["action_name"] == "bars[159695]"
    assert mocked_call.call_args.kwargs["collect_attempts"] is True


def test_fetch_daily_mootdx_with_diagnostics_marks_timeout_heavy_success_as_degraded(monkeypatch):
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
    akshare_client._clear_mootdx_unavailable()

    try:
        result_df, source, diagnostics = asyncio.run(
            akshare_client._fetch_daily_mootdx_with_diagnostics("159695", "20260410", "20260410")
        )
        state = akshare_client._get_mootdx_unavailable_state()
    finally:
        akshare_client._clear_mootdx_unavailable()

    assert source == "mootdx"
    assert list(result_df["date"]) == ["2026-04-10"]
    assert diagnostics["ok"] is True
    assert diagnostics["timeout_failures"] == 2
    assert diagnostics["fallback_recommended"] is True
    assert state["summary"] == "mootdx timeout x2，切换 fallback"
    assert state["until"] > 0


def test_sync_gpcw_files_uses_shared_affair_loader(monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:")
    try:
        class FakeAffair:
            @staticmethod
            def files():
                return [{"filename": "gpcw20260331.zip", "filesize": 120000}]

            @staticmethod
            def parse(*, downdir, filename, columns):
                assert Path(downdir) == tmp_path
                assert filename == "gpcw20260331.zip"
                assert columns == tdx_affair_client._SELECTED_GPCW_COLUMNS
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

        assert result["files_synced"] == 1
        assert result["rows_upserted"] == 2
        assert rows == [
            ("000001", "2026-03-31", 1.23, 11.0, 45.6, 7.8),
            ("000002", "2026-03-31", 2.34, 22.0, None, None),
        ]
    finally:
        conn.close()


def test_ensure_table_adds_missing_gpcw_columns():
    conn = sqlite3.connect(":memory:")
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


def test_fetch_sw_industry_all_with_source_prefers_tdx_research_runtime(monkeypatch):
    expected = [
        {
            "stock_code": "600036",
            "sw_level1": "银行",
            "sw_level2": "股份制银行",
            "sw_level3": "全国性股份制银行",
            "sw_code": "881398.SH",
        }
    ]

    async def _fake_tdx():
        return expected

    monkeypatch.setattr(akshare_client, "_fetch_tdx_research_industry_all", _fake_tdx)

    rows, source = asyncio.run(akshare_client.fetch_sw_industry_all_with_source())

    assert rows == expected
    assert source == "tdx_research_industry"


def test_fetch_sw_industry_all_with_source_blocks_when_tdx_unavailable(monkeypatch):
    async def _fake_tdx():
        raise ImportError("tqcenter not installed")

    monkeypatch.setattr(akshare_client, "_fetch_tdx_research_industry_all", _fake_tdx)

    rows, source = asyncio.run(akshare_client.fetch_sw_industry_all_with_source())

    assert rows == []
    assert source == "tdx_research_industry_error:tqcenter not installed"


def test_fetch_tdx_research_industry_all_combines_three_levels(monkeypatch):
    async def _fake_call(method_name, *args, **kwargs):
        if method_name == "get_stock_list":
            level = args[0]
            if level == "16":
                return [{"Code": "881001.SH", "Name": "煤炭"}]
            if level == "17":
                return [{"Code": "881101.SH", "Name": "煤炭开采"}]
            if level == "18":
                return [{"Code": "881201.SH", "Name": "焦煤"}]
        if method_name == "get_stock_list_in_sector":
            sector_code = args[0]
            if sector_code == "881001.SH":
                return ["600001.SH", "600002.SH"]
            if sector_code == "881101.SH":
                return ["600001.SH"]
            if sector_code == "881201.SH":
                return ["600001.SH"]
        raise AssertionError(f"unexpected call: {method_name} {args} {kwargs}")

    monkeypatch.setattr(akshare_client, "_call_tdx_research_api", _fake_call)

    rows = asyncio.run(akshare_client._fetch_tdx_research_industry_all())

    assert rows == [
        {
            "stock_code": "600001",
            "sw_level1": "煤炭",
            "sw_level2": "煤炭开采",
            "sw_level3": "焦煤",
            "sw_code": "881201.SH",
        },
        {
            "stock_code": "600002",
            "sw_level1": "煤炭",
            "sw_level2": "",
            "sw_level3": "",
            "sw_code": "881001.SH",
        },
    ]


def test_collect_tdx_level_assignments_blocks_duplicate_membership(monkeypatch):
    async def _fake_rows(_level_code):
        return [
            {"code": "801001", "name": "行业A"},
            {"code": "801002", "name": "行业B"},
        ]

    async def _fake_members(sector_code):
        if sector_code == "801001":
            return ["600001"]
        if sector_code == "801002":
            return ["600001"]
        raise AssertionError(f"unexpected sector_code: {sector_code}")

    monkeypatch.setattr(akshare_client, "_fetch_tdx_research_sector_rows", _fake_rows)
    monkeypatch.setattr(akshare_client, "_fetch_tdx_research_sector_members", _fake_members)

    try:
        asyncio.run(akshare_client._collect_tdx_level_assignments("17", "sw_level2"))
    except ValueError as exc:
        message = str(exc)
        assert "tdx_research_industry_duplicate:sw_level2" in message
        assert "600001:801001->801002" in message
    else:
        assert False, "expected duplicate membership to block sync"


def test_test_industry_availability_reports_tdx_failure_reason(monkeypatch):
    async def _fake_tdx_probe():
        return False, "tdx_research_industry_error:tqcenter not installed"

    monkeypatch.setattr(akshare_client, "_test_tdx_industry_availability", _fake_tdx_probe)

    ok, source = asyncio.run(akshare_client.test_industry_availability())

    assert ok is False
    assert source == "tdx_research_industry_error:tqcenter not installed"