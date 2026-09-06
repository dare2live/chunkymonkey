"""Fixed schema for E0 holders_top10 formal land→accept tracer.

Accepted truth is the landing/canonical pair.  Shadow compare uses
``PROVIDER_FIELDS`` only.  Episode enrichment columns live on canonical as
nullable ``ENRICHMENT_FIELDS`` so formal-only writes no longer depend on a
legacy mirror for research rebuild.

Phase A (2026-09, ingest_holders_raw T1a): the provider
(``RPT_F10_EH_FREEHOLDERS``) returns 47 fields per row; ``_clean``
historically kept only 13. This module additionally defines ``RAW_FIELDS``
(the 47-field typed raw layer used by the Phase A fetch → staging path;
DDL/writer live in a separate task, not this file) and ``RawFetch`` (the
carrier type between the cleaner and the raw writer). Deliberately NOT part
of the canonical schema below: ``SCHEMA_VERSION`` / ``CONTRACT_VERSION`` /
``_SCHEMA_PAYLOAD`` / ``CANONICAL_ROW_FIELDS`` are byte-identical to the
pre-Phase-A committed version (verified in
``test_holders_top10_schema.py`` against ``git show HEAD``) — which
provider fields, if any, are worth promoting onto canonical is a decision
for *after* Phase A's read-only staging run, not before it (see
``.git/cm_worklog/ingest_holders_raw/DESIGN.md``). Promoting a field onto
canonical bumps the schema/contract version and, via
``holders_top10_acceptance.py``'s live ``ALTER ADD COLUMN`` path, mutates
the production table the moment any holders batch is next accepted — that
is a real, undone decision, not a no-op, so it must not ride in ahead of
the staging evidence that would justify it.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.security_day_partition import _freeze, _plain, schema_contract_hash

DATASET_ID = "tier0.disclosure.top10_float_holders_period"
LANDING_TABLE = "landing_miaoxiang_holders_top10"
CANONICAL_TABLE = "canonical_top10_float_holders_period"
SCHEMA_ID = "tier0.disclosure.top10_float_holders_period.canonical"
SCHEMA_VERSION = "2"
WRITER_ID = "services.data_sources.holders_top10_acceptance"
CONTRACT_VERSION = "3"
SOURCE = "miaoxiang"
API = "RPT_F10_EH_FREEHOLDERS"
# Retired 2026-07-26 — table DROPped; land-from-legacy / mirror refuse.
COMPATIBILITY_RETIRED = True
COMPATIBILITY_TABLE = "fact_top10_holder_period"  # sentinel name only; do not SQL
PARTITION_FIELD = "notice_date"
GRAIN = (
    "stock_code",
    "report_date",
    "holder_set",
    "holder_rank",
    "row_seq",
    "is_exit_row",
)


def assign_unique_holders_row_seq(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Renumber ``row_seq`` so GRAIN is unique within a partition load.

    Miaoxiang can emit multiple holder_name rows at the same HOLDER_RANK;
    legacy ``_clean`` historically hard-coded ``row_seq=1``, which then fails
    accept with DUPLICATE_GRAIN. ``row_seq`` exists in GRAIN for this case —
    assign stable 1..n ordered by holder_name within
    (stock_code, report_date, holder_set, holder_rank, is_exit_row).
    """

    from collections import defaultdict

    prepared = [dict(row) for row in rows]
    prepared.sort(
        key=lambda r: (
            str(r.get("stock_code") or ""),
            str(r.get("report_date") or ""),
            str(r.get("holder_set") or ""),
            int(r.get("holder_rank") or 0),
            bool(r.get("is_exit_row")),
            str(r.get("holder_name") or ""),
            str(r.get("notice_date") or ""),
        )
    )
    counters: dict[tuple[Any, ...], int] = defaultdict(int)
    out: list[dict[str, Any]] = []
    for row in prepared:
        key = (
            str(row.get("stock_code") or ""),
            str(row.get("report_date") or ""),
            str(row.get("holder_set") or ""),
            int(row.get("holder_rank") or 0),
            bool(row.get("is_exit_row")),
        )
        counters[key] += 1
        row["row_seq"] = counters[key]
        out.append(row)
    return out


# Shadow / provider identity projection (stable compare surface).
PROVIDER_FIELDS = (
    "stock_code",
    "report_date",
    "holder_set",
    "holder_rank",
    "row_seq",
    "holder_name",
    "hold_ratio_float",
    "notice_date",
    "is_exit_row",
)
# Episode rebuild columns carried on canonical (nullable for historical canary).
ENRICHMENT_FIELDS = (
    "holder_name_norm",
    "share_class",
    "shares_approx",
    "change_status",
    "hold_change_num",
    "holder_type",
)

