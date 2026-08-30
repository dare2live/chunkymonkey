"""TDX MAC capital_flow: codec, empty-payload failover, no bestip."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from services.data_sources.sibling_repos import get_sibling_repos
from services.data_sources.tdxhub_mac import (
    CMD_STOCK_ZJLX,
    JSON_PREFIX_LEN,
    MAC_HEAD_FLAG_CAPITAL_FLOW,
    MAC_HEAD_FLAG_DEFAULT,
    MSG_ID_CAPITAL_FLOW,
    capital_flow,
    encode_capital_flow_request,
    encode_mac_frame,
    mac_client,
    map_vendor_imbalance,
    parse_capital_flow_json,
    reject_mac_qfq,
    setup_command_bytes,
    tdxhub_setup_command_bytes,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "tdx_mac_capital_flow.json"
)
_FORBIDDEN = (
    "from_best_host",
    "ping_mac_all",
    "save_best_host",
    "_FALLBACK_MAC_HOSTS",
)


def _tdx_frame(body: bytes) -> bytes:
    return struct.pack("<IIIHH", 0, 0, 0, len(body), len(body)) + body


def _flow_body(payload) -> bytes:
    blob = json.dumps(payload, ensure_ascii=False).encode("gbk")
    return b"\x00" * JSON_PREFIX_LEN + blob


class _FakeSock:
    def __init__(self, frames: list[bytes]):
        self._buf = bytearray(b"".join(frames))
        self.sent = bytearray()

    def recv(self, n: int) -> bytes:
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def close(self) -> None:
        return None

    def shutdown(self, *_a) -> None:
        return None

    def settimeout(self, *_a) -> None:
        return None


def test_mac_header_and_capital_flow_request_layout():
    default = encode_mac_frame(1, b"ab")
    flag, customize, version, zipsize, unzipsize = struct.unpack_from("<BIBHH", default)
    assert flag == MAC_HEAD_FLAG_DEFAULT
    assert customize == 0
    assert version == 1
    assert zipsize == unzipsize == 4

    raw = encode_capital_flow_request(0, "000001")
    flag, customize, version, zipsize, unzipsize = struct.unpack_from("<BIBHH", raw)
    assert flag == MAC_HEAD_FLAG_CAPITAL_FLOW
    assert customize == 0
    assert version == 1
    msg_id = struct.unpack_from("<H", raw, 10)[0]
    assert msg_id == MSG_ID_CAPITAL_FLOW
    market, code, cmd = struct.unpack("<H8s16x21s", raw[12:])
    assert market == 0
    assert code.split(b"\x00", 1)[0] == b"000001"
    assert cmd.split(b"\x00", 1)[0] == CMD_STOCK_ZJLX


def test_parse_fixture_json_roundtrip():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body = _flow_body([fixture["today"], fixture["five_days"]])
    parsed = parse_capital_flow_json(body)
    mapped = map_vendor_imbalance(parsed)
    assert mapped["status"] == "ok"
    assert mapped["layer"] == "tdx_mac_capital_flow"
    assert mapped["vendor_main_in"] == 12.5
    assert mapped["vendor_main_out"] == 3.0
    assert mapped["vendor_retail_in"] == 8.0
    assert mapped["vendor_retail_out"] == 9.5
    assert mapped["vendor_main_imbalance"] == 9.5
    assert mapped["accepted"] is False
    assert mapped["vendor_five_day_proxy"] == fixture["five_days"]
    assert "conserved" not in json.dumps(mapped).lower()


def test_parse_live_list_shaped_json():
    """MAC 0x1218 body is a JSON list, not a dict of English keys."""
    body = _flow_body([[12.5, 3.0, 8.0, 9.5], [1.0, 0.4, 0.2, 0.3, 0.0, 0.0]])
    parsed = parse_capital_flow_json(body)
    mapped = map_vendor_imbalance(parsed)
    assert mapped["status"] == "ok"
    assert mapped["vendor_main_in"] == 12.5
    assert mapped["vendor_main_out"] == 3.0
    assert mapped["vendor_retail_in"] == 8.0
    assert mapped["vendor_retail_out"] == 9.5
    assert mapped["vendor_main_imbalance"] == 9.5


def test_empty_payload_is_not_success():
    assert map_vendor_imbalance([])["status"] == "empty_recon"
    assert map_vendor_imbalance(None)["status"] == "empty_recon"
    assert parse_capital_flow_json(b"\x00" * JSON_PREFIX_LEN) == []
    assert parse_capital_flow_json(_flow_body([])) == []


def test_qfq_rejected_on_capital_flow():
    with pytest.raises(ValueError, match="banned tdx adjust"):
        reject_mac_qfq("qfq")
    with pytest.raises(TypeError, match="mac_client"):
        capital_flow(object(), 0, "000001")


def test_setup_bytes_match_tdxhub_setupcmd():
    if not get_sibling_repos().is_present("tdxhub"):
        pytest.skip(
            "sibling repo 'tdxhub' missing; CI environment does not include provider dependencies"
        )

    assert setup_command_bytes() == tdxhub_setup_command_bytes()


def test_empty_payload_host_is_skipped(monkeypatch):
    import services.data_sources.tdxhub_mac as mac

    hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709)]
    monkeypatch.setattr(mac, "iter_hq_candidates", lambda **_k: hosts)
    monkeypatch.setattr(mac, "tcp_open", lambda ip, port, timeout=1.5: True)

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    nonempty = [fixture["today"], fixture["five_days"]]

    def _connect(server, *, timeout):
        ip = server[0]
        setup = [_tdx_frame(b"x")] * 3
        if ip == "1.1.1.1":
            return _FakeSock(setup + [_tdx_frame(_flow_body([]))])
        return _FakeSock(setup + [_tdx_frame(_flow_body(nonempty))])

    monkeypatch.setattr(mac, "connect_raw", _connect)
    conn = mac_client()
    assert conn.server == ("2.2.2.2", 7709)
    assert conn.protocol == "mac"


def test_mac_sources_never_call_bestip_or_quotes_factory():
    root = Path(__file__).resolve().parents[2] / "services" / "data_sources"
    mac_text = (root / "tdxhub_mac.py").read_text(encoding="utf-8")
    adapter = (root / "sources" / "tdxhub.py").read_text(encoding="utf-8")
    assert "Quotes.factory" not in mac_text
    assert "bestip(" not in mac_text
    assert "config.set" not in mac_text
    assert "Path.home()" not in mac_text
    for needle in _FORBIDDEN:
        assert needle not in mac_text
        assert needle not in adapter
    # quotes_client still pins BESTIP in-process; MAC wrappers must not.
    wrappers = adapter.split("def mac_client", 1)[1]
    assert "Quotes.factory" not in wrappers
    assert "bestip(" not in wrappers
    assert "from_best_host" not in adapter
