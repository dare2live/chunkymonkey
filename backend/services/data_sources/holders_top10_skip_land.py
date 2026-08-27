"""Idempotent skip for holders_top10 land when ACCEPTED row content is unchanged.

Knife 2: stop uuid re-land storm without deleting landing evidence.
Identity is accepted landing row_hashes + contract/config, not the fetch
envelope clock (observed_at/available_at). Clock in payload_hash would make
every same-day re-click a new batch.
"""
from __future__ import annotations

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.holders_top10_schema import DATASET_ID, LANDING_TABLE


def find_accepted_batch_with_same_payload(
    conn,
    *,
    partition: str,
    contract_hash: str,
    config_hash: str,
    row_signatures: list[str],
) -> str | None:
    """Return current accepted batch_id when landing row hashes match, else None."""

    row = conn.execute(
        f"""
        SELECT ap.batch_id
          FROM {ACCEPTED_TABLE} ap
          JOIN {INGEST_BATCH_TABLE} ib
            ON ib.batch_id = ap.batch_id
         WHERE ap.dataset_id = ?
           AND ap.partition_value = ?
           AND ib.status = 'ACCEPTED'
           AND ib.contract_hash = ?
           AND ib.config_hash = ?
         LIMIT 1
        """,
        [DATASET_ID, partition, contract_hash, config_hash],
    ).fetchone()
    if row is None or not row[0]:
        return None
    accepted_id = str(row[0])
    existing = conn.execute(
        f"""
        SELECT CAST(row_ordinal AS VARCHAR) || ':' || row_hash
          FROM "{LANDING_TABLE}"
         WHERE batch_id = ?
         ORDER BY row_ordinal
        """,
        [accepted_id],
    ).fetchall()
    existing_sigs = [str(r[0]) for r in existing]
    if existing_sigs != list(row_signatures):
        return None
    return accepted_id


__all__ = ["find_accepted_batch_with_same_payload"]
