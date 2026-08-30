"""TDX MAC-protocol capital-flow adapter.

A separate raw-socket connection from ``quotes_client`` / StdQuotes. Walks the
same official HQHOST catalog (``iter_hq_candidates``) with MAC frames. Failover
only after TCP open + pytdx setup handshake + nonempty ``capital_flow`` payload.

Never writes the tdxhub runtime config file, never runs ``bestip``, never
reuses a Quotes socket. Vendor imbalance fields are named as vendor
proxies, not conserved money.
"""
from __future__ import annotations

import ast
import json
import socket
import struct
import zlib
from typing import Any

from services.data_sources.sibling_repos import ensure_import_path
from services.data_sources.sources.tdxhub import (
    _SMOKE_CODE,
    _SMOKE_MARKET,
    forget_good_host,
    hosts_with_memory,
    iter_hq_candidates,
    remember_good_host,
    tcp_open,
)

MAC_HEAD_FLAG_DEFAULT = 0x1C
MAC_HEAD_FLAG_CAPITAL_FLOW = 0x02
MSG_ID_CAPITAL_FLOW = 0x1218
CMD_STOCK_ZJLX = b"Stock_ZJLX"
TDX_RSP_HEADER_LEN = 16
JSON_PREFIX_LEN = 27
LAYER = "tdx_mac_capital_flow"
METHOD = "tdx_mac_0x1218_stock_zjlx"
VENDOR = "tdx_mac"
UNIT = "vendor_imbalance_unspecified"

# Same three pytdx / tdxhub SetupCmd1/2/3 bytes. Handshake only; not Quotes.
SETUP_HEX = (
    "0c0218930001030003000d0001",
    "0c0218940001030003000d0002",
    "0c031899000120002000db0fd5d0c9ccd6a4a8af0000008fc22540130000d500c9ccbdf0d7ea00000002",
)

_TODAY_ALIASES = {
    "vendor_main_in": ("main_in", "主力流入", "主力买入"),
    "vendor_main_out": ("main_out", "主力流出", "主力卖出"),
    "vendor_retail_in": ("retail_in", "small_in", "散户流入"),
    "vendor_retail_out": ("retail_out", "small_out", "散户流出"),
}


def setup_command_bytes() -> tuple[bytes, bytes, bytes]:
    return tuple(bytes.fromhex(item) for item in SETUP_HEX)  # type: ignore[return-value]


def tdxhub_setup_command_bytes() -> tuple[bytes, bytes, bytes]:
    """Byte-equal lock against tdxhub SetupCmd1/2/3 drift."""
    ensure_import_path("tdxhub", strict=True)
    from tdxhub.protocol.parser.setup_commands import (  # noqa: E402
        SetupCmd1,
        SetupCmd2,
        SetupCmd3,
    )

    out: list[bytes] = []
    for cls in (SetupCmd1, SetupCmd2, SetupCmd3):
        cmd = cls(client=None)
        cmd.setup()
        out.append(bytes(cmd.send_pkg))
    return (out[0], out[1], out[2])


def encode_mac_frame(
    msg_id: int,
    body: bytes,
    *,
    head_flag: int = MAC_HEAD_FLAG_DEFAULT,
) -> bytes:
    inner = struct.pack("<H", int(msg_id)) + body
    zipsize = unzipsize = len(inner)
    header = struct.pack(
        "<BIBHH", int(head_flag), 0, 1, zipsize, unzipsize
    )
    return header + inner


def encode_capital_flow_request(market: int, code: str) -> bytes:
    code_bytes = str(code).encode("gbk")
    body = struct.pack("<H8s16x21s", int(market), code_bytes, CMD_STOCK_ZJLX)
    return encode_mac_frame(
        MSG_ID_CAPITAL_FLOW, body, head_flag=MAC_HEAD_FLAG_CAPITAL_FLOW
    )


def parse_tdx_response_header(head_buf: bytes) -> tuple[int, int]:
    """Same ``<IIIHH`` layout as tdxhub ``BaseParser._call_api``."""
    if len(head_buf) != TDX_RSP_HEADER_LEN:
        raise RuntimeError("head_buf is not 0x10 : " + str(head_buf))
    _i1, _i2, _i3, zip_size, unzip_size = struct.unpack("<IIIHH", head_buf)
    return int(zip_size), int(unzip_size)


