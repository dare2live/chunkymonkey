"""Holders episode enrichment: canonical-only spine.

Institution-profile rebuild reads accepted canonical provider/enrichment
columns.  After E0-HIST + fact retire (2026-07-26), ``fact_top10_holder_period``
is gone — no LEFT JOIN / legacy_only union.  ``新进`` rows legitimately null
``hold_change_num`` (no prior position).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE,
    ENRICHMENT_FIELDS,
    PROVIDER_FIELDS,
)

FieldStatus = Literal["ACCEPTED", "PARTIAL", "DERIVED"]
FieldSource = Literal[
    "canonical",
    "canonical_derived",
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
            "canonical_spine_accepted; enrichment_fields_partial: "
            + ",".join(partial)
            if partial
            else "all_episode_fields_on_canonical; holders_compat_retired"
        ),
        "fields": [item.as_dict() for item in fields],
        "rebuild_source": "canonical_only",
        "legacy_table": None,
        "canonical_table": CANONICAL_TABLE,
    }


def holders_episode_events_sql(
    *,
    canonical_qual: str | None = None,
    legacy_qual: str | None = None,  # noqa: ARG001 — kept for call-site compat; ignored
) -> str:
    """SQL for episode event rows from canonical only (compat plane retired)."""

    canon = canonical_qual or CANONICAL_TABLE
    return f"""
    SELECT
        COALESCE(c.holder_name_norm, c.holder_name) AS holder_name_norm,
        c.stock_code AS stock_code,
        c.report_date AS report_date,
        COALESCE(
            c.change_status,
            CASE WHEN c.is_exit_row THEN '退出' ELSE NULL END
        ) AS change_status,
        c.is_exit_row AS is_exit_row,
        c.shares_approx AS shares_approx,
        c.hold_change_num AS hold_change_num,
        c.holder_type AS holder_type,
        c.notice_date AS notice_date,
        COALESCE(c.share_class, 'A') AS share_class,
        c.holder_rank AS holder_rank,
        c.row_seq AS row_seq,
        CAST(NULL AS VARCHAR) AS raw_hash
    FROM {canon} AS c
    """


def holders_period_keys_sql(
    *,
    canonical_qual: str | None = None,
    legacy_qual: str | None = None,
) -> str:
    """Distinct (stock_code, report_date) from the canonical-only spine."""

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
