"""Idempotent skip for holders_top10 land when ACCEPTED payload is unchanged.

Knife 2: stop uuid re-land storm without deleting landing evidence.
"""
from __future__ import annotations

from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.holders_top10_schema import DATASET_ID


def find_accepted_batch_with_same_payload(
    conn,
    *,
    partition: str,
    payload_hash: str,
    contract_hash: str,
    config_hash: str,
) -> str | None:
    """Return current accepted batch_id when payload_hash matches, else None."""

    row = conn.execute(
        f"""
        SELECT ap.batch_id
          FROM {ACCEPTED_TABLE} ap
          JOIN {INGEST_BATCH_TABLE} ib
            ON ib.batch_id = ap.batch_id
         WHERE ap.dataset_id = ?
           AND ap.partition_value = ?
           AND ib.status = 'ACCEPTED'
           AND ib.payload_hash = ?
           AND ib.contract_hash = ?
           AND ib.config_hash = ?
         LIMIT 1
        """,
        [DATASET_ID, partition, payload_hash, contract_hash, config_hash],
    ).fetchone()
    if row is None or not row[0]:
        return None
    return str(row[0])


__all__ = ["find_accepted_batch_with_same_payload"]
