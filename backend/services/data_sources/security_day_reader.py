"""Fail-closed reader helpers for accepted security-day partitions."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from services.data_sources.accepted_schema import (
    ACCEPTED_TABLE,
    verify_accepted_evidence_schema,
)
from services.data_sources.security_day_partition import (
    SecurityDayAcceptedPartition,
    SecurityDayDomain,
    SecurityDayError,
    _aware,
    _columns,
    canonical_content_hash,
)


def verify_security_day_read_schema(conn, domain: SecurityDayDomain) -> None:
    """Read-only schema proof for trusted loaders — never CREATE/DDL."""

    expected_landing = {
        "batch_id",
        "row_ordinal",
        "request_json",
        "payload_json",
        "row_hash",
    }
    expected_canonical = {
        str(field["name"]) for field in tuple(domain.schema_payload["fields"])
    }
    try:
        verify_accepted_evidence_schema(conn, error_type=SecurityDayError)
        landing_cols = set(_columns(conn, domain.landing_table))
        canonical_cols = set(_columns(conn, domain.canonical_table))
    except SecurityDayError:
        raise
    except Exception as exc:
        raise SecurityDayError(
            f"no_accepted_security_day_schema dataset_id={domain.dataset_id} "
            f"read_failed={str(exc)[:200]}"
        ) from exc
    if landing_cols != expected_landing:
        raise SecurityDayError(
            f"{domain.landing_table} schema drift: "
            f"missing={sorted(expected_landing - landing_cols)} "
            f"extra={sorted(landing_cols - expected_landing)}"
        )
    if canonical_cols != expected_canonical:
        raise SecurityDayError(
            f"{domain.canonical_table} schema drift: "
            f"missing={sorted(expected_canonical - canonical_cols)} "
            f"extra={sorted(canonical_cols - expected_canonical)}"
        )


def load_accepted_security_day_partition(
    conn,
    domain: SecurityDayDomain,
    observation_date: date,
    decision_time: datetime,
    *,
    contract_hash: str,
    config_hash: str,
) -> SecurityDayAcceptedPartition:
    """Fail-closed reader for one accepted security-day partition."""

    verify_security_day_read_schema(conn, domain)
    cutoff = _aware(decision_time, "decision_time")
    partition = observation_date.strftime("%Y%m%d")
    pointer = conn.execute(
        f"""
        SELECT batch_id, contract_hash, config_hash, content_hash, row_count,
               available_at, accepted_at
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [domain.dataset_id, partition],
    ).fetchone()
    if pointer is None:
        raise SecurityDayError(
            f"no_accepted_partition dataset_id={domain.dataset_id} "
            f"partition={partition}"
        )
    (
        batch_id,
        stored_contract_hash,
        stored_config_hash,
        content_hash,
        row_count,
        available_at,
        accepted_at,
    ) = pointer
    if str(stored_contract_hash) != contract_hash or str(stored_config_hash) != config_hash:
        raise SecurityDayError("accepted_partition_contract_drift")
    available = _aware(available_at, "available_at")
    accepted = _aware(accepted_at, "accepted_at")
    usable_at = max(available, accepted)
    if usable_at > cutoff:
        raise SecurityDayError(
            "accepted_partition_not_visible_at_decision_time "
            f"usable_at={usable_at.isoformat()} decision_time={cutoff.isoformat()}"
        )
    codes = conn.execute(
        f"""
        SELECT ts_code FROM {domain.canonical_table}
         WHERE trade_date = ? AND ingest_batch_id = ?
         ORDER BY ts_code
        """,
        [observation_date, str(batch_id)],
    ).fetchall()
    ts_codes = frozenset(str(row[0]) for row in codes)
    if len(ts_codes) != int(row_count):
        raise SecurityDayError(
            f"accepted_partition_row_count_mismatch pointer={row_count} "
            f"canonical={len(ts_codes)}"
        )
    if not ts_codes:
        raise SecurityDayError("accepted_partition_has_zero_members")
    other_fields = [name for name in domain.provider_fields if name != "ts_code"]
    recomputed = conn.execute(
        f"""
        SELECT ts_code, {", ".join(other_fields)}
          FROM {domain.canonical_table}
         WHERE trade_date = ? AND ingest_batch_id = ?
         ORDER BY ts_code
        """,
        [observation_date, str(batch_id)],
    ).fetchall()
    rebuilt: list[dict[str, Any]] = []
    for row in recomputed:
        item: dict[str, Any] = {"ts_code": str(row[0])}
        offset = 1
        for name in domain.provider_fields:
            if name == "ts_code":
                continue
            value = row[offset]
            offset += 1
            if name == "trade_date":
                if isinstance(value, date):
                    item[name] = value
                else:
                    compact = str(value).replace("-", "")
                    item[name] = date(
                        int(compact[:4]), int(compact[4:6]), int(compact[6:8])
                    )
            else:
                item[name] = value
        rebuilt.append(item)
    if canonical_content_hash(rebuilt, domain.provider_fields) != str(content_hash):
        raise SecurityDayError("accepted_partition_content_hash_mismatch")
    return SecurityDayAcceptedPartition(
        dataset_id=domain.dataset_id,
        partition_value=partition,
        batch_id=str(batch_id),
        contract_hash=str(stored_contract_hash),
        config_hash=str(stored_config_hash),
        content_hash=str(content_hash),
        row_count=int(row_count),
        available_at=available,
        accepted_at=accepted,
        ts_codes=ts_codes,
    )


def lineage_fields() -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "available_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_timestamp",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "availability_time",
        },
        {
            "name": "ingest_batch_id",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "batch_identifier",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "source_batch",
        },
        {
            "name": "source_row_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "landing_row_lineage",
        },
        {
            "name": "contract_version",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "version_identifier",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "definition_version",
        },
        {
            "name": "config_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "definition_and_policy_hash",
        },
        {
            "name": "built_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_timestamp",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "built_time",
        },
    )


__all__ = [
    "lineage_fields",
    "load_accepted_security_day_partition",
    "verify_security_day_read_schema",
]
