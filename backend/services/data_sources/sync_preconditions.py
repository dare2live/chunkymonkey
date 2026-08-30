"""Shared hard preconditions for supported sync CLI entrypoints."""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from services.data_sources.sources.tushare import probe_authorization
from services.writer_lock import AUTH_VERIFIED_LEASE_ENV, WriterLease


REPO = Path(__file__).resolve().parents[3]


class CalendarFoundationError(RuntimeError):
    """The shared calendar contract is unavailable for a date-driven sync."""


def ensure_calendar_foundation(
    domains: Iterable[str],
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Fail closed for every sync except the sole trade_cal bootstrap domain."""
    domain_set = {str(domain) for domain in domains}
    if domain_set == {"trade_cal"}:
        return
    cmd = [
        sys.executable,
        "backend/scripts/check_continuity_integrity.py",
        "--only",
        "calendar_horizon",
        "--domain",
        "trade_cal",
        "--strict",
        "--json",
    ]
    proc = runner(cmd, cwd=str(REPO), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CalendarFoundationError("calendar_not_ready")


def authorization_preflight(
    *,
    lease: WriterLease,
    adapter_factory: Callable[[str], Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Reuse a parent proof only for a validated inherited lock; direct CLI probes once."""
    proof = os.environ.get(AUTH_VERIFIED_LEASE_ENV, "")
    lease_id = str(lease.lease_id or "")
    if (
        lease.inherited
        and proof
        and lease_id
        and secrets.compare_digest(proof, lease_id)
    ):
        return {"inherited_authorization": True}

    # 授权探测硬编码 tushare adapter, 语义上就是 tushare 源专属参数 (2026-08-30 从
    # defaults 移入 sources.tushare)；legacy 兜底读 defaults 保持旧式最小 registry (无 sources 段) 可用。
    timeout = ((registry.get("sources") or {}).get("tushare") or {}).get("auth_probe_timeout_seconds")
    if timeout is None:
        timeout = (registry.get("defaults") or {}).get("auth_probe_timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("sync_registry.sources.tushare.auth_probe_timeout_seconds 必须是正数")
    return probe_authorization(
        adapter_factory("tushare"), timeout_seconds=float(timeout)
    )


__all__ = [
    "CalendarFoundationError",
    "authorization_preflight",
    "ensure_calendar_foundation",
]
