#!/usr/bin/env python3
"""Legacy experiment evidence store builder; current owner=docs/strategy_validation_contract.md。

在隔离的 experiment_store.duckdb 中建 4 张历史 evidence 表:
verdict / ic_scan / lineage / pit_audit。幂等 CREATE IF NOT EXISTS。
PIT 纪律: ic_scan 带 data_snapshot 维度 (as-of); lineage 带 input/output hash + built_at 防 snapshot 回溯泄漏。

本脚本只维护历史 evidence-store schema，不声明 job executor、provider backend 或发布资格。
用法: python backend/scripts/build_experiment_store.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"

DDL = """
CREATE TABLE IF NOT EXISTS fact_experiment_verdict (
    verdict_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    run_id TEXT,
    verdict TEXT,                       -- GO / REJECT / CONDITIONAL
    ts TEXT,                            -- ISO8601
    prereg_hash TEXT,                   -- 冻结判据 hash (防事后挪门柱)
    judges_json TEXT,                   -- J1-J3 判官结果
    gate_blockers_json TEXT,            -- leakage/gate 阻断项
    confirmed_by_owner INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fact_consumer_alpha_ic_scan (
    data_snapshot TEXT NOT NULL,        -- 数据快照日 (PIT as-of)
    consumer_id TEXT NOT NULL,          -- 消费者 (公式/特征族)
    metric TEXT NOT NULL,               -- ic / ic_ir / sharpe / n_windows
    value DOUBLE,
    n_windows INTEGER,
    run_id TEXT,
    built_at TEXT,
    PRIMARY KEY (data_snapshot, consumer_id, metric)
);

CREATE TABLE IF NOT EXISTS pipeline_artifact_lineage (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT,
    input_tables_hash TEXT,             -- 输入表内容 hash (防 snapshot 漂移)
    output_tables_hash TEXT,
    artifact_path TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_pit_audit_log (
    log_id TEXT PRIMARY KEY,
    run_id TEXT,
    step TEXT,                          -- 每步 PIT 校验 (非仅最终判决)
    check_name TEXT,
    passed INTEGER,
    detail_json TEXT,
    ts TEXT
);

"""


def main() -> int:
    m = yaml.safe_load(open(MANIFEST, encoding="utf-8"))
    db = REPO / m["databases"]["experiment_store"]["path"]
    fresh = not db.exists()
    conn = duck_connect(str(db), read_only=False)
    try:
        conn.execute("SET enable_progress_bar=false")
        conn.executescript(DDL)
        conn.execute("CHECKPOINT")
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()]
        cons = conn.execute("SELECT count(*) FROM duckdb_constraints()").fetchone()[0]
    finally:
        conn.close()
    print(f"{db.name} ({'新建' if fresh else '已存在'}): {len(tables)} 表 / {cons} 约束")
    for t in tables:
        print(f"  {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
