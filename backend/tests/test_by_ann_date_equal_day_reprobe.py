"""by_ann_date equal/advance keeps watermark day (holders equal-wm bug class).

Sparse disclosure domains must not permanent-skip wm day when the calendar window
advances past an incompletely populated announcement day. Full-day re-pull is
cheap (~tens–hundreds of rows); policy = ann_reprobe via frontier_decision.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

REG = {
    "defaults": {
        "target_db": "tushare_raw",
        "fetch_timeout_seconds": 120,
        "execution_policy": {"mode": "enabled", "reason": "active"},
    },
    "domains": {
        "synthetic_ann_domain": {
            "source": "tushare",
            "api": "forecast",
            "target_table": "raw_tushare_synthetic_ann_test",
            "grain": ["ann_date", "ts_code"],
            "batch_mode": "by_ann_date",
            "date_param": "ann_date",
            "data_start": "20260101",
            "allow_empty_batch": True,
        },
    },
}


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


class _CapturingAdapter:
    def __init__(self):
        self.calls: list[dict] = []

    def fetch_raw(self, api, **params):
        self.calls.append(dict(params))
        return [{"ann_date": params["ann_date"], "ts_code": "000001.SZ"}]


@pytest.fixture()
def env(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    adapter = _CapturingAdapter()
    monkeypatch.setattr(sr, "_adapter", lambda name: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: shared)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(
        sr,
        "_calendar_days",
        lambda start, end=None: ["20260722", "20260723"],
    )
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec, **_kwargs: sr.DomainEligibility(
            "20260723", False, "published"
        ),
    )
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    monkeypatch.setattr(sr, "_pending_failure_start", lambda _spec: None)
    yield c, adapter
    c.close()


def test_routine_incremental_keeps_ann_watermark_day(env, monkeypatch):
    """wm=20260722 end=20260723: must re-pull wm day (late same-day filers)."""
    _c, adapter = env
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: "20260722")
    res = sr.run_domain("synthetic_ann_domain", registry=REG, end="20260723")
    assert res["ok"] is True
    dates = sorted(c["ann_date"] for c in adapter.calls)
    assert dates == ["20260722", "20260723"], (
        f"by_ann_date incremental must keep watermark day for equal-day reprobe, "
        f"got {dates}"
    )


def test_equal_frontier_single_day_still_pulls(env, monkeypatch):
    """wm == eligible_end: single-day window must still fetch (population unproven)."""
    _c, adapter = env
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: "20260723")
    monkeypatch.setattr(
        sr,
        "_calendar_days",
        lambda start, end=None: ["20260723"],
    )
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec, **_kwargs: sr.DomainEligibility(
            "20260723", False, "published"
        ),
    )
    res = sr.run_domain("synthetic_ann_domain", registry=REG, end="20260723")
    assert res["ok"] is True
    dates = [c["ann_date"] for c in adapter.calls]
    assert dates == ["20260723"]
