import sqlite3
import sys
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


def test_fetch_latest_snapshot_batch_uses_shared_server_iterator(monkeypatch):
    factory_calls = []

    class FakeClient:
        def __init__(self, server):
            self.server = server

        def finance(self, symbol):
            if self.server == ("2.2.2.2", 7709):
                return pd.DataFrame([{"updated_date": "2026-04-13", "zongzichan": 100.0}])
            return pd.DataFrame()

        def close(self):
            return None

    class FakeQuotes:
        @staticmethod
        def factory(*, server, **_kwargs):
            factory_calls.append(server)
            if server == ("1.1.1.1", 7709):
                raise RuntimeError("first server failed")
            return FakeClient(server)

    monkeypatch.setattr(financial_client, "iter_tdx_servers", lambda: (("1.1.1.1", 7709), ("2.2.2.2", 7709)))
    monkeypatch.setattr(financial_client, "get_tdx_quotes_class", lambda: FakeQuotes)

    result = financial_client._fetch_latest_snapshot_batch(["000001"])

    assert factory_calls == [("1.1.1.1", 7709), ("2.2.2.2", 7709)]
    assert list(result.keys()) == ["000001"]


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
                    [{"report_date": 20260331.0, "基本每股收益": 1.23}],
                    index=["000001"],
                )

        monkeypatch.setattr(tdx_affair_client, "get_tdx_affair_class", lambda: FakeAffair)

        result = tdx_affair_client.sync_gpcw_files(conn, quarters=1, downdir=str(tmp_path))
        row = conn.execute(
            "SELECT stock_code, report_date, eps FROM raw_gpcw_detail WHERE stock_code = '000001'"
        ).fetchone()

        assert result["files_synced"] == 1
        assert result["rows_upserted"] == 1
        assert row == ("000001", "2026-03-31", 1.23)
    finally:
        conn.close()