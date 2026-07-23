"""Knife 1b: margin v3 bounded calendar catchup (SSE+SZSE only)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.data_sources import sync_runner as sr
from services.data_sources.margin_catchup import (
    MarginCatchupError,
    land_then_accept_margin_day,
)
from services.pipeline.frozen_domain_observe import margin_hard_gate_required


def test_live_hard_gate_stays_off_without_product_blocking():
    assert margin_hard_gate_required() is False


def test_hard_gate_requires_explicit_product_blocking(monkeypatch):
    registry = sr.load_registry()
    spec = dict(sr.domain_spec(registry, "margin"))
    spec["product_blocking"] = True
    spec["execution_policy"] = {"mode": "enabled", "reason": "bounded_calendar_catchup"}
    monkeypatch.setattr(
        sr,
        "load_registry",
        lambda: {"defaults": registry.get("defaults", {}), "domains": {"margin": spec}},
    )
    monkeypatch.setattr(sr, "domain_spec", lambda reg, domain: reg["domains"][domain])
    assert margin_hard_gate_required(registry={"domains": {"margin": spec}}) is True


def test_catchup_window_refuses_pre_coverage_start():
    with pytest.raises(sr.SyncWindowError, match="coverage_start"):
        sr._require_authorized_margin_catchup_window(
            backfill=False,
            resume=False,
            start="20260715",
            end="20260717",
            max_dates=None,
            eligible_end="20260722",
            coverage_start="20260717",
        )


def test_catchup_window_refuses_beyond_eligible_end():
    with pytest.raises(sr.SyncWindowError, match="eligible_end"):
        sr._require_authorized_margin_catchup_window(
            backfill=False,
            resume=False,
            start="20260717",
            end="20260723",
            max_dates=None,
            eligible_end="20260722",
            coverage_start="20260717",
        )


def test_land_then_accept_requires_v3(monkeypatch):
    spec = sr.domain_spec(sr.load_registry(), "margin")
    bad = dict(spec)
    bad["dataset_contract"] = {
        **spec["dataset_contract"],
        "contract_version": "2",
    }
    with pytest.raises(MarginCatchupError, match="contract_version>=3"):
        land_then_accept_margin_day(
            object(),
            object(),
            bad,
            "20260717",
            fetch_logical_batch=lambda *_a, **_k: [],
        )


def test_drain_margin_is_inapplicable():
    result = sr.drain_domain("margin")
    assert result["status"] == "drain_inapplicable"
    assert "bounded_calendar_catchup" in result["reason"]


def test_acquire_margin_catchup_plans_gap(monkeypatch, tmp_path):
    from services.pipeline.context import PipelineContext
    from services.pipeline import margin_catchup_acquire

    run_calls = []

    class _Conn:
        def execute(self, sql, *_a, **_k):
            # No v3 accepted / canonical yet → plan from coverage_start.
            return SimpleNamespace(fetchone=lambda: (None,))

        def close(self):
            pass

    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda *_a, **_k: sr.DomainEligibility(
            "20260722", True, "next_trading_session_published"
        ),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end: [
            d
            for d in (
                "20260716",
                "20260717",
                "20260720",
                "20260721",
                "20260722",
            )
            if start <= d <= end
        ],
    )
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **kwargs: run_calls.append((domain, kwargs))
        or {
            "status": "ok",
            "rows": 2,
            "last_date": "20260722",
            "failed_batches": 0,
            "contract_version": "3",
        },
    )
    monkeypatch.setattr("services.duck_adapter.connect", lambda *_a, **_k: _Conn())

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "run.log")
    try:
        outcomes = margin_catchup_acquire.run_margin_bounded_catchup(ctx)
    finally:
        ctx.close()

    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "land_then_accept"
    assert outcomes[0]["start"] == "20260717"
    assert outcomes[0]["eligible_end"] == "20260722"
    assert len(run_calls) == 1
    assert run_calls[0][0] == "margin"
    assert run_calls[0][1]["start"] == "20260717"
    assert run_calls[0][1]["end"] == "20260722"
    assert run_calls[0][1]["trigger_mode"] == "manual"


def test_acquire_margin_catchup_skips_when_current(monkeypatch, tmp_path):
    from services.pipeline.context import PipelineContext
    from services.pipeline import margin_catchup_acquire

    run_calls = []

    class _Conn:
        def execute(self, sql, *_a, **_k):
            # v3 accepted already at eligible_end → skip, do not call run_domain.
            return SimpleNamespace(fetchone=lambda: ("20260722",))

        def close(self):
            pass

    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda *_a, **_k: sr.DomainEligibility(
            "20260722", True, "next_trading_session_published"
        ),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end: [
            d
            for d in ("20260720", "20260721", "20260722")
            if start <= d <= end
        ],
    )
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **kwargs: run_calls.append((domain, kwargs))
        or {"status": "ok"},
    )
    monkeypatch.setattr("services.duck_adapter.connect", lambda *_a, **_k: _Conn())

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "run.log")
    try:
        outcomes = margin_catchup_acquire.run_margin_bounded_catchup(ctx)
    finally:
        ctx.close()

    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == "latest_eligible_already_present"
    assert outcomes[0]["local_max"] == "20260722"
    assert run_calls == []


def test_acquire_margin_catchup_schedules_partial_gap(monkeypatch, tmp_path):
    """Stale v3 local_max with later eligible_end → schedule once from next day."""
    from services.pipeline.context import PipelineContext
    from services.pipeline import margin_catchup_acquire

    run_calls = []

    class _Conn:
        def execute(self, sql, *_a, **_k):
            return SimpleNamespace(fetchone=lambda: ("20260717",))

        def close(self):
            pass

    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda *_a, **_k: sr.DomainEligibility(
            "20260722", True, "next_trading_session_published"
        ),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end: [
            d
            for d in (
                "20260717",
                "20260720",
                "20260721",
                "20260722",
            )
            if start <= d <= end
        ],
    )
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **kwargs: run_calls.append((domain, kwargs))
        or {
            "status": "ok",
            "rows": 6,
            "last_date": "20260722",
            "failed_batches": 0,
            "contract_version": "3",
        },
    )
    monkeypatch.setattr("services.duck_adapter.connect", lambda *_a, **_k: _Conn())

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "run.log")
    try:
        outcomes = margin_catchup_acquire.run_margin_bounded_catchup(ctx)
    finally:
        ctx.close()

    assert outcomes[0]["action"] == "land_then_accept"
    assert outcomes[0]["start"] == "20260720"
    assert run_calls[0][1]["start"] == "20260720"
    assert run_calls[0][1]["end"] == "20260722"


def test_run_acquire_wires_margin_catchup(monkeypatch, tmp_path):
    """Click-update acquire path must invoke margin planner (not only one-shot CLI)."""
    from services.pipeline import acquire as acquire_mod
    from services.pipeline import preflight as preflight_mod
    from services.pipeline.context import PipelineContext

    called = {"n": 0}

    monkeypatch.setattr(preflight_mod, "ensure_pipeline_sync_ready", lambda ctx: None)
    monkeypatch.setattr(preflight_mod, "ensure_tushare_authorized", lambda ctx: None)
    monkeypatch.setattr(acquire_mod, "_sync_holders_aif10", lambda ctx: None)
    monkeypatch.setattr(acquire_mod, "_sync_qfii", lambda ctx: None)
    monkeypatch.setattr(acquire_mod, "_sync_org_holding", lambda ctx: None)
    monkeypatch.setattr(acquire_mod, "_sync_registry_drain", lambda ctx: [])
    monkeypatch.setattr(
        acquire_mod, "_sync_formal_on_demand_security_days", lambda ctx: []
    )
    monkeypatch.setattr(acquire_mod, "_build_trading_calendar", lambda ctx: None)
    monkeypatch.setattr(
        acquire_mod, "_refresh_active_a_stock_master", lambda ctx: None
    )
    monkeypatch.setattr(
        acquire_mod,
        "_finalize_acquire_delta",
        lambda ctx, drain_results=None, formal_outcomes=None: None,
    )

    import services.pipeline.margin_catchup_acquire as mca
    import services.pipeline.frozen_domain_observe as fdo

    def _catchup(ctx):
        called["n"] += 1
        return [
            {
                "domain": "margin",
                "action": "skip",
                "reason": "latest_eligible_already_present",
            }
        ]

    monkeypatch.setattr(mca, "run_margin_bounded_catchup", _catchup)
    monkeypatch.setattr(fdo, "observe_frozen_on_demand_domains", lambda ctx: [])

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "acq.log", dry=False)
    try:
        acquire_mod.run_acquire(ctx)
    finally:
        ctx.close()

    assert called["n"] == 1