def decompress_tdx_body(body: bytes, zip_size: int, unzip_size: int) -> bytes:
    if zip_size != unzip_size:
        return zlib.decompress(body)
    return bytes(body)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def recv_tdx_body(sock: socket.socket) -> bytes:
    head = recv_exact(sock, TDX_RSP_HEADER_LEN)
    zip_size, unzip_size = parse_tdx_response_header(head)
    body = recv_exact(sock, zip_size)
    if zip_size and len(body) != zip_size:
        raise RuntimeError(f"short TDX body: got {len(body)} want {zip_size}")
    return decompress_tdx_body(body, zip_size, unzip_size)


def handshake_mac(sock: socket.socket) -> None:
    for pkg in setup_command_bytes():
        sock.sendall(pkg)
        recv_tdx_body(sock)


def parse_capital_flow_json(body: bytes) -> list[Any]:
    if body is None or len(body) <= JSON_PREFIX_LEN:
        return []
    text = body[JSON_PREFIX_LEN:].decode("gbk", errors="strict").strip("\x00").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(text)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lookup_alias(row: dict[str, Any], names: tuple[str, ...]) -> float | None:
    if not isinstance(row, dict):
        return None
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return _as_float(row[name])
        if name.lower() in lower:
            return _as_float(lower[name.lower()])
    return None


def _today_vendor_fields(today: Any) -> dict[str, float | None] | None:
    """Live 0x1218 JSON is a list ``[main_in, main_out, retail_in, retail_out]``.

    Dict aliases are accepted for fixtures / recon dumps. Empty → None.
    """
    if isinstance(today, dict):
        mapped = {
            key: _lookup_alias(today, aliases) for key, aliases in _TODAY_ALIASES.items()
        }
        if all(v is None for v in mapped.values()):
            return None
        return mapped
    if isinstance(today, (list, tuple)) and len(today) >= 2:
        mapped = {
            "vendor_main_in": _as_float(today[0] if len(today) > 0 else None),
            "vendor_main_out": _as_float(today[1] if len(today) > 1 else None),
            "vendor_retail_in": _as_float(today[2] if len(today) > 2 else None),
            "vendor_retail_out": _as_float(today[3] if len(today) > 3 else None),
        }
        if all(v is None for v in mapped.values()):
            return None
        return mapped
    return None


def map_vendor_imbalance(python_list: list[Any] | None) -> dict[str, Any]:
    """Named vendor-imbalance proxy. Never a conserved-money total."""
    rows = list(python_list or [])
    today = rows[0] if rows else None
    five_days = rows[1] if len(rows) > 1 else None
    mapped = _today_vendor_fields(today)
    if mapped is None:
        vendor_fields: dict[str, Any] | list[Any] | None
        if isinstance(today, dict):
            vendor_fields = dict(today)
        elif isinstance(today, (list, tuple)):
            vendor_fields = list(today)
        else:
            vendor_fields = None
        out = {
            "status": "empty_recon",
            "layer": LAYER,
            "method": METHOD,
            "vendor": VENDOR,
            "accepted": False,
        }
        if vendor_fields is not None:
            out["vendor_fields"] = vendor_fields
        return out
    main_in = mapped["vendor_main_in"]
    main_out = mapped["vendor_main_out"]
    imbalance = None
    if main_in is not None and main_out is not None:
        imbalance = main_in - main_out
    return {
        "status": "ok",
        "layer": LAYER,
        "method": METHOD,
        "vendor": VENDOR,
        "unit": UNIT,
        "accepted": False,
        "primary_cut": False,
        **mapped,
        "vendor_main_imbalance": imbalance,
        "vendor_fields": dict(today) if isinstance(today, dict) else list(today),
        "vendor_five_day_proxy": five_days,
    }


def capital_flow_nonempty(payload: dict[str, Any] | None) -> bool:
    if not payload or payload.get("status") != "ok":
        return False
    for key in (
        "vendor_main_in",
        "vendor_main_out",
        "vendor_retail_in",
        "vendor_retail_out",
    ):
        if payload.get(key) is not None:
            return True
    return False


def reject_mac_qfq(adjust: str | None = None) -> None:
    raw = str(adjust or "").strip().lower()
    if raw in {"qfq", "hfq", "01", "02", "before", "after"} or "qfq" in raw:
        raise ValueError(f"banned tdx adjust={adjust!r}; MAC capital_flow is unadjusted")


