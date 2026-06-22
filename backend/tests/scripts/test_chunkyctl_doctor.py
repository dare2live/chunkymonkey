"""chunkyctl doctor 最小重建单测 — 核心聚合/巡检纯函数 (run_doctor 本体走 subprocess+DB 由实跑验证)。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import chunkyctl  # noqa: E402


def test_aggregate_verdict_fail_priority():
    """FAIL 优先 > WARN > PASS; returncode!=0 且非WARN/PASS 也算 FAIL。"""
    assert chunkyctl._aggregate_verdict([{"verdict": "PASS"}, {"verdict": "PASS"}]) == "PASS"
    assert chunkyctl._aggregate_verdict([{"verdict": "PASS"}, {"verdict": "WARN"}]) == "WARN"
    assert chunkyctl._aggregate_verdict([{"verdict": "WARN"}, {"verdict": "FAIL"}]) == "FAIL"
    # returncode!=0 (无 verdict) → FAIL
    assert chunkyctl._aggregate_verdict([{"returncode": 1}]) == "FAIL"
    # WARN 带 returncode!=0 仍 WARN (moth 不在 PATH 降级场景)
    assert chunkyctl._aggregate_verdict([{"verdict": "WARN", "returncode": 127}]) == "WARN"


def test_collect_alert_flags_no_flags(tmp_path, monkeypatch):
    """无 ALERT flag → PASS count=0。"""
    monkeypatch.setattr(chunkyctl.glob, "glob", lambda pat: [])
    r = chunkyctl.collect_alert_flags()
    assert r["verdict"] == "PASS" and r["count"] == 0


def test_json_from_stdout():
    assert chunkyctl._json_from_stdout({"stdout": '{"verdict":"PASS"}'}) == {"verdict": "PASS"}
    assert chunkyctl._json_from_stdout({"stdout": "not json"}) is None
    assert chunkyctl._json_from_stdout({"stdout": "[1,2]"}) is None   # 非 dict
