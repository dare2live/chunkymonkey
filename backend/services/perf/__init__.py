"""Performance optimization layer (Phase 1 shard / manifest / reducer).

按 stock/quant_experiment_optimization_and_codegraph_brief.md verdict:
shard parallel + reducer 单写入 DuckDB, 避 multi-writer lock 竞争.

2026-06-14 地基-reset (commit 639e0dfb): prepared_signal_set / prepared_panel 已删 →
__init__ 残留死 import → `import services.perf` ModuleNotFoundError (2026-06-22 conformance
审计 P0-8 抓出, 0 外部消费者)。仅 shard_runner/fast_path/benchmark 幸存。
"""
from services.perf.shard_runner import (
    ShardSpec,
    ShardManifest,
    export_snapshot,
    run_shards,
    reduce_to_duckdb,
)
from services.perf.fast_path import (
    SimResult,
    ExitReason,
    compute_sharpe,
    compute_mean_ret,
    compute_ic_ir,
    compute_objectives_from_arrays,
)
from services.perf.benchmark import (
    BenchmarkReport,
    benchmark_section,
    save_benchmark,
    load_benchmarks,
    compare_benchmarks,
)

__all__ = [
    "ShardSpec",
    "ShardManifest",
    "export_snapshot",
    "run_shards",
    "reduce_to_duckdb",
    "SimResult",
    "ExitReason",
    "compute_sharpe",
    "compute_mean_ret",
    "compute_ic_ir",
    "compute_objectives_from_arrays",
    "BenchmarkReport",
    "benchmark_section",
    "save_benchmark",
    "load_benchmarks",
    "compare_benchmarks",
]
