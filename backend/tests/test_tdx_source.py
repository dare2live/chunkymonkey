import sys
import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import services.akshare_client as akshare_client
import services.financial_client as financial_client
import services.tdx_affair_client as tdx_affair_client
import services.tdx_source as tdx_source
from scripts.build_tdx_gpcw_auto_features import build_tdx_gpcw_auto_features
from scripts.profile_tdx_gpcw_fields import profile_tdx_gpcw_fields


def test_tdx_source_prefers_workspace_tdxhub_fork_when_present():
    local_path = tdx_source.workspace_tdxhub_path()
    if local_path is None:
        pytest.skip("workspace tdxhub fork is not checked out next to chunky-monkey-v2")

    tdx_source.ensure_workspace_tdxhub_path()

    import tdxhub
    from tdxhub.quotes import StdQuotes

    Path(tdxhub.__file__).resolve().relative_to(local_path.resolve())
    assert hasattr(StdQuotes, "bars_records")
    assert hasattr(StdQuotes, "index_bars_records")


def test_iter_tdx_servers_prefers_custom_and_deduplicates(monkeypatch):
    monkeypatch.setenv("CM_TDX_SERVERS", "1.1.1.1:7709,2.2.2.2:7709,1.1.1.1:7709")
    monkeypatch.setattr(
        tdx_source,
        "_load_hq_hosts",
        lambda: (("2.2.2.2", 7709), ("3.3.3.3", 7709)),
    )

    servers = tdx_source.iter_tdx_servers()

    assert servers == (("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709))


def test_tdx_server_health_reorders_future_server_iteration(monkeypatch):
    conn = duck_mem()
    monkeypatch.delenv("CM_TDX_SERVERS", raising=False)
    monkeypatch.setattr(
        tdx_source,
        "_load_hq_hosts",
        lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)),
    )

    tdx_source.reset_tdx_quotes_pool()
    try:
        record = tdx_source.record_tdx_server_attempts(
            conn,
            [
                {"server": ("1.1.1.1", 7709), "ok": False, "error_type": "TimeoutError", "elapsed_sec": 1.5},
                {"server": ("2.2.2.2", 7709), "ok": True, "elapsed_sec": 0.2},
                {"server": ("3.3.3.3", 7709), "ok": True, "elapsed_sec": 0.3},
            ],
            capability="kline_daily_raw",
            run_id="unit",
        )
        loaded = tdx_source.load_tdx_server_health(conn, capability="kline_daily_raw")
        servers = tdx_source.iter_tdx_servers()
        first_request = tdx_source._iter_tdx_servers_for_request(prefer_last_success=False)
        second_request = tdx_source._iter_tdx_servers_for_request(prefer_last_success=False)
        third_request = tdx_source._iter_tdx_servers_for_request(prefer_last_success=False)
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert record["updated_server_count"] == 3
    assert loaded["loaded_server_count"] == 2
    assert loaded["servers"] == ["2.2.2.2:7709", "3.3.3.3:7709"]
    assert servers[0] == ("2.2.2.2", 7709)
    assert set(servers) == {("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)}
    assert [first_request[0], second_request[0], third_request[0]] == [
        ("2.2.2.2", 7709),
        ("3.3.3.3", 7709),
        ("2.2.2.2", 7709),
    ]


def test_tdx_server_health_skips_non_retryable_operation_errors():
    conn = duck_mem()

    record = tdx_source.record_tdx_server_attempts(
        conn,
        [
            {"server": ("1.1.1.1", 7709), "ok": False, "error_type": "NotImplementedError", "elapsed_sec": 0.01},
            {"server": ("2.2.2.2", 7709), "ok": True, "elapsed_sec": 0.2},
        ],
        capability="kline_daily_raw",
        run_id="unit",
    )
    rows = conn.execute(
        """
        SELECT server_host, success_count, failure_count
          FROM mart_tdx_server_health
         ORDER BY server_host
        """
    ).fetchall()

    assert record["skipped_non_retryable_count"] == 1
    assert [(row["server_host"], row["success_count"], row["failure_count"]) for row in rows] == [
        ("2.2.2.2", 1, 0)
    ]


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
    assert "connect_elapsed_sec" in attempts[0]
    assert "operation_elapsed_sec" in attempts[0]
    assert attempts[0]["pooled_client"] is False
    assert attempts[1]["server"] == ("2.2.2.2", 7709)
    assert attempts[1]["ok"] is True
    assert "connect_elapsed_sec" in attempts[1]
    assert "operation_elapsed_sec" in attempts[1]


def test_call_tdx_quotes_with_retry_respects_max_attempts_and_timeout(monkeypatch):
    factory_calls = []

    class FakeQuotes:
        @staticmethod
        def factory(*, server, timeout, **_kwargs):
            factory_calls.append((server, timeout))
            raise TimeoutError("timed out")

    monkeypatch.setattr(tdx_source, "get_tdx_quotes_class", lambda: FakeQuotes)
    monkeypatch.setattr(
        tdx_source,
        "iter_tdx_servers",
        lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)),
    )

    tdx_source.reset_tdx_quotes_pool()
    try:
        with pytest.raises(RuntimeError):
            tdx_source.call_tdx_quotes_with_retry(
                lambda client: client.quotes(["000001"]),
                action_name="quotes",
                max_attempts=2,
                connect_timeout=0.2,
            )
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert factory_calls == [
        (("1.1.1.1", 7709), 0.2),
        (("2.2.2.2", 7709), 0.2),
    ]


