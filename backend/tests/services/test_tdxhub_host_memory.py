"""TDX host memory: remember the host whose handshake actually succeeded.

TCP-open is misleading (several hosts accept TCP then fail the TDX
handshake, each failure costing a full timeout — see ``tdxhub.py``
docstring). Once a host's handshake has actually answered for a given
protocol, retry it first next time instead of re-walking the whole
candidate table. HQ and MAC are different wire frames over the same
catalog, so their memories must stay isolated: an HQ handshake success
says nothing about MAC handshake success on the same host.

All handshakes here are faked via monkeypatch — no network access.
"""
from __future__ import annotations

import pytest

import services.data_sources.sources.tdxhub as adapter
import services.data_sources.tdxhub_mac as mac


@pytest.fixture(autouse=True)
def _clear_host_memory():
    """Every test starts and ends with an empty cache so tests never bleed
    into each other or into unrelated test files sharing the process."""
    adapter._LAST_GOOD_HOST.clear()
    yield
    adapter._LAST_GOOD_HOST.clear()


def _fake_open_quotes(fail_ips: frozenset[str] = frozenset()):
    calls: list[tuple[str, int]] = []

    def _open(server, timeout=8.0):
        calls.append(server)
        if server[0] in fail_ips:
            raise RuntimeError(f"head_buf is not 0x10 : simulated fail for {server}")
        return type("FakeQuotes", (), {"server": server})()

    return _open, calls


def _fake_open_mac(fail_ips: frozenset[str] = frozenset()):
    calls: list[tuple[str, int]] = []

    def _open(server, timeout=8.0):
        calls.append(server)
        if server[0] in fail_ips:
            raise RuntimeError(f"simulated MAC fail for {server}")
        return type("FakeMac", (), {"server": server})()

    return _open, calls


def test_empty_cache_walks_and_remembers_success(monkeypatch):
    hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)]
    monkeypatch.setattr(adapter, "iter_hq_candidates", lambda **_k: hosts)
    monkeypatch.setattr(adapter, "tcp_open", lambda ip, port, timeout=1.5: True)
    open_fn, calls = _fake_open_quotes(fail_ips=frozenset({"1.1.1.1", "2.2.2.2"}))
    monkeypatch.setattr(adapter, "open_quotes", open_fn)

    client = adapter.quotes_client()

    assert client.server == ("3.3.3.3", 7709)
    assert calls == hosts
    assert adapter._LAST_GOOD_HOST["hq"] == ("3.3.3.3", 7709)


def test_cached_host_is_tried_first(monkeypatch):
    adapter.remember_good_host("hq", ("3.3.3.3", 7709))
    hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)]
    monkeypatch.setattr(adapter, "iter_hq_candidates", lambda **_k: hosts)
    monkeypatch.setattr(adapter, "tcp_open", lambda ip, port, timeout=1.5: True)
    open_fn, calls = _fake_open_quotes()
    monkeypatch.setattr(adapter, "open_quotes", open_fn)

    client = adapter.quotes_client()

    # Remembered host is the very first (and only) handshake attempt.
    assert calls == [("3.3.3.3", 7709)]
    assert client.server == ("3.3.3.3", 7709)


def test_cached_host_fails_falls_back_and_is_evicted(monkeypatch):
    adapter.remember_good_host("hq", ("3.3.3.3", 7709))
    hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)]
    monkeypatch.setattr(adapter, "iter_hq_candidates", lambda **_k: hosts)
    monkeypatch.setattr(adapter, "tcp_open", lambda ip, port, timeout=1.5: True)
    open_fn, calls = _fake_open_quotes(fail_ips=frozenset({"3.3.3.3", "1.1.1.1"}))
    monkeypatch.setattr(adapter, "open_quotes", open_fn)

    client = adapter.quotes_client()

    assert calls[0] == ("3.3.3.3", 7709)  # cached host attempted first
    assert client.server == ("2.2.2.2", 7709)  # falls back and succeeds
    assert adapter._LAST_GOOD_HOST["hq"] == ("2.2.2.2", 7709)  # re-remembered
    assert ("3.3.3.3", 7709) not in adapter._LAST_GOOD_HOST.values()  # evicted


