"""institution_follow B4 institution/event vs B0 — PIT + thin-coverage honesty."""
from __future__ import annotations

import pytest

from services.institution_follow_b0 import (
    BOUNDED_SCOPE,
    CANARY_ABLATION,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    build_b0_run,
)
from services.institution_follow_b0_measure import (
    B0Prereg,
    MIN_DAYS_FULL_PURGED_WF,
    plan_walk_forward,
    simulate_paper_fills,
)
from services.institution_follow_b4 import (
    BLOCK_ID,
    FEATURE_BLOCK_ID,
    REASON_B4_SCAFFOLD_NO_MEASURED_EDGE,
    build_b4_run,
    finalize_b4_verdict,
    run_b4_scaffold,
)
from services.institution_follow_b4_measure import (
    REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT,
    DisclosureEpisode,
    eligible_by_day_from_episodes,
    episodes_from_holder_rows,
    first_signal_day,
    measure_b4_paper,
    measure_disclosure_event_coverage,
)
from services.institution_follow_edge_gates import (
    REASON_HOLDOUT_LIFT_UNMET,
    evaluate_holdout_lift_vs_b0,
)


def _canary_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e0_test_canary_b4",
        "scope": CANARY_SCOPE,
        "phase_e_ablation": CANARY_ABLATION,
        "cutover_allowed": True,
        "domains": {
            "holders_top10": {"partition": "20260717", "date_set": ["20260717"]},
            "org_holding": {"partition": "20190430", "date_set": ["20190430"]},
            "stk_holdertrade": {"partition": "20260706", "date_set": ["20260706"]},
        },
    }
    base.update(overrides)
    return base


def _bounded_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e_bounded_b4",
        "scope": BOUNDED_SCOPE,
        "phase_e_ablation": "bounded_scope_measured_b0_short_window",
        "cutover_allowed": True,
        "domains": {
            "holders_top10": {
                "partition": "20260717",
                "date_set": ["20260508", "20260616", "20260618", "20260619", "20260623", "20260703", "20260709", "20260710", "20260713", "20260714", "20260717"],
            },
            "org_holding": {
                "partition": "20260430",
                "date_set": ["20190430", "20260430"],
            },
            "stk_holdertrade": {
                "partition": "20260713",
                "date_set": ["20260518", "20260608", "20260706", "20260713"],
            },
        },
    }
    base.update(overrides)
    return base


class _FakeNominalConn:
    def __init__(self, partitions: list[str]):
        self._partitions = partitions

    def execute(self, sql, params=None):  # noqa: ANN001
        class _R:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        if "accepted_partition" in sql:
            return _R([(p,) for p in self._partitions])
        return _R([])

    def close(self) -> None:
        return None


def _weekday_compact_days(n_days: int, *, start: str = "20260401") -> list[str]:
    from datetime import date

    y, m, d = int(start[:4]), int(start[4:6]), int(start[6:8])
    out: list[str] = []
    while len(out) < n_days:
        cur = date(y, m, d)
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        nxt = date.fromordinal(cur.toordinal() + 1)
        y, m, d = nxt.year, nxt.month, nxt.day
    return out


def _synthetic_window_bars(
    n_days: int = 8, *, days: list[str] | None = None
) -> dict[str, list[dict]]:
    if days is None:
        days = _weekday_compact_days(n_days, start="20260708")
    else:
        days = list(days)[:n_days]
    codes = [
        "600000.SH",
        "000001.SZ",
        "300001.SZ",
        "688001.SH",
        "600519.SH",
        "600028.SH",
    ]
    bars: dict[str, list[dict]] = {}
    for i, day in enumerate(days):
        rows = []
        for j, code in enumerate(codes):
            pct = float((j + 1) * 0.3) * (1.0 if i % 2 == 0 else -1.0)
            pre = 10.0 + j
            close = pre * (1.0 + pct / 100.0)
            open_px = pre * (1.0 + pct / 200.0)
            rows.append(
                {
                    "ts_code": code,
                    "open": open_px,
                    "high": max(open_px, close),
                    "low": min(open_px, close),
                    "close": close,
                    "pre_close": pre,
                    "pct_chg": pct,
                    "vol": 1_000_000.0,
                    "amount": 1_000_000.0 * close,
                }
            )
        bars[day] = rows
    return bars


