"""TDX host memory: remember the host whose handshake actually succeeded.

TCP-open is misleading (several hosts accept TCP then fail the TDX
handshake, each failure costing a full timeout — see ``tdxhub.py``
docstring). Once a host's handshake has actually answered for a given
protocol, retry it first next time instead of re-walking the whole
candidate table. HQ and MAC are different wire frames over the same
catalog, so their memories must stay isolated: an HQ handshake success
says nothing about MAC handshake success on the same host.

The memory now also persists to disk (``TDXHUB_HOST_MEMORY_PATH``) so a
*new process* — tomorrow's daily sync, a parallel backfill worker — does
not have to re-pay the cold candidate walk either. Every test here is
isolated from the real ``data/scratch/`` file via the autouse fixture
below, which points the env var at a fresh ``tmp_path`` per test — this
project has a standing lesson that tests must carry their own fixture
and never assert on host-environment state
(``feedback-test-must-carry-its-own-fixture``).

All handshakes here are faked via monkeypatch — no network access.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import services.data_sources.sources.tdxhub as adapter
import services.data_sources.tdxhub_mac as mac


@pytest.fixture(autouse=True)
def _clear_host_memory(monkeypatch, tmp_path):
    """Isolate every test from both the in-memory cache and any real
    on-disk host-memory file.

    ``TDXHUB_HOST_MEMORY_PATH`` is pointed at a fresh per-test
    ``tmp_path`` so nothing here ever reads or writes the real
    ``data/scratch/tdxhub_host_memory.json`` — that file does not even
    need to exist for this suite to run. ``monkeypatch`` restores the
    env var automatically at teardown. The in-memory dict is cleared
    before and after so tests never bleed into each other or into
    unrelated test files sharing the process.
    """
    monkeypatch.setenv(
        "TDXHUB_HOST_MEMORY_PATH", str(tmp_path / "tdxhub_host_memory.json")
    )
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


# --- cross-process persistence -------------------------------------------


def test_persists_across_simulated_process_restart():
    """Write in "process 1", clear the in-memory cache to simulate a brand
    new "process 2" starting cold, and confirm it hits the disk-backed
    memory on its very first lookup — this is the whole point of the fix:
    a new process must not re-pay the full candidate walk."""
    adapter.remember_good_host("hq", ("7.7.7.7", 7709))

    adapter._LAST_GOOD_HOST.clear()  # simulate a fresh process

    ordered = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709), ("2.2.2.2", 7709)])

    assert ordered[0] == ("7.7.7.7", 7709)
    assert adapter._LAST_GOOD_HOST["hq"] == ("7.7.7.7", 7709)  # hydrated back into memory


def test_persisted_entry_carries_a_timestamp():
    """No active TTL expiry (see tdxhub.py remember_good_host docstring),
    but every persisted entry must carry ``saved_at`` so a TTL — or just
    manual diagnosis of a stale memory — can be added later without a
    schema migration."""
    adapter.remember_good_host("hq", ("7.7.7.7", 7709))

    path = Path(os.environ["TDXHUB_HOST_MEMORY_PATH"])
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["hq"]["ip"] == "7.7.7.7"
    assert raw["hq"]["port"] == 7709
    assert isinstance(raw["hq"]["saved_at"], (int, float))
    assert raw["hq"]["saved_at"] > 0


# --- disk failure modes degrade silently ----------------------------------


def test_missing_memory_file_degrades_silently():
    """No file at all (first run ever, or data/scratch/ swept clean) must
    behave exactly like an empty cache, not raise."""
    path = Path(os.environ["TDXHUB_HOST_MEMORY_PATH"])
    assert not path.exists()

    ordered = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709), ("2.2.2.2", 7709)])

    assert ordered == [("1.1.1.1", 7709), ("2.2.2.2", 7709)]
    assert "hq" not in adapter._LAST_GOOD_HOST


def test_corrupt_memory_file_degrades_silently():
    """A half-written or hand-edited file that is not valid JSON must not
    blow up the candidate walk — it must be treated as no memory at all."""
    path = Path(os.environ["TDXHUB_HOST_MEMORY_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json::", encoding="utf-8")

    ordered = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709), ("2.2.2.2", 7709)])

    assert ordered == [("1.1.1.1", 7709), ("2.2.2.2", 7709)]
    assert "hq" not in adapter._LAST_GOOD_HOST


def test_non_object_json_degrades_silently():
    """Valid JSON that isn't a ``{...}`` object (e.g. a bare list) must
    also collapse to "no memory" rather than crash on ``.get``."""
    path = Path(os.environ["TDXHUB_HOST_MEMORY_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")

    ordered = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709)])

    assert ordered == [("1.1.1.1", 7709)]


def test_write_failure_does_not_raise(monkeypatch):
    """Simulate "no write permission" deterministically (chmod-based tests
    are flaky under root/CI users that bypass permission bits) by making
    the final atomic rename blow up. remember_good_host must swallow it —
    persistence failing must never block taking data — and the in-process
    cache (what actually matters for *this* process) must still work."""

    def _boom(*_a, **_k):
        raise PermissionError("no write permission")

    monkeypatch.setattr(os, "replace", _boom)

    adapter.remember_good_host("hq", ("3.3.3.3", 7709))  # must not raise

    assert adapter._LAST_GOOD_HOST["hq"] == ("3.3.3.3", 7709)


def test_read_failure_does_not_raise(monkeypatch):
    """A read-side OSError (e.g. permission denied) must also degrade to
    "no memory" instead of propagating out of the candidate walk."""
    path = Path(os.environ["TDXHUB_HOST_MEMORY_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hq": {"ip": "7.7.7.7", "port": 7709}}), encoding="utf-8")

    real_read_text = Path.read_text

    def _boom(self, *a, **k):
        if self == path:
            raise PermissionError("no read permission")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _boom)

    ordered = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709)])

    assert ordered == [("1.1.1.1", 7709)]  # no crash, treated as no memory


# --- eviction and cross-protocol isolation, mirrored onto disk -----------


def test_forget_good_host_evicts_disk_too():
    """Eviction must not be memory-only, or a host that has gone offline
    would keep coming back to life every time a new process hydrates from
    the stale file — the exact bug this whole feature must not reintroduce."""
    adapter.remember_good_host("hq", ("8.8.8.8", 7709))

    adapter.forget_good_host("hq", ("8.8.8.8", 7709))

    adapter._LAST_GOOD_HOST.clear()  # simulate a fresh process re-hydrating
    ordered = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709), ("2.2.2.2", 7709)])

    assert ordered == [("1.1.1.1", 7709), ("2.2.2.2", 7709)]


def test_forget_good_host_mismatched_server_does_not_evict_disk():
    """Same match-before-evict guard the in-memory eviction has always had
    (see ``forget_good_host`` docstring) must hold on disk too: a failure
    on some *other* host in the walk must not wipe out a still-good
    persisted memory."""
    adapter.remember_good_host("hq", ("8.8.8.8", 7709))

    adapter.forget_good_host("hq", ("9.9.9.9", 7709))  # different host, no match

    adapter._LAST_GOOD_HOST.clear()  # simulate a fresh process re-hydrating
    ordered = adapter.hosts_with_memory("hq", [("1.1.1.1", 7709)])

    assert ordered[0] == ("8.8.8.8", 7709)  # survives — mismatched target didn't evict it


def test_hq_and_mac_disk_slots_are_isolated():
    """The wire-frame isolation this module has always enforced in memory
    (see module docstring) must hold on disk too: HQ and MAC are separate
    keys in the same JSON object, never one slot."""
    adapter.remember_good_host("hq", ("1.1.1.1", 7709))
    adapter.remember_good_host("mac", ("2.2.2.2", 7709))

    adapter._LAST_GOOD_HOST.clear()  # simulate a fresh process re-hydrating both

    ordered_hq = adapter.hosts_with_memory("hq", [("9.9.9.9", 7709)])
    ordered_mac = adapter.hosts_with_memory("mac", [("9.9.9.9", 7709)])

    assert ordered_hq[0] == ("1.1.1.1", 7709)
    assert ordered_mac[0] == ("2.2.2.2", 7709)

    path = Path(os.environ["TDXHUB_HOST_MEMORY_PATH"])
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["hq"]["ip"] == "1.1.1.1"
    assert raw["mac"]["ip"] == "2.2.2.2"