def test_call_tdx_quotes_with_retry_caps_default_attempts(monkeypatch):
    factory_calls = []

    class FakeQuotes:
        @staticmethod
        def factory(*, server, **_kwargs):
            factory_calls.append(server)
            raise TimeoutError("timed out")

    monkeypatch.delenv("CM_TDX_MAX_ATTEMPTS", raising=False)
    monkeypatch.setattr(tdx_source, "get_tdx_quotes_class", lambda: FakeQuotes)
    monkeypatch.setattr(
        tdx_source,
        "iter_tdx_servers",
        lambda: tuple((f"1.1.1.{idx}", 7709) for idx in range(12)),
    )

    tdx_source.reset_tdx_quotes_pool()
    try:
        with pytest.raises(RuntimeError):
            tdx_source.call_tdx_quotes_with_retry(
                lambda client: client.quotes(["000001"]),
                action_name="quotes",
            )
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert len(factory_calls) == 8


def test_call_tdx_quotes_with_retry_allows_uncapped_attempts(monkeypatch):
    factory_calls = []

    class FakeQuotes:
        @staticmethod
        def factory(*, server, **_kwargs):
            factory_calls.append(server)
            raise TimeoutError("timed out")

    monkeypatch.setenv("CM_TDX_MAX_ATTEMPTS", "0")
    monkeypatch.setattr(tdx_source, "get_tdx_quotes_class", lambda: FakeQuotes)
    monkeypatch.setattr(
        tdx_source,
        "iter_tdx_servers",
        lambda: tuple((f"1.1.1.{idx}", 7709) for idx in range(3)),
    )

    tdx_source.reset_tdx_quotes_pool()
    try:
        with pytest.raises(RuntimeError):
            tdx_source.call_tdx_quotes_with_retry(
                lambda client: client.quotes(["000001"]),
                action_name="quotes",
            )
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert len(factory_calls) == 3


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


def test_tdx_server_iteration_can_disable_last_success_affinity(monkeypatch):
    monkeypatch.setattr(
        tdx_source,
        "iter_tdx_servers",
        lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)),
    )

    tdx_source.reset_tdx_quotes_pool()
    try:
        tdx_source._mark_tdx_server_success(("2.2.2.2", 7709))
        without_affinity = tdx_source._iter_tdx_servers_for_request(prefer_last_success=False)
    finally:
        tdx_source.reset_tdx_quotes_pool()

    tdx_source.reset_tdx_quotes_pool()
    try:
        tdx_source._mark_tdx_server_success(("2.2.2.2", 7709))
        with_affinity = tdx_source._iter_tdx_servers_for_request(prefer_last_success=True)
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert without_affinity[0] == ("1.1.1.1", 7709)
    assert with_affinity[0] == ("2.2.2.2", 7709)


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


