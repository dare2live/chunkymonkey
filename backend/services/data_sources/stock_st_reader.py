"""Trusted fail-closed reader for accepted same-day ST membership."""
from __future__ import annotations

from datetime import date, datetime

from services.data_sources.security_day_partition import (
    SecurityDayAcceptedPartition,
    SecurityDayError,
)
from services.data_sources.security_day_reader import (
    load_accepted_security_day_partition,
)
from services.data_sources.stock_st_contract import load_stock_st_contract
from services.data_sources.stock_st_schema import DOMAIN


class StockStTruthUnavailable(RuntimeError):
    def __init__(self, status: str, reason: str):
        if status not in {"BLOCKED", "NOT_EVALUATED"}:
            raise ValueError(f"invalid stock_st status={status!r}")
        self.status = status
        self.reason = reason
        super().__init__(f"{status}: {reason}")


def load_accepted_stock_st_membership_from_conn(
    conn,
    observation_date: date,
    decision_time: datetime,
) -> SecurityDayAcceptedPartition:
    contract = load_stock_st_contract()
    try:
        return load_accepted_security_day_partition(
            conn,
            DOMAIN,
            observation_date,
            decision_time,
            contract_hash=contract.contract_hash,
            config_hash=contract.config_hash,
        )
    except SecurityDayError as exc:
        detail = str(exc)
        status = (
            "NOT_EVALUATED"
            if detail.startswith("no_accepted_partition")
            or "not_visible_at_decision_time" in detail
            else "BLOCKED"
        )
        raise StockStTruthUnavailable(status, detail) from exc


def open_accepted_stock_st_membership(
    observation_date: date,
    decision_time: datetime,
) -> SecurityDayAcceptedPartition:
    from services.data_access.resolver import connect_ro

    conn = connect_ro("tushare_raw")
    try:
        return load_accepted_stock_st_membership_from_conn(
            conn, observation_date, decision_time
        )
    except StockStTruthUnavailable:
        raise
    except Exception as exc:
        raise StockStTruthUnavailable(
            "NOT_EVALUATED",
            f"no_accepted_stock_st_partition dataset_id={DOMAIN.dataset_id} "
            f"read_failed={str(exc)[:300]}",
        ) from exc
    finally:
        conn.close()


__all__ = [
    "StockStTruthUnavailable",
    "load_accepted_stock_st_membership_from_conn",
    "open_accepted_stock_st_membership",
]
