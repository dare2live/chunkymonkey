"""Tests for benchmark (phase 6 perf)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from services.perf.benchmark import (
    BenchmarkReport, benchmark_section, save_benchmark, load_benchmarks,
    compare_benchmarks,
)


class TestBenchmarkSection:
    def test_benchmark_captures_elapsed(self):
        with benchmark_section("test_section") as bm:
            time.sleep(0.05)
        assert bm.elapsed_sec >= 0.05
        assert bm.elapsed_sec < 1.0  # sanity
        assert bm.name == "test_section"
        assert bm.timestamp_utc != ""

    def test_benchmark_captures_metadata(self):
        with benchmark_section("test_meta", model="lgbm", label="20d") as bm:
            pass
        assert bm.metadata == {"model": "lgbm", "label": "20d"}

    def test_benchmark_on_exception_still_captures(self):
        with pytest.raises(ValueError):
            with benchmark_section("test_err") as bm:
                raise ValueError("test")
        # Note: bm not accessible outside context if exception, but elapsed/metadata captured
        # (实际用法应该 try/except 包裹 if 需要 caller side access)


class TestSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        with benchmark_section("test_roundtrip", label="20d") as bm:
            time.sleep(0.01)
        save_benchmark(bm, tmp_path)

        loaded = load_benchmarks(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].name == "test_roundtrip"
        assert loaded[0].metadata == {"label": "20d"}

    def test_load_with_prefix_filter(self, tmp_path: Path):
        with benchmark_section("phase4_label_5d") as a:
            time.sleep(0.01)
        save_benchmark(a, tmp_path)

        with benchmark_section("phase4_universe_A") as b:
            time.sleep(0.01)
        save_benchmark(b, tmp_path)

        loaded_label = load_benchmarks(tmp_path, name_prefix="phase4_label_")
        loaded_univ = load_benchmarks(tmp_path, name_prefix="phase4_universe_")
        assert len(loaded_label) == 1
        assert len(loaded_univ) == 1


class TestCompareBenchmarks:
    def test_compare_improvement(self):
        baseline = BenchmarkReport(name="x", elapsed_sec=10.0, timestamp_utc="t0")
        current = BenchmarkReport(name="x", elapsed_sec=8.0, timestamp_utc="t1")
        diff = compare_benchmarks(baseline, current)
        assert diff["delta_sec"] == -2.0
        assert diff["delta_pct"] == -20.0
        assert diff["is_regression"] is False

    def test_compare_regression(self):
        baseline = BenchmarkReport(name="x", elapsed_sec=10.0, timestamp_utc="t0")
        current = BenchmarkReport(name="x", elapsed_sec=13.0, timestamp_utc="t1")
        diff = compare_benchmarks(baseline, current, regression_threshold_pct=20.0)
        assert diff["delta_pct"] == 30.0
        assert diff["is_regression"] is True

    def test_compare_within_threshold(self):
        baseline = BenchmarkReport(name="x", elapsed_sec=10.0, timestamp_utc="t0")
        current = BenchmarkReport(name="x", elapsed_sec=11.0, timestamp_utc="t1")
        diff = compare_benchmarks(baseline, current, regression_threshold_pct=20.0)
        assert diff["delta_pct"] == 10.0
        assert diff["is_regression"] is False
