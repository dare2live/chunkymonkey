"""Performance optimization layer (Phase 1 shard / manifest / reducer).

按 stock/quant_experiment_optimization_and_codegraph_brief.md verdict:
shard parallel + reducer 单写入 DuckDB, 避 multi-writer lock 竞争.
"""
from services.perf.shard_runner import (
    ShardSpec,
    ShardManifest,
    export_snapshot,
    run_shards,
    reduce_to_duckdb,
)
from services.perf.prepared_signal_set import (
    PreparedSignalSet,
    build_from_df,
)
from services.perf.fast_path import (
    SimResult,
    ExitReason,
    compute_sharpe,
    compute_mean_ret,
    compute_ic_ir,
    compute_objectives_from_arrays,
)

__all__ = [
    "ShardSpec",
    "ShardManifest",
    "export_snapshot",
    "run_shards",
    "reduce_to_duckdb",
    "PreparedSignalSet",
    "build_from_df",
    "SimResult",
    "ExitReason",
    "compute_sharpe",
    "compute_mean_ret",
    "compute_ic_ir",
    "compute_objectives_from_arrays",
]