class MacConnection:
    """Raw MAC socket. ``protocol='mac'`` so Quotes sockets cannot be reused."""

    protocol = "mac"

    def __init__(self, sock: socket.socket, server: tuple[str, int]):
        self._sock = sock
        self.server = (str(server[0]), int(server[1]))

    def close(self) -> None:
        sock = self._sock
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # rule-compliance: ok evidence=tdx-mac-socket-close-best-effort
            pass
        try:
            sock.close()
        except Exception:  # rule-compliance: ok evidence=tdx-mac-socket-close-best-effort
            pass

    def request_capital_flow_body(self, market: int, code: str) -> bytes:
        self._sock.sendall(encode_capital_flow_request(int(market), str(code)))
        return recv_tdx_body(self._sock)


def connect_raw(server: tuple[str, int], *, timeout: float) -> socket.socket:
    sock = socket.create_connection((str(server[0]), int(server[1])), timeout=timeout)
    sock.settimeout(timeout)
    return sock


def open_mac(
    server: tuple[str, int],
    *,
    timeout: float = 8.0,
    sock: socket.socket | None = None,
) -> MacConnection:
    """Connect, handshake, smoke nonempty capital_flow for 000001."""
    created = sock is None
    if sock is None:
        sock = connect_raw(server, timeout=timeout)
    try:
        handshake_mac(sock)
        conn = MacConnection(sock, server)
        payload = capital_flow(conn, _SMOKE_MARKET, _SMOKE_CODE)
        if not capital_flow_nonempty(payload):
            raise RuntimeError(f"empty capital_flow from {server}")
        return conn
    except Exception:
        if created:
            try:
                sock.close()
            except Exception:  # rule-compliance: ok evidence=tdx-mac-socket-close-best-effort
                pass
        raise


def mac_client(**kwargs: Any) -> MacConnection:
    """Return a MAC connection whose handshake actually answers capital_flow."""
    timeout = float(kwargs.pop("timeout", 8))
    max_hosts = int(kwargs.pop("max_hosts", 40))
    tcp_timeout = float(kwargs.pop("tcp_timeout", 1.5))
    explicit = kwargs.pop("server", None)
    if explicit is not None:
        if not isinstance(explicit, (tuple, list)) or len(explicit) != 2:
            raise TypeError(f"server must be (ip, port), got {explicit!r}")
        return open_mac((str(explicit[0]), int(explicit[1])), timeout=timeout)

    last: BaseException | None = None
    handshake_tries = 0
    for ip, port in hosts_with_memory("mac", iter_hq_candidates()):
        if not tcp_open(ip, port, timeout=tcp_timeout):
            continue
        handshake_tries += 1
        try:
            conn = open_mac((ip, port), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — next host
            last = exc
            forget_good_host("mac", (ip, port))
            if handshake_tries >= max_hosts:
                break
            continue
        remember_good_host("mac", (ip, port))
        return conn
    raise RuntimeError(
        f"no handshake-ready TDX MAC after {handshake_tries} TCP-open hosts: {last!r}"
    )


def capital_flow(
    conn: MacConnection,
    market: int,
    code: str,
    *,
    adjust: str | None = None,
) -> dict[str, Any]:
    reject_mac_qfq(adjust)
    if getattr(conn, "protocol", None) != "mac":
        raise TypeError(
            "capital_flow requires mac_client() connection; "
            "never reuse quotes_client / StdQuotes"
        )
    body = conn.request_capital_flow_body(int(market), str(code))
    if body is None or len(body) == 0:
        return {
            "status": "empty_recon",
            "layer": LAYER,
            "method": METHOD,
            "vendor": VENDOR,
            "accepted": False,
        }
    parsed = parse_capital_flow_json(body)
    out = map_vendor_imbalance(parsed)
    out["market"] = int(market)
    out["code"] = str(code)
    return out


__all__ = [
    "LAYER",
    "METHOD",
    "MacConnection",
    "capital_flow",
    "capital_flow_nonempty",
    "encode_capital_flow_request",
    "encode_mac_frame",
    "handshake_mac",
    "mac_client",
    "map_vendor_imbalance",
    "open_mac",
    "parse_capital_flow_json",
    "parse_tdx_response_header",
    "recv_tdx_body",
    "setup_command_bytes",
    "tdxhub_setup_command_bytes",
]
