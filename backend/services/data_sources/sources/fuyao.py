"""Fuyao (HiThink finance) adapter — official sibling checkout, not a vendored copy.

Dump sign+download lives in ``../fuyao/python/marketdb/providers/dump.py``.
This module only locates that tree and re-exports the downloader. It does not
import or run their ``marketdb`` DuckDB warehouse.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.data_sources.sibling_repos import ensure_import_path

ALIAS = "fuyao"
API_BASE_URL = "https://fuyao.aicubes.cn"


def fuyao_root() -> Path:
    return ensure_import_path(ALIAS, strict=True)


def dump_downloader(*, api_key: str, cache_dir: Path, **kwargs: Any):
    """Official Parquet dump client (daily-k / daily-k-10d / adjustment-factors)."""
    ensure_import_path(ALIAS, strict=True)
    from marketdb.providers.dump import DumpDownloader  # noqa: E402

    return DumpDownloader(
        api_base_url=API_BASE_URL,
        api_key=api_key,
        cache_dir=Path(cache_dir),
        **kwargs,
    )


def dump_kinds():
    ensure_import_path(ALIAS, strict=True)
    from marketdb.providers.dump import DownloadKind  # noqa: E402

    return DownloadKind


def resolve_api_key() -> str | None:
    """Env ``HITHINK_FINANCE_API_KEY`` then official user-level credentials.env."""
    ensure_import_path(ALIAS, strict=True)
    from marketdb.credentials import resolve_api_key as _resolve  # noqa: E402

    return _resolve()


__all__ = [
    "ALIAS",
    "API_BASE_URL",
    "dump_downloader",
    "dump_kinds",
    "fuyao_root",
    "resolve_api_key",
]
