"""TDX ``block`` as namespace ``tdx_block``. Parallel to SW / DC / THS; never merged."""
from __future__ import annotations

import struct
from collections import OrderedDict
from typing import Any, Iterable, Mapping

from services.data_sources.tdxhub_kline_recon import bars_as_records, reject_tdx_adjust

NAMESPACE = "tdx_block"
HEADER_BYTES = 384
NAME_BYTES = 9
CODE_BYTES = 7
SLOT_BYTES = 2800
BLOCK_FILES = ("block.dat", "block_zs.dat", "block_fg.dat", "block_gn.dat")


def _require_quotes_api(client: Any, *, op: str) -> Any:
    if getattr(client, "protocol", None) == "mac":
        raise TypeError(f"{op} requires quotes_client(); never reuse mac_client")
    api = getattr(client, "client", None)
    if api is None or not hasattr(api, "get_and_parse_block_info"):
        raise TypeError(f"{op} requires quotes_client() get_and_parse_block_info")
    return api


def encode_block_dat(blocks: Iterable[Mapping[str, Any]]) -> bytes:
    """Minimal TDX block.dat bytes. Test/codec helper, not a live dump writer."""
    payload = list(blocks)
    buf = bytearray(HEADER_BYTES)
    buf.extend(struct.pack("<H", len(payload)))
    for item in payload:
        name = str(item.get("name") or "").encode("gbk", "ignore")[:NAME_BYTES]
        buf.extend(name.ljust(NAME_BYTES, b"\x00"))
        members = [str(code) for code in (item.get("members") or []) if str(code).strip()]
        block_type = int(item.get("vendor_block_type") or item.get("block_type") or 0)
        buf.extend(struct.pack("<HH", len(members), block_type))
        slot = bytearray(SLOT_BYTES)
        pos = 0
        for code in members:
            raw = code.encode("utf-8", "ignore")[:CODE_BYTES]
            slot[pos : pos + CODE_BYTES] = raw.ljust(CODE_BYTES, b"\x00")
            pos += CODE_BYTES
        buf.extend(slot)
    return bytes(buf)


def parse_block_dat(data: bytes, *, source_file: str) -> dict[str, Any]:
    source_file = str(source_file)
    empty = {
        "namespace": NAMESPACE,
        "source_file": source_file,
        "status": "empty_recon",
        "blocks": [],
        "crosswalk": None,
        "merged_namespaces": (),
    }
    if not data or len(data) < HEADER_BYTES + 2:
        return empty
    pos = HEADER_BYTES
    (num,) = struct.unpack("<H", data[pos : pos + 2])
    pos += 2
    blocks: list[dict[str, Any]] = []
    for index in range(num):
        if pos + NAME_BYTES + 4 > len(data):
            break
        name = data[pos : pos + NAME_BYTES].decode("gbk", "ignore").rstrip("\x00")
        pos += NAME_BYTES
        stock_count, block_type = struct.unpack("<HH", data[pos : pos + 4])
        pos += 4
        begin = pos
        members: list[str] = []
        for _ in range(int(stock_count)):
            if pos + CODE_BYTES > len(data):
                break
            code = data[pos : pos + CODE_BYTES].decode("utf-8", "ignore").rstrip("\x00")
            pos += CODE_BYTES
            if code:
                members.append(code)
        pos = begin + SLOT_BYTES
        blocks.append(
            {
                "namespace": NAMESPACE,
                "source_file": source_file,
                "vendor_block_id": f"{source_file}:{index}:{int(block_type)}",
                "vendor_block_type": int(block_type),
                "name": name,
                "members": members,
            }
        )
    if not blocks:
        return empty
    return {
        "namespace": NAMESPACE,
        "source_file": source_file,
        "status": "ok",
        "blocks": blocks,
        "crosswalk": None,
        "merged_namespaces": (),
    }


def records_to_blocks(raw: Any, *, source_file: str) -> dict[str, Any]:
    source_file = str(source_file)
    groups: OrderedDict[tuple[Any, str], list[str]] = OrderedDict()
    for item in bars_as_records(raw):
        name = str(item.get("blockname") or item.get("name") or "")
        btype = item.get("block_type")
        key = (btype, name)
        code = str(item.get("code") or "").strip()
        groups.setdefault(key, [])
        if code:
            groups[key].append(code)
    blocks: list[dict[str, Any]] = []
    for index, ((btype, name), members) in enumerate(groups.items()):
        vendor_type = 0 if btype is None else int(btype)
        blocks.append(
            {
                "namespace": NAMESPACE,
                "source_file": source_file,
                "vendor_block_id": f"{source_file}:{index}:{vendor_type}",
                "vendor_block_type": vendor_type,
                "name": name,
                "members": members,
            }
        )
    return {
        "namespace": NAMESPACE,
        "source_file": source_file,
        "status": "ok" if blocks else "empty_recon",
        "blocks": blocks,
        "crosswalk": None,
        "merged_namespaces": (),
    }


def fetch_block(
    client: Any,
    *,
    tofile: str = "block.dat",
    adjust: str | None = None,
) -> dict[str, Any]:
    reject_tdx_adjust(adjust)
    api = _require_quotes_api(client, op="block")
    raw = api.get_and_parse_block_info(tofile)
    if raw is None:
        return records_to_blocks([], source_file=tofile)
    if isinstance(raw, (bytes, bytearray)):
        return parse_block_dat(bytes(raw), source_file=tofile)
    return records_to_blocks(raw, source_file=tofile)


__all__ = [
    "BLOCK_FILES",
    "NAMESPACE",
    "encode_block_dat",
    "fetch_block",
    "parse_block_dat",
    "records_to_blocks",
]
