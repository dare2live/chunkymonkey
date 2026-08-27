"""Fuyao (HiThink finance) adapter — official sibling checkout, not a vendored copy.

Dump sign+download lives in ``../fuyao/python/marketdb/providers/dump.py``.
This module only locates that tree and re-exports the downloader. It does not
import or run their ``marketdb`` DuckDB warehouse. REST calendar/identity
calls go through ``rest_json`` (stdlib urllib), not their DuckDB warehouse.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from services.data_sources.sibling_repos import ensure_import_path

ALIAS = "fuyao"
API_BASE_URL = "https://fuyao.aicubes.cn"


class FuyaoRestError(RuntimeError):
    def __init__(self, message: str, *, http: int | None = None, code: int | None = None):
        super().__init__(message)
        self.http = http
        self.code = code


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


def rest_json(
    path: str,
    *,
    api_key: str,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    """GET an official REST path. Envelope ``code=0`` required. Does not use marketdb."""
    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None}
    )
    url = API_BASE_URL.rstrip("/") + path
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={"X-api-key": api_key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            http = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raise FuyaoRestError(
            f"http {exc.code} {path}",
            http=int(exc.code),
        ) from exc
    except urllib.error.URLError as ext:
        raise FuyaoRestError(f"transport {path}: {ext}") from ext
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FuyaoRestError(f"non-json {path} http={http}") from exc
    code = int(payload.get("code") or 0)
    if code != 0:
        raise FuyaoRestError(
            f"code={code} message={payload.get('message')}",
            http=http,
            code=code,
        )
    return payload.get("data")


__all__ = [
    "ALIAS",
    "API_BASE_URL",
    "FuyaoRestError",
    "dump_downloader",
    "dump_kinds",
    "fuyao_root",
    "resolve_api_key",
    "rest_json",
]
