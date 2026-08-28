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
"""
from __future__ import annotations

import os
import socket
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
    for ip, port in iter_hq_candidates():
        if not tcp_open(ip, port, timeout=tcp_timeout):
            continue
        handshake_tries += 1
        try:
            return open_quotes((ip, port), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — next host
            last = exc
            if handshake_tries >= max_hosts:
                break
            continue
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


__all__ = [
    "ALIAS",
    "capital_flow",
    "is_hq_transport_error",
    "iter_hq_candidates",
    "load_connect_cfg_hq",
    "mac_client",
    "open_quotes",
    "parse_hq_server",
    "quotes_client",
    "reader_client",
    "tcp_open",
    "tdxhub_root",
]
