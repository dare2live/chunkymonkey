"""Provider capture helpers for authorized single-day security partitions.

Kept separate from land→accept mechanics so the acceptance module stays under
the god-file ratchet.  Domain runtimes own publication; this module only shapes
provider pages into :class:`SecurityDayLandingBatch`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isinf, isnan
from typing import Any

from services.data_sources.availability import SyncWindowError, publication_cutoff
from services.data_sources.security_day_partition import (
    SecurityDayDomain,
    SecurityDayError,
    SecurityDayLandingBatch,
    _partition,
)


@dataclass(frozen=True)
class ProviderPage:
    """A ``fetch_rows`` return value carrying request-level provenance metadata
    alongside the rows themselves.

    2026-09-16 (刀2): the fuyao dump + baostock daily adapter has metadata that
    describes the *whole page*, not any one row — which dump release/kind was
    used, its content hash, which reference source backed pre_close, and how
    many rows fell into which pre_close_origin bucket. None of that belongs on
    a per-row dict (rows already carry their own ``pre_close_origin``); it
    belongs in ``ingest_batch.request_json`` where audits can find it without
    re-deriving it from the rows.

    A ``fetch_rows`` callable that has nothing extra to report keeps returning
    a plain sequence of row mappings — :func:`capture_security_day_provider_rows`
    treats that identically to ``ProviderPage(rows, {})``. This type is purely
    additive: no existing caller (which all return plain sequences today) is
    affected.

    返修 (blocking 发现 #1 修复): ``__len__``/``__bool__`` delegate to ``rows``
    so a ``ProviderPage`` is truthy/falsy exactly like the plain sequence it
    replaces — ``sync_runner._fetch_with_retry``'s ``if rows: return rows`` /
    zero-rows retry logic (and any other existing ``if fetch_rows(...):``
    caller) keeps working unchanged on the new return type without needing to
    know this class exists.
    """

    rows: Sequence[Mapping[str, Any]]
    request_meta: Mapping[str, Any]

    def __len__(self) -> int:
        return len(self.rows)

    def __bool__(self) -> bool:
        return bool(self.rows)


def _normalize_provider_value(value: Any) -> Any:
    """Provider nulls often arrive as float NaN; landing JSON requires None."""

    if isinstance(value, float) and (isnan(value) or isinf(value)):
        return None
    return value


def project_security_day_provider_row(
    domain: SecurityDayDomain, row: Mapping[str, Any]
) -> dict[str, Any]:
    """Project one provider row onto the domain's declared fields only."""

    if not isinstance(row, Mapping):
        raise SecurityDayError(f"{domain.domain}: provider row must be a mapping")
    missing = [name for name in domain.provider_fields if name not in row]
    if missing:
        raise SecurityDayError(
            f"{domain.domain}_provider_row_missing_fields missing={missing!r}"
        )
    # 2026-09-13: 投影按白名单重建 dict, 所以域级增补列 (enrichment_fields) 必须在这里
    # 显式放行 —— 否则适配器逐行给出的值在**落地之前**就被丢掉, 而失败要等到 accept 才以
    # MISSING_ENRICHMENT 暴露, 错误点离根因两步远。实测过: 不放行时投影结果里没有该键。
    # 缺键在这里就炸 (不是拖到 accept): land 是它第一次有机会被发现的地方, 早一步失败,
    # 错误也更贴近"适配器没给"这个真实根因。
    missing_enrichment = [name for name in domain.enrichment_fields if name not in row]
    if missing_enrichment:
        raise SecurityDayError(
            f"{domain.domain}_provider_row_missing_enrichment "
            f"missing={missing_enrichment!r} —— 适配器必须逐行给出这些值; "
            "不按批兜底 (同一批内不同行可以不同源)"
        )
    return {
        name: _normalize_provider_value(row[name])
        for name in (*domain.provider_fields, *domain.enrichment_fields)
    }


def build_security_day_landing_batch(
    domain: SecurityDayDomain,
    *,
    trade_date: str,
    rows: Sequence[Mapping[str, Any]],
    observed_at: datetime,
    batch_id: str,
    request_meta: Mapping[str, Any] | None = None,
) -> SecurityDayLandingBatch:
    """Assemble one landing batch from an already-captured provider page.

    ``request_meta`` (2026-09-16, 刀2): page-level provenance to fold into the
    landing batch's ``request`` mapping (which lands in ``ingest_batch.
    request_json``), alongside the always-present ``api``/``trade_date`` keys.
    Optional and additive — ``None``/empty leaves ``request`` exactly as it
    was before this parameter existed. Colliding with the two reserved keys
    is rejected outright (construction-time error) rather than silently
    overwritten, same discipline as ``SecurityDayDomain.__post_init__``'s
    enrichment/provider/lineage clash check.
    """

    partition = _partition(trade_date)
    if partition < domain.coverage_start:
        raise SecurityDayError(
            f"{domain.domain}: trade_date={partition} before "
            f"coverage_start={domain.coverage_start}"
        )
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise SecurityDayError("observed_at must be a timezone-aware datetime")
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        raise SecurityDayError("batch_id must be non-empty")
    if not rows:
        raise SecurityDayError(
            f"{domain.domain} capture rejects empty provider rows "
            f"trade_date={partition}"
        )
    projected = tuple(project_security_day_provider_row(domain, row) for row in rows)
    for row in projected:
        compact = _partition(row["trade_date"])
        if compact != partition:
            raise SecurityDayError(
                f"{domain.domain}_partition_mismatch "
                f"row_trade_date={compact} expected={partition}"
            )
    # Consumer publication stays event-timed: available_at never precedes the
    # typed cutoff even when manual sync observes the provider earlier.
    observed_utc = observed_at.astimezone(timezone.utc)
    try:
        cutoff_utc = publication_cutoff(
            domain.availability_policy,
            partition_value=partition,
            trading_day_values=(partition,),
        ).astimezone(timezone.utc)
    except SyncWindowError as exc:
        raise SecurityDayError(
            f"{domain.domain}_publication_cutoff_unproven partition={partition}: {exc}"
        ) from exc
    available_at = max(observed_utc, cutoff_utc)
    request: dict[str, Any] = {"api": domain.api, "trade_date": partition}
    if request_meta:
        overlap = set(request_meta) & set(request)
        if overlap:
            raise SecurityDayError(
                f"{domain.domain}_request_meta_collides_with_reserved_keys "
                f"{sorted(overlap)} —— request_meta 不许覆盖 api/trade_date"
            )
        request.update(dict(request_meta))
    return SecurityDayLandingBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=observed_at,
        available_at=available_at,
        rows=projected,
        request=request,
        source=domain.source,
        contract_version=domain.contract_version,
    )


