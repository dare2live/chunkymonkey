"""Phase 1 性能优化 — shard / manifest / reducer 架构.

按 quant_experiment_optimization_and_codegraph_brief.md verdict:
- 只读输入快照 → 多 shard 并行计算 → artifact 输出 → reducer 单写入 DuckDB

设计:
1. **Manifest**: shard run plan (input snapshot path, shard list, output dir)
2. **Shard worker**: 单 process 跑独立 shard (e.g. 1 horizon / 1 universe / 1 model),
   读 read-only snapshot, 写 parquet artifact, 不直接 write DuckDB (避 single-writer lock 竞争).
3. **Reducer**: 顺序读 N parquet artifacts → 1 DuckDB INSERT (single writer, idempotent).

优势:
- N shards 并行 (Mac mini 8 core 可跑 5-7 shards 并行)
- DuckDB single writer lock 只在 reducer 短时持有
- 失败 shard 可单独重跑 (manifest 记录 status)

使用:
    from services.perf.shard_runner import ShardManifest, run_shards, reduce_to_duckdb

    manifest = ShardManifest(
        run_id="phase4_label_ablation_20260517",
        snapshot_path="data/smartmoney.duckdb.readonly_snapshot.parquet",
        shards=[
            {"name": "5d", "horizon": "fwd_cost_after_5d", "model_id": "lgbm_..._5d"},
            {"name": "10d", "horizon": "fwd_cost_after_10d", "model_id": "lgbm_..._10d"},
            {"name": "20d", "horizon": "fwd_cost_after_20d", "model_id": "lgbm_..._20d"},
        ],
        output_dir="data/audit/artifacts/phase4_label_ablation_20260517",
    )
    run_shards(manifest, worker_fn=train_p0b_shard, max_workers=3)
    reduce_to_duckdb(manifest, target_tables=["mart_p0b_oos_predictions", "mart_p0b_walkforward_eval"])
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Any

import duckdb

log = logging.getLogger("perf.shard_runner")


@dataclass
class ShardSpec:
    """单个 shard 的输入参数 + 状态."""
    name: str
    params: dict  # 任意 dict, 由 worker_fn 解析 (e.g. {"horizon": "fwd_cost_after_5d", "model_id": "lgbm_..."})
    status: str = "pending"  # pending / running / done / failed
    artifact_path: str | None = None
    elapsed_sec: float | None = None
    error: str | None = None


@dataclass
class ShardManifest:
    """shard run 计划 + 共享 input snapshot."""
    run_id: str
    snapshot_path: str  # read-only input (e.g. parquet from mart_p0a_feature_label_panel_v3)
    output_dir: str
    shards: list[ShardSpec] = field(default_factory=list)

    def save(self) -> None:
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        manifest_path = Path(self.output_dir) / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump({
                "run_id": self.run_id,
                "snapshot_path": self.snapshot_path,
                "output_dir": self.output_dir,
                "shards": [asdict(s) for s in self.shards],
            }, f, indent=2)
        log.info(f"manifest saved: {manifest_path}")

    @classmethod
    def load(cls, manifest_path: str | Path) -> "ShardManifest":
        with open(manifest_path) as f:
            d = json.load(f)
        m = cls(run_id=d["run_id"], snapshot_path=d["snapshot_path"],
                output_dir=d["output_dir"])
        m.shards = [ShardSpec(**s) for s in d.get("shards", [])]
        return m


def export_snapshot(db_path: str, query: str, output_parquet: str) -> dict:
    """从 DuckDB read-only 导出 query 结果 to parquet (input snapshot).

    使用 single read connection — 完成后即释放, 不阻塞其他 writer.

    Returns:
        {"rows": int, "cols": int, "size_mb": float, "elapsed_sec": float}.
    """
    t0 = time.time()
    Path(output_parquet).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(db_path, read_only=True)
    try:
        conn.execute(f"COPY ({query}) TO '{output_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        n_rows = conn.execute(f"SELECT COUNT(*) FROM '{output_parquet}'").fetchone()[0]
        n_cols = len(conn.execute(f"DESCRIBE SELECT * FROM '{output_parquet}'").fetchall())
    finally:
        conn.close()
    size_mb = Path(output_parquet).stat().st_size / 1e6
    elapsed = time.time() - t0
    log.info(f"snapshot exported: {output_parquet} {n_rows:,} × {n_cols} cols ({size_mb:.1f} MB, {elapsed:.1f}s)")
    return {"rows": n_rows, "cols": n_cols, "size_mb": size_mb, "elapsed_sec": elapsed}


def _run_one_shard(args):
    """worker subprocess entry — 解包 args + invoke worker_fn."""
    shard_spec, worker_fn_name, manifest_dict = args
    # 动态 import worker_fn (multiprocessing pickling 限制)
    import importlib
    module_name, func_name = worker_fn_name.rsplit(".", 1)
    module = importlib.import_module(module_name)
    worker_fn = getattr(module, func_name)
    t0 = time.time()
    try:
        artifact_path = worker_fn(shard_spec, manifest_dict)
        shard_spec.status = "done"
        shard_spec.artifact_path = str(artifact_path)
    except Exception as e:
        shard_spec.status = "failed"
        shard_spec.error = f"{type(e).__name__}: {e}"
    shard_spec.elapsed_sec = time.time() - t0
    return shard_spec


def run_shards(manifest: ShardManifest, worker_fn_name: str, max_workers: int = 3) -> ShardManifest:
    """并行跑全 shard, 各写独立 parquet artifact.

    worker_fn_name: str dotted path (e.g. "services.perf.workers.train_lgbm_shard")
                    fn signature: (shard: ShardSpec, manifest_dict: dict) -> Path (artifact)
    """
    log.info(f"=== run_shards: {len(manifest.shards)} shards, max_workers={max_workers} ===")
    manifest_dict = {"run_id": manifest.run_id, "snapshot_path": manifest.snapshot_path,
                     "output_dir": manifest.output_dir}
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_run_one_shard, (s, worker_fn_name, manifest_dict)): i
                   for i, s in enumerate(manifest.shards)}
        for fut in as_completed(futures):
            i = futures[fut]
            updated = fut.result()
            manifest.shards[i] = updated
            log.info(f"  shard {updated.name}: {updated.status} ({updated.elapsed_sec:.1f}s)"
                     + (f" → {updated.artifact_path}" if updated.artifact_path else "")
                     + (f" ERROR: {updated.error}" if updated.error else ""))
    manifest.save()
    n_done = sum(1 for s in manifest.shards if s.status == "done")
    n_failed = sum(1 for s in manifest.shards if s.status == "failed")
    log.info(f"=== run_shards: {n_done} done / {n_failed} failed ===")
    return manifest


def reduce_to_duckdb(manifest: ShardManifest, target_table: str,
                     mode: str = "delete_insert",
                     delete_clause: str | None = None) -> dict:
    """Reducer 阶段: 读 N parquet artifacts → 顺序 INSERT INTO target_table (single writer).

    mode='delete_insert' (default): DELETE matching 条件 + INSERT (idempotent).
    delete_clause: SQL fragment for DELETE (e.g. "model_id IN ('lgbm_..._5d', ..._10d, ..._20d)").

    Returns: {"rows_inserted": int, "elapsed_sec": float, "shards_consumed": int}.
    """
    t0 = time.time()
    # 找 smartmoney.duckdb 默认路径
    repo_root = Path(__file__).resolve().parents[3]
    db_path = repo_root / "data" / "smartmoney.duckdb"

    artifacts = [s.artifact_path for s in manifest.shards
                 if s.status == "done" and s.artifact_path]
    log.info(f"=== reduce_to_duckdb: {len(artifacts)} artifacts → {target_table} ===")

    if not artifacts:
        log.warning("  no done artifacts to reduce, skip")
        return {"rows_inserted": 0, "elapsed_sec": 0, "shards_consumed": 0}

    conn = duckdb.connect(str(db_path))
    try:
        if mode == "delete_insert" and delete_clause:
            conn.execute(f"DELETE FROM {target_table} WHERE {delete_clause}")
            log.info(f"  cleared existing rows: WHERE {delete_clause}")
        rows_inserted = 0
        for artifact_path in artifacts:
            # COPY parquet rows → target (列对应)
            conn.execute(f"INSERT INTO {target_table} SELECT * FROM '{artifact_path}'")
            r = conn.execute(f"SELECT COUNT(*) FROM '{artifact_path}'").fetchone()[0]
            rows_inserted += r
            log.info(f"  consumed {artifact_path}: +{r:,} rows")
    finally:
        conn.close()
    elapsed = time.time() - t0
    log.info(f"=== reduce_to_duckdb: {rows_inserted:,} rows inserted in {elapsed:.1f}s ===")
    return {"rows_inserted": rows_inserted, "elapsed_sec": elapsed, "shards_consumed": len(artifacts)}
