"""Paper fills + §8.1 chase for institution_follow B0–B4.

Extracted from ``institution_follow_b0_measure`` to keep that module under the
god-file ratchet (<=800 lines). Types remain owned by the measure module.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.institution_follow_b0_measure import (
    B0Prereg,
    BareKPaperMetrics,
    PaperFillRecord,
    UNKNOWN,
    WalkForwardPlan,
)


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

    holdout = days[-holdout_n:]
    body = days[:-holdout_n]
    body_n = len(body)
    eval_len = max(horizon, 1)
    last_train_end = body_n - embargo - eval_len
    folds: list[WalkForwardFold] = []
    seen_train_ends: set[int] = set()
    for train_end in (
        max(body_n // 3, horizon + 2),
        max(2 * body_n // 3, horizon + 2),
        last_train_end,
    ):
        train_end = min(max(train_end, horizon + 2), last_train_end)
        if train_end in seen_train_ends or train_end < horizon + 2:
            continue
        seen_train_ends.add(train_end)
        train = body[:train_end]
        emb = body[train_end : train_end + embargo]
        eval_dates = body[train_end + embargo : train_end + embargo + eval_len]
        if train and eval_dates:
            folds.append(
                WalkForwardFold(
                    fold_id=f"purged_fold_{len(folds)}",
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


def _code6(ts_code: str) -> str:
    return str(ts_code or "").split(".", 1)[0]


def _select_top_k(
    day_bars: Mapping[str, Mapping[str, Any]],
    *,
    prereg: B0Prereg,
    st_codes: set[str],
    eligible_codes: set[str] | None = None,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for code, bar in day_bars.items():
        if not _board_ok(code, prereg.board_prefixes):
            continue
        if code in st_codes or _code6(code) in st_codes:
            continue
        if eligible_codes is not None and (
            code not in eligible_codes and _code6(code) not in eligible_codes
        ):
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
    holdout = set(plan.holdout_dates)
    embargo = {d for fold in plan.folds for d in fold.embargo_dates}
    if entry_date in holdout:
        if exit_date not in set(plan.trading_days):
            return None
        if entry_date in embargo or exit_date in embargo:
            return None
        return "one_touch_holdout"
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


_CHASEABLE_ENTRY_REASONS = frozenset(
    {
        "suspended_entry_stub",
        "limit_up_buy_blocked_stub",
        "missing_entry_bar",
        "bad_entry_px",
    }
)


def simulate_paper_fills(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    plan: WalkForwardPlan,
    *,
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
    eligible_by_day: Mapping[str, set[str]] | None = None,
) -> tuple[PaperFillRecord, ...]:
    """T+1 nominal open → T+2 open; optional eligible filter + §8.1 chase."""

    cfg = prereg or B0Prereg()
    days = list(plan.trading_days)
    by_day = _bar_index(bars_by_day)
    st_set = {str(x) for x in (st_codes or ())}
    buy_cost = cfg.commission_rate + cfg.slippage_rate
    sell_cost = cfg.commission_rate + cfg.stamp_tax_rate + cfg.slippage_rate
    max_chase = max(int(cfg.max_chase_days), 0)
    fills: list[PaperFillRecord] = []
    for i, signal_date in enumerate(days):
        if i + 1 + cfg.label_horizon_days >= len(days):
            break
        eligible = (
            set(eligible_by_day.get(signal_date) or ())
            if eligible_by_day is not None
            else None
        )
        for code in _select_top_k(
            by_day.get(signal_date) or {},
            prereg=cfg,
            st_codes=st_set,
            eligible_codes=eligible,
        ):
            chosen: PaperFillRecord | None = None
            for chase in range(0, max_chase + 1):
                entry_idx = i + 1 + chase
                exit_idx = entry_idx + cfg.label_horizon_days
                if exit_idx >= len(days):
                    chosen = _rec(
                        signal_date=signal_date,
                        entry_date=days[min(entry_idx, len(days) - 1)],
                        exit_date=days[-1],
                        ts_code=code,
                        role="expanding_eval",
                        status="unfilled",
                        reason="chase_expired_window_end",
                    )
                    break
                entry_date = days[entry_idx]
                exit_date = days[exit_idx]
                role = _signal_role(signal_date, entry_date, exit_date, plan)
                if role is None:
                    # Chase pushed into embargo/holdout-ineligible — keep trying
                    # later opens within max_chase; else expire.
                    if chase == max_chase:
                        chosen = _rec(
                            signal_date=signal_date,
                            entry_date=entry_date,
                            exit_date=exit_date,
                            ts_code=code,
                            role="expanding_eval",
                            status="unfilled",
                            reason="chase_expired_role_blocked",
                        )
                    continue
                fill = _simulate_one(
                    code=code,
                    signal_date=signal_date,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    role=role,
                    by_day=by_day,
                    buy_cost=buy_cost,
                    sell_cost=sell_cost,
                )
                if (
                    fill.status == "unfilled"
                    and fill.reason in _CHASEABLE_ENTRY_REASONS
                    and chase < max_chase
                ):
                    continue
                if (
                    fill.status == "unfilled"
                    and fill.reason in _CHASEABLE_ENTRY_REASONS
                    and chase >= max_chase
                    and max_chase > 0
                ):
                    chosen = _rec(
                        signal_date=signal_date,
                        entry_date=entry_date,
                        exit_date=exit_date,
                        ts_code=code,
                        role=role,
                        status="unfilled",
                        reason="chase_expired_unfilled",
                        entry_px=fill.entry_px,
                    )
                    break
                chosen = fill
                break
            if chosen is not None:
                fills.append(chosen)
    return tuple(fills)


def metrics_from_fills(
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



__all__ = [
    "is_limit_down_open",
    "is_limit_up_open",
    "is_suspended",
    "limit_up_pct",
    "metrics_from_fills",
    "simulate_paper_fills",
]