def test_call_tdx_quotes_with_retry_stops_on_not_implemented(monkeypatch):
    factory_calls = []

    class FakeClient:
        def __init__(self, server):
            self.server = server

        def close(self):
            return None

    class FakeQuotes:
        @staticmethod
        def factory(*, server, **_kwargs):
            factory_calls.append(server)
            return FakeClient(server)

    monkeypatch.setattr(tdx_source, "get_tdx_quotes_class", lambda: FakeQuotes)
    monkeypatch.setattr(
        tdx_source,
        "iter_tdx_servers",
        lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)),
    )

    tdx_source.reset_tdx_quotes_pool()
    try:
        with pytest.raises(RuntimeError) as exc_info:
            tdx_source.call_tdx_quotes_with_retry(
                lambda _client: (_ for _ in ()).throw(NotImplementedError("unsupported operation")),
                action_name="quotes.unsupported",
                collect_attempts=True,
                max_attempts=3,
            )
        attempts = getattr(exc_info.value, "tdx_attempts")
        health = tdx_source._get_server_health_snapshot()
    finally:
        tdx_source.reset_tdx_quotes_pool()

    assert factory_calls == [("1.1.1.1", 7709)]
    assert len(attempts) == 1
    assert attempts[0]["error_type"] == "NotImplementedError"
    assert float(health[("1.1.1.1", 7709)].get("unavailable_until") or 0.0) == 0.0


def test_fetch_latest_snapshot_batch_uses_shared_quotes_pool(monkeypatch):
    mocked_call = mock.Mock(
        return_value=({"000001": {"updated_date": "2026-04-13", "zongzichan": 100.0}}, "tdxhub_1.1.1.1:7709")
    )
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
    mocked_call = mock.Mock(
        return_value=([
            {
                "date": "2026-04-10",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "vol": 12345.0,
                "amount": 67890.0,
            },
            {
                "date": "2026-04-13",
                "open": 10.2,
                "high": 10.6,
                "low": 10.0,
                "close": 10.4,
                "vol": 22345.0,
                "amount": 77890.0,
            },
        ], "tdxhub_1.1.1.1:7709")
    )
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)

    result_rows, source = asyncio.run(akshare_client.fetch_index_kline("000001", "20260410", "20260413"))

    assert source == "tdxhub_index"
    assert [row["date"] for row in result_rows] == ["2026-04-10", "2026-04-13"]
    assert [row["volume"] for row in result_rows] == [12345.0, 22345.0]
    assert mocked_call.call_args.kwargs["action_name"] == "index_bars[000001]"


def test_fetch_daily_tdxhub_with_diagnostics_uses_shared_quotes_pool(monkeypatch):
    attempts = [{"server": ("1.1.1.1", 7709), "ok": True, "rows": 1, "elapsed_sec": 0.01}]
    mocked_call = mock.Mock(
        return_value=([
            {
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 12345.0,
                "amount": 67890.0,
                "date": "2026-04-10",
            },
        ], "tdxhub_1.1.1.1:7709", attempts)
    )
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)
    akshare_client._clear_tdxhub_unavailable()

    result_rows, source, diagnostics = asyncio.run(
        akshare_client._fetch_daily_tdxhub_with_diagnostics("159695", "20260410", "20260410")
    )

    assert source == "tdxhub"
    assert [row["date"] for row in result_rows] == ["2026-04-10"]
    assert diagnostics["ok"] is True
    assert diagnostics["attempts"] == attempts
    assert mocked_call.call_args.kwargs["action_name"] == "bars[159695]"
    assert mocked_call.call_args.kwargs["collect_attempts"] is True


def test_fetch_daily_tdxhub_with_diagnostics_marks_timeout_heavy_success_as_degraded(monkeypatch):
    attempts = [
        {"server": ("1.1.1.1", 7709), "ok": False, "error_type": "timeout", "error": "timed out", "elapsed_sec": 5.0},
        {"server": ("2.2.2.2", 7709), "ok": False, "error_type": "timeout", "error": "timed out", "elapsed_sec": 5.0},
        {"server": ("3.3.3.3", 7709), "ok": True, "rows": 1, "elapsed_sec": 0.4},
    ]
    mocked_call = mock.Mock(
        return_value=([
            {
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 12345.0,
                "amount": 67890.0,
                "date": "2026-04-10",
            },
        ], "tdxhub_3.3.3.3:7709", attempts)
    )
    monkeypatch.setattr(akshare_client, "call_tdx_quotes_with_retry", mocked_call)
    akshare_client._clear_tdxhub_unavailable()

    try:
        result_rows, source, diagnostics = asyncio.run(
            akshare_client._fetch_daily_tdxhub_with_diagnostics("159695", "20260410", "20260410")
        )
        state = akshare_client._get_tdxhub_unavailable_state()
    finally:
        akshare_client._clear_tdxhub_unavailable()

    assert source == "tdxhub"
    assert [row["date"] for row in result_rows] == ["2026-04-10"]
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
                return [
                    {
                        "stock_code": "000001",
                        "report_date": 20260331.0,
                        "基本每股收益": 1.23,
                        "合同负债(万元)": 11.0,
                        "预收款项": 99.0,
                        "其中：营业成本": 45.6,
                        "col328": 7.8,
                    },
                    {
                        "stock_code": "000002",
                        "report_date": 20260331.0,
                        "基本每股收益": 2.34,
                        "预收款项": 22.0,
                    },
                ]

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
        # DuckDB REAL = FLOAT32, 故用近似比较.
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