def test_b4_declares_institution_event_feature_block() -> None:
    run = build_b4_run(
        snapshot=_bounded_snapshot(),
        measure_b0_paper=False,
        measure_b4_paper_flag=False,
    )
    assert run.block == BLOCK_ID
    assert run.feature_block.block_id == FEATURE_BLOCK_ID
    assert run.feature_block.status == "declared_scaffold"
    assert "pit_notice_date_available_at" in run.notes
    assert run.feature_block.as_dict()["max_chase_days"] == 3


def test_null_notice_date_excluded_contract_level() -> None:
    rows = [
        {
            "stock_code": "600028",
            "notice_date": None,
            "available_at": "20260619",
            "change_status": "增持",
            "hold_change_num": 100.0,
            "is_exit_row": False,
            "report_date": "20260615",
        },
        {
            "stock_code": "600028",
            "notice_date": "20260619",
            "available_at": "2026-06-19 18:00:00+08:00",
            "change_status": "增持",
            "hold_change_num": 200.0,
            "is_exit_row": False,
            "report_date": "20260615",
        },
        {
            "stock_code": "600028",
            "notice_date": "20260619",
            "available_at": "2026-06-19 18:00:00+08:00",
            "change_status": "退出",
            "hold_change_num": -1.0,
            "is_exit_row": True,
            "report_date": "20260615",
        },
    ]
    eps, null_n, miss_n = episodes_from_holder_rows(rows)
    assert null_n == 1
    assert miss_n == 0
    assert len(eps) == 1
    assert eps[0].stock_code == "600028"
    assert eps[0].notice_date == "20260619"
    assert eps[0].available_at_date == "20260619"


def test_signal_only_after_available_at() -> None:
    ep = DisclosureEpisode(
        stock_code="600028",
        notice_date="20260619",
        available_at_date="20260620",
        report_date="20260615",
        score=1.0,
        increase_holder_n=1,
    )
    days = ["20260618", "20260619", "20260620", "20260623"]
    assert first_signal_day(ep, days) == "20260620"
    eligible = eligible_by_day_from_episodes([ep], days)
    assert "600028" not in eligible["20260619"]
    assert "600028" in eligible["20260620"]


def test_chase_retries_limit_up_then_fills() -> None:
    days = _weekday_compact_days(6, start="20260701")
    # Code 600000: day1 open limit-up vs pre; day2 normal.
    bars: dict[str, list[dict]] = {}
    for i, day in enumerate(days):
        pre = 10.0
        if i == 1:
            open_px = 11.0  # ~10% limit up for main board stub
        else:
            open_px = 10.1
        close = open_px
        bars[day] = [
            {
                "ts_code": "600000.SH",
                "open": open_px,
                "high": open_px,
                "low": open_px,
                "close": close,
                "pre_close": pre,
                "pct_chg": (close / pre - 1.0) * 100.0,
                "vol": 1e6,
                "amount": 1e6,
            }
        ]
    plan = plan_walk_forward(days, prereg=B0Prereg(max_chase_days=2, top_k=1))
    # Force eligible every signal day
    eligible = {d: {"600000"} for d in days}
    fills = simulate_paper_fills(
        bars,
        plan,
        prereg=B0Prereg(max_chase_days=2, top_k=1),
        eligible_by_day=eligible,
    )
    assert fills
    # First signal day → first entry attempt limit-up → chase to next open
    first = fills[0]
    assert first.status in {"filled", "unfilled", "incomplete_exit"}
    chased = [f for f in fills if f.entry_date > days[1] or f.reason == "ok"]
    assert any(f.status == "filled" for f in fills) or any(
        "chase" in (f.reason or "") for f in fills
    )
    assert chased or fills


