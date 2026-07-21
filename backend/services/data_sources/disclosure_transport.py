"""Thin transport orchestration for E0 disclosure domains.

Strangler surfaces (caller-only composition; not a second dragon):
- S1 land-only via :func:`land_disclosure_partition_from_rows`
- S2 accept-from-landing via :func:`accept_disclosure_from_landing` (zero fetch)
- :func:`land_then_accept_disclosure_partition` = S1 then S2 in the caller

Production ``disclosure_dual_write`` uses land_then_accept. Fused
``publish_accepted_*`` helpers remain thin aliases for older callers/tests.
Domains: holders_top10 (miaoxiang), org_holding (miaoxiang), stk_holdertrade
(tushare).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

DISCLOSURE_TRANSPORT_DOMAINS = frozenset(
    {"holders_top10", "org_holding", "stk_holdertrade"}
)


class DisclosureTransportError(RuntimeError):
    """Disclosure transport refused an unsafe request."""


def _aware(value: datetime | str | None, *, partition: str) -> datetime:
    if value is None:
        return datetime(
            int(partition[:4]),
            int(partition[4:6]),
            int(partition[6:8]),
            18,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ).astimezone(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DisclosureTransportError("observed_at must be timezone-aware")
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        raise DisclosureTransportError("observed_at must be non-empty")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DisclosureTransportError("observed_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _partition_yyyymmdd(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        raise DisclosureTransportError(f"partition must be YYYYMMDD; got {value!r}")
    return digits[:8]


def land_disclosure_partition_from_rows(
    domain: str,
    conn,
    *,
    partition: str,
    rows: Sequence[Mapping[str, Any]],
    observed_at: datetime | str | None = None,
    available_at: datetime | str | None = None,
    batch_id: str | None = None,
    request: Mapping[str, Any] | None = None,
    bootstrap: bool = True,
) -> Any:
    """S1: persist provider-shaped rows to landing only.

    Does not write canonical / accepted_partition. Requires disclosure
    execution handoff (propagated here).
    """

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise DisclosureTransportError(
            f"unsupported disclosure domain={domain!r}; "
            f"allowed={sorted(DISCLOSURE_TRANSPORT_DOMAINS)}"
        )
    material = [dict(row) for row in rows]
    if not material:
        raise DisclosureTransportError(
            f"domain={domain} land-only rejects empty rows"
        )
    part = _partition_yyyymmdd(partition)
    event_at = _aware(available_at or observed_at, partition=part)
    resolved_batch_id = (
        str(batch_id).strip()
        if batch_id
        else f"{domain}:{part}:{uuid4().hex[:12]}"
    )
    if not resolved_batch_id:
        raise DisclosureTransportError("batch_id must be non-empty")

    from services.data_sources.formal_execution import (
        propagate_disclosure_execution_contract,
    )

    if domain == "holders_top10":
        from services.data_sources.holders_top10_acceptance import (
            HoldersTop10LandingBatch,
            ensure_holders_top10_acceptance_schema,
            land_holders_top10_batch,
        )
        from services.data_sources.holders_top10_contract import (
            load_holders_top10_contract,
            verify_holders_top10_contract,
        )
        from services.data_sources.holders_top10_schema import API, SOURCE

        contract = verify_holders_top10_contract(load_holders_top10_contract())
        handed = propagate_disclosure_execution_contract("holders_top10", contract)
        if bootstrap:
            ensure_holders_top10_acceptance_schema(conn)
        batch = HoldersTop10LandingBatch(
            batch_id=resolved_batch_id,
            partition_value=part,
            observed_at=event_at,
            available_at=event_at,
            rows=material,
            request=dict(request or {"api": API, "notice_date": part, "source": SOURCE}),
        )
        land_holders_top10_batch(conn, batch, handed, handoff=handed)
        return batch

    if domain == "org_holding":
        from services.data_sources.org_holding_acceptance import (
            OrgHoldingLandingBatch,
            ensure_org_holding_acceptance_schema,
            land_org_holding_batch,
        )
        from services.data_sources.org_holding_contract import (
            load_org_holding_contract,
            verify_org_holding_contract,
        )
        from services.data_sources.org_holding_schema import (
            API,
            CONTRACT_VERSION,
            SOURCE,
        )

        contract = verify_org_holding_contract(load_org_holding_contract())
        handed = propagate_disclosure_execution_contract("org_holding", contract)
        if bootstrap:
            ensure_org_holding_acceptance_schema(conn)
        batch = OrgHoldingLandingBatch(
            batch_id=resolved_batch_id,
            partition_value=part,
            observed_at=event_at,
            available_at=event_at,
            rows=material,
            request=dict(
                request or {"api": API, "available_date": part, "source": SOURCE}
            ),
            source=SOURCE,
            contract_version=CONTRACT_VERSION,
        )
        land_org_holding_batch(conn, batch, handed, handoff=handed)
        return batch

    from services.data_sources.stk_holdertrade_acceptance import (
        StkHoldertradeLandingBatch,
        ensure_stk_holdertrade_acceptance_schema,
        land_stk_holdertrade_batch,
    )
    from services.data_sources.stk_holdertrade_contract import (
        load_stk_holdertrade_contract,
        verify_stk_holdertrade_contract,
    )
    from services.data_sources.stk_holdertrade_schema import (
        API,
        CONTRACT_VERSION,
        SOURCE,
    )

    contract = verify_stk_holdertrade_contract(load_stk_holdertrade_contract())
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    if bootstrap:
        ensure_stk_holdertrade_acceptance_schema(conn)
    batch = StkHoldertradeLandingBatch(
        batch_id=resolved_batch_id,
        partition_value=part,
        observed_at=event_at,
        available_at=event_at,
        rows=material,
        request=dict(request or {"api": API, "ann_date": part, "source": SOURCE}),
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )
    land_stk_holdertrade_batch(conn, batch, handed, handoff=handed)
    return batch


def accept_disclosure_from_landing(
    domain: str,
    conn,
    batch_id: str,
    *,
    bootstrap: bool = False,
) -> Any:
    """S2: accept one LANDED disclosure batch. Zero provider fetch."""

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise DisclosureTransportError(
            f"unsupported disclosure domain={domain!r}; "
            f"allowed={sorted(DISCLOSURE_TRANSPORT_DOMAINS)}"
        )
    resolved = str(batch_id or "").strip()
    if not resolved:
        raise DisclosureTransportError("--accept-from-landing requires batch_id")

    from services.data_sources.formal_execution import (
        propagate_disclosure_execution_contract,
    )

    if domain == "holders_top10":
        from services.data_sources.holders_top10_acceptance import (
            accept_holders_top10_batch,
            ensure_holders_top10_acceptance_schema,
        )
        from services.data_sources.holders_top10_contract import (
            load_holders_top10_contract,
            verify_holders_top10_contract,
        )

        contract = verify_holders_top10_contract(load_holders_top10_contract())
        handed = propagate_disclosure_execution_contract("holders_top10", contract)
        if bootstrap:
            ensure_holders_top10_acceptance_schema(conn)
        return accept_holders_top10_batch(conn, resolved, handed, handoff=handed)

    if domain == "org_holding":
        from services.data_sources.org_holding_acceptance import (
            accept_org_holding_batch,
            ensure_org_holding_acceptance_schema,
        )
        from services.data_sources.org_holding_contract import (
            load_org_holding_contract,
            verify_org_holding_contract,
        )

        contract = verify_org_holding_contract(load_org_holding_contract())
        handed = propagate_disclosure_execution_contract("org_holding", contract)
        if bootstrap:
            ensure_org_holding_acceptance_schema(conn)
        return accept_org_holding_batch(conn, resolved, handed, handoff=handed)

    from services.data_sources.stk_holdertrade_acceptance import (
        accept_stk_holdertrade_batch,
        ensure_stk_holdertrade_acceptance_schema,
    )
    from services.data_sources.stk_holdertrade_contract import (
        load_stk_holdertrade_contract,
        verify_stk_holdertrade_contract,
    )

    contract = verify_stk_holdertrade_contract(load_stk_holdertrade_contract())
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    if bootstrap:
        ensure_stk_holdertrade_acceptance_schema(conn)
    return accept_stk_holdertrade_batch(conn, resolved, handed, handoff=handed)


def land_then_accept_disclosure_partition(
    domain: str,
    conn,
    *,
    partition: str,
    rows: Sequence[Mapping[str, Any]],
    observed_at: datetime | str | None = None,
    available_at: datetime | str | None = None,
    batch_id: str | None = None,
    request: Mapping[str, Any] | None = None,
    bootstrap: bool = True,
) -> Any:
    """Caller-only S1→S2 composition (production dual_write path)."""

    batch = land_disclosure_partition_from_rows(
        domain,
        conn,
        partition=partition,
        rows=rows,
        observed_at=observed_at,
        available_at=available_at,
        batch_id=batch_id,
        request=request,
        bootstrap=bootstrap,
    )
    return accept_disclosure_from_landing(
        domain, conn, str(batch.batch_id), bootstrap=False
    )


def disclosure_target_db_alias(domain: str) -> str:
    """DB alias for CLI accept-from-landing (manifest path)."""

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise DisclosureTransportError(
            f"unsupported disclosure domain={domain!r}"
        )
    if domain == "stk_holdertrade":
        return "tushare_raw"
    return "smartmoney"


__all__ = [
    "DISCLOSURE_TRANSPORT_DOMAINS",
    "DisclosureTransportError",
    "accept_disclosure_from_landing",
    "disclosure_target_db_alias",
    "land_disclosure_partition_from_rows",
    "land_then_accept_disclosure_partition",
]
