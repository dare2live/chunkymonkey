"""TDX unadjusted daily K recon: mapping, banned qfq, same OHLC set-diff as Fuyao."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from conftest import duck_mem
from services.data_sources.fuyao_kline_recon import ACCEPTED_K_TABLE
from services.data_sources.tdxhub_kline_recon import (
    compare_tdx_kline,
    lday_stem_ts_code,
    load_tdx_kline,
    protocol_market,
    records_to_rows,
    reject_tdx_adjust,
)


def test_protocol_market_uses_suffix_not_leading_nine():
    assert protocol_market("920008.BJ") == (2, "920008")
    assert protocol_market("000001.SZ") == (0, "000001")
    assert protocol_market("600519.SH") == (1, "600519")


def test_lday_stem_drops_shanghai_index():
    assert lday_stem_ts_code("sz000001") == "000001.SZ"
    assert lday_stem_ts_code("sh600519") == "600519.SH"
    assert lday_stem_ts_code("sh000001") is None
    assert lday_stem_ts_code("sz399001") is None


def test_qfq_is_rejected():
    with pytest.raises(ValueError, match="banned tdx adjust"):
        reject_tdx_adjust("qfq")
    reject_tdx_adjust(None)
    reject_tdx_adjust("")


def test_tdx_kline_set_diff_and_lot_volume_scale():
    con = duck_mem()
    load_tdx_kline(
        con,
        [
            ("000001.SZ", date(2026, 8, 20), 10.0, 10.2, 9.8, 10.1, 1000.0, 1_010_000.0),
            ("600519.SH", date(2026, 8, 20), 1400.0, 1410.0, 1390.0, 1405.05, 500.0, 70_000_000.0),
            ("000002.SZ", date(2026, 8, 20), 8.0, 8.1, 7.9, 8.0, 100.0, 80_000.0),
        ],
    )
    con.execute(
        f"""
        CREATE TABLE {ACCEPTED_K_TABLE} (
            ts_code VARCHAR, trade_date DATE,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            vol DOUBLE, amount DOUBLE
        )
        """
    )
    con.executemany(
        f"INSERT INTO {ACCEPTED_K_TABLE} VALUES (?, DATE '2026-08-20', ?, ?, ?, ?, ?, ?)",
        [
            ("000001.SZ", 10.0, 10.2, 9.8, 10.1, 1000.0, 1010.0),
            ("600519.SH", 1400.0, 1410.0, 1390.0, 1400.00, 500.0, 70000.0),
            ("600000.SH", 8.0, 8.1, 7.9, 8.0, 100.0, 80.0),
        ],
    )
    report = compare_tdx_kline(con)
    assert report["source"] == "tdxhub_unadjusted"
    assert report["intersection"] == 2
    assert report["only_source"] == 1
    assert report["only_accepted"] == 1
    assert report["ohlc_match"] == 1
    assert report["ohlc_mismatch"] == 1
    assert report["vol_scale_hypothesis"] == "confirmed"
    assert report["amount_scale_hypothesis"] == "confirmed"


def test_hq_transport_error_and_env_candidate_order(monkeypatch):
    from services.data_sources.sources.tdxhub import (
        is_hq_transport_error,
        iter_hq_candidates,
        parse_hq_server,
        quotes_client,
    )

    assert parse_hq_server("180.153.18.170:7709") == ("180.153.18.170", 7709)
    assert is_hq_transport_error(RuntimeError("head_buf is not 0x10 : b''"))
    assert not is_hq_transport_error(ValueError("banned tdx adjust='qfq'"))
    monkeypatch.delenv("TDXHUB_CONNECT_CFG", raising=False)
    monkeypatch.setenv("TDXHUB_HQ", "9.9.9.9:7709")
    got = iter_hq_candidates([("a", "1.1.1.1", 7709), ("b", "9.9.9.9", 7709)])
    assert got[0] == ("9.9.9.9", 7709)
    assert got[1] == ("1.1.1.1", 7709)

    import services.data_sources.sources.tdxhub as adapter

    hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)]
    monkeypatch.setattr(adapter, "iter_hq_candidates", lambda **_k: hosts)
    monkeypatch.setattr(adapter, "tcp_open", lambda ip, port, timeout=1.5: ip != "1.1.1.1")
    calls: list[tuple[str, int]] = []

    def fake_open(server, timeout=8.0):
        calls.append(server)
        if server[0] == "2.2.2.2":
            raise RuntimeError("head_buf is not 0x10 : b''")
        return type("C", (), {"server": server})()

    monkeypatch.setattr(adapter, "open_quotes", fake_open)
    monkeypatch.delenv("TDXHUB_HQ", raising=False)
    client = quotes_client()
    assert client.server == ("3.3.3.3", 7709)
    assert calls == [("2.2.2.2", 7709), ("3.3.3.3", 7709)]


def test_official_connect_cfg_is_read_without_bestip(tmp_path, monkeypatch):
    from services.data_sources.sources.tdxhub import iter_hq_candidates, load_connect_cfg_hq

    cfg = tmp_path / "connect.cfg"
    cfg.write_bytes(
        (
            "[HQHOST]\n"
            "HostNum=2\n"
            "HostName01=上海电信主站Z1\n"
            "IPAddress01=180.153.18.170\n"
            "Port01=7709\n"
            "HostName02=北京联通主站Z1\n"
            "IPAddress02=202.108.253.130\n"
            "Port02=7709\n"
        ).encode("gbk")
    )
    monkeypatch.delenv("TDXHUB_HQ", raising=False)
    monkeypatch.setenv("TDXHUB_CONNECT_CFG", str(cfg))
    got = iter_hq_candidates([("community", "1.1.1.1", 7709)])
    assert got[:3] == [
        ("180.153.18.170", 7709),
        ("202.108.253.130", 7709),
        ("1.1.1.1", 7709),
    ]
    assert load_connect_cfg_hq(cfg) == [
        ("180.153.18.170", 7709),
        ("202.108.253.130", 7709),
    ]
    src = Path(__file__).resolve().parents[2] / "services" / "data_sources" / "sources" / "tdxhub.py"
    text = src.read_text(encoding="utf-8")
    assert "bestip(" not in text
    assert "configure_hosts_from_connect_cfg" not in text


def test_records_to_rows_filters_window():
    rows = records_to_rows(
        [
            {"datetime": "2026-08-19 15:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 10, "amount": 100},
            {"datetime": "2026-08-20 15:00", "open": 2, "high": 2, "low": 2, "close": 2, "vol": 20, "amount": 200},
            {"datetime": "2026-08-21 15:00", "open": 3, "high": 3, "low": 3, "close": 3, "vol": 30, "amount": 300},
        ],
        "000001.SZ",
        start=date(2026, 8, 20),
        end=date(2026, 8, 20),
    )
    assert len(rows) == 1
    assert rows[0][1] == date(2026, 8, 20)
    assert rows[0][6] == 20.0


def test_bars_as_records_and_category_fallback():
    from services.data_sources.tdxhub_kline_recon import bars_as_records, fetch_unadjusted_bars

    class _Frame:
        empty = False

        def __init__(self, rows):
            self._rows = rows

        def __len__(self):
            return len(self._rows)

        def to_dict(self, orient):
            assert orient == "records"
            return self._rows

    rows = bars_as_records(
        _Frame(
            [
                {
                    "datetime": "2026-08-20 15:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 100,
                    "amount": 1000,
                }
            ]
        )
    )
    assert rows[0]["close"] == 10.5
    assert bars_as_records(None) == []

    class _Api:
        def __init__(self):
            self.calls = []

        def get_security_bars(self, cat, market, code, start, offset):
            self.calls.append((cat, market, code))
            if cat == 9:
                return None
            return [
                {
                    "datetime": "2026-08-20 15:00",
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "vol": 10,
                    "amount": 100,
                }
            ]

    client = type("C", (), {"client": _Api()})()
    got = fetch_unadjusted_bars(
        client, "000001.SZ", start=date(2026, 8, 20), end=date(2026, 8, 20), offset=8
    )
    assert len(got) == 1
    assert client._cm_daily_category == 4
    assert client.client.calls[0][0] == 9
    assert client.client.calls[1][0] == 4
