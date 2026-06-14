"""S0 consumer_alpha 验证执行器测试 — 守死亡条款 + 留档写入回归。

覆盖: family 契约校验 / gate-before-run (泄漏死) / 矩阵枚举+映射铁律 / prereg_hash 确定性+敏感性
(谄媚死) / dry 写 3 留档表且 ic_scan 空不造假 (估计死) / 硬阻断 return 非零。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.experiment_jobs import load_experiment_job_contract  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "backend" / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cav = _load("experiment_consumer_alpha_validation")
bes = _load("build_experiment_store")


# ---- family 契约 ----
def test_family_contract_validates():
    c = load_experiment_job_contract()
    f = c.require_family("consumer_alpha_validation")
    assert set(f.required_gates) == {"data_health_snapshot", "pit_audit", "leakage_consumer_scan"}
    tables = {a.table for a in f.artifact_contracts if a.kind == "table"}
    assert tables == {"fact_experiment_verdict", "experiment_pit_audit_log",
                      "pipeline_artifact_lineage", "fact_consumer_alpha_ic_scan"}


def test_plan_blocks_without_gate_evidence():
    c = load_experiment_job_contract()
    p = c.plan("consumer_alpha_validation", input_snapshot="x", objective="y", rollback_plan="z")
    assert not p.ready_to_run
    assert all(b.startswith("missing_gate_evidence") for b in p.blocked_reasons)


def test_plan_ready_with_gate_evidence():
    c = load_experiment_job_contract()
    p = c.plan("consumer_alpha_validation", input_snapshot="x", objective="y", rollback_plan="z",
               gate_evidence=("data_health_snapshot=a", "pit_audit=b", "leakage_consumer_scan=c"))
    assert p.ready_to_run


# ---- 矩阵枚举 + 映射铁律 ----
def test_load_matrix_rejects_bad_consumer_ref(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\nconsumers:\n  - {id: x, kind: feature_ic, metrics: [ic]}\n"
        "mapping_law: {event: feature_ic}\n"
        "data_candidates:\n  - {id: d1, landing: event, consumer_ids: [nonexist]}\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="未知 consumer"):
        cav.load_matrix(bad)


def test_enumerate_cells_count_and_routing():
    m = cav.load_matrix()
    cells = cav.enumerate_cells(m)
    assert len(cells) == 7  # 6 候选, kpl 显式双 consumer = 7
    kpl = [c["consumer_id"] for c in cells if c["data_candidate"] == "kpl_limit_step"]
    assert set(kpl) == {"technical_formula", "feature_panel_ic"}
    # 映射铁律: event/fundamental/chip/infra -> feature_ic
    inc = [c for c in cells if c["data_candidate"] == "income_fina_indicator"]
    assert inc and inc[0]["consumer_id"] == "feature_panel_ic"


def test_no_unrouted_landing():
    m = cav.load_matrix()
    landings = {dc["landing"] for dc in m["data_candidates"]}
    assert landings <= set(m["mapping_law"]) | {None}


# ---- prereg_hash (谄媚死) ----
def test_prereg_hash_deterministic_and_sensitive():
    m = cav.load_matrix()
    h1 = cav.prereg_hash(m)
    assert h1 == cav.prereg_hash(m)  # 确定性
    m2 = cav.load_matrix()
    m2["mapping_law"]["event"] = "formula_signal"  # 改判据
    assert cav.prereg_hash(m2) != h1  # 敏感性 (改判据 hash 变)


def test_check_prereg_match_and_mismatch(tmp_path):
    h = "deadbeef" * 8
    good = tmp_path / "p_good.md"
    good.write_text(f"# prereg\nprereg_hash: {h}\n", encoding="utf-8")
    ok, _ = cav.check_prereg(good, h)
    assert ok
    bad = tmp_path / "p_bad.md"
    bad.write_text("# prereg\nprereg_hash: 0000\n", encoding="utf-8")
    ok2, msg = cav.check_prereg(bad, h)
    assert not ok2 and "谄媚死" in msg


# ---- 执行器留档写入 (estimate 死: dry 不造假) ----
def _tmp_store(tmp_path: Path) -> Path:
    store = tmp_path / "exp.duckdb"
    conn = duck_connect(str(store), read_only=False)
    try:
        conn.executescript(bes.DDL)
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    return store


def test_executor_writes_留档_dry(tmp_path):
    store = _tmp_store(tmp_path)
    rc = cav.main([
        "--input-snapshot", "x@20260614", "--objective", "test", "--rollback-plan", "wipe",
        "--run-id", "t_dry_1", "--store", str(store),
    ])
    assert rc == 0
    conn = duck_connect(str(store), read_only=True)
    try:
        v = conn.execute("SELECT verdict, prereg_hash FROM fact_experiment_verdict WHERE run_id='t_dry_1'").fetchone()
        assert v and v[0] == "DRY_SKELETON" and len(v[1]) == 64
        assert conn.execute("SELECT count(*) FROM experiment_pit_audit_log WHERE run_id='t_dry_1'").fetchone()[0] >= 3
        assert conn.execute("SELECT count(*) FROM pipeline_artifact_lineage WHERE run_id='t_dry_1'").fetchone()[0] == 1
        # ic_scan 空: dry 不写假 IC (measured-not-estimated)
        assert conn.execute("SELECT count(*) FROM fact_consumer_alpha_ic_scan").fetchone()[0] == 0
    finally:
        conn.close()


def test_executor_hard_blocker_aborts(tmp_path):
    store = _tmp_store(tmp_path)
    # 未知 backend = 硬阻断 (非 gate), 即便 dry 也 return 2
    rc = cav.main([
        "--backend", "nonexist", "--input-snapshot", "x", "--objective", "y", "--rollback-plan", "z",
        "--run-id", "t_block", "--store", str(store),
    ])
    assert rc == 2