def test_hq_and_mac_memory_are_isolated(monkeypatch):
    adapter.remember_good_host("hq", ("9.9.9.9", 7709))

    mac_hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709)]
    monkeypatch.setattr(mac, "iter_hq_candidates", lambda **_k: mac_hosts)
    monkeypatch.setattr(mac, "tcp_open", lambda ip, port, timeout=1.5: True)
    open_fn, calls = _fake_open_mac()
    monkeypatch.setattr(mac, "open_mac", open_fn)

    conn = mac.mac_client()

    # mac's remembered-host-first behaviour is not polluted by hq's cache.
    assert calls[0] == ("1.1.1.1", 7709)
    assert conn.server == ("1.1.1.1", 7709)
    assert adapter._LAST_GOOD_HOST["mac"] == ("1.1.1.1", 7709)
    assert adapter._LAST_GOOD_HOST["hq"] == ("9.9.9.9", 7709)  # untouched by the mac run

    # And the reverse: mac's memory does not leak into hq ordering.
    ordered_hq = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709), ("2.2.2.2", 7709)])
    assert ordered_hq[0] == ("9.9.9.9", 7709)  # still hq's own remembered host
    assert adapter._LAST_GOOD_HOST["mac"] == ("1.1.1.1", 7709)  # mac slot unaffected by hq lookup


def test_explicit_server_bypasses_cache_hq(monkeypatch):
    adapter.remember_good_host("hq", ("9.9.9.9", 7709))
    open_fn, calls = _fake_open_quotes()
    monkeypatch.setattr(adapter, "open_quotes", open_fn)

    client = adapter.quotes_client(server=("5.5.5.5", 7709))

    assert client.server == ("5.5.5.5", 7709)
    assert calls == [("5.5.5.5", 7709)]  # candidate loop never entered
    assert adapter._LAST_GOOD_HOST["hq"] == ("9.9.9.9", 7709)  # cache neither read nor written


def test_explicit_server_bypasses_cache_mac(monkeypatch):
    adapter.remember_good_host("mac", ("9.9.9.9", 7709))
    open_fn, calls = _fake_open_mac()
    monkeypatch.setattr(mac, "open_mac", open_fn)

    conn = mac.mac_client(server=("5.5.5.5", 7709))

    assert conn.server == ("5.5.5.5", 7709)
    assert calls == [("5.5.5.5", 7709)]
    assert adapter._LAST_GOOD_HOST["mac"] == ("9.9.9.9", 7709)


def test_all_candidates_fail_raises_original_error_shape_hq(monkeypatch):
    hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709)]
    monkeypatch.setattr(adapter, "iter_hq_candidates", lambda **_k: hosts)
    monkeypatch.setattr(adapter, "tcp_open", lambda ip, port, timeout=1.5: True)
    open_fn, _calls = _fake_open_quotes(fail_ips=frozenset({"1.1.1.1", "2.2.2.2"}))
    monkeypatch.setattr(adapter, "open_quotes", open_fn)

    with pytest.raises(RuntimeError, match=r"no handshake-ready TDX HQ after 2 TCP-open hosts"):
        adapter.quotes_client()

    assert "hq" not in adapter._LAST_GOOD_HOST


def test_all_candidates_fail_raises_original_error_shape_mac(monkeypatch):
    hosts = [("1.1.1.1", 7709), ("2.2.2.2", 7709)]
    monkeypatch.setattr(mac, "iter_hq_candidates", lambda **_k: hosts)
    monkeypatch.setattr(mac, "tcp_open", lambda ip, port, timeout=1.5: True)
    open_fn, _calls = _fake_open_mac(fail_ips=frozenset({"1.1.1.1", "2.2.2.2"}))
    monkeypatch.setattr(mac, "open_mac", open_fn)

    with pytest.raises(RuntimeError, match=r"no handshake-ready TDX MAC after 2 TCP-open hosts"):
        mac.mac_client()

    assert "mac" not in adapter._LAST_GOOD_HOST


def test_hosts_with_memory_dedupes_and_preserves_order():
    adapter.remember_good_host("hq", ("2.2.2.2", 7709))

    ordered = adapter.hosts_with_memory(
        "hq", [("1.1.1.1", 7709), ("2.2.2.2", 7709), ("3.3.3.3", 7709)]
    )

    assert ordered == [("2.2.2.2", 7709), ("1.1.1.1", 7709), ("3.3.3.3", 7709)]
