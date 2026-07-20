"""Phase C Tier1/2 publish writer with PIT truncation (fail-closed).

Emits lineage-bearing ``StockStateDaily`` / ``MarketContextPublishEnvelope``
rows from timed inputs. Inputs with ``available_at`` after the decision date
are excluded. Missing ``available_at`` or typed lineage config fails closed.

This writer never marks ``published=True`` / accepted_partition / StrategyRelease
— even if config ``allow_published`` is flipped. Status stays
``WRITTEN_UNPUBLISHED`` until a separately proven accept path exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from services.tier12_publish_contract import (
    MarketContextPublishEnvelope,
    PublishLineageReport,
    StockStateDaily,
    attest_market_context_publishable,
    attest_stock_state_publishable,
    config_hash_for,
)

_DEFAULT_CFG = Path(__file__).resolve().parents[1] / "config" / "tier12_publish.yaml"


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _available_day(value: Any) -> str:
    """Calendar day of an available_at token (compact or ISO/timestamp)."""

    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return ""


@dataclass(frozen=True)
class TimedInput:
    """Evidence row with explicit availability (PIT axis)."""

    entity_id: str
    trade_date: str
    available_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def normalized(self) -> TimedInput:
        return TimedInput(
            entity_id=str(self.entity_id or "").strip(),
            trade_date=_compact_day(self.trade_date),
            available_at=str(self.available_at or "").strip(),
            payload=dict(self.payload),
        )


@dataclass(frozen=True)
class Tier12PublishConfig:
    stock_definition_version: str
    stock_eligible_universe_id: str
    stock_axes: tuple[str, ...]
    trend_lookback_bars: int
    stock_availability_policy: Mapping[str, Any]
    market_definition_version: str
    market_eligible_universe_id: str
    market_method: str
    min_adv_dec_ratio: float
    board_prefixes: tuple[str, ...]
    market_availability_policy: Mapping[str, Any]
    allow_published: bool
    artifact_dir: str
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Tier12PublishConfig:
        if not isinstance(raw, Mapping):
            raise ValueError("tier12 publish config must be a mapping")
        stock = raw.get("stock_state") or {}
        market = raw.get("market_context") or {}
        publish = raw.get("publish") or {}
        if not isinstance(stock, Mapping) or not isinstance(market, Mapping):
            raise ValueError("stock_state/market_context must be mappings")
        def_v = str(stock.get("definition_version") or "").strip()
        if not def_v:
            raise ValueError("stock_state.definition_version is required")
        m_def = str(market.get("definition_version") or "").strip()
        if not m_def:
            raise ValueError("market_context.definition_version is required")
        univ = str(stock.get("eligible_universe_id") or "").strip()
        if not univ:
            raise ValueError("stock_state.eligible_universe_id is required")
        m_univ = str(market.get("eligible_universe_id") or "").strip()
        if not m_univ:
            raise ValueError("market_context.eligible_universe_id is required")
        axes = tuple(str(a) for a in (stock.get("axes") or ["trend"]))
        prefixes = tuple(
            str(p) for p in (market.get("board_prefixes") or ("60", "00", "30", "68"))
        )
        return cls(
            stock_definition_version=def_v,
            stock_eligible_universe_id=univ,
            stock_axes=axes,
            trend_lookback_bars=int(stock.get("trend_lookback_bars") or 5),
            stock_availability_policy=dict(stock.get("availability_policy") or {}),
            market_definition_version=m_def,
            market_eligible_universe_id=m_univ,
            market_method=str(market.get("method") or "").strip(),
            min_adv_dec_ratio=float(market.get("min_adv_dec_ratio") or 1.0),
            board_prefixes=prefixes,
            market_availability_policy=dict(market.get("availability_policy") or {}),
            allow_published=bool(publish.get("allow_published", False)),
            artifact_dir=str(
                publish.get("artifact_dir") or "data/lineage/tier12_publish_batches"
            ),
            raw=dict(raw),
        )

    def stock_config_for_hash(self) -> dict[str, Any]:
        return {
            "definition_version": self.stock_definition_version,
            "eligible_universe_id": self.stock_eligible_universe_id,
            "axes": list(self.stock_axes),
            "trend_lookback_bars": self.trend_lookback_bars,
            "availability_policy": dict(self.stock_availability_policy),
        }

    def market_context_config_for_hash(self) -> dict[str, Any]:
        return {
            "definition_version": self.market_definition_version,
            "eligible_universe_id": self.market_eligible_universe_id,
            "method": self.market_method,
            "min_adv_dec_ratio": self.min_adv_dec_ratio,
            "board_prefixes": list(self.board_prefixes),
            "availability_policy": dict(self.market_availability_policy),
        }


def load_tier12_publish_config(path: str | Path | None = None) -> Tier12PublishConfig:
    cfg_path = Path(path) if path is not None else _DEFAULT_CFG
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    return Tier12PublishConfig.from_mapping(raw)


def pit_truncate_inputs(
    inputs: Sequence[TimedInput],
    decision_date: str,
) -> list[TimedInput]:
    """Keep inputs whose available_at calendar day <= decision_date.

    Missing/blank available_at fails closed (ValueError). Trade dates after
    decision_date are also excluded.
    """

    day = _compact_day(decision_date)
    if len(day) != 8:
        raise ValueError(f"invalid decision_date: {decision_date!r}")
    kept: list[TimedInput] = []
    for raw in inputs:
        item = raw.normalized() if hasattr(raw, "normalized") else raw
        avail = _available_day(item.available_at)
        if not avail:
            raise ValueError(
                "available_at is required for PIT truncation (fail closed)"
            )
        trade = _compact_day(item.trade_date)
        if avail > day:
            continue
        if trade and trade > day:
            continue
        kept.append(
            TimedInput(
                entity_id=item.entity_id,
                trade_date=trade,
                available_at=str(item.available_at).strip(),
                payload=dict(item.payload),
            )
        )
    return kept


def _board_ok(ts_code: str, prefixes: Sequence[str]) -> bool:
    code = str(ts_code or "").split(".", 1)[0]
    return any(code.startswith(p) for p in prefixes)


def _axis_trend_from_closes(closes: Sequence[float]) -> str | None:
    if len(closes) < 2:
        return None
    first = float(closes[0])
    last = float(closes[-1])
    if last > first:
        return "up"
    if last < first:
        return "down"
    return "flat"


def _eod_available_at(decision_date: str) -> str:
    day = _compact_day(decision_date)
    return f"{day}T160000+0800"


def _input_snapshot_id(kind: str, decision_date: str, n_kept: int) -> str:
    day = _compact_day(decision_date)
    return f"{kind}:{day}:n{n_kept}"


def _build_stock_states(
    decision_date: str,
    kept: Sequence[TimedInput],
    config: Tier12PublishConfig,
) -> list[StockStateDaily]:
    day = _compact_day(decision_date)
    by_code: dict[str, list[TimedInput]] = {}
    for item in kept:
        if not item.entity_id:
            continue
        by_code.setdefault(item.entity_id, []).append(item)

    cfg_hash = config_hash_for(config.stock_config_for_hash())
    snapshot = _input_snapshot_id("nominal_ohlcv_pit", day, len(kept))
    avail = _eod_available_at(day)
    out: list[StockStateDaily] = []
    lookback = max(2, int(config.trend_lookback_bars))
    for code in sorted(by_code):
        rows = sorted(
            by_code[code],
            key=lambda r: (_compact_day(r.trade_date), str(r.available_at)),
        )
        # Prefer bars on/before decision; use lookback window ending at decision.
        closes: list[float] = []
        for r in rows:
            if _compact_day(r.trade_date) > day:
                continue
            close = r.payload.get("close")
            if close is None:
                continue
            closes.append(float(close))
        window = closes[-lookback:] if closes else []
        # Only emit a row when the stock has a decision-day observation.
        has_decision_bar = any(_compact_day(r.trade_date) == day for r in rows)
        if not has_decision_bar:
            continue
        out.append(
            StockStateDaily(
                stock_code=code,
                trade_date=day,
                axis_trend=_axis_trend_from_closes(window),
                is_breakout_event=False,
                definition_version=config.stock_definition_version,
                config_hash=cfg_hash,
                input_snapshot_id=snapshot,
                eligible_universe_id=config.stock_eligible_universe_id,
                available_at=avail,
                details={
                    "writer": "tier12_publish_writer",
                    "trend_lookback_bars": lookback,
                    "bars_used": len(window),
                    "coverage_reason": (
                        None if len(window) >= 2 else "insufficient_history"
                    ),
                },
            )
        )
    return out


def _build_market_context(
    decision_date: str,
    kept: Sequence[TimedInput],
    config: Tier12PublishConfig,
) -> MarketContextPublishEnvelope:
    day = _compact_day(decision_date)
    day_bars = [r for r in kept if _compact_day(r.trade_date) == day]
    adv = dec = flat = used = 0
    skipped_off_board = 0
    for r in day_bars:
        ts = str(r.payload.get("ts_code") or r.entity_id)
        if not _board_ok(ts, config.board_prefixes):
            skipped_off_board += 1
            continue
        pct = r.payload.get("pct_chg")
        if pct is None:
            continue
        used += 1
        p = float(pct)
        if p > 0:
            adv += 1
        elif p < 0:
            dec += 1
        else:
            flat += 1

    if used == 0:
        trust = "UNAVAILABLE"
        risk_on: bool | None = None
    else:
        trust = "READY"
        if dec == 0 and adv > 0:
            risk_on = True
        elif dec == 0:
            risk_on = False
        else:
            risk_on = (float(adv) / float(dec)) >= float(config.min_adv_dec_ratio)

    return MarketContextPublishEnvelope(
        decision_time=day,
        available_at=_eod_available_at(day),
        definition_version=config.market_definition_version,
        config_hash=config_hash_for(config.market_context_config_for_hash()),
        input_snapshot_id=_input_snapshot_id("nominal_breadth_pit", day, len(day_bars)),
        eligible_universe_id=config.market_eligible_universe_id,
        trust_status=trust,
        risk_on=risk_on,
        details={
            "writer": "tier12_publish_writer",
            "method": config.market_method,
            "adv_n": adv,
            "dec_n": dec,
            "flat_n": flat,
            "row_count_used": used,
            "skipped_off_board": skipped_off_board,
            "b_pit_cutover_allowed": False,
            "not_pulse_mart": True,
        },
    )


@dataclass(frozen=True)
class Tier12WriteBatch:
    decision_date: str
    stock_states: tuple[StockStateDaily, ...]
    market_context: MarketContextPublishEnvelope | None
    stock_attestations: tuple[PublishLineageReport, ...]
    market_attestation: PublishLineageReport | None
    pit_excluded_count: int
    status: str
    published: bool
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_date": self.decision_date,
            "stock_states": [r.as_dict() for r in self.stock_states],
            "market_context": (
                self.market_context.as_dict() if self.market_context else None
            ),
            "stock_attestations": [a.as_dict() for a in self.stock_attestations],
            "market_attestation": (
                self.market_attestation.as_dict() if self.market_attestation else None
            ),
            "pit_excluded_count": self.pit_excluded_count,
            "status": self.status,
            "published": self.published,
            "notes": list(self.notes),
        }


def write_tier12_batch(
    *,
    decision_date: str,
    inputs: Sequence[TimedInput],
    config: Tier12PublishConfig | None = None,
    emit_artifact: bool = False,
    artifact_root: Path | None = None,
) -> Tier12WriteBatch:
    """Build lineage-bearing Tier1/2 outputs under PIT truncation.

    Always returns ``published=False`` and status ``WRITTEN_UNPUBLISHED``.
    Config ``allow_published`` cannot override this hard gate.
    """

    cfg = config or load_tier12_publish_config()
    day = _compact_day(decision_date)
    if len(day) != 8:
        raise ValueError(f"invalid decision_date: {decision_date!r}")

    normalized = [i.normalized() for i in inputs]
    kept = pit_truncate_inputs(normalized, day)
    excluded = len(normalized) - len(kept)

    stocks = tuple(_build_stock_states(day, kept, cfg))
    market = _build_market_context(day, kept, cfg)
    stock_atts = tuple(attest_stock_state_publishable(r) for r in stocks)
    market_att = attest_market_context_publishable(market)

    notes = (
        "phase_c_writer",
        "written_unpublished",
        "not_accepted_partition",
        "not_strategy_release",
        "not_pulse_mart_cutover",
        "pit_available_at_le_decision_date",
    )
    if cfg.allow_published:
        notes = notes + ("allow_published_ignored_hard_gate",)

    # Hard gate: never publish from this writer.
    published = False
    status = "WRITTEN_UNPUBLISHED"

    batch = Tier12WriteBatch(
        decision_date=day,
        stock_states=stocks,
        market_context=market,
        stock_attestations=stock_atts,
        market_attestation=market_att,
        pit_excluded_count=excluded,
        status=status,
        published=published,
        notes=notes,
    )

    if emit_artifact:
        root = artifact_root or Path(cfg.artifact_dir)
        if not root.is_absolute():
            # Resolve relative to repo root (parents: services -> backend -> repo)
            root = Path(__file__).resolve().parents[2] / root
        root.mkdir(parents=True, exist_ok=True)
        out_path = root / f"batch_{day}.json"
        out_path.write_text(
            json.dumps(batch.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    return batch


__all__ = [
    "TimedInput",
    "Tier12PublishConfig",
    "Tier12WriteBatch",
    "load_tier12_publish_config",
    "pit_truncate_inputs",
    "write_tier12_batch",
]
