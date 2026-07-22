"""manual_job_wrapper soft-degrade notification coalesce."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[3] / "scripts" / "manual_job_wrapper.py"


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("manual_job_wrapper", WRAPPER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_tmp_paths(mjw, tmp_path, monkeypatch):
    real_path = mjw.Path

    def path_factory(*args, **kwargs):
        p = real_path(*args, **kwargs)
        if str(p).startswith("/tmp/chunkymonkey_"):
            return tmp_path / p.name
        return p

    monkeypatch.setattr(mjw, "Path", path_factory)


def test_soft_degrade_skips_fail_notification(tmp_path, monkeypatch):
    """rc=1 (soft_waiting_clock) → no osascript FAIL banner; ALERT flag still written."""
    mjw = _load_wrapper()
    osascript_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        cmd = list(cmd)
        if cmd and Path(cmd[0]).name == "osascript":
            osascript_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(mjw.subprocess, "run", fake_run)
    _patch_tmp_paths(mjw, tmp_path, monkeypatch)
    # Isolate from any live data/reports/daily_*.json (stale soft must not matter).
    monkeypatch.setattr(mjw, "_load_run_outcome", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", ["manual_job_wrapper.py", "daily_update", "true"])
    assert mjw.main() == 1
    assert (tmp_path / "chunkymonkey_ALERT_daily_update.flag").exists()
    assert osascript_calls == []
    log_text = (tmp_path / "chunkymonkey_daily_update.log").read_text(encoding="utf-8")
    assert "soft_waiting_clock: skip FAIL notification" in log_text


def test_hard_fail_still_notifies(tmp_path, monkeypatch):
    """rc=5 (tier0 hard block) still fires job FAIL notification."""
    mjw = _load_wrapper()
    osascript_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        cmd = list(cmd)
        if cmd and Path(cmd[0]).name == "osascript":
            osascript_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 5)

    monkeypatch.setattr(mjw.subprocess, "run", fake_run)
    _patch_tmp_paths(mjw, tmp_path, monkeypatch)
    # Stale soft report (exit_code=1) must not suppress hard rc=5 FAIL banner.
    monkeypatch.setattr(
        mjw,
        "_load_run_outcome",
        lambda *_a, **_k: {
            "run_outcome": "soft_waiting_clock",
            "run_outcome_label": "等时钟 / 软观测",
            "report_path": "/tmp/stale.json",
            "exit_code": 1,
        },
    )
    monkeypatch.setattr(sys, "argv", ["manual_job_wrapper.py", "daily_update", "true"])
    assert mjw.main() == 5
    assert len(osascript_calls) == 1
    assert "job FAIL" in osascript_calls[0][2]


def test_matching_report_outcome_skips_fail(tmp_path, monkeypatch):
    """Report run_outcome_exit_code==rc → outcome-keyed soft skip."""
    mjw = _load_wrapper()
    osascript_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        cmd = list(cmd)
        if cmd and Path(cmd[0]).name == "osascript":
            osascript_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(mjw.subprocess, "run", fake_run)
    _patch_tmp_paths(mjw, tmp_path, monkeypatch)
    monkeypatch.setattr(
        mjw,
        "_load_run_outcome",
        lambda *_a, **_k: {
            "run_outcome": "soft_waiting_clock",
            "run_outcome_label": "等时钟 / 软观测",
            "report_path": str(tmp_path / "daily_20260722.json"),
            "exit_code": 1,
        },
    )
    monkeypatch.setattr(sys, "argv", ["manual_job_wrapper.py", "daily_update", "true"])
    assert mjw.main() == 1
    assert osascript_calls == []
    log_text = (tmp_path / "chunkymonkey_daily_update.log").read_text(encoding="utf-8")
    assert "soft_waiting_clock: skip FAIL notification" in log_text
    assert "report=" in log_text
