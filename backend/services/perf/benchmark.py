"""Phase 6 性能优化 — benchmark + audit guardrails.

按 Codex brief 第六阶段:
- benchmark: 各 subsystem timing baseline (per stock × per trial × per window)
- guardrails: regression detection (best run vs current run)

API:
    from services.perf.benchmark import (
        BenchmarkReport, benchmark_section, save_benchmark, compare_benchmarks
    )

    with benchmark_section("phase4_label_ablation_5d") as bm:
        run_train_p0b(label="5d")
    # bm.elapsed_sec, bm.peak_memory_mb 自动捕获

    save_benchmark(bm, "data/audit/benchmarks/")
    # 后续 compare:
    diff = compare_benchmarks(baseline_path, current_path)
    # diff = {"phase4_label_ablation_5d": {"baseline_sec": 600, "current_sec": 450, "delta_pct": -25%}}
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Optional

log = logging.getLogger("perf.benchmark")


@dataclass
class BenchmarkReport:
    """Single benchmark section result."""
    name: str
    elapsed_sec: float
    peak_memory_mb: Optional[float] = None  # rss peak
    timestamp_utc: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@contextmanager
def benchmark_section(name: str, **metadata):
    """Context manager 测 timing + memory.

    Usage:
        with benchmark_section("phase4_label_5d", label="fwd_cost_after_5d") as bm:
            run_train_p0b(...)
        log.info(f"  elapsed: {bm.elapsed_sec:.1f}s, mem: {bm.peak_memory_mb:.1f}MB")
    """
    t0 = time.time()
    mem_baseline = _get_rss_mb()
    mem_peak = mem_baseline
    bm = BenchmarkReport(
        name=name,
        elapsed_sec=0.0,
        peak_memory_mb=None,
        timestamp_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        metadata=dict(metadata),
    )
    try:
        yield bm
        bm.elapsed_sec = time.time() - t0
        # Best effort peak rss after yield (single sample, real peak need tracker thread)
        mem_after = _get_rss_mb()
        if mem_baseline is not None and mem_after is not None:
            bm.peak_memory_mb = max(mem_baseline, mem_after)
        elif mem_after is not None:
            bm.peak_memory_mb = mem_after
        elif mem_baseline is not None:
            bm.peak_memory_mb = mem_baseline
    except Exception as e:
        bm.elapsed_sec = time.time() - t0
        bm.metadata["error"] = f"{type(e).__name__}: {e}"
        raise


def _get_rss_mb() -> Optional[float]:
    """Get current process RSS in MB. Returns None if psutil not available."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1e6
    except Exception:
        return None


def save_benchmark(bm: BenchmarkReport, output_dir: str | Path) -> Path:
    """Save benchmark to JSON file (one file per run, append timestamp).

    Returns: Path to saved file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fname = f"benchmark_{bm.name}_{bm.timestamp_utc.replace(':', '-')}.json"
    fpath = output_dir / fname
    with open(fpath, "w") as f:
        json.dump(bm.to_dict(), f, indent=2)
    log.info(f"  benchmark saved: {fpath}")
    return fpath


def load_benchmarks(input_dir: str | Path, name_prefix: Optional[str] = None) -> list[BenchmarkReport]:
    """Load all benchmark JSON in dir (optional name_prefix filter)."""
    input_dir = Path(input_dir)
    if not input_dir.exists():
        return []
    reports = []
    for fpath in sorted(input_dir.glob("benchmark_*.json")):
        with open(fpath) as f:
            d = json.load(f)
        if name_prefix and not d.get("name", "").startswith(name_prefix):
            continue
        reports.append(BenchmarkReport(**d))
    return reports


def compare_benchmarks(baseline: BenchmarkReport, current: BenchmarkReport,
                       regression_threshold_pct: float = 20.0) -> dict:
    """Compare 2 benchmarks. Returns diff dict + regression flag.

    Args:
        regression_threshold_pct: 当 current > baseline by N% → flag regression.

    Returns:
        {
            "name": str,
            "baseline_sec": float,
            "current_sec": float,
            "delta_sec": float,
            "delta_pct": float,
            "is_regression": bool,
            "memory_delta_mb": float | None,
        }
    """
    if baseline.name != current.name:
        log.warning(f"benchmark name mismatch: {baseline.name} vs {current.name}")
    delta_sec = current.elapsed_sec - baseline.elapsed_sec
    delta_pct = (delta_sec / baseline.elapsed_sec * 100) if baseline.elapsed_sec > 0 else 0.0
    is_regression = delta_pct > regression_threshold_pct
    memory_delta = None
    if baseline.peak_memory_mb is not None and current.peak_memory_mb is not None:
        memory_delta = current.peak_memory_mb - baseline.peak_memory_mb
    return {
        "name": baseline.name,
        "baseline_sec": baseline.elapsed_sec,
        "current_sec": current.elapsed_sec,
        "delta_sec": delta_sec,
        "delta_pct": delta_pct,
        "is_regression": is_regression,
        "memory_delta_mb": memory_delta,
    }
