"""TDX full-history unadjusted K pagination, xdxr events, tdx_block namespace."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.data_sources.sources.tdxhub import block, xdxr
from services.data_sources.taxonomy_recon import (
    compare_named_memberships,
    name_collision_relation,
    reject_tdx_block,
)
from services.data_sources.tdxhub_block import (
    NAMESPACE as TDX_BLOCK_NS,
    encode_block_dat,
    parse_block_dat,
    records_to_blocks,
)
from services.data_sources.tdxhub_kline_recon import (
    MAX_BARS_PER_PAGE,
    fetch_unadjusted_bars,
    live_history_probe,
    reject_tdx_adjust,
)
from services.data_sources.tdxhub_xdxr import OHLCV_KEYS, map_xdxr_events

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
XDXR_FIXTURE = FIXTURES / "tdx_xdxr_events.json"
BLOCK_FIXTURE = FIXTURES / "tdx_block_sample.json"
KLINE_RECON = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "data_sources"
    / "tdxhub_kline_recon.py"
)
TEN_YEAR_BARS = 2500


def _weekdays_ending(end: date, n: int) -> list[date]:
    """Synthetic weekdays for a fake HQ. Not a CN holiday calendar."""
    days: list[date] = []
    cursor = end
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


class _PagedBars:
    """Newest-first HQ: ``start=0`` is the most recent page. Max 800/call."""

    def __init__(self, n: int = TEN_YEAR_BARS, end: date = date(2026, 8, 28)):
        self.calls: list[tuple[int, int, str, int, int]] = []
        newest_first = list(reversed(_weekdays_ending(end, n)))
        self.bars = [
            {
                "datetime": f"{d.isoformat()} 15:00",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "vol": 1.0,
                "amount": 1.0,
            }
            for d in newest_first
        ]

    def get_security_bars(self, cat, market, code, start, count):
        self.calls.append((int(cat), int(market), str(code), int(start), int(count)))
        if int(count) > MAX_BARS_PER_PAGE:
            raise AssertionError(f"protocol max is {MAX_BARS_PER_PAGE}, got count={count}")
        if int(cat) != 9:
            return []
        lo = int(start)
        hi = lo + int(count)
        return self.bars[lo:hi]


def test_ten_year_depth_requires_multiple_start_pages():
    api = _PagedBars()
    client = type("C", (), {"client": api})()
    days = _weekdays_ending(date(2026, 8, 28), TEN_YEAR_BARS)
    rows = fetch_unadjusted_bars(
        client, "000001.SZ", start=days[0], end=days[-1]
    )
    starts = [c[3] for c in api.calls]
    counts = [c[4] for c in api.calls]
    assert all(n <= MAX_BARS_PER_PAGE for n in counts)
    assert MAX_BARS_PER_PAGE in counts
    assert 0 in starts
    assert 800 in starts
    assert 1600 in starts
    assert 2400 in starts
    assert starts != [0]
    assert not (len(api.calls) == 1 and api.calls[0][3:] == (0, 30))
    assert not (len(api.calls) == 1 and api.calls[0][3:] == (0, 800))
    assert len({row[1] for row in rows}) == TEN_YEAR_BARS
    assert len(rows) == TEN_YEAR_BARS
    assert {c[0] for c in api.calls} == {9}


def test_single_page_cannot_claim_ten_year_coverage():
    """A one-shot ``start=0, count=30|800`` window is not 10y depth."""
    api = _PagedBars()
    one = api.get_security_bars(9, 0, "000001", 0, 800)
    thirty = _PagedBars().get_security_bars(9, 0, "000001", 0, 30)
    assert len(one) == 800
    assert len(thirty) == 30
    assert len(one) < TEN_YEAR_BARS
    assert len(thirty) < TEN_YEAR_BARS


def test_qfq_and_hfq_rejected_on_history_paths():
    client = type("C", (), {"client": _PagedBars()})()
    with pytest.raises(ValueError, match="banned tdx adjust"):
        fetch_unadjusted_bars(
            client,
            "000001.SZ",
            start=date(2026, 8, 1),
            end=date(2026, 8, 28),
            adjust="qfq",
        )
    with pytest.raises(ValueError, match="banned tdx adjust"):
        reject_tdx_adjust("hfq")
    with pytest.raises(ValueError, match="banned tdx adjust"):
        xdxr(client, "000001.SZ", adjust="qfq")
    with pytest.raises(ValueError, match="banned tdx adjust"):
        block(client, adjust="hfq")


def test_pagination_does_not_copy_holiday_guess():
    text = KLINE_RECON.read_text(encoding="utf-8")
    assert "2.8" not in text
    assert "3.5" not in text
    assert "get_k_data" not in text


def test_xdxr_is_events_not_ohlcv():
    fixture = json.loads(XDXR_FIXTURE.read_text(encoding="utf-8"))

    class _Api:
        def __init__(self):
            self.calls: list[tuple[int, str]] = []

        def get_xdxr_info(self, market, code):
            self.calls.append((int(market), str(code)))
            return fixture["events"]

    api = _Api()
    client = type("C", (), {"client": api})()
    payload = xdxr(client, "000001.SZ")
    assert api.calls == [(0, "000001")]
    assert payload["kind"] == "corporate_action_events"
    assert payload["grain"] == "event"
    assert payload["is_qfq"] is False
    assert payload["is_daily_factor"] is False
    assert payload["is_ohlcv"] is False
    assert payload["status"] == "ok"
    assert len(payload["events"]) == 2
    assert payload["events"][0]["event_date"] == "2024-06-13"
    assert payload["events"][0]["kind"] == "corporate_action_event"
    assert payload["events"][0]["category"] == 1
    for event in payload["events"]:
        for key in OHLCV_KEYS:
            assert key not in event

    bj_calls: list[tuple[int, str]] = []

    class _Bj:
        def get_xdxr_info(self, market, code):
            bj_calls.append((int(market), str(code)))
            return []

    xdxr(type("C", (), {"client": _Bj()})(), "920008.BJ")
    assert bj_calls == [(2, "920008")]

    mapped = map_xdxr_events(
        [{**fixture["events"][0], "open": 10, "close": 11, "vol": 1}],
        "000001.SZ",
        market=0,
        code="000001",
    )
    assert "open" not in mapped["events"][0]
    assert mapped["is_ohlcv"] is False


def test_xdxr_and_block_reject_mac_socket():
    mac = type("M", (), {"protocol": "mac"})()
    with pytest.raises(TypeError, match="quotes_client"):
        xdxr(mac, "000001.SZ")
    with pytest.raises(TypeError, match="quotes_client"):
        block(mac)


def test_tdx_block_codec_is_own_namespace():
    fixture = json.loads(BLOCK_FIXTURE.read_text(encoding="utf-8"))
    raw = encode_block_dat(fixture["blocks"])
    parsed = parse_block_dat(raw, source_file=fixture["source_file"])
    assert parsed["namespace"] == TDX_BLOCK_NS
    assert parsed["crosswalk"] is None
    assert parsed["merged_namespaces"] == ()
    assert parsed["status"] == "ok"
    assert [b["name"] for b in parsed["blocks"]] == ["银行", "白酒"]
    assert parsed["blocks"][0]["vendor_block_id"] == "block_gn.dat:0:101"
    assert parsed["blocks"][0]["members"] == ["000001", "600000"]
    assert parsed["blocks"][1]["vendor_block_id"] == "block_gn.dat:1:202"
    by_name = {b["name"]: b["members"] for b in parsed["blocks"]}
    collision = name_collision_relation("银行")
    assert collision["identity"] is False
    assert collision["relation"] == "name_collision_candidate"
    with pytest.raises(ValueError, match="four taxonomy chains"):
        reject_tdx_block("tdx_block")
    with pytest.raises(ValueError, match="four-chain"):
        compare_named_memberships(
            {"银行": by_name["银行"]},
            {"银行": ["000001.SZ", "600000.SH"]},
            left_ns="tdx_block",
            right_ns="sw_industry",
        )

    class _Api:
        def get_and_parse_block_info(self, tofile):
            assert tofile == "block_gn.dat"
            return [
                {
                    "blockname": "银行",
                    "block_type": 101,
                    "code_index": 0,
                    "code": "000001",
                },
                {
                    "blockname": "银行",
                    "block_type": 101,
                    "code_index": 1,
                    "code": "600000",
                },
            ]

    live_shaped = block(
        type("C", (), {"client": _Api()})(), tofile="block_gn.dat"
    )
    assert live_shaped["namespace"] == TDX_BLOCK_NS
    assert live_shaped["blocks"][0]["members"] == ["000001", "600000"]
    grouped = records_to_blocks(
        [{"blockname": "银行", "block_type": 101, "code": "000001"}],
        source_file="block.dat",
    )
    assert grouped["namespace"] == TDX_BLOCK_NS


def test_live_probe_is_unprobed_without_hq(monkeypatch):
    monkeypatch.delenv("TDXHUB_CONNECT_CFG", raising=False)
    monkeypatch.delenv("TDXHUB_HQ", raising=False)
    got = live_history_probe()
    assert got["status"] == "live_unprobed"
    assert got["bars"] == "live_unprobed"
    assert got["xdxr"] == "live_unprobed"
    assert got["block"] == "live_unprobed"
    assert "unset" in got["reason"]