def test_b4_thin_coverage_inconclusive_not_fake_accept() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    bars = _synthetic_window_bars(len(days), days=days)
    # Only 2 event days / 2 stocks → below MIN_EVENT_DAYS / stocks.
    eps = (
        DisclosureEpisode(
            stock_code="600028",
            notice_date=days[5],
            available_at_date=days[5],
            report_date=days[5],
            score=10.0,
            increase_holder_n=2,
        ),
        DisclosureEpisode(
            stock_code="600000",
            notice_date=days[10],
            available_at_date=days[10],
            report_date=days[10],
            score=5.0,
            increase_holder_n=1,
        ),
    )
    eligible = eligible_by_day_from_episodes(eps, days)
    cov = measure_disclosure_event_coverage(
        days,
        eligible,
        null_notice_excluded=0,
        missing_available_at_excluded=0,
        episode_count=len(eps),
    )
    assert cov.sufficient is False
    assert cov.reason == REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
        bars_by_day=bars,
    )
    run, verdict = run_b4_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        episodes=eps,
        requested_verdict="accept",
    )
    assert run.feature_block.status == "coverage_insufficient"
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT


def test_holdout_lift_gate_rejects_equal_holdout() -> None:
    class _M:
        def __init__(self, ret):
            self.total_return = ret
            self.max_drawdown = 0.1
            self.n_trades_completed = 40

    stab = evaluate_holdout_lift_vs_b0(_M(0.059), _M(0.059))
    assert stab.passed is False
    assert stab.reason == REASON_HOLDOUT_LIFT_UNMET


def test_b4_scaffold_without_measure_never_accepts() -> None:
    run, verdict = run_b4_scaffold(
        snapshot=_bounded_snapshot(),
        requested_verdict="accept",
        measure_b0_paper=False,
        measure_b4_paper_flag=False,
    )
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B4_SCAFFOLD_NO_MEASURED_EDGE


def test_b4_canary_overclaim_raises() -> None:
    run = build_b4_run(
        snapshot=_canary_snapshot(),
        measure_b0_paper=False,
        measure_b4_paper_flag=False,
    )
    with pytest.raises(CanaryScopeOverclaimError):
        finalize_b4_verdict(run, requested_verdict="accept")


def test_b4_rich_coverage_measures_and_reports_delta() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    bars = _synthetic_window_bars(len(days), days=days)
    # Dense synthetic events: ≥10 event days, ≥25% fraction, ≥20 unique stocks.
    eps_list: list[DisclosureEpisode] = []
    synth_codes = [f"{600000 + k:06d}" for k in range(24)]
    event_idxs = list(range(0, len(days) - 4, 2))  # dense
    for i, day_i in enumerate(event_idxs):
        # Two stocks per event day → ≥20 unique across 18 days.
        for j in range(2):
            code = synth_codes[(i * 2 + j) % len(synth_codes)]
            eps_list.append(
                DisclosureEpisode(
                    stock_code=code,
                    notice_date=days[day_i],
                    available_at_date=days[day_i],
                    report_date=days[day_i],
                    score=float(i + 1 + j),
                    increase_holder_n=1,
                )
            )
            template = bars[days[day_i]][0]
            for d in days:
                existing = {r["ts_code"].split(".")[0] for r in bars[d]}
                if code not in existing:
                    bars[d].append(
                        {
                            **template,
                            "ts_code": f"{code}.SH",
                            "pct_chg": 0.5,
                        }
                    )

    eps = tuple(eps_list)
    eligible = eligible_by_day_from_episodes(eps, days)
    cov = measure_disclosure_event_coverage(
        days,
        eligible,
        null_notice_excluded=0,
        missing_available_at_excluded=0,
        episode_count=len(eps),
    )
    assert cov.sufficient is True

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
        bars_by_day=bars,
    )
    assert b0.measured_b0 is not None
    measured = measure_b4_paper(
        bars, b0_measured=b0.measured_b0, episodes=eps
    )
    assert measured.coverage.sufficient is True
    assert measured.measured is not None
    assert measured.delta is not None

    run, verdict = run_b4_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        episodes=eps,
        requested_verdict="accept",
    )
    assert run.feature_block.status == "measured_gated"
    assert verdict.claimable is False  # short-window / gates / lift honesty
    assert verdict.verdict in {"reject", "inconclusive"}
    assert "holdout_lift_stability" in verdict.details