def capture_security_day_provider_rows(
    domain: SecurityDayDomain,
    *,
    trade_date: str,
    fetch_rows: Callable[
        [Mapping[str, Any]], Sequence[Mapping[str, Any]] | ProviderPage | None
    ],
    observed_at: datetime,
) -> SecurityDayLandingBatch:
    """Fetch one trade_date partition and build a landing batch.

    ``fetch_rows`` may return a plain sequence of row mappings (every existing
    caller today) or a :class:`ProviderPage` carrying page-level
    ``request_meta`` alongside the rows — both shapes are handled identically
    except that a ``ProviderPage``'s ``request_meta`` is folded into the
    landing batch's ``request`` (see :func:`build_security_day_landing_batch`).
    """

    partition = _partition(trade_date)
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise SecurityDayError("observed_at must be a timezone-aware datetime")
    request = {"api": domain.api, "trade_date": partition}
    page = fetch_rows(request)
    if page is None:
        raise SecurityDayError(
            f"{domain.domain}_provider_fetch_failed trade_date={partition}"
        )
    if isinstance(page, ProviderPage):
        rows = page.rows
        request_meta = page.request_meta
    else:
        rows = page
        request_meta = None
    stamp = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch_id = f"{domain.domain}:{partition}:{stamp}"
    return build_security_day_landing_batch(
        domain,
        trade_date=partition,
        rows=rows,
        observed_at=observed_at,
        batch_id=batch_id,
        request_meta=request_meta,
    )


__all__ = [
    "ProviderPage",
    "build_security_day_landing_batch",
    "capture_security_day_provider_rows",
    "project_security_day_provider_row",
]