CANONICAL_ROW_FIELDS = PROVIDER_FIELDS + ENRICHMENT_FIELDS

# ── Raw evidence layer (column-typed definition; DDL/writer live elsewhere)──
# DESIGN.md §1/§2. One column per provider key, in the provider's OWN
# verbatim names/casing (unlike every other tuple in this module, which uses
# our lowercase canonical names) — the raw table is meant to be a faithful
# column-typed mirror of the API response, not a re-derivation of it. Types
# are load-bearing, not decorative:
#   - dates stored VARCHAR as-is ('2026-06-30 00:00:00'); no implicit cast —
#     provider format is not ISO and shouldn't be reinterpreted at land time.
#   - HOLD_NUM_CHANGE is polymorphic ('新进'/'不变'/signed number) -> VARCHAR.
#   - HOLD_NUM / XZCHANGE: provider's own SQL types them INTEGER, but
#     large-holder share counts exceed int32 (2.1e9) -> BIGINT (探针实测).
# Unknown provider keys (schema drift) are not silently dropped: the raw
# writer is expected to route them into an extra_json sidecar column, and a
# >0 audit count on that means RAW_FIELDS must be widened (provider's own
# schema doc lists 40 fields; the live response already has all 47 —
# DESIGN.md §1).
RAW_FIELDS: tuple[tuple[str, str], ...] = (
    ("SECUCODE", "VARCHAR"),
    ("SECURITY_CODE", "VARCHAR"),
    ("ORG_CODE", "VARCHAR"),
    ("END_DATE", "VARCHAR"),
    ("HOLDER_NAME", "VARCHAR"),
    ("HOLD_NUM_CHANGE", "VARCHAR"),
    ("IS_HOLDORG", "VARCHAR"),
    ("SECURITY_NAME_ABBR", "VARCHAR"),
    ("HOLDER_CODE", "VARCHAR"),
    ("SECURITY_TYPE_CODE", "VARCHAR"),
    ("HOLDER_STATE", "VARCHAR"),
    ("HOLD_CHANGE", "VARCHAR"),
    ("HOLDER_TYPE", "VARCHAR"),
    ("SHARES_TYPE", "VARCHAR"),
    ("UPDATE_DATE", "VARCHAR"),
    ("REPORT_DATE_NAME", "VARCHAR"),
    ("HOLDER_NEW", "VARCHAR"),
    ("FREE_RATIO_QOQ", "VARCHAR"),
    ("HOLDER_STATEE", "VARCHAR"),
    ("IS_REPORT", "VARCHAR"),
    ("HOLDER_CODE_OLD", "VARCHAR"),
    ("HOLDER_NEWTYPE", "VARCHAR"),
    ("HOLDNUM_CHANGE_NAME", "VARCHAR"),
    ("IS_MAX_REPORTDATE", "VARCHAR"),
    ("COOPERATION_HOLDER_MARK", "VARCHAR"),
    ("MXID", "VARCHAR"),
    ("LISTING_STATE", "VARCHAR"),
    ("NEW_CHANGE_RATIO", "VARCHAR"),
    ("HOLDER_STATE_NEW", "VARCHAR"),
    ("HOLD_ORG_CODE_SOURCE", "VARCHAR"),
    ("HOLD_NUM_ABBR", "VARCHAR"),
    ("IS_DJG", "VARCHAR"),
    ("IS_SJKZR", "VARCHAR"),
    ("CAMRELATION_GROUP_LABEL", "VARCHAR"),
    ("IS_LANDSTOCK", "VARCHAR"),
    ("HOLDER_RELATION_LABLE", "VARCHAR"),
    ("NOTICE_DATE", "VARCHAR"),
    ("FREE_HOLDNUM_RATIO", "DOUBLE"),
    ("CHANGE_RATIO", "DOUBLE"),
    ("HOLDER_MARKET_CAP", "DOUBLE"),
    ("HOLD_RATIO", "DOUBLE"),
    ("HOLD_RATIO_CHANGE", "DOUBLE"),
    ("HOLD_CHANGE_RATIOTB", "DOUBLE"),
    ("LISTED_SHARES_RATIO", "DOUBLE"),
    ("HOLD_NUM", "BIGINT"),
    ("XZCHANGE", "BIGINT"),
    ("HOLDER_RANK", "INTEGER"),
)


