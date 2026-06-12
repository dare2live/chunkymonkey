"""ops_manual_run router 单测 — 手动任务触发 (2026-06-12 自动调度退役决议)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.ops_manual_run as opsmod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # flag 目录与 job 表全部隔离到 tmp, 不碰生产 /tmp flag
    monkeypatch.setattr(opsmod, "_FLAG_DIR", tmp_path)
    log = tmp_path / "job.log"
    monkeypatch.setattr(
        opsmod,
        "MANUAL_JOBS",
        {
            "demo_job": {
                "argv": ["/bin/echo", "demo"],
                "pattern": "demo_pattern_xyz",
                "log": str(log),
                "extra_flags": [str(tmp_path / "extra.flag")],
                "label": "测试任务",
            }
        },
    )
    app = FastAPI()
    app.include_router(opsmod.router, prefix="/api/v3/ops")
    return TestClient(app), tmp_path, log


def test_list_jobs_idle(client):
    c, tmp, log = client
    r = c.get("/api/v3/ops/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    j = jobs[0]
    assert j["job"] == "demo_job"
    assert j["running"] is False  # pgrep 找不到 demo_pattern_xyz
    assert j["alert_flags"] == {"chunkymonkey_ALERT_demo_job.flag": False, "extra.flag": False}
    assert j["log_tail"] == [] and j["log_mtime"] is None


def test_status_reads_log_and_flags(client):
    c, tmp, log = client
    log.write_text("line1\nline2\n")
    (tmp / "chunkymonkey_ALERT_demo_job.flag").write_text("fail")
    (tmp / "extra.flag").write_text("degraded")
    j = c.get("/api/v3/ops/jobs/demo_job").json()
    assert j["log_tail"] == ["line1", "line2"]
    assert j["alert_flags"]["chunkymonkey_ALERT_demo_job.flag"] is True
    assert j["alert_flags"]["extra.flag"] is True
    assert j["log_mtime"] is not None


def test_unknown_job_404(client):
    c, _, _ = client
    assert c.get("/api/v3/ops/jobs/nope").status_code == 404
    assert c.post("/api/v3/ops/jobs/nope/run").status_code == 404


def test_run_spawns_detached(client, monkeypatch):
    c, _, _ = client
    spawned: dict = {}

    def fake_spawn(job, spec):
        spawned["job"] = job
        return 12345

    monkeypatch.setattr(opsmod, "_spawn", fake_spawn)
    r = c.post("/api/v3/ops/jobs/demo_job/run")
    assert r.status_code == 200
    assert r.json() == {"job": "demo_job", "started": True, "pid": 12345}
    assert spawned["job"] == "demo_job"


def test_run_rejects_when_running(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setattr(opsmod, "_is_running", lambda spec: True)
    r = c.post("/api/v3/ops/jobs/demo_job/run")
    assert r.status_code == 409


def test_spawn_argv_includes_wrapper():
    spec = opsmod.MANUAL_JOBS["daily_update"]
    # wrapper 前缀契约: 告警链 (flag + 通知) 依赖 launchd_job_wrapper, 手动触发不许绕开
    assert str(opsmod._WRAPPER).endswith("scripts/launchd_job_wrapper.py")
    assert spec["argv"][0] == "/bin/bash"
    assert spec["argv"][1].endswith("scripts/daily_update.sh")


def test_production_registry_shape():
    # 生产注册表契约: 每个 job 必须含五要素 (前端面板渲染依赖)
    for job, spec in opsmod.MANUAL_JOBS.items():
        for key in ("argv", "pattern", "log", "extra_flags", "label"):
            assert key in spec, f"{job} 缺 {key}"
        assert isinstance(spec["argv"], list) and spec["argv"]
