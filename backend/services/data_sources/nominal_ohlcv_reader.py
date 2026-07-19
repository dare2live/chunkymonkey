"""Trusted fail-closed reader for accepted nominal OHLCV membership."""
from __future__ import annotations

from datetime import date, datetime

from services.data_sources.nominal_ohlcv_contract import load_nominal_ohlcv_contract
from services.data_sources.nominal_ohlcv_schema import DOMAIN
from services.data_sources.security_day_partition import (
    SecurityDayAcceptedPartition,
    SecurityDayError,
)
from services.data_sources.security_day_reader import (
    load_accepted_security_day_partition,
)


class NominalOhlcvTruthUnavailable(RuntimeError):
    def __init__(self, status: str, reason: str):
        if status not in {"BLOCKED", "NOT_EVALUATED"}:
            raise ValueError(f"invalid nominal ohlcv status={status!r}")
        self.status = status
        self.reason = reason
        super().__init__(f"{status}: {reason}")


def load_accepted_nominal_ohlcv_membership_from_conn(
    conn,
    observation_date: date,
    decision_time: datetime,
) -> SecurityDayAcceptedPartition:
    contract = load_nominal_ohlcv_contract()
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
            or detail.startswith("no_accepted_security_day_schema")
            or "not_visible_at_decision_time" in detail
            else "BLOCKED"
        )
        raise NominalOhlcvTruthUnavailable(status, detail) from exc


def open_accepted_nominal_ohlcv_membership(
    observation_date: date,
    decision_time: datetime,
) -> SecurityDayAcceptedPartition:
    """Open live tushare_raw read-only and prove one accepted partition."""

    from services.data_access.resolver import connect_ro

    conn = None
    try:
        conn = connect_ro("tushare_raw")
        return load_accepted_nominal_ohlcv_membership_from_conn(
            conn, observation_date, decision_time
        )
    except NominalOhlcvTruthUnavailable:
        raise
    except Exception as exc:
        raise NominalOhlcvTruthUnavailable(
            "NOT_EVALUATED",
            f"no_accepted_nominal_ohlcv_partition dataset_id={DOMAIN.dataset_id} "
            f"read_failed={str(exc)[:300]}",
        ) from exc
    finally:
        if conn is not None:
            conn.close()


__all__ = [
    "NominalOhlcvTruthUnavailable",
    "load_accepted_nominal_ohlcv_membership_from_conn",
    "open_accepted_nominal_ohlcv_membership",
]
