"""Thin transport orchestration for E0 disclosure domains.

Strangler surfaces (caller-only composition; not a second dragon):
- S1 land-only via :func:`land_disclosure_partition_from_rows`,
  :func:`land_disclosure_partition_from_legacy` (local legacy), or
  :func:`land_disclosure_partition_from_provider` (bounded provider;
  ``stk_holdertrade`` by_ann_date + ``holders_top10`` by_notice_date)
- S2 accept-from-landing via :func:`accept_disclosure_from_landing` (zero fetch)
- :func:`land_then_accept_disclosure_partition` = S1 then S2 in the caller

Production ``disclosure_dual_write`` uses land_then_accept. Fused
``publish_accepted_*`` helpers remain thin aliases for older callers/tests.
CLI: ``--land-only --from-local-raw`` (all three) or ``--land-only`` provider
for ``stk_holdertrade`` / ``holders_top10`` (≤40d; no mass dump).
Local-raw empty partitions are typed ``empty_skip`` (continue window);
provider empty stays fail-closed / stop-on-first-fail.
``org_holding`` remains local-raw (by-period ~830k/period = mass; no
by-calendar-date faucet; BLOCKED for provider land).
Domains: holders_top10 (miaoxiang), org_holding (miaoxiang),
stk_holdertrade (tushare).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

DISCLOSURE_TRANSPORT_DOMAINS = frozenset(
    {"holders_top10", "org_holding", "stk_holdertrade"}
)
# Full-market-by-date provider land (formal-shaped acquire). Not by_ts_code.
# org_holding excluded: by-period full-market ~830k rows = mass dump ban.
DISCLOSURE_PROVIDER_LAND_DOMAINS = frozenset(
    {"stk_holdertrade", "holders_top10"}
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


def load_disclosure_legacy_partition_rows(
    domain: str,
    conn,
    *,
    partition: str,
    stock_codes: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Load provider-shaped rows from local legacy tables (no network).

    Empty result is a valid load outcome; callers fail closed as needed.
    """

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise DisclosureTransportError(
            f"unsupported disclosure domain={domain!r}; "
            f"allowed={sorted(DISCLOSURE_TRANSPORT_DOMAINS)}"
        )
    part = _partition_yyyymmdd(partition)
    codes = [c.strip() for c in (stock_codes or []) if c and str(c).strip()]

    if domain == "holders_top10":
        from services.data_sources.holders_top10_schema import COMPATIBILITY_RETIRED

        if COMPATIBILITY_RETIRED:
            raise DisclosureTransportError(
                "holders_compat_retired: fact_top10_holder_period dropped; "
                "use provider land or accepted canonical"
            )
        from services.data_sources.holders_top10_schema import (
            CANONICAL_ROW_FIELDS,
            COMPATIBILITY_TABLE,
            SOURCE,
            assign_unique_holders_row_seq,
        )

        cols = ", ".join(CANONICAL_ROW_FIELDS)
        raw = conn.execute(
            f"""
            SELECT {cols}
              FROM {COMPATIBILITY_TABLE}
             WHERE source = ?
               AND replace(CAST(notice_date AS VARCHAR), '-', '') = ?
             ORDER BY stock_code, holder_rank, row_seq, holder_name
            """,
            [SOURCE, part],
        ).fetchall()
        return assign_unique_holders_row_seq(
            [dict(zip(CANONICAL_ROW_FIELDS, row, strict=True)) for row in raw]
        )

    if domain == "org_holding":
        from services.data_sources.org_holding_schema import (
            COMPATIBILITY_TABLE,
            PROVIDER_FIELDS,
        )

        cols = ", ".join(PROVIDER_FIELDS)
        sql = f"""
            SELECT {cols}
              FROM {COMPATIBILITY_TABLE}
             WHERE replace(CAST(available_date AS VARCHAR), '-', '') = ?
        """
        params: list[Any] = [part]
        if codes:
            placeholders = ", ".join("?" for _ in codes)
            sql += f" AND stock_code IN ({placeholders})"
            params.extend(codes)
        sql += " ORDER BY stock_code, holder_code, fund_derivecode, report_date"
        raw = conn.execute(sql, params).fetchall()
        return [dict(zip(PROVIDER_FIELDS, row, strict=True)) for row in raw]

    from services.data_sources.stk_holdertrade_schema import (
        COMPATIBILITY_TABLE,
        PROVIDER_FIELDS,
    )

    cols = ", ".join(PROVIDER_FIELDS)
    raw = conn.execute(
        f"""
        SELECT {cols}
          FROM {COMPATIBILITY_TABLE}
         WHERE replace(CAST(ann_date AS VARCHAR), '-', '') = ?
         ORDER BY ts_code, holder_name, in_de
        """,
        [part],
    ).fetchall()
    return [dict(zip(PROVIDER_FIELDS, row, strict=True)) for row in raw]


def land_disclosure_partition_from_legacy(
    domain: str,
    conn,
    *,
    partition: str,
    observed_at: datetime | str | None = None,
    available_at: datetime | str | None = None,
    batch_id: str | None = None,
    stock_codes: Sequence[str] | None = None,
    bootstrap: bool = True,
) -> Any:
    """S1: land one disclosure partition from local legacy rows only.

    Does not write canonical / accepted_partition and does not call providers.
    """

    part = _partition_yyyymmdd(partition)
    rows = load_disclosure_legacy_partition_rows(
        domain, conn, partition=part, stock_codes=stock_codes
    )
    if not rows:
        raise DisclosureTransportError(
            f"domain={domain} no legacy rows for partition={part}"
        )
    return land_disclosure_partition_from_rows(
        domain,
        conn,
        partition=part,
        rows=rows,
        observed_at=observed_at,
        available_at=available_at,
        batch_id=batch_id,
        bootstrap=bootstrap,
    )


