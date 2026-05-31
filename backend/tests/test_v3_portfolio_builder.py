"""Tests for v3 portfolio builder router helpers."""
from __future__ import annotations

import asyncio
import csv

import pytest


def test_backtest_endpoint_uses_regime_segment_service(tmp_path, monkeypatch):
    from routers import v3_portfolio_builder as subject

    nav_path = tmp_path / "portfolio_backtest_nav.csv"
    with nav_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "strategy_nav", "benchmark_nav"])
        writer.writeheader()
        for index in range(63):
            strategy_nav = 1.0
            benchmark_nav = 1.0
            if index == 60:
                strategy_nav = 1.10
                benchmark_nav = 1.12
            elif index == 61:
                strategy_nav = 0.99
                benchmark_nav = 0.88
            elif index == 62:
                strategy_nav = 1.089
                benchmark_nav = 1.0
            writer.writerow({
                "date": f"D{index:03d}",
                "strategy_nav": strategy_nav,
                "benchmark_nav": benchmark_nav,
            })

    monkeypatch.setattr(subject, "NAV_CSV_PATH", nav_path)

    payload = asyncio.run(subject.get_portfolio_backtest(start=None, end=None, sample=0))

    assert payload["ok"] is True
    segments = payload["regime_segments"]
    assert [item["regime"] for item in segments] == ["bull", "bear", "sideways"]
    assert segments[0]["n_days"] == 1
    assert segments[0]["avg_daily_ret"] == pytest.approx(0.10)
    assert segments[1]["n_days"] == 1
    assert segments[1]["avg_daily_ret"] == pytest.approx(-0.10)
    assert segments[2]["n_days"] == 1
    assert segments[2]["avg_daily_ret"] == pytest.approx(0.10)
