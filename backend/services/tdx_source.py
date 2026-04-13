"""
tdx_source.py - shared tdxhub runtime helpers.

The Python import path remains `mootdx` for compatibility, but the project
expects that package to be provided by the dare2live/tdxhub fork.
"""

import logging
import os
from typing import Optional


logger = logging.getLogger("cm-api")

DEFAULT_TDX_SERVERS: tuple[tuple[str, int], ...] = (
    ("110.41.147.114", 7709),
    ("124.70.199.56", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("116.205.163.254", 7709),
)


def parse_tdx_server_string(value: str) -> Optional[tuple[str, int]]:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _load_hq_hosts() -> tuple[tuple[str, int], ...]:
    try:
        from mootdx.consts import HQ_HOSTS as hosts

        return tuple((host, port) for _name, host, port in hosts)
    except ImportError:
        logger.warning("[tdxhub] 无法导入 mootdx.consts.HQ_HOSTS，使用内置后备服务器列表")
        return DEFAULT_TDX_SERVERS


def iter_tdx_servers() -> tuple[tuple[str, int], ...]:
    custom_raw = [item.strip() for item in os.environ.get("CM_TDX_SERVERS", "").split(",") if item.strip()]
    custom = [parse_tdx_server_string(item) for item in custom_raw]
    ordered: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    for server in [item for item in custom if item is not None] + list(_load_hq_hosts()):
        if server in seen:
            continue
        seen.add(server)
        ordered.append(server)
    return tuple(ordered)


def get_tdx_quotes_class():
    try:
        from mootdx.quotes import Quotes

        return Quotes
    except ImportError:
        return None


def get_tdx_affair_class():
    try:
        from mootdx.affair import Affair

        return Affair
    except ImportError:
        return None