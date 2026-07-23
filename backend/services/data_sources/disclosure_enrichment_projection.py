"""Holders episode enrichment: canonical spine + typed legacy join.

Institution-profile rebuild prefers accepted canonical provider/enrichment
columns.  After E0-HIST, live canonical carries enrichment fields; ``新进``
rows legitimately null ``hold_change_num`` (no prior position).  Attestation is
ACCEPTED when those semantics hold — never a silent legacy-only rebuild.
Legacy join remains only for periods absent from canonical (legacy_only).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE,
    COMPATIBILITY_TABLE,
    ENRICHMENT_FIELDS,
    GRAIN,
    PROVIDER_FIELDS,
    SOURCE,
)

FieldStatus = Literal["ACCEPTED", "PARTIAL", "DERIVED"]
FieldSource = Literal[
    "canonical",
    "canonical_prefer_else_legacy",
    "canonical_derived",
    "legacy_join",
]


@dataclass(frozen=True)
class EnrichmentFieldAttestation:
    field: str
    status: FieldStatus
    source: FieldSource
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "status": self.status,
            "source": self.source,
            "reason": self.reason,
        }


# Episode rebuild consumes these columns (see institution_profile.build_episodes).
_EPISODE_FIELDS: tuple[str, ...] = (
    "holder_name_norm",
    "stock_code",
    "report_date",
    "change_status",
    "is_exit_row",
    "shares_approx",
    "hold_change_num",
    "holder_type",
    "notice_date",
    "share_class",
    "holder_rank",
    "row_seq",
)


def holders_field_attestations() -> tuple[EnrichmentFieldAttestation, ...]:
    """Typed per-field status for feature-store profile honesty."""

    spine = []
    for name in PROVIDER_FIELDS:
        if name == "holder_name":
            continue
        spine.append(
            EnrichmentFieldAttestation(
                field=name,
                status="ACCEPTED",
                source="canonical",
                reason="accepted_canonical_provider_field",
            )
        )
    spine.append(
        EnrichmentFieldAttestation(
            field="holder_name_norm",
            status="DERIVED",
            source="canonical_derived",
            reason="coalesce_canonical_enrichment_or_holder_name",
        )
    )
    for name in ENRICHMENT_FIELDS:
        if name == "holder_name_norm":
            continue
        if name == "hold_change_num":
            spine.append(
                EnrichmentFieldAttestation(
                    field=name,
                    status="ACCEPTED",
                    source="canonical",
                    reason=(
                        "canonical_enrichment_accepted; "
                        "null_ok_when_change_status_is_xinjin_no_prior_position"
                    ),
                )
            )
            continue
        spine.append(
            EnrichmentFieldAttestation(
                field=name,
                status="ACCEPTED",
                source="canonical",
                reason="canonical_enrichment_accepted_after_e0_hist",
            )
        )
    return tuple(spine)


def feature_store_profiles_attestation() -> dict[str, Any]:
    fields = holders_field_attestations()
    partial = [item.field for item in fields if item.status == "PARTIAL"]
    return {
        "status": "PARTIAL" if partial else "ACCEPTED",
        "reason": (
            "canonical_spine_accepted; enrichment_fields_partial_until_"
            "historical_partitions_carry_canonical_enrichment: "
            + ",".join(partial)
            if partial
            else (
                "all_episode_fields_on_canonical; "
                "legacy_join_only_for_periods_absent_from_canonical"
            )
        ),
        "fields": [item.as_dict() for item in fields],
        "rebuild_source": "canonical_spine_legacy_enrichment_projection",
        "legacy_table": COMPATIBILITY_TABLE,
        "canonical_table": CANONICAL_TABLE,
    }


def holders_episode_events_sql(
    *,
    canonical_qual: str | None = None,
    legacy_qual: str | None = None,
) -> str:
    """SQL for episode event rows: canonical LEFT JOIN legacy + legacy-only.

    ``canonical_qual`` / ``legacy_qual`` default to bare table names (tests) or
    ``sm.<table>`` when feature_store has attached smartmoney.

    Canonical enrichment wins when present. Legacy fills only missing
    enrichment cells and periods absent from canonical (legacy_only).
    """

    canon = canonical_qual or CANONICAL_TABLE
    legacy = legacy_qual or COMPATIBILITY_TABLE
    grain_join = " AND ".join(f"c.{name} IS NOT DISTINCT FROM l.{name}" for name in GRAIN)
    return f"""
    WITH canon AS (
        SELECT *
          FROM {canon}
    ),
    legacy AS (
        SELECT *
          FROM {legacy}
         WHERE source = '{SOURCE}'
    ),
    from_canon AS (
        SELECT
            COALESCE(c.holder_name_norm, c.holder_name, l.holder_name_norm, l.holder_name)
                AS holder_name_norm,
            c.stock_code AS stock_code,
            c.report_date AS report_date,
            COALESCE(
                c.change_status,
                CASE WHEN c.is_exit_row THEN '退出' ELSE NULL END,
                l.change_status
            ) AS change_status,
            c.is_exit_row AS is_exit_row,
            COALESCE(c.shares_approx, l.shares_approx) AS shares_approx,
            COALESCE(c.hold_change_num, l.hold_change_num) AS hold_change_num,
            COALESCE(c.holder_type, l.holder_type) AS holder_type,
            c.notice_date AS notice_date,
            COALESCE(c.share_class, l.share_class, 'A') AS share_class,
            c.holder_rank AS holder_rank,
            c.row_seq AS row_seq,
            l.raw_hash AS raw_hash
        FROM canon c
        LEFT JOIN legacy l ON {grain_join}
    ),
    legacy_only AS (
        SELECT
            COALESCE(l.holder_name_norm, l.holder_name) AS holder_name_norm,
            l.stock_code AS stock_code,
            l.report_date AS report_date,
            l.change_status AS change_status,
            l.is_exit_row AS is_exit_row,
            l.shares_approx AS shares_approx,
            l.hold_change_num AS hold_change_num,
            l.holder_type AS holder_type,
            l.notice_date AS notice_date,
            COALESCE(l.share_class, 'A') AS share_class,
            l.holder_rank AS holder_rank,
            l.row_seq AS row_seq,
            l.raw_hash AS raw_hash
        FROM legacy l
        WHERE NOT EXISTS (
            SELECT 1 FROM canon c WHERE {grain_join}
        )
    )
    SELECT * FROM from_canon
    UNION ALL
    SELECT * FROM legacy_only
    """


def holders_period_keys_sql(
    *,
    canonical_qual: str | None = None,
    legacy_qual: str | None = None,
) -> str:
    """Distinct (stock_code, report_date) from the same projection spine."""

    events = holders_episode_events_sql(
        canonical_qual=canonical_qual, legacy_qual=legacy_qual
    )
    return f"""
    SELECT DISTINCT stock_code, report_date
      FROM ({events}) AS episode_events
     WHERE length(report_date) = 8
    """


def summarize_projection_coverage(conn) -> Mapping[str, Any]:
    """Optional diagnostic counts; not a readiness certificate."""

    sql = holders_episode_events_sql()
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM ({sql}) AS t").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}:{exc}", "rows": 0}
    return {"ok": True, "rows": int(n), "fields": list(_EPISODE_FIELDS)}


__all__ = [
    "EnrichmentFieldAttestation",
    "feature_store_profiles_attestation",
    "holders_episode_events_sql",
    "holders_field_attestations",
    "holders_period_keys_sql",
    "summarize_projection_coverage",
]
