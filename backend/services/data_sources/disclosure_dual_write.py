"""E0 strangler dual-write: formal land→accept then legacy compatibility mirror.

Production disclosure writers route here by default.  Legacy-only direct writes
remain available only via ``authorize_nonconforming_direct_write`` escape hatch
(tests/emergency).  Research still reads compatibility tables; DatasetSnapshot
and ``/api/v3/inst`` cutover stay blocked until live shadow MATCH + consumer switch.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DisclosureDualWriteOutcome:
    domain: str
    status: str
    partitions: tuple[str, ...]
    canonical_rows: int
    legacy_rows_written: int
    batch_ids: tuple[str, ...]
    rejection_code: str | None = None


class DisclosureDualWriteError(RuntimeError):
    """Formal+mirror dual-write failed closed."""

    def __init__(self, domain: str, *, reason: str, detail: str):
        self.domain = domain
        self.reason = reason
        self.detail = detail
        super().__init__(f"domain={domain} reason={reason} {detail}")


def _compact_yyyymmdd(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def _iso_date(value: Any) -> str | None:
    compact = _compact_yyyymmdd(value)
    if compact is None:
        return None
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _default_event_instant(partition: str) -> datetime:
    return datetime(
        int(partition[:4]),
        int(partition[4:6]),
        int(partition[6:8]),
        18,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc)


def _project(row: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {name: row.get(name) for name in fields}


def _require_accepted(domain: str, outcome: Any) -> None:
    status = str(getattr(outcome, "status", "") or "")
    if status != "ACCEPTED":
        code = getattr(outcome, "rejection_code", None)
        raise DisclosureDualWriteError(
            domain,
            reason="formal_accept_rejected",
            detail=f"status={status!r} rejection_code={code!r}",
        )


def write_holders_top10_formal_then_mirror(
    conn,
    rows: Iterable[Mapping[str, Any]],
    *,
    observed_at: datetime | str | None = None,
    available_at: datetime | str | None = None,
    mirror: Callable[[Any, list[dict[str, Any]]], int] | None = None,
) -> DisclosureDualWriteOutcome:
    """Publish by notice_date (stock-merge) then mirror full legacy rows."""

    from services.data_sources.holders_top10_acceptance import (
        HoldersTop10LandingBatch,
        publish_accepted_holders_top10_partition,
    )
    from services.data_sources.holders_top10_contract import load_holders_top10_contract
    from services.data_sources.holders_top10_schema import (
        API,
        CANONICAL_TABLE,
        PROVIDER_FIELDS,
        SOURCE,
    )

    material = [dict(row) for row in rows]
    if not material:
        return DisclosureDualWriteOutcome(
            domain="holders_top10",
            status="EMPTY",
            partitions=(),
            canonical_rows=0,
            legacy_rows_written=0,
            batch_ids=(),
        )

    by_partition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in material:
        partition = _compact_yyyymmdd(row.get("notice_date"))
        if partition is None:
            raise DisclosureDualWriteError(
                "holders_top10",
                reason="missing_notice_date",
                detail="formal path requires notice_date on every row",
            )
        provider = _project(row, PROVIDER_FIELDS)
        provider["notice_date"] = partition
        provider["report_date"] = _compact_yyyymmdd(provider.get("report_date"))
        by_partition[partition].append(provider)

    stocks = {str(row.get("stock_code") or "").strip() for row in material}
    stocks.discard("")
    batch_ids: list[str] = []
    canonical_total = 0
    contract = load_holders_top10_contract()

    for partition, provider_rows in sorted(by_partition.items()):
        event_at = available_at or observed_at or _default_event_instant(partition)
        # Per-stock writers must merge, not wipe other stocks on the same notice_date.
        others: list[dict[str, Any]] = []
        try:
            existing = conn.execute(
                f"""
                SELECT {", ".join(PROVIDER_FIELDS)}
                  FROM {CANONICAL_TABLE}
                 WHERE notice_date = ?
                """,
                [partition],
            ).fetchall()
            for existing_row in existing:
                mapped = dict(zip(PROVIDER_FIELDS, existing_row, strict=True))
                if str(mapped.get("stock_code") or "").strip() in stocks:
                    continue
                others.append(mapped)
        except Exception:  # noqa: BLE001 — table may not exist yet
            others = []

        merged = others + provider_rows
        batch_id = f"holders_top10:{partition}:{uuid4().hex[:12]}"
        outcome = publish_accepted_holders_top10_partition(
            conn,
            HoldersTop10LandingBatch(
                batch_id=batch_id,
                partition_value=partition,
                observed_at=event_at,
                available_at=event_at,
                rows=merged,
                request={"api": API, "notice_date": partition, "source": SOURCE},
            ),
            contract,
        )
        _require_accepted("holders_top10", outcome)
        batch_ids.append(batch_id)
        canonical_total = max(canonical_total, int(outcome.row_count or 0))

    mirror_fn = mirror or _default_holders_legacy_mirror
    legacy_n = mirror_fn(conn, material)
    return DisclosureDualWriteOutcome(
        domain="holders_top10",
        status="ACCEPTED",
        partitions=tuple(sorted(by_partition)),
        canonical_rows=canonical_total,
        legacy_rows_written=int(legacy_n),
        batch_ids=tuple(batch_ids),
    )


def _default_holders_legacy_mirror(conn, rows: list[dict[str, Any]]) -> int:
    from services.holders_aif10 import _write_legacy_direct

    return _write_legacy_direct(conn, rows)


def write_org_holding_formal_then_mirror(
    conn,
    rows: Iterable[Mapping[str, Any]],
    *,
    observed_at: datetime | str | None = None,
    available_at: datetime | str | None = None,
    mirror: Callable[[Any, list[dict[str, Any]]], int] | None = None,
) -> DisclosureDualWriteOutcome:
    """Publish by available_date then mirror full legacy rows (ISO dates)."""

    from services.data_sources.org_holding_acceptance import (
        OrgHoldingLandingBatch,
        publish_accepted_org_holding_partition,
    )
    from services.data_sources.org_holding_contract import load_org_holding_contract
    from services.data_sources.org_holding_schema import (
        API,
        CONTRACT_VERSION,
        PROVIDER_FIELDS,
        SOURCE,
    )

    material = [dict(row) for row in rows]
    if not material:
        return DisclosureDualWriteOutcome(
            domain="org_holding",
            status="EMPTY",
            partitions=(),
            canonical_rows=0,
            legacy_rows_written=0,
            batch_ids=(),
        )

    by_partition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in material:
        partition = _compact_yyyymmdd(row.get("available_date"))
        if partition is None:
            raise DisclosureDualWriteError(
                "org_holding",
                reason="missing_available_date",
                detail="formal path requires available_date on every row",
            )
        provider = _project(row, PROVIDER_FIELDS)
        provider["available_date"] = partition
        provider["report_date"] = _compact_yyyymmdd(provider.get("report_date"))
        by_partition[partition].append(provider)

    batch_ids: list[str] = []
    canonical_total = 0
    contract = load_org_holding_contract()
    for partition, provider_rows in sorted(by_partition.items()):
        event_at = available_at or observed_at or _default_event_instant(partition)
        batch_id = f"org_holding:{partition}:{uuid4().hex[:12]}"
        outcome = publish_accepted_org_holding_partition(
            conn,
            OrgHoldingLandingBatch(
                batch_id=batch_id,
                partition_value=partition,
                observed_at=event_at,
                available_at=event_at,
                rows=provider_rows,
                request={"api": API, "available_date": partition, "source": SOURCE},
                source=SOURCE,
                contract_version=CONTRACT_VERSION,
            ),
            contract,
        )
        _require_accepted("org_holding", outcome)
        batch_ids.append(batch_id)
        canonical_total += int(outcome.row_count or 0)

    # Legacy store keeps ISO dates for research continuity.
    legacy_rows = []
    for row in material:
        mirrored = dict(row)
        if mirrored.get("report_date") is not None:
            mirrored["report_date"] = _iso_date(mirrored["report_date"])
        if mirrored.get("available_date") is not None:
            mirrored["available_date"] = _iso_date(mirrored["available_date"])
        legacy_rows.append(mirrored)

    mirror_fn = mirror or _default_org_holding_legacy_mirror
    legacy_n = mirror_fn(conn, legacy_rows)
    return DisclosureDualWriteOutcome(
        domain="org_holding",
        status="ACCEPTED",
        partitions=tuple(sorted(by_partition)),
        canonical_rows=canonical_total,
        legacy_rows_written=int(legacy_n),
        batch_ids=tuple(batch_ids),
    )


def _default_org_holding_legacy_mirror(conn, rows: list[dict[str, Any]]) -> int:
    from services.org_holding_aif10 import _upsert_rows_legacy_direct

    return _upsert_rows_legacy_direct(conn, rows)


def write_stk_holdertrade_formal_then_mirror(
    conn,
    rows: Iterable[Mapping[str, Any]],
    *,
    observed_at: datetime | str | None = None,
    available_at: datetime | str | None = None,
    mirror: Callable[[Any, list[dict[str, Any]]], int] | None = None,
) -> DisclosureDualWriteOutcome:
    """Publish by ann_date then mirror provider fields to legacy raw table."""

    from services.data_sources.stk_holdertrade_acceptance import (
        StkHoldertradeLandingBatch,
        publish_accepted_stk_holdertrade_partition,
    )
    from services.data_sources.stk_holdertrade_contract import (
        load_stk_holdertrade_contract,
    )
    from services.data_sources.stk_holdertrade_schema import (
        API,
        COMPATIBILITY_TABLE,
        CONTRACT_VERSION,
        PROVIDER_FIELDS,
        SOURCE,
    )

    material = [dict(row) for row in rows]
    if not material:
        return DisclosureDualWriteOutcome(
            domain="stk_holdertrade",
            status="EMPTY",
            partitions=(),
            canonical_rows=0,
            legacy_rows_written=0,
            batch_ids=(),
        )

    by_partition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in material:
        partition = _compact_yyyymmdd(row.get("ann_date"))
        if partition is None:
            raise DisclosureDualWriteError(
                "stk_holdertrade",
                reason="missing_ann_date",
                detail="formal path requires ann_date on every row",
            )
        provider = _project(row, PROVIDER_FIELDS)
        provider["ann_date"] = partition
        by_partition[partition].append(provider)

    batch_ids: list[str] = []
    canonical_total = 0
    contract = load_stk_holdertrade_contract()
    for partition, provider_rows in sorted(by_partition.items()):
        event_at = available_at or observed_at or _default_event_instant(partition)
        batch_id = f"stk_holdertrade:{partition}:{uuid4().hex[:12]}"
        outcome = publish_accepted_stk_holdertrade_partition(
            conn,
            StkHoldertradeLandingBatch(
                batch_id=batch_id,
                partition_value=partition,
                observed_at=event_at,
                available_at=event_at,
                rows=provider_rows,
                request={"api": API, "ann_date": partition, "source": SOURCE},
                source=SOURCE,
                contract_version=CONTRACT_VERSION,
            ),
            contract,
        )
        _require_accepted("stk_holdertrade", outcome)
        batch_ids.append(batch_id)
        canonical_total += int(outcome.row_count or 0)

    def _default_mirror(c, legacy_rows: list[dict[str, Any]]) -> int:
        from services.data_sources.disclosure_boundaries import (
            authorize_nonconforming_direct_write,
        )

        authorize_nonconforming_direct_write(
            "stk_holdertrade", conformity="NONCONFORMING"
        )
        cols = list(PROVIDER_FIELDS)
        placeholders = ", ".join("?" for _ in cols)
        col_sql = ", ".join(cols)
        # Partition replace for research continuity (same grain as formal).
        for partition in by_partition:
            c.execute(
                f"DELETE FROM {COMPATIBILITY_TABLE} WHERE ann_date = ?",
                [partition],
            )
        c.executemany(
            f"INSERT INTO {COMPATIBILITY_TABLE} ({col_sql}) VALUES ({placeholders})",
            [tuple(row.get(name) for name in cols) for row in legacy_rows],
        )
        return len(legacy_rows)

    mirror_fn = mirror or _default_mirror
    projected = []
    for row in material:
        item = _project(row, PROVIDER_FIELDS)
        item["ann_date"] = _compact_yyyymmdd(item.get("ann_date"))
        projected.append(item)
    legacy_n = mirror_fn(conn, projected)
    return DisclosureDualWriteOutcome(
        domain="stk_holdertrade",
        status="ACCEPTED",
        partitions=tuple(sorted(by_partition)),
        canonical_rows=canonical_total,
        legacy_rows_written=int(legacy_n),
        batch_ids=tuple(batch_ids),
    )


def accept_stk_holdertrade_partition_from_legacy(
    conn,
    ann_date: str,
    *,
    rewrite_legacy: bool = False,
) -> DisclosureDualWriteOutcome:
    """E0 canary: land→accept one ann_date from existing legacy rows.

    Default keeps legacy untouched (no-op mirror; avoids partition DELETE).
    Target DB is the registry alias for ``raw_tushare_stk_holdertrade``
    (normally ``tushare_raw``).
    """
    from services.data_sources.stk_holdertrade_schema import (
        COMPATIBILITY_TABLE,
        PROVIDER_FIELDS,
    )

    digits = "".join(ch for ch in str(ann_date or "") if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"ann_date must be YYYYMMDD, got {ann_date!r}")
    partition = digits[:8]
    cols = ", ".join(PROVIDER_FIELDS)
    raw = conn.execute(
        f"""
        SELECT {cols}
          FROM {COMPATIBILITY_TABLE}
         WHERE replace(CAST(ann_date AS VARCHAR), '-', '') = ?
         ORDER BY ts_code, holder_name, in_de
        """,
        [partition],
    ).fetchall()
    rows = [dict(zip(PROVIDER_FIELDS, row, strict=True)) for row in raw]
    if not rows:
        raise ValueError(
            f"no legacy stk_holdertrade rows for ann_date={partition}"
        )

    def _noop_mirror(_conn, material):
        return len(material)

    return write_stk_holdertrade_formal_then_mirror(
        conn,
        rows,
        mirror=(
            None if rewrite_legacy else _noop_mirror
        ),
    )


__all__ = [
    "DisclosureDualWriteError",
    "DisclosureDualWriteOutcome",
    "accept_stk_holdertrade_partition_from_legacy",
    "write_holders_top10_formal_then_mirror",
    "write_org_holding_formal_then_mirror",
    "write_stk_holdertrade_formal_then_mirror",
]