def test_sync_gpcw_files_skips_unchanged_manifest(monkeypatch, tmp_path):
    conn = duck_mem()
    parse_calls = []
    try:
        class FakeAffair:
            @staticmethod
            def files():
                return [{"filename": "gpcw20260331.zip", "filesize": 120000}]

            @staticmethod
            def parse(*, downdir, filename, columns):
                parse_calls.append(filename)
                return [
                    {
                        "stock_code": "000001",
                        "report_date": 20260331.0,
                        "基本每股收益": 1.23,
                    }
                ]

        monkeypatch.setattr(tdx_affair_client, "get_tdx_affair_class", lambda: FakeAffair)

        first = tdx_affair_client.sync_gpcw_files(conn, quarters=1, downdir=str(tmp_path))
        second = tdx_affair_client.sync_gpcw_files(conn, quarters=1, downdir=str(tmp_path))
        manifest = conn.execute(
            """
            SELECT parse_status, row_count, file_list_hash
            FROM mart_tdx_gpcw_file_manifest
            WHERE filename = 'gpcw20260331.zip'
            """
        ).fetchone()

        assert parse_calls == ["gpcw20260331.zip"]
        assert first["files_synced"] == 1
        assert first["affected_report_dates"] == ["2026-03-31"]
        assert second["files_synced"] == 0
        assert second["skipped_unchanged"] == 1
        assert second["manifest_rows_upserted"] == 1
        assert manifest["parse_status"] == "success"
        assert manifest["row_count"] == 1
        assert manifest["file_list_hash"]
    finally:
        conn.close()


def test_sync_gpcw_files_rebuilds_changed_report_slice(monkeypatch, tmp_path):
    conn = duck_mem()
    state = {"filesize": 120000, "eps": 1.0}
    try:
        class FakeAffair:
            @staticmethod
            def files():
                return [{"filename": "gpcw20260331.zip", "filesize": state["filesize"]}]

            @staticmethod
            def parse(*, downdir, filename, columns):
                return [
                    {
                        "stock_code": "000001",
                        "report_date": 20260331.0,
                        "基本每股收益": state["eps"],
                    }
                ]

        monkeypatch.setattr(tdx_affair_client, "get_tdx_affair_class", lambda: FakeAffair)

        first = tdx_affair_client.sync_gpcw_files(conn, quarters=1, downdir=str(tmp_path))
        conn.execute(
            """
            CREATE TABLE fact_tdx_gpcw_auto_feature_quarterly (
                feature_set_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                feature_name TEXT,
                feature_value DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_tdx_gpcw_auto_feature_quarterly
            VALUES ('tdx_gpcw_auto_v1', '000001', '2026-03-31', 'stale_feature', 9.9)
            """
        )
        state["filesize"] = 130000
        state["eps"] = 9.0

        second = tdx_affair_client.sync_gpcw_files(conn, quarters=1, downdir=str(tmp_path))
        row = conn.execute(
            "SELECT stock_code, report_date, eps FROM raw_gpcw_detail"
        ).fetchone()
        stale_count = conn.execute(
            "SELECT COUNT(*) FROM fact_tdx_gpcw_auto_feature_quarterly WHERE report_date = '2026-03-31'"
        ).fetchone()[0]

        assert first["rows_upserted"] == 1
        assert second["files_synced"] == 1
        assert second["affected_report_dates"] == ["2026-03-31"]
        assert second["deleted_slices"]["raw_gpcw_detail"] == 1
        assert second["deleted_slices"]["raw_tdx_gpcw_wide"] == 1
        assert second["deleted_slices"]["fact_tdx_gpcw_auto_feature_quarterly"] == 1
        assert row["stock_code"] == "000001"
        assert row["report_date"] == "2026-03-31"
        assert row["eps"] == pytest.approx(9.0)
        assert stale_count == 0
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
            row[0]
            for row in conn.execute("DESCRIBE raw_gpcw_detail").fetchall()
        }

        assert "contract_liabilities" in columns
        assert "operating_cost" in columns
        assert "operating_cost_single_quarter" in columns
    finally:
        conn.close()


