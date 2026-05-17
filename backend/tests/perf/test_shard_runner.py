"""Tests for shard_runner.py (Phase 1 performance optimization)."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from services.perf import ShardSpec, ShardManifest, export_snapshot, reduce_to_duckdb


class TestShardManifest:
    def test_manifest_save_load_roundtrip(self, tmp_path: Path):
        m = ShardManifest(
            run_id="test_run",
            snapshot_path=str(tmp_path / "snap.parquet"),
            output_dir=str(tmp_path / "out"),
        )
        m.shards = [
            ShardSpec(name="a", params={"k1": 1}),
            ShardSpec(name="b", params={"k2": "v"}, status="done", artifact_path="x.parquet"),
        ]
        m.save()
        assert (tmp_path / "out" / "manifest.json").exists()

        m2 = ShardManifest.load(tmp_path / "out" / "manifest.json")
        assert m2.run_id == "test_run"
        assert len(m2.shards) == 2
        assert m2.shards[0].name == "a"
        assert m2.shards[1].status == "done"
        assert m2.shards[1].artifact_path == "x.parquet"


class TestExportSnapshot:
    def test_export_snapshot_to_parquet(self, tmp_path: Path):
        # Setup tiny in-memory DuckDB → file (so export reads from file)
        db_path = tmp_path / "test.duckdb"
        c = duckdb.connect(str(db_path))
        c.execute("CREATE TABLE t(x INT, y TEXT)")
        c.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        c.close()

        parquet_path = tmp_path / "out.parquet"
        result = export_snapshot(
            str(db_path),
            query="SELECT * FROM t",
            output_parquet=str(parquet_path),
        )
        assert parquet_path.exists()
        assert result["rows"] == 3
        assert result["cols"] == 2
        assert result["elapsed_sec"] > 0

        # Verify can re-read
        c2 = duckdb.connect(":memory:")
        r = c2.execute(f"SELECT COUNT(*) FROM '{parquet_path}'").fetchone()[0]
        c2.close()
        assert r == 3


class TestReduceToDuckdb:
    def test_reduce_with_delete_insert(self, tmp_path: Path, monkeypatch):
        # Setup target DuckDB
        db_path = tmp_path / "target.duckdb"
        c = duckdb.connect(str(db_path))
        c.execute("CREATE TABLE target(model_id TEXT, score DOUBLE)")
        c.execute("INSERT INTO target VALUES ('old', 0.1), ('keep', 0.9)")
        c.close()

        # Create artifact parquets
        art1 = tmp_path / "a1.parquet"
        art2 = tmp_path / "a2.parquet"
        c2 = duckdb.connect(":memory:")
        c2.execute(f"COPY (SELECT 'new1' as model_id, 0.5 as score) TO '{art1}'")
        c2.execute(f"COPY (SELECT 'new2' as model_id, 0.7 as score) TO '{art2}'")
        c2.close()

        # Build manifest pointing at these artifacts
        m = ShardManifest(run_id="r", snapshot_path="x", output_dir=str(tmp_path))
        m.shards = [
            ShardSpec(name="s1", params={}, status="done", artifact_path=str(art1)),
            ShardSpec(name="s2", params={}, status="done", artifact_path=str(art2)),
        ]

        # Monkeypatch reduce_to_duckdb to use tmp db (default uses repo data/)
        import services.perf.shard_runner as sr
        original = sr.duckdb
        # Use a wrapper that intercepts the connect() call to redirect to tmp db
        class _DuckdbProxy:
            def connect(self, path, read_only=False):
                if "smartmoney.duckdb" in str(path):
                    return original.connect(str(db_path))
                return original.connect(path, read_only=read_only)
        monkeypatch.setattr(sr, "duckdb", _DuckdbProxy())

        result = reduce_to_duckdb(m, target_table="target",
                                  mode="delete_insert",
                                  delete_clause="model_id LIKE 'new%' OR model_id = 'old'")
        assert result["shards_consumed"] == 2
        assert result["rows_inserted"] == 2

        # Verify final state
        c3 = duckdb.connect(str(db_path), read_only=True)
        rows = c3.execute("SELECT model_id FROM target ORDER BY model_id").fetchall()
        c3.close()
        assert [r[0] for r in rows] == ["keep", "new1", "new2"]  # 'old' deleted, 'keep' preserved