def fetch_disclosure_provider_partition_rows(
    domain: str,
    *,
    partition: str,
    fetch_rows: Callable[[str, str], Sequence[Mapping[str, Any]] | None] | None = None,
) -> list[dict[str, Any]]:
    """Fetch provider-shaped rows for one disclosure partition (no land/accept).

    Default paths (formal-shaped full-market-by-date):
    - ``stk_holdertrade`` = tushare by ``ann_date``
    - ``holders_top10`` = miaoxiang by ``UPDATE_DATE`` (= notice_date)

    ``org_holding`` by-date provider land banned: aif10
    ``RPT_MAIN_ORGHOLDDETAIL`` is by-period full-market (~830k rows/period; no
    NOTICE_DATE) — not a ≤40d by-date faucet. Daily path = incremental-by-period
    check in ``org_holding_aif10`` (fetch missing plannable → accept from
    local-raw). Inject ``fetch_rows`` for tests or use ``--from-local-raw``.
    """

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise DisclosureTransportError(
            f"unsupported disclosure domain={domain!r}; "
            f"allowed={sorted(DISCLOSURE_TRANSPORT_DOMAINS)}"
        )
    part = _partition_yyyymmdd(partition)
    if fetch_rows is not None:
        return [dict(row) for row in (fetch_rows(domain, part) or ())]
    if domain not in DISCLOSURE_PROVIDER_LAND_DOMAINS:
        if domain == "org_holding":
            raise DisclosureTransportError(
                "domain=org_holding has no safe by-date provider land "
                "(aif10 by-period ~830k rows/period; no NOTICE_DATE); "
                "use --from-local-raw"
            )
        raise DisclosureTransportError(
            f"domain={domain} has no full-market-by-date provider land; "
            "use --from-local-raw (by_ts_code/period acquire is non-formal)"
        )
    if domain == "stk_holdertrade":
        from services.data_sources.sync_runner import _adapter

        adapter = _adapter("tushare")
        raw = adapter.fetch_raw("stk_holdertrade", ann_date=part) or ()
        return [dict(row) for row in raw]
    if domain == "holders_top10":
        from services.holders_aif10 import fetch_holders_top10_by_notice_date

        return list(fetch_holders_top10_by_notice_date(part))
    raise DisclosureTransportError(
        f"domain={domain} provider land not wired"
    )


def land_disclosure_partition_from_provider(
    domain: str,
    conn,
    *,
    partition: str,
    observed_at: datetime | str | None = None,
    available_at: datetime | str | None = None,
    batch_id: str | None = None,
    bootstrap: bool = True,
    fetch_rows: Callable[[str, str], Sequence[Mapping[str, Any]] | None] | None = None,
) -> Any:
    """S1: land one disclosure partition from provider faucet (no accept).

    Preserves provider rows as ``raw_evidence``; no universe exclude-then-fetch.
    """

    part = _partition_yyyymmdd(partition)
    rows = fetch_disclosure_provider_partition_rows(
        domain, partition=part, fetch_rows=fetch_rows
    )
    if not rows:
        raise DisclosureTransportError(
            f"domain={domain} no provider rows for partition={part}"
        )
    if domain == "holders_top10":
        request = {
            "api": "RPT_F10_EH_FREEHOLDERS",
            "notice_date": part,
            "source": "miaoxiang",
            "acquire_mode": "provider",
        }
    elif domain == "stk_holdertrade":
        request = {
            "api": "stk_holdertrade",
            "ann_date": part,
            "source": "tushare",
            "acquire_mode": "provider",
        }
    else:
        request = {"partition": part, "acquire_mode": "provider"}
    return land_disclosure_partition_from_rows(
        domain,
        conn,
        partition=part,
        rows=rows,
        observed_at=observed_at,
        available_at=available_at,
        batch_id=batch_id,
        request=request,
        bootstrap=bootstrap,
    )


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
        request_payload = dict(
            request or {"api": API, "notice_date": part, "source": SOURCE}
        )
        batch = HoldersTop10LandingBatch(
            batch_id=resolved_batch_id,
            partition_value=part,
            observed_at=event_at,
            available_at=event_at,
            rows=material,
            request=request_payload,
        )
        landed_id = land_holders_top10_batch(conn, batch, handed, handoff=handed)
        # Skip-land may return an existing ACCEPTED batch_id (same payload_hash).
        if landed_id != resolved_batch_id:
            from dataclasses import replace

            batch = replace(batch, batch_id=landed_id)
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
    merge_grains: bool = False,
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
        return accept_org_holding_batch(
            conn, resolved, handed, handoff=handed, merge_grains=merge_grains
        )

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
    merge_grains: bool = False,
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
        domain, conn, str(batch.batch_id), bootstrap=False, merge_grains=merge_grains
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
    "DISCLOSURE_PROVIDER_LAND_DOMAINS",
    "DISCLOSURE_TRANSPORT_DOMAINS",
    "DisclosureTransportError",
    "accept_disclosure_from_landing",
    "disclosure_target_db_alias",
    "fetch_disclosure_provider_partition_rows",
    "land_disclosure_partition_from_legacy",
    "land_disclosure_partition_from_provider",
    "land_disclosure_partition_from_rows",
    "land_then_accept_disclosure_partition",
    "load_disclosure_legacy_partition_rows",
]