@dataclass(frozen=True, slots=True)
class RawFetch:
    """One provider fetch's raw rows — carrier between the cleaner and the
    raw-evidence writer.

    Producer: ``holders_aif10.build_rows`` (returns ``tuple[RawFetch,
    list[dict]]``). Consumer: ``land_holders_top10_raw_fetch(conn, fetch) ->
    list[row_hash]`` (raw-table single writer, Tx-R). See DESIGN.md §1 layer
    diagram: provider -> [this] -> raw landing table -> (clean +
    derive_exits) -> cleaned landing table -> canonical.

    Deliberately thin: normalization into ``RAW_FIELDS`` columns, per-row
    ``row_hash`` computation, ``extra_json`` handling for unknown keys, and
    ``(fetch_id, row_ordinal)`` PRIMARY KEY assignment all happen in the raw
    writer, not here — this type only needs to carry what one provider call
    produced.

    Fields:
      fetch_id: caller-chosen identifier. The raw writer is idempotent by
        ``(fetch_id, row_ordinal)``, so a caller that wants a re-fetch to
        safely no-op on replay must derive a *stable* id (e.g. stock_code +
        a run_id, or a content hash) — a fresh ``uuid4()`` per call defeats
        the idempotency the PK exists to give you.
      stock_code: which stock this fetch covers. Needed for DESIGN.md §4 A3's
        per-stock replay-equality check (raw rows for a stock -> _clean ->
        _derive_exits must reproduce that stock's canonical rows exactly).
      request: the provider call parameters (api/secucode/page_size/filters
        etc.), kept verbatim as evidence of what was asked for — mirrors
        ``HoldersTop10LandingBatch.request`` in holders_top10_acceptance.py.
      rows: the provider's raw rows in original order, each a mapping keyed
        by provider field names (``RAW_FIELDS`` keys). A key not in
        ``RAW_FIELDS`` is not this type's concern — unknown-key handling
        (``extra_json``) lives in the raw writer.
    """

    fetch_id: str
    stock_code: str
    request: Mapping[str, Any]
    rows: tuple[Mapping[str, Any], ...]


_SCHEMA_PAYLOAD: dict[str, Any] = {
    "schema_id": SCHEMA_ID,
    "schema_version": SCHEMA_VERSION,
    "dataset_id": DATASET_ID,
    "canonical_table": CANONICAL_TABLE,
    "primary_key": list(GRAIN),
    "duplicate_policy": "reject",
    "fields": [
        {
            "name": "stock_code",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "security_code",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "report_date",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "report_period_yyyymmdd",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_set",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "holder_set_label",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_rank",
            "duckdb_type": "INTEGER",
            "nullable": False,
            "unit": "rank",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "row_seq",
            "duckdb_type": "INTEGER",
            "nullable": False,
            "unit": "row_sequence",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_name",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "holder_name_label",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "hold_ratio_float",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "percent",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "notice_date",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "calendar_date_yyyymmdd",
            "null_semantics": "forbidden",
            "origin": "provider",
            "role": "availability_event_time",
        },
        {
            "name": "is_exit_row",
            "duckdb_type": "BOOLEAN",
            "nullable": False,
            "unit": "derived_exit_flag",
            "null_semantics": "forbidden",
            "origin": "derived",
        },
        {
            "name": "holder_name_norm",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "holder_name_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "share_class",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "share_class_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "shares_approx",
            "duckdb_type": "BIGINT",
            "nullable": True,
            "unit": "share_count",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "change_status",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "change_status_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "hold_change_num",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "share_count_delta",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "holder_type",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "holder_type_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "available_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_instant",
            "null_semantics": "forbidden",
            "origin": "batch",
            "role": "publication_visibility",
        },
        {
            "name": "ingest_batch_id",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "batch_id",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "source_row_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "contract_version",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "version_token",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "config_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "built_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_instant",
            "null_semantics": "forbidden",
            "origin": "system",
        },
    ],
}
SCHEMA_CONTRACT: Mapping[str, Any] = _freeze(_SCHEMA_PAYLOAD)
SCHEMA_HASH = schema_contract_hash(SCHEMA_CONTRACT)


def schema_contract_payload() -> dict[str, Any]:
    return _plain(SCHEMA_CONTRACT)


__all__ = [
    "ACCEPTED_TABLE",
    "API",
    "CANONICAL_ROW_FIELDS",
    "CANONICAL_TABLE",
    "COMPATIBILITY_RETIRED",
    "COMPATIBILITY_TABLE",
    "CONTRACT_VERSION",
    "DATASET_ID",
    "ENRICHMENT_FIELDS",
    "GRAIN",
    "INGEST_BATCH_TABLE",
    "LANDING_TABLE",
    "PARTITION_FIELD",
    "PROVIDER_FIELDS",
    "RAW_FIELDS",
    "RawFetch",
    "SCHEMA_CONTRACT",
    "SCHEMA_HASH",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SOURCE",
    "WRITER_ID",
    "assign_unique_holders_row_seq",
    "schema_contract_payload",
]
