"""TDXHub adapter — official sibling checkout. Unadjusted protocol/vipdoc only.

Do not call ``adjust=qfq/hfq``. That path is banned as execution SSOT.

Official HQ catalog is the TDX client's ``connect.cfg`` ``[HQHOST]`` list
(``TDXHUB_CONNECT_CFG``). ``HQ_HOSTS`` is a frozen snapshot of those official
client/broker names, not a live HTTP catalog and not random community IPs.

TCP-open is not enough: several HQ hosts accept TCP then return an empty
TDX header (``head_buf is not 0x10``). ``quotes_client`` walks hosts until
handshake + one daily bar for ``000001`` succeeds. ``mac_client`` walks the
same catalog on a *separate* raw socket until handshake + nonempty
``capital_flow`` for ``000001`` succeeds. Never runs tdxhub ``bestip`` (that
writes the tdxhub runtime config file). Never reuse the StdQuotes socket for
MAC frames.

``xdxr`` is corporate-action events (not qfq / not a daily factor).
``block`` is namespace ``tdx_block``, parallel to SW / DC / THS — names are
labels, not crosswalk keys. Both ride ``quotes_client``, never MAC.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from services.data_sources.sibling_repos import ensure_import_path

HQ_HOSTS_PROVENANCE = (
    "Frozen snapshot of official TDX client/broker HQ names "
    "(tdxpy transcribed dumped connect.cfg names + mootdx 双线主站 extras). "
    "tdxhub 292e761 2026-04-11 liveness-filtered (14/38 alive, dead commented); "
    "8ba706d 2026-04-13 merged tdxpy+mootdx to 117 and claimed all alive. "
    "Not a live official HTTP catalog (tdx.com.cn/connect.cfg 404). "
    "Live official list = local TDX client connect.cfg [HQHOST] via TDXHUB_CONNECT_CFG. "
    "TCP ping is not a TDX handshake."
)

ALIAS = "tdxhub"
_SMOKE_MARKET = 0
_SMOKE_CODE = "000001"  # rule-compliance: ok evidence=tdx-hq-handshake-ping-sz000001


def tdxhub_root() -> Path:
    return ensure_import_path(ALIAS, strict=True)


def parse_hq_server(raw: str) -> tuple[str, int]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty TDX HQ server")
    if ":" not in text:
        raise ValueError(f"TDX HQ must be ip:port, got {raw!r}")
    host, _, port_text = text.rpartition(":")
    return host.strip(), int(port_text)


def tcp_open(ip: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        sock = socket.create_connection((ip, int(port)), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


def is_hq_transport_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    needles = (
        "head_buf",
        "0x10",
        "timeout",
        "timed out",
        "connect",
        "connection",
        "broken pipe",
        "reset",
        "eof",
        "recv",
    )
    return any(n in text for n in needles)


def _hq_host_table() -> list[tuple[str, str, int]]:
    """Frozen official-name snapshot in tdxhub.consts. Not the live client catalog."""
    ensure_import_path(ALIAS, strict=True)
    from tdxhub.consts import HQ_HOSTS  # noqa: E402

    return [(str(name), str(ip), int(port)) for name, ip, port in HQ_HOSTS]


def load_connect_cfg_hq(path: str | Path) -> list[tuple[str, int]]:
    """Read official TDX client ``connect.cfg`` ``[HQHOST]`` entries.

    Uses tdxhub's parser only. Does not run ``bestip`` and does not write
    the tdxhub runtime config file.
    """
    cfg = Path(path)
    if not cfg.is_file():
        raise FileNotFoundError(f"TDX connect.cfg not found: {cfg}")
    ensure_import_path(ALIAS, strict=True)
    from tdxhub.server import parse_connect_cfg  # noqa: E402

    groups = parse_connect_cfg(cfg)
    return [(str(ip), int(port)) for _name, ip, port in groups.get("HQ") or []]


def iter_hq_candidates(
    hosts: Iterable[tuple[str, str, int]] | None = None,
    *,
    explicit: tuple[str, int] | None = None,
) -> list[tuple[str, int]]:
    ordered: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def _add(ip: str, port: int) -> None:
        key = (str(ip), int(port))
        if key in seen:
            return
        seen.add(key)
        ordered.append(key)

    if explicit is not None:
        _add(*explicit)
    env = os.environ.get("TDXHUB_HQ", "").strip()
    if env:
        _add(*parse_hq_server(env))
    cfg_path = os.environ.get("TDXHUB_CONNECT_CFG", "").strip()
    if cfg_path:
        for ip, port in load_connect_cfg_hq(cfg_path):
            _add(ip, port)
    for _name, ip, port in hosts if hosts is not None else _hq_host_table():
        _add(ip, port)
    return ordered


_LAST_GOOD_HOST: dict[str, tuple[str, int]] = {}

_HOST_MEMORY_PATH_ENV = "TDXHUB_HOST_MEMORY_PATH"
# data/scratch/ is gitignored (.gitignore line 33) — scratch/derived, never a
# source of truth. Losing this file just means the next process re-walks the
# candidate table once, same as before this cache existed; it is an
# optimization, never a dependency for taking data. No host IP is hardcoded
# here or anywhere else in this module — every entry is learned at runtime
# from a handshake that actually answered.
_DEFAULT_HOST_MEMORY_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "scratch" / "tdxhub_host_memory.json"
)


def _host_memory_path() -> Path:
    """Resolve the on-disk host-memory path.

    Env-overridable (``TDXHUB_HOST_MEMORY_PATH``) so tests can point this
    at a throwaway ``tmp_path`` instead of the real ``data/scratch/`` —
    otherwise state persisted by one test run would leak into the next.
    """
    override = os.environ.get(_HOST_MEMORY_PATH_ENV, "").strip()
    return Path(override) if override else _DEFAULT_HOST_MEMORY_PATH


def _parse_memory_entry(entry: Any) -> tuple[str, int] | None:
    if not isinstance(entry, dict):
        return None
    ip, port = entry.get("ip"), entry.get("port")
    if ip is None or port is None:
        return None
    try:
        return (str(ip), int(port))
    except (TypeError, ValueError):
        return None


def _read_host_memory_file() -> dict[str, Any]:
    """Best-effort read of the persisted host-memory file.

    Missing file, corrupt JSON, unreadable permissions, or a file that
    doesn't even hold a JSON object at the top level all collapse to
    "no memory" here — a bad cache file must never block taking data, it
    can only cost the same candidate walk a cold process would have paid
    anyway before this cache existed.
    """
    try:
        raw = _host_memory_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:  # rule-compliance: ok evidence=tdxhub-host-memory-read-best-effort
        return {}
    return data if isinstance(data, dict) else {}


def _write_host_memory_file(data: dict[str, Any]) -> None:
    """Best-effort atomic write of the whole host-memory file.

    Writes to a sibling temp file and ``os.replace``s it into place so a
    concurrent reader (another sync process starting up at the same
    time) can never observe a half-written file. Any failure along the
    way — missing directory (created if possible, otherwise swallowed),
    no write permission, disk full — is swallowed: the in-process
    ``_LAST_GOOD_HOST`` cache still works for this process, only
    cross-process persistence is lost for this write.
    """
    path = _host_memory_path()
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp_name, str(path))
        tmp_name = None
    except Exception:  # rule-compliance: ok evidence=tdxhub-host-memory-write-best-effort
        pass
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:  # rule-compliance: ok evidence=tdxhub-host-memory-tmp-cleanup-best-effort
                pass


def _remembered_host(protocol: str) -> tuple[str, int] | None:
    """In-memory lookup, falling back to disk and hydrating memory on hit.

    A fresh process starts with an empty ``_LAST_GOOD_HOST``; reading
    through to the persisted file here — once — is what makes the memory
    survive that restart. Once hydrated, later calls in the same process
    stay purely in-memory and never touch disk again (until
    ``forget_good_host`` evicts the slot).
    """
    key = str(protocol)
    if key in _LAST_GOOD_HOST:
        return _LAST_GOOD_HOST[key]
    parsed = _parse_memory_entry(_read_host_memory_file().get(key))
    if parsed is not None:
        _LAST_GOOD_HOST[key] = parsed
    return parsed


def _evict_host_memory_file(key: str, target: tuple[str, int] | None) -> None:
    """Mirror an in-memory eviction onto disk, same match-before-evict rule.

    Checked against whatever the *disk* currently holds, independently
    of this process's in-memory state — the two can legitimately
    disagree (e.g. a fresh process that has not hydrated from disk yet),
    and a stale on-disk host must not survive an eviction just because
    this process's memory did not happen to have it cached.
    """
    data = _read_host_memory_file()
    if key not in data:
        return
    if target is not None and _parse_memory_entry(data.get(key)) != target:
        return
    data.pop(key, None)
    _write_host_memory_file(data)


def remember_good_host(protocol: str, server: tuple[str, int]) -> None:
    """Cache the host whose handshake actually answered, keyed by protocol.

    Protocols are isolated on purpose: a std-HQ handshake succeeding on a
    host says nothing about whether the MAC frame handshake would succeed
    on the same host (different wire protocol). ``"hq"`` and ``"mac"`` each
    get their own slot and never share one — including on disk, where
    they are separate keys in the same JSON object.

    Persisted immediately (with a ``saved_at`` timestamp, for future
    diagnosis — see module docstring note on TTL) so the very next
    process — tomorrow's daily sync, a parallel backfill worker — skips
    the cold candidate walk too, not just this one.
    """
    key = str(protocol)
    value = (str(server[0]), int(server[1]))
    _LAST_GOOD_HOST[key] = value
    data = _read_host_memory_file()
    data[key] = {"ip": value[0], "port": value[1], "saved_at": time.time()}
    _write_host_memory_file(data)


def forget_good_host(protocol: str, server: tuple[str, int] | None = None) -> None:
    """Drop a protocol's cached host, in memory and on disk.

    With ``server`` given, only evicts when it still matches what is
    cached (so a failure on some *other*, non-cached host in the same
    walk cannot accidentally wipe out a still-good memory). Without it,
    unconditionally clears the slot. This is how a host that has gone
    offline stops being resurrected: the very next handshake failure
    against it evicts it from disk too, not just from this process.
    """
    key = str(protocol)
    target = None if server is None else (str(server[0]), int(server[1]))
    if target is None:
        _LAST_GOOD_HOST.pop(key, None)
    else:
        current = _LAST_GOOD_HOST.get(key)
        if current == target:
            _LAST_GOOD_HOST.pop(key, None)
    _evict_host_memory_file(key, target)


def hosts_with_memory(
    protocol: str, candidates: Iterable[tuple[str, int]]
) -> list[tuple[str, int]]:
    """Return ``candidates`` reordered so protocol's remembered host is first.

    Relative order of the rest is preserved and nothing is duplicated.
    Does not itself call ``iter_hq_candidates`` — callers build the base
    list themselves, which keeps that call monkeypatch-able per module.
    The remembered host may come from this process's own memory or, on
    a fresh process, from the persisted file (see ``_remembered_host``).
    """
    remembered = _remembered_host(protocol)
    ordered: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    if remembered is not None:
        ordered.append(remembered)
        seen.add(remembered)
    for ip, port in candidates:
        key = (str(ip), int(port))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _payload_len(raw: Any) -> int:
    if raw is None:
        return 0
    if getattr(raw, "empty", None) is True:
        return 0
    try:
        return int(len(raw))
    except TypeError:
        return 0


def open_quotes(server: tuple[str, int], *, timeout: float = 8.0):
    """Connect to ``server`` and smoke unadjusted daily bars for ``000001``.

    StdQuotes re-reads ``BESTIP.HQ`` after kwargs; pin it in-process so a
    stale ``~/.tdxhub/config.json`` cannot silently steal the socket.
    Does not write that file. ``raise_exception=True`` so empty/None is not
    a silent success. Tries daily categories 9 then 4.
    """
    ensure_import_path(ALIAS, strict=True)
    from tdxhub import config  # noqa: E402
    from tdxhub.quotes import Quotes  # noqa: E402

    config.setup()
    bestip = config.get("BESTIP")
    pinned = dict(bestip) if isinstance(bestip, dict) else {}
    pinned["HQ"] = [str(server[0]), int(server[1])]
    config.set("BESTIP", pinned)
    client = Quotes.factory(
        market="std",
        server=server,
        timeout=timeout,
        heartbeat=False,
        auto_retry=True,
        raise_exception=True,
    )
    try:
        last: BaseException | None = None
        for cat in (9, 4):
            try:
                raw = client.client.get_security_bars(int(cat), _SMOKE_MARKET, _SMOKE_CODE, 0, 5)
            except Exception as exc:  # noqa: BLE001
                last = exc
                continue
            if _payload_len(raw) <= 0:
                continue
            setattr(client, "_cm_daily_category", int(cat))
            return client
        raise RuntimeError(f"empty daily bars from {server}: {last!r}")
    except Exception:
        try:
            client.close()
        except Exception:  # rule-compliance: ok evidence=tdx-socket-close-best-effort
            pass
        raise


def quotes_client(
    **kwargs: Any,
):
    """Return a std Quotes client whose HQ handshake actually answers bars."""
    timeout = float(kwargs.pop("timeout", 8))
    max_hosts = int(kwargs.pop("max_hosts", 40))
    tcp_timeout = float(kwargs.pop("tcp_timeout", 1.5))
    explicit = kwargs.pop("server", None)
    if explicit is not None:
        if not isinstance(explicit, (tuple, list)) or len(explicit) != 2:
            raise TypeError(f"server must be (ip, port), got {explicit!r}")
        return open_quotes((str(explicit[0]), int(explicit[1])), timeout=timeout)

    last: BaseException | None = None
    handshake_tries = 0
    for ip, port in hosts_with_memory("hq", iter_hq_candidates()):
        if not tcp_open(ip, port, timeout=tcp_timeout):
            continue
        handshake_tries += 1
        try:
            client = open_quotes((ip, port), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — next host
            last = exc
            forget_good_host("hq", (ip, port))
            if handshake_tries >= max_hosts:
                break
            continue
        remember_good_host("hq", (ip, port))
        return client
    raise RuntimeError(
        f"no handshake-ready TDX HQ after {handshake_tries} TCP-open hosts: {last!r}"
    )


def reader_client(*, tdxdir: str | Path, **kwargs: Any):
    ensure_import_path(ALIAS, strict=True)
    from tdxhub.reader import Reader  # noqa: E402

    return Reader.factory(market="std", tdxdir=str(tdxdir), **kwargs)


def mac_client(**kwargs: Any):
    """MAC-protocol client. Isolated from quotes_client / StdQuotes."""
    from services.data_sources.tdxhub_mac import mac_client as _mac_client

    return _mac_client(**kwargs)


def capital_flow(conn: Any, market: int, code: str, **kwargs: Any) -> dict[str, Any]:
    """Vendor imbalance proxy via MAC ``0x1218`` / ``Stock_ZJLX``. Not conserved money."""
    from services.data_sources.tdxhub_mac import capital_flow as _capital_flow

    return _capital_flow(conn, market, code, **kwargs)


def xdxr(client: Any, ts_code: str, **kwargs: Any) -> dict[str, Any]:
    """Corporate-action events via Quotes ``get_xdxr_info``. Not qfq."""
    from services.data_sources.tdxhub_xdxr import fetch_xdxr

    return fetch_xdxr(client, ts_code, **kwargs)


def block(client: Any, **kwargs: Any) -> dict[str, Any]:
    """Vendor ``tdx_block`` membership. Not SW / DC / THS; no name crosswalk."""
    from services.data_sources.tdxhub_block import fetch_block

    return fetch_block(client, **kwargs)


__all__ = [
    "ALIAS",
    "block",
    "capital_flow",
    "forget_good_host",
    "hosts_with_memory",
    "is_hq_transport_error",
    "iter_hq_candidates",
    "load_connect_cfg_hq",
    "mac_client",
    "open_quotes",
    "parse_hq_server",
    "quotes_client",
    "reader_client",
    "remember_good_host",
    "tcp_open",
    "tdxhub_root",
    "xdxr",
]
