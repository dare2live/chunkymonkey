"""Measured B0 bare-K WF + paper fills (short-window honest minimal protocol)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Sequence

from services.data_sources.nominal_ohlcv_schema import CANONICAL_TABLE
from services.universe import ACTIVE_A_SHARE_PREFIXES

ProtocolKind = Literal["purged_walk_forward", "honest_minimal_short_window"]
UNKNOWN = "unknown"
LABEL_HORIZON_DAYS = 1
EMBARGO_DAYS = 1
IN_WINDOW_HOLDOUT_DAYS = 2
MIN_DAYS_FULL_PURGED_WF = 40
MIN_FOLDS_CLAIMABLE = 3
MIN_TRADES_CLAIMABLE = 30
TOP_K = 5
COMMISSION_RATE = 0.00025
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.0005
BOARD_PREFIXES = tuple(ACTIVE_A_SHARE_PREFIXES)
REASON_SHORT_WINDOW = "measured_short_window_insufficient_power"
REASON_PAPER_MEASURED = "measured_b0_paper_short_window"


@dataclass(frozen=True)
class B0Prereg:
    signal: str = "cross_sectional_1d_momentum_top_k"
    label_horizon_days: int = LABEL_HORIZON_DAYS
    embargo_days: int = EMBARGO_DAYS
    in_window_holdout_days: int = IN_WINDOW_HOLDOUT_DAYS
    min_days_full_purged_wf: int = MIN_DAYS_FULL_PURGED_WF
    min_folds_claimable: int = MIN_FOLDS_CLAIMABLE
    min_trades_claimable: int = MIN_TRADES_CLAIMABLE
    top_k: int = TOP_K
    commission_rate: float = COMMISSION_RATE
    stamp_tax_rate: float = STAMP_TAX_RATE
    slippage_rate: float = SLIPPAGE_RATE
    entry: str = "t1_nominal_open"
    exit: str = "t2_nominal_open"
    board_prefixes: tuple[str, ...] = BOARD_PREFIXES

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["board_prefixes"] = list(self.board_prefixes)
        d["capacity_model"] = UNKNOWN
        d["suspend_limit_model"] = "stub"
        return d


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: str
    train_dates: tuple[str, ...]
    embargo_dates: tuple[str, ...]
    eval_dates: tuple[str, ...]
    role: str

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("train_dates", "embargo_dates", "eval_dates"):
            d[key] = list(getattr(self, key))
        return d


@dataclass(frozen=True)
class WalkForwardPlan:
    protocol: ProtocolKind
    claimable_protocol: bool
    reason: str
    trading_days: tuple[str, ...]
    folds: tuple[WalkForwardFold, ...]
    holdout_dates: tuple[str, ...]
    embargo_days: int
    label_horizon_days: int
    one_touch_holdout: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "claimable_protocol": self.claimable_protocol,
            "reason": self.reason,
            "trading_days": list(self.trading_days),
            "folds": [f.as_dict() for f in self.folds],
            "holdout_dates": list(self.holdout_dates),
            "embargo_days": self.embargo_days,
            "label_horizon_days": self.label_horizon_days,
            "one_touch_holdout": self.one_touch_holdout,
        }


@dataclass(frozen=True)
class PaperFillRecord:
    signal_date: str
    entry_date: str
    exit_date: str
    ts_code: str
    entry_px: float | None
    exit_px: float | None
    gross_return: float | None
    net_return: float | None
    status: str
    fold_role: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BareKPaperMetrics:
    total_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    payoff_ratio: float | None
    turnover: float | None
    n_signals: int
    n_trades_completed: int
    n_unfilled: int
    n_incomplete_exit: int
    annualized_return: Any = UNKNOWN
    sharpe: Any = UNKNOWN
    capacity: Any = UNKNOWN
    excess_return: Any = UNKNOWN
    stability_by_year: Any = UNKNOWN
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["details"] = dict(self.details)
        return d


@dataclass(frozen=True)
class MeasuredB0Result:
    prereg: B0Prereg
    walk_forward: WalkForwardPlan
    fills: tuple[PaperFillRecord, ...]
    metrics: BareKPaperMetrics
    holdout_metrics: BareKPaperMetrics
    claimable: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "prereg": self.prereg.as_dict(),
            "walk_forward": self.walk_forward.as_dict(),
            "fills": [f.as_dict() for f in self.fills],
            "metrics": self.metrics.as_dict(),
            "holdout_metrics": self.holdout_metrics.as_dict(),
            "claimable": self.claimable,
            "reason": self.reason,
            "paper_fills": "measured",
        }


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _board_ok(ts_code: str, prefixes: Sequence[str]) -> bool:
    code = str(ts_code or "")
    digits = code.split(".", 1)[0]
    return len(digits) >= 2 and digits[:2] in set(prefixes)


def limit_up_pct(ts_code: str) -> float:
    """Stub A-share limit band by board prefix (main 10%, 创业/科创 20%)."""

    digits = str(ts_code or "").split(".", 1)[0]
    prefix = digits[:2] if len(digits) >= 2 else ""
    if prefix in {"30", "68"}:
        return 0.20
    return 0.10


def is_limit_up_open(open_px: float, pre_close: float, ts_code: str) -> bool:
    if pre_close <= 0 or open_px <= 0:
        return True
    band = limit_up_pct(ts_code)
    return open_px >= pre_close * (1.0 + band) - 1e-9


def is_limit_down_open(open_px: float, pre_close: float, ts_code: str) -> bool:
    if pre_close <= 0 or open_px <= 0:
        return True
    band = limit_up_pct(ts_code)
    return open_px <= pre_close * (1.0 - band) + 1e-9


def is_suspended(bar: Mapping[str, Any]) -> bool:
    """Stub: zero/missing volume ⇒ 停牌-like untradable."""

    try:
        vol = float(bar.get("vol") or 0.0)
    except (TypeError, ValueError):
        return True
    return vol <= 0.0


def plan_walk_forward(
    trading_days: Sequence[str],
    *,
    prereg: B0Prereg | None = None,
) -> WalkForwardPlan:
    """Build purged WF plan, or honest minimal protocol for short windows."""

    cfg = prereg or B0Prereg()
    days = tuple(_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8)
    days = tuple(sorted(set(days)))
    n = len(days)
    horizon = cfg.label_horizon_days
    embargo = max(cfg.embargo_days, horizon)
    holdout_n = cfg.in_window_holdout_days

    if n < cfg.min_days_full_purged_wf:
        need = holdout_n + embargo + horizon + 2
        if n < need:
            return WalkForwardPlan(
                protocol="honest_minimal_short_window",
                claimable_protocol=False,
                reason=REASON_SHORT_WINDOW,
                trading_days=days,
                folds=(),
                holdout_dates=days[-min(holdout_n, n) :] if n else (),
                embargo_days=embargo,
                label_horizon_days=horizon,
                one_touch_holdout=True,
            )
        holdout = days[-holdout_n:]
        embargo_dates = days[-(holdout_n + embargo) : -holdout_n]
        train = days[: -(holdout_n + embargo)]
        # Eval for the single fold = train signal dates that still have exit
        # inside the non-holdout region (exit needs +2 trading days).
        eval_cut = len(train) - (horizon + 1)
        eval_dates = train[: max(eval_cut, 0)]
        fold = WalkForwardFold(
            fold_id="short_window_fold_0",
            train_dates=train,
            embargo_dates=embargo_dates,
            eval_dates=tuple(eval_dates),
            role="expanding_eval",
        )
        holdout_fold = WalkForwardFold(
            fold_id="short_window_holdout",
            train_dates=train,
            embargo_dates=embargo_dates,
            eval_dates=holdout,
            role="one_touch_holdout",
        )
        return WalkForwardPlan(
            protocol="honest_minimal_short_window",
            claimable_protocol=False,
            reason=REASON_SHORT_WINDOW,
            trading_days=days,
            folds=(fold, holdout_fold),
            holdout_dates=holdout,
            embargo_days=embargo,
            label_horizon_days=horizon,
            one_touch_holdout=True,
        )

    # Longer windows: three expanding purged folds (no Optuna).
    holdout = days[-holdout_n:]
    body = days[:-holdout_n]
    body_n = len(body)
    folds: list[WalkForwardFold] = []
    for i, train_end in enumerate(
        (
            max(body_n // 3, horizon + 2),
            max(2 * body_n // 3, horizon + 2),
            body_n - embargo,
        )
    ):
        train_end = min(max(train_end, horizon + 2), body_n - embargo)
        train = body[:train_end]
        emb = body[train_end : train_end + embargo]
        eval_dates = body[train_end + embargo : train_end + embargo + max(horizon, 1)]
        if train and eval_dates:
            folds.append(
                WalkForwardFold(
                    fold_id=f"purged_fold_{i}",
                    train_dates=tuple(train),
                    embargo_dates=tuple(emb),
                    eval_dates=tuple(eval_dates),
                    role="purged_eval",
                )
            )
    ok = len(folds) >= cfg.min_folds_claimable
    return WalkForwardPlan(
        protocol="purged_walk_forward",
        claimable_protocol=ok,
        reason="purged_walk_forward_ready" if ok else REASON_SHORT_WINDOW,
        trading_days=days,
        folds=tuple(folds),
        holdout_dates=holdout,
        embargo_days=embargo,
        label_horizon_days=horizon,
        one_touch_holdout=True,
    )


def _bar_index(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for day, rows in bars_by_day.items():
        d = _norm_day(day)
        by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("ts_code") or "")
            if code:
                by_code[code] = dict(row)
        out[d] = by_code
    return out


def _select_top_k(
    day_bars: Mapping[str, Mapping[str, Any]],
    *,
    prereg: B0Prereg,
    st_codes: set[str],
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for code, bar in day_bars.items():
        if not _board_ok(code, prereg.board_prefixes):
            continue
        if code in st_codes or code.split(".", 1)[0] in st_codes:
            continue
        if is_suspended(bar):
            continue
        try:
            score = float(bar.get("pct_chg"))
        except (TypeError, ValueError):
            continue
        scored.append((score, code))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in scored[: prereg.top_k]]


def _signal_role(
    signal_date: str,
    entry_date: str,
    exit_date: str,
    plan: WalkForwardPlan,
) -> str | None:
    """Return fold role for a completable signal, or None if purged/blocked."""

    holdout = set(plan.holdout_dates)
    embargo = set()
    for fold in plan.folds:
        embargo.update(fold.embargo_dates)

    # One-touch holdout: entry on a reserved holdout day. Signal may sit on
    # the embargo boundary (features use only signal-day info); train labels
    # must not overlap this entry.
    if entry_date in holdout:
        if exit_date not in set(plan.trading_days):
            return None
        if entry_date in embargo or exit_date in embargo:
            return None
        return "one_touch_holdout"

    # Eval path: full lifecycle stays before embargo/holdout (no leakage).
    if (
        signal_date in holdout
        or entry_date in holdout
        or exit_date in holdout
        or signal_date in embargo
        or entry_date in embargo
        or exit_date in embargo
    ):
        return None
    return "expanding_eval"


def _rec(
    *,
    signal_date: str,
    entry_date: str,
    exit_date: str,
    ts_code: str,
    role: str,
    status: str,
    reason: str,
    entry_px: float | None = None,
    exit_px: float | None = None,
    gross_return: float | None = None,
    net_return: float | None = None,
) -> PaperFillRecord:
    return PaperFillRecord(
        signal_date=signal_date,
        entry_date=entry_date,
        exit_date=exit_date,
        ts_code=ts_code,
        entry_px=entry_px,
        exit_px=exit_px,
        gross_return=gross_return,
        net_return=net_return,
        status=status,
        fold_role=role,
        reason=reason,
    )


def _simulate_one(
    *,
    code: str,
    signal_date: str,
    entry_date: str,
    exit_date: str,
    role: str,
    by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    buy_cost: float,
    sell_cost: float,
) -> PaperFillRecord:
    entry_bar = (by_day.get(entry_date) or {}).get(code)
    if entry_bar is None:
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="unfilled",
            reason="missing_entry_bar",
        )
    try:
        entry_open = float(entry_bar["open"])
        entry_pre = float(entry_bar.get("pre_close") or 0.0)
    except (TypeError, ValueError, KeyError):
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="unfilled",
            reason="bad_entry_px",
        )
    if is_suspended(entry_bar):
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="unfilled",
            reason="suspended_entry_stub",
            entry_px=entry_open,
        )
    if is_limit_up_open(entry_open, entry_pre, code):
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="unfilled",
            reason="limit_up_buy_blocked_stub",
            entry_px=entry_open,
        )

    exit_bar = (by_day.get(exit_date) or {}).get(code)
    if exit_bar is None:
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="incomplete_exit",
            reason="missing_exit_bar",
            entry_px=entry_open,
        )
    try:
        exit_open = float(exit_bar["open"])
        exit_pre = float(exit_bar.get("pre_close") or 0.0)
    except (TypeError, ValueError, KeyError):
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="incomplete_exit",
            reason="bad_exit_px",
            entry_px=entry_open,
        )
    if is_suspended(exit_bar) or is_limit_down_open(exit_open, exit_pre, code):
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="incomplete_exit",
            reason=(
                "suspended_exit_stub"
                if is_suspended(exit_bar)
                else "limit_down_sell_blocked_stub"
            ),
            entry_px=entry_open,
            exit_px=exit_open,
        )
    if entry_open <= 0 or exit_open <= 0:
        return _rec(
            signal_date=signal_date,
            entry_date=entry_date,
            exit_date=exit_date,
            ts_code=code,
            role=role,
            status="unfilled",
            reason="non_positive_px",
            entry_px=entry_open,
            exit_px=exit_open,
        )
    gross = exit_open / entry_open - 1.0
    net = (exit_open * (1.0 - sell_cost)) / (entry_open * (1.0 + buy_cost)) - 1.0
    return _rec(
        signal_date=signal_date,
        entry_date=entry_date,
        exit_date=exit_date,
        ts_code=code,
        role=role,
        status="filled",
        reason="ok",
        entry_px=entry_open,
        exit_px=exit_open,
        gross_return=gross,
        net_return=net,
    )


def simulate_paper_fills(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    plan: WalkForwardPlan,
    *,
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
) -> tuple[PaperFillRecord, ...]:
    """T+1 nominal open entry, T+2 open exit, with cost + limit/suspend stubs."""

    cfg = prereg or B0Prereg()
    days = list(plan.trading_days)
    by_day = _bar_index(bars_by_day)
    st_set = {str(x) for x in (st_codes or ())}
    buy_cost = cfg.commission_rate + cfg.slippage_rate
    sell_cost = cfg.commission_rate + cfg.stamp_tax_rate + cfg.slippage_rate
    fills: list[PaperFillRecord] = []
    for i, signal_date in enumerate(days):
        if i + 1 + cfg.label_horizon_days >= len(days):
            break
        entry_date = days[i + 1]
        exit_date = days[i + 1 + cfg.label_horizon_days]
        role = _signal_role(signal_date, entry_date, exit_date, plan)
        if role is None:
            continue
        for code in _select_top_k(
            by_day.get(signal_date) or {}, prereg=cfg, st_codes=st_set
        ):
            fills.append(
                _simulate_one(
                    code=code,
                    signal_date=signal_date,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    role=role,
                    by_day=by_day,
                    buy_cost=buy_cost,
                    sell_cost=sell_cost,
                )
            )
    return tuple(fills)


def _metrics_from_fills(
    fills: Sequence[PaperFillRecord],
    *,
    trading_day_count: int,
    top_k: int,
) -> BareKPaperMetrics:
    completed = [f for f in fills if f.status == "filled" and f.net_return is not None]
    unfilled = sum(1 for f in fills if f.status == "unfilled")
    incomplete = sum(1 for f in fills if f.status == "incomplete_exit")
    n_signals = len({f.signal_date for f in fills})

    if not completed:
        return BareKPaperMetrics(
            total_return=None,
            max_drawdown=None,
            win_rate=None,
            payoff_ratio=None,
            turnover=None,
            n_signals=n_signals,
            n_trades_completed=0,
            n_unfilled=unfilled,
            n_incomplete_exit=incomplete,
            details={"note": "no_completed_fills"},
        )

    rets = [float(f.net_return) for f in completed]  # type: ignore[arg-type]
    # Equal-notional daily bags: mean net return per entry_date, then compound.
    by_entry: dict[str, list[float]] = {}
    for f in completed:
        by_entry.setdefault(f.entry_date, []).append(float(f.net_return))  # type: ignore[arg-type]
    daily = [sum(v) / len(v) for _, v in sorted(by_entry.items())]
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in daily:
        nav *= 1.0 + r
        peak = max(peak, nav)
        dd = (peak - nav) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    total_return = nav - 1.0

    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    win_rate = len(wins) / len(rets) if rets else None
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss_abs = abs(sum(losses) / len(losses)) if losses else None
    if avg_win is not None and avg_loss_abs is not None and avg_loss_abs > 0:
        payoff_ratio: float | None = avg_win / avg_loss_abs
        payoff_status = "measured"
    else:
        payoff_ratio = None
        payoff_status = UNKNOWN

    # Round-trip notional proxy: 2 sides * completed / (days * top_k slots).
    denom = max(trading_day_count, 1) * max(top_k, 1)
    turnover = (2.0 * len(completed)) / denom

    return BareKPaperMetrics(
        total_return=total_return,
        max_drawdown=max_dd,
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        turnover=turnover,
        n_signals=n_signals,
        n_trades_completed=len(completed),
        n_unfilled=unfilled,
        n_incomplete_exit=incomplete,
        annualized_return=UNKNOWN,
        sharpe=UNKNOWN,
        capacity=UNKNOWN,
        excess_return=UNKNOWN,
        stability_by_year=UNKNOWN,
        details={
            "avg_win": avg_win,
            "avg_loss": (-avg_loss_abs if avg_loss_abs is not None else None),
            "payoff_ratio_status": payoff_status,
            "daily_bag_count": len(daily),
            "mean_trade_net_return": sum(rets) / len(rets),
        },
    )


def evaluate_claimable(
    plan: WalkForwardPlan,
    metrics: BareKPaperMetrics,
    *,
    prereg: B0Prereg | None = None,
) -> tuple[bool, str]:
    cfg = prereg or B0Prereg()
    if not plan.claimable_protocol:
        return False, REASON_SHORT_WINDOW
    if metrics.n_trades_completed < cfg.min_trades_claimable:
        return False, REASON_SHORT_WINDOW
    if len(plan.folds) < cfg.min_folds_claimable:
        return False, REASON_SHORT_WINDOW
    # Positive thresholds for accept are intentionally not auto-passed here;
    # claimable flag only means protocol power is sufficient to decide later.
    return True, "protocol_power_sufficient"


def measure_b0_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    trading_days: Sequence[str] | None = None,
    *,
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
) -> MeasuredB0Result:
    cfg = prereg or B0Prereg()
    days = (
        list(trading_days)
        if trading_days is not None
        else sorted({_norm_day(d) for d in bars_by_day})
    )
    plan = plan_walk_forward(days, prereg=cfg)
    fills = simulate_paper_fills(
        bars_by_day, plan, prereg=cfg, st_codes=st_codes
    )
    metrics = _metrics_from_fills(
        fills, trading_day_count=len(plan.trading_days), top_k=cfg.top_k
    )
    holdout_fills = tuple(
        f for f in fills if f.fold_role == "one_touch_holdout"
    )
    holdout_metrics = _metrics_from_fills(
        holdout_fills,
        trading_day_count=len(plan.holdout_dates) or 1,
        top_k=cfg.top_k,
    )
    claimable, reason = evaluate_claimable(plan, metrics, prereg=cfg)
    if not claimable:
        reason = REASON_SHORT_WINDOW
    else:
        reason = REASON_PAPER_MEASURED
    return MeasuredB0Result(
        prereg=cfg,
        walk_forward=plan,
        fills=fills,
        metrics=metrics,
        holdout_metrics=holdout_metrics,
        claimable=claimable,
        reason=reason,
    )


def load_nominal_bars_by_day(
    conn,
    trading_days: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """Load accepted canonical nominal OHLCV for the measured window."""

    days = sorted({_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8})
    if not days:
        return {}
    placeholders = ", ".join(["?"] * len(days))
    sql = f"""
        SELECT replace(CAST(trade_date AS VARCHAR), '-', '') AS d,
               ts_code, open, high, low, close, pre_close, pct_chg, vol, amount
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '') IN ({placeholders})
         ORDER BY 1, ts_code
    """
    rows = conn.execute(sql, days).fetchall()
    out: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for row in rows:
        # duckdb may return tuple or mapping-like
        if hasattr(row, "keys"):
            d = _norm_day(row["d"])
            item = {
                "ts_code": str(row["ts_code"]),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "pre_close": row["pre_close"],
                "pct_chg": row["pct_chg"],
                "vol": row["vol"],
                "amount": row["amount"],
            }
        else:
            d = _norm_day(row[0])
            item = {
                "ts_code": str(row[1]),
                "open": row[2],
                "high": row[3],
                "low": row[4],
                "close": row[5],
                "pre_close": row[6],
                "pct_chg": row[7],
                "vol": row[8],
                "amount": row[9],
            }
        out.setdefault(d, []).append(item)
    return out


__all__ = [
    "BOARD_PREFIXES",
    "B0Prereg",
    "BareKPaperMetrics",
    "EMBARGO_DAYS",
    "LABEL_HORIZON_DAYS",
    "MIN_DAYS_FULL_PURGED_WF",
    "MIN_TRADES_CLAIMABLE",
    "MeasuredB0Result",
    "PaperFillRecord",
    "REASON_PAPER_MEASURED",
    "REASON_SHORT_WINDOW",
    "TOP_K",
    "UNKNOWN",
    "WalkForwardFold",
    "WalkForwardPlan",
    "evaluate_claimable",
    "is_limit_down_open",
    "is_limit_up_open",
    "is_suspended",
    "limit_up_pct",
    "load_nominal_bars_by_day",
    "measure_b0_paper",
    "plan_walk_forward",
    "simulate_paper_fills",
]
