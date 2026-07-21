"""S4: swappable acquire at the security-day land boundary only.

Two modes feed the same landing projection:
- ``provider_tushare`` — live faucet adapter → provider-shaped rows
- ``local_legacy_raw_materialize`` — local ``raw_tushare_*`` rows → same shape

Accept (S2) stays separate and must never call this module.
Does not revive the retired multi-source fallback registry / plugin bus.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from services.data_sources.security_day_partition import SecurityDayError

_SECURITY_DAY_ACQUIRE_DOMAINS = frozenset({"daily", "stock_st"})

AcquireMode = Literal["provider_tushare", "local_legacy_raw_materialize"]

ACQUIRE_MODE_PROVIDER_TUSHARE: AcquireMode = "provider_tushare"
ACQUIRE_MODE_LOCAL_LEGACY_RAW: AcquireMode = "local_legacy_raw_materialize"
SUPPORTED_ACQUIRE_MODES = frozenset(
    {ACQUIRE_MODE_PROVIDER_TUSHARE, ACQUIRE_MODE_LOCAL_LEGACY_RAW}
)


@dataclass(frozen=True)
class SecurityDayAcquireResult:
    """Provider-shaped rows plus honest acquire lineage (land-only concern)."""

    rows: tuple[Mapping[str, Any], ...]
    acquire_mode: AcquireMode
    lineage_note: str
    source_ref: str


def _domain_acquire_tables(domain: str) -> tuple[tuple[str, ...], str, str]:
    if domain == "daily":
        from services.data_sources.nominal_ohlcv_schema import DOMAIN as OHLCV_DOMAIN

        return (
            OHLCV_DOMAIN.provider_fields,
            OHLCV_DOMAIN.compatibility_table,
            OHLCV_DOMAIN.api,
        )
    if domain == "stock_st":
        from services.data_sources.stock_st_schema import DOMAIN as ST_DOMAIN

        return (
            ST_DOMAIN.provider_fields,
            ST_DOMAIN.compatibility_table,
            ST_DOMAIN.api,
        )
    raise SecurityDayError(
        f"security_day_acquire unsupported domain={domain!r}; "
        f"allowed={sorted(_SECURITY_DAY_ACQUIRE_DOMAINS)}"
    )


def acquire_security_day_local_raw(
    conn,
    domain: str,
    *,
    trade_date: str,
) -> SecurityDayAcquireResult:
    """Read local legacy-raw rows for one trade_date (no provider I/O)."""

    if domain not in _SECURITY_DAY_ACQUIRE_DOMAINS:
        raise SecurityDayError(
            f"security_day_acquire unsupported domain={domain!r}; "
            f"allowed={sorted(_SECURITY_DAY_ACQUIRE_DOMAINS)}"
        )
    partition = str(trade_date).replace("-", "")
    provider_fields, raw_table, _api = _domain_acquire_tables(domain)
    cols = ", ".join(provider_fields)
    raw_rows = tuple(
        dict(zip(provider_fields, row, strict=True))
        for row in conn.execute(
            f"SELECT {cols} FROM {raw_table} WHERE trade_date = ?",
            [partition],
        ).fetchall()
    )
    return SecurityDayAcquireResult(
        rows=raw_rows,
        acquire_mode=ACQUIRE_MODE_LOCAL_LEGACY_RAW,
        lineage_note=f"cli_from_local_raw:{raw_table}",
        source_ref=raw_table,
    )


def acquire_security_day_provider(
    domain: str,
    *,
    trade_date: str,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
) -> SecurityDayAcquireResult:
    """Run the provider faucet callable once; return provider-shaped rows."""

    if domain not in _SECURITY_DAY_ACQUIRE_DOMAINS:
        raise SecurityDayError(
            f"security_day_acquire unsupported domain={domain!r}; "
            f"allowed={sorted(_SECURITY_DAY_ACQUIRE_DOMAINS)}"
        )
    if fetch_rows is None:
        raise SecurityDayError("provider acquire requires fetch_rows")
    partition = str(trade_date).replace("-", "")
    _fields, _table, api = _domain_acquire_tables(domain)
    rows = tuple(fetch_rows({"trade_date": partition}) or ())
    return SecurityDayAcquireResult(
        rows=rows,
        acquire_mode=ACQUIRE_MODE_PROVIDER_TUSHARE,
        lineage_note=f"provider_tushare:{domain}:{api}",
        source_ref=f"tushare:{api}",
    )


def resolve_security_day_acquire(
    mode: str,
    domain: str,
    *,
    trade_date: str,
    conn=None,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None]
    | None = None,
) -> SecurityDayAcquireResult:
    """Single land-path entry: choose faucet mode without touching accept."""

    normalized = str(mode or "").strip()
    if normalized not in SUPPORTED_ACQUIRE_MODES:
        raise SecurityDayError(
            f"unknown acquire_mode={mode!r}; "
            f"allowed={sorted(SUPPORTED_ACQUIRE_MODES)}"
        )
    if normalized == ACQUIRE_MODE_LOCAL_LEGACY_RAW:
        if conn is None:
            raise SecurityDayError("local_legacy_raw acquire requires conn")
        return acquire_security_day_local_raw(conn, domain, trade_date=trade_date)
    return acquire_security_day_provider(
        domain,
        trade_date=trade_date,
        fetch_rows=fetch_rows,  # type: ignore[arg-type]
    )


__all__ = [
    "ACQUIRE_MODE_LOCAL_LEGACY_RAW",
    "ACQUIRE_MODE_PROVIDER_TUSHARE",
    "SUPPORTED_ACQUIRE_MODES",
    "AcquireMode",
    "SecurityDayAcquireResult",
    "acquire_security_day_local_raw",
    "acquire_security_day_provider",
    "resolve_security_day_acquire",
]
