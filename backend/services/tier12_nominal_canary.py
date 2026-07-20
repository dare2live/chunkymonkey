"""Load accepted canonical nominal bars into Phase C TimedInput canaries.

Live ``canonical_nominal_ohlcv_daily.available_at`` is the retrospective
accept/ingest timestamp (authorized short-window sync), **not** the domain
publication axis. For PIT truncation into the Tier1/2 writer we stamp
**contractual** ``same_day_at 18:00`` availability from
``nominal_ohlcv`` DOMAIN policy. Using raw row timestamps at decision dates
before accept-time would false-exclude every bar — fail closed instead of
silently skipping.

Does not accept-publish, cut over consumers, or claim Phase C complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from services.data_sources.nominal_ohlcv_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    DOMAIN,
)
from services.tier12_publish_writer import TimedInput

AvailableAtMode = Literal["contractual", "raw_row"]

# Honest policy id recorded on smoke artifacts / fixtures.
CONTRACTUAL_AVAILABLE_AT_POLICY = "contractual_same_day_at_1800"
RAW_ROW_AVAILABLE_AT_NOTE = (
    "live_canonical_available_at_is_retrospective_accept_timestamp"
)


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def contractual_nominal_available_at(trade_date: str) -> str:
    """Domain publication instant for one trade_date (same_day_at 18:00 +0800)."""

    day = _compact_day(trade_date)
    if len(day) != 8:
        raise ValueError(f"invalid trade_date for available_at: {trade_date!r}")
    if DOMAIN.availability_rule != "same_day_at":
        raise ValueError(
            f"unsupported nominal availability_rule={DOMAIN.availability_rule!r}"
        )
    at = str(DOMAIN.availability_at or "18:00").strip()
    hh, mm = at.split(":", 1)
    return f"{day}T{int(hh):02d}{int(mm):02d}00+0800"


def _entity_id(ts_code: str) -> str:
    code = str(ts_code or "").strip()
    if not code:
        raise ValueError("ts_code is required")
    return code.split(".", 1)[0]


def timed_inputs_from_nominal_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    available_at_mode: AvailableAtMode = "contractual",
) -> list[TimedInput]:
    """Map nominal OHLCV row dicts → TimedInput (fail closed on missing fields)."""

    if available_at_mode not in {"contractual", "raw_row"}:
        raise ValueError(f"unknown available_at_mode={available_at_mode!r}")
    out: list[TimedInput] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("nominal row must be a mapping")
        ts_code = str(raw.get("ts_code") or "").strip()
        trade_date = _compact_day(raw.get("trade_date"))
        if not ts_code or len(trade_date) != 8:
            raise ValueError("nominal row requires ts_code and trade_date")
        close = raw.get("close")
        pct_chg = raw.get("pct_chg")
        if close is None or pct_chg is None:
            raise ValueError(
                f"nominal row missing close/pct_chg ts_code={ts_code} "
                f"trade_date={trade_date}"
            )
        if available_at_mode == "contractual":
            available_at = contractual_nominal_available_at(trade_date)
        else:
            available_at = str(raw.get("available_at") or "").strip()
            if not available_at:
                raise ValueError(
                    f"raw_row available_at missing ts_code={ts_code} "
                    f"trade_date={trade_date}"
                )
        out.append(
            TimedInput(
                entity_id=_entity_id(ts_code),
                trade_date=trade_date,
                available_at=available_at,
                payload={
                    "ts_code": ts_code,
                    "close": float(close),
                    "pct_chg": float(pct_chg),
                    "open": raw.get("open"),
                    "high": raw.get("high"),
                    "low": raw.get("low"),
                    "vol": raw.get("vol"),
                    "amount": raw.get("amount"),
                },
            )
        )
    return out


@dataclass(frozen=True)
class NominalCanaryLoad:
    decision_date: str
    lookback_days: tuple[str, ...]
    codes: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    accepted_row_count: int
    available_at_mode: AvailableAtMode
    available_at_policy: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_date": self.decision_date,
            "lookback_days": list(self.lookback_days),
            "codes": list(self.codes),
            "row_count": len(self.rows),
            "accepted_row_count": self.accepted_row_count,
            "available_at_mode": self.available_at_mode,
            "available_at_policy": self.available_at_policy,
            "notes": list(self.notes),
            "dataset_id": DATASET_ID,
            "canonical_table": CANONICAL_TABLE,
        }


def _require_accepted_partition(conn, decision_date: str) -> int:
    day = _compact_day(decision_date)
    row = conn.execute(
        """
        SELECT row_count
          FROM accepted_partition
         WHERE dataset_id = ?
           AND partition_value = ?
        """,
        [DATASET_ID, day],
    ).fetchone()
    if row is None:
        raise ValueError(
            f"no_accepted_partition dataset_id={DATASET_ID} "
            f"partition_value={day}"
        )
    count = int(row[0] if not hasattr(row, "keys") else row["row_count"])
    if count <= 0:
        raise ValueError(
            f"accepted_partition_zero_rows dataset_id={DATASET_ID} "
            f"partition_value={day}"
        )
    return count


def _lookback_trading_days(conn, decision_date: str, n_days: int) -> list[str]:
    day = _compact_day(decision_date)
    if n_days < 1:
        raise ValueError("n_days must be >= 1")
    rows = conn.execute(
        f"""
        SELECT DISTINCT replace(CAST(trade_date AS VARCHAR), '-', '') AS d
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '') <= ?
         ORDER BY 1 DESC
         LIMIT ?
        """,
        [day, int(n_days)],
    ).fetchall()
    days = [
        _compact_day(r[0] if not hasattr(r, "keys") else r["d"]) for r in rows
    ]
    days = [d for d in days if len(d) == 8]
    if day not in days:
        raise ValueError(
            f"decision_date={day} has no rows in {CANONICAL_TABLE}"
        )
    return sorted(days)


def _pick_canary_codes(
    conn,
    decision_date: str,
    *,
    max_codes: int,
    board_prefixes: Sequence[str],
) -> list[str]:
    day = _compact_day(decision_date)
    prefixes = tuple(str(p) for p in board_prefixes if str(p))
    if not prefixes:
        raise ValueError("board_prefixes required for canary code pick")
    # Prefer stable lexicographic sample (reproducible), board-filtered.
    like_clauses = " OR ".join(["ts_code LIKE ?"] * len(prefixes))
    params: list[Any] = [day] + [f"{p}%" for p in prefixes] + [int(max_codes)]
    rows = conn.execute(
        f"""
        SELECT ts_code
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '') = ?
           AND ({like_clauses})
         ORDER BY ts_code
         LIMIT ?
        """,
        params,
    ).fetchall()
    codes = [
        str(r[0] if not hasattr(r, "keys") else r["ts_code"]).strip()
        for r in rows
    ]
    codes = [c for c in codes if c]
    if not codes:
        raise ValueError(
            f"no board-filtered codes on decision_date={day} "
            f"prefixes={list(prefixes)}"
        )
    return codes


def load_accepted_nominal_canary(
    conn,
    decision_date: str,
    *,
    lookback_trading_days: int = 5,
    max_codes: int = 20,
    board_prefixes: Sequence[str] = ("60", "00", "30", "68"),
    available_at_mode: AvailableAtMode = "contractual",
) -> NominalCanaryLoad:
    """Read-only canary from accepted canonical nominal OHLCV.

    Requires an ``accepted_partition`` pointer for ``decision_date``. Does not
    write DuckDB. ``available_at_mode='raw_row'`` is for bad-case proofs only.
    """

    day = _compact_day(decision_date)
    if len(day) != 8:
        raise ValueError(f"invalid decision_date: {decision_date!r}")
    accepted_n = _require_accepted_partition(conn, day)
    days = _lookback_trading_days(conn, day, lookback_trading_days)
    codes = _pick_canary_codes(
        conn, day, max_codes=max_codes, board_prefixes=board_prefixes
    )
    placeholders_d = ", ".join(["?"] * len(days))
    placeholders_c = ", ".join(["?"] * len(codes))
    sql = f"""
        SELECT ts_code,
               replace(CAST(trade_date AS VARCHAR), '-', '') AS trade_date,
               open, high, low, close, pct_chg, vol, amount,
               CAST(available_at AS VARCHAR) AS available_at
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '') IN ({placeholders_d})
           AND ts_code IN ({placeholders_c})
         ORDER BY trade_date, ts_code
    """
    fetched = conn.execute(sql, list(days) + list(codes)).fetchall()
    rows: list[dict[str, Any]] = []
    for r in fetched:
        if hasattr(r, "keys"):
            rows.append(
                {
                    "ts_code": str(r["ts_code"]),
                    "trade_date": _compact_day(r["trade_date"]),
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "pct_chg": r["pct_chg"],
                    "vol": r["vol"],
                    "amount": r["amount"],
                    "available_at": str(r["available_at"] or ""),
                }
            )
        else:
            rows.append(
                {
                    "ts_code": str(r[0]),
                    "trade_date": _compact_day(r[1]),
                    "open": r[2],
                    "high": r[3],
                    "low": r[4],
                    "close": r[5],
                    "pct_chg": r[6],
                    "vol": r[7],
                    "amount": r[8],
                    "available_at": str(r[9] or ""),
                }
            )
    if not rows:
        raise ValueError(
            f"canary_zero_rows decision_date={day} codes={len(codes)} "
            f"days={days}"
        )
    notes = (
        "phase_c_nominal_canary",
        "not_accepted_tier12_publish",
        "not_strategy_release",
        "canary_scope_not_full_universe",
        RAW_ROW_AVAILABLE_AT_NOTE,
    )
    policy = (
        CONTRACTUAL_AVAILABLE_AT_POLICY
        if available_at_mode == "contractual"
        else "raw_row_available_at"
    )
    return NominalCanaryLoad(
        decision_date=day,
        lookback_days=tuple(days),
        codes=tuple(codes),
        rows=tuple(rows),
        accepted_row_count=accepted_n,
        available_at_mode=available_at_mode,
        available_at_policy=policy,
        notes=notes,
    )


def assert_tier12_smoke_batch(batch: Any, *, decision_date: str) -> dict[str, Any]:
    """Fail-closed smoke gate over a writer batch (lineage + unpublished)."""

    day = _compact_day(decision_date)
    errors: list[str] = []
    if getattr(batch, "status", None) != "WRITTEN_UNPUBLISHED":
        errors.append(f"status={getattr(batch, 'status', None)!r}")
    if getattr(batch, "published", None) is not False:
        errors.append(f"published={getattr(batch, 'published', None)!r}")
    stocks = tuple(getattr(batch, "stock_states", ()) or ())
    if not stocks:
        errors.append("stock_states_empty")
    for r in stocks:
        for field in (
            "definition_version",
            "config_hash",
            "input_snapshot_id",
            "eligible_universe_id",
            "available_at",
        ):
            if not getattr(r, field, None):
                errors.append(f"stock_missing_{field}:{getattr(r, 'stock_code', '?')}")
        avail_day = _compact_day(getattr(r, "available_at", ""))
        if avail_day and avail_day > day:
            errors.append(
                f"stock_future_available_at:{getattr(r, 'stock_code', '?')}:"
                f"{getattr(r, 'available_at', None)}"
            )
    market = getattr(batch, "market_context", None)
    if market is None:
        errors.append("market_context_missing")
    else:
        for field in (
            "definition_version",
            "config_hash",
            "input_snapshot_id",
            "eligible_universe_id",
            "available_at",
        ):
            if not getattr(market, field, None):
                errors.append(f"market_missing_{field}")
        avail_day = _compact_day(getattr(market, "available_at", ""))
        if avail_day and avail_day > day:
            errors.append(f"market_future_available_at:{market.available_at}")
    atts = tuple(getattr(batch, "stock_attestations", ()) or ())
    if len(atts) != len(stocks):
        errors.append("stock_attestation_count_mismatch")
    for a in atts:
        if getattr(a, "published", None) is not False:
            errors.append("stock_attestation_published")
        if getattr(a, "status", None) != "PUBLISHABLE_SCAFFOLD":
            errors.append(f"stock_attestation_status={getattr(a, 'status', None)!r}")
    m_att = getattr(batch, "market_attestation", None)
    if m_att is None:
        errors.append("market_attestation_missing")
    else:
        if getattr(m_att, "published", None) is not False:
            errors.append("market_attestation_published")
        if getattr(m_att, "status", None) != "PUBLISHABLE_SCAFFOLD":
            errors.append(f"market_attestation_status={m_att.status!r}")
    if errors:
        raise ValueError("tier12_smoke_failed: " + "; ".join(errors[:20]))
    return {
        "ok": True,
        "decision_date": day,
        "stock_state_count": len(stocks),
        "pit_excluded_count": int(getattr(batch, "pit_excluded_count", 0) or 0),
        "status": batch.status,
        "published": False,
        "definition_version": stocks[0].definition_version,
        "config_hash": stocks[0].config_hash,
        "available_at": stocks[0].available_at,
        "market_definition_version": market.definition_version,
        "market_config_hash": market.config_hash,
        "market_available_at": market.available_at,
        "market_attestation_status": m_att.status,
    }


__all__ = [
    "CONTRACTUAL_AVAILABLE_AT_POLICY",
    "RAW_ROW_AVAILABLE_AT_NOTE",
    "NominalCanaryLoad",
    "assert_tier12_smoke_batch",
    "contractual_nominal_available_at",
    "load_accepted_nominal_canary",
    "timed_inputs_from_nominal_rows",
]
