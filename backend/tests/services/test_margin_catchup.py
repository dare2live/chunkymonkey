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
            return SimpleNamespace(fetchone=lambda: ("20260716",))

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