def test_ensure_table_migrates_existing_gpcw_manifest_schema():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE mart_tdx_gpcw_file_manifest (
                filename TEXT PRIMARY KEY,
                parse_status TEXT
            )
            """
        )

        tdx_affair_client._ensure_table(conn)
        columns = {
            row[0]
            for row in conn.execute("DESCRIBE mart_tdx_gpcw_file_manifest").fetchall()
        }

        assert "report_date" in columns
        assert "download_sha256" in columns
        assert "file_list_hash" in columns
        assert "row_count" in columns
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


def test_build_tdx_gpcw_auto_features_partial_rebuilds_report_date_only():
    conn = duck_mem()
    try:
        tdx_affair_client._ensure_table(conn)
        raw_rows = [
            ("000001", "2026-03-31", 10.0, 100.0),
            ("000002", "2026-03-31", 12.0, 120.0),
            ("000001", "2026-06-30", 20.0, 200.0),
            ("000002", "2026-06-30", 24.0, 240.0),
        ]
        conn.executemany(
            """
            INSERT INTO raw_gpcw_detail
            (stock_code, report_date, contract_liabilities, revenue)
            VALUES (?, ?, ?, ?)
            """,
            raw_rows,
        )
        conn.executemany(
            """
            INSERT INTO raw_tdx_gpcw_wide
            (stock_code, report_date, source_file, field_values_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    stock_code,
                    report_date,
                    "gpcw_test.zip",
                    json.dumps(
                        {"合同负债(万元)": contract_liabilities, "营业收入": revenue},
                        ensure_ascii=False,
                    ),
                )
                for stock_code, report_date, contract_liabilities, revenue in raw_rows
            ],
        )
        profile_tdx_gpcw_fields(conn, profile_run_id="partial_profile", min_coverage=0.0)

        full = build_tdx_gpcw_auto_features(
            conn,
            feature_set_id="test_auto",
            profile_run_id="partial_profile",
            max_base_fields=4,
        )
        before_0331 = conn.execute(
            """
            SELECT COUNT(*) FROM fact_tdx_gpcw_auto_feature_quarterly
            WHERE feature_set_id = 'test_auto' AND report_date = '2026-03-31'
            """
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE raw_gpcw_detail
            SET contract_liabilities = 200.0
            WHERE stock_code = '000001' AND report_date = '2026-06-30'
            """
        )

        partial = build_tdx_gpcw_auto_features(
            conn,
            feature_set_id="test_auto",
            profile_run_id="partial_profile",
            max_base_fields=4,
            report_dates=["20260630"],
        )
        after_0331 = conn.execute(
            """
            SELECT COUNT(*) FROM fact_tdx_gpcw_auto_feature_quarterly
            WHERE feature_set_id = 'test_auto' AND report_date = '2026-03-31'
            """
        ).fetchone()[0]
        rebuilt_value = conn.execute(
            """
            SELECT feature_value
            FROM fact_tdx_gpcw_auto_feature_quarterly
            WHERE feature_set_id = 'test_auto'
              AND stock_code = '000001'
              AND report_date = '2026-06-30'
              AND feature_name = 'auto_contract_liabilities_level'
            """
        ).fetchone()[0]

        assert full["rows"] > 0
        assert before_0331 > 0
        assert partial["rebuilt_report_dates"] == ["2026-06-30"]
        assert partial["rebuilt_rows"] > 0
        assert partial["rebuilt_quarters"] == 1
        assert after_0331 == before_0331
        assert rebuilt_value == pytest.approx(200.0)
    finally:
        conn.close()
