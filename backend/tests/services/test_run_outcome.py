"""Typed run_outcome single compute point (Phase 1 treadmill fix)."""
from __future__ import annotations

import json

from services.pipeline.run_outcome import (
    OUTCOME_HARD_FAIL,
    OUTCOME_SOFT_WAITING,
    OUTCOME_SUCCESS,
    classify_msg,
    derive_run_outcome,
)
from services.notification import dispatcher as disp


def test_classify_soft_clock_patterns():
    assert classify_msg("domain daily pending_publish reason=pre_available_after_zero_rows") == "soft"
    assert classify_msg("pending_publish_reason=same_day_vendor_vacuum") == "soft"
    assert classify_msg("sync_registry drain 有残余缺口或域错误 (见 log)") == "soft"
    assert classify_msg("still_failed=['20260722'] vacuum") == "soft"


def test_classify_hard_blocks():
    assert classify_msg("AUTH BLOCK: token_expired (四阶段未启动; exit 3)") == "hard"
    assert classify_msg("PREFLIGHT BLOCK: margin:scope_blocked") == "hard"
    assert classify_msg("TIER0 BLOCK during acquire: formal stock_st") == "hard"
    assert classify_msg("WRITER BLOCK: busy owner=pipeline.run") == "hard"


def test_classify_other_ops_degraded():
    assert classify_msg("continuity/integrity 审查 FAIL — 库存断流") == "other"
    assert classify_msg("post-acquire watermark SLA alert (见 data/audit/...)") == "other"


def test_rollup_soft_named_and_other_is_soft_waiting():
    info = derive_run_outcome(
        [
            "sync_registry drain 有残余缺口或域错误 (见 log)",
            "continuity/integrity 审查 FAIL — 库存",
            "post-acquire watermark SLA alert (见 x)",
        ]
    )
    assert info["run_outcome"] == OUTCOME_SOFT_WAITING
    assert info["exit_code"] == 1
    assert info["run_outcome_reason"] == "soft_waiting_clock_with_ops_observe"


def test_rollup_other_only_still_soft_not_fail():
    """Continuity/SLA alone must not paint FAIL (adversarial Phase 1 call)."""
    info = derive_run_outcome(["continuity/integrity 审查 FAIL"])
    assert info["run_outcome"] == OUTCOME_SOFT_WAITING
    assert info["exit_code"] == 1
    assert info["run_outcome_reason"] == "ops_observe_non_hard_degraded"


def test_rollup_hard_beats_soft():
    info = derive_run_outcome(
        [
            "pending_publish daily",
            "TIER0 BLOCK during acquire: zero_rows kidnap",
        ]
    )
    assert info["run_outcome"] == OUTCOME_HARD_FAIL
    assert info["exit_code"] == 5
    assert info["run_outcome_reason"] == "hard_tier0"


def test_rollup_clean_success():
    info = derive_run_outcome([])
    assert info["run_outcome"] == OUTCOME_SUCCESS
    assert info["exit_code"] == 0


def test_hard_exit_code_without_msg_text():
    info = derive_run_outcome([], hard_exit_code=2)
    assert info["run_outcome"] == OUTCOME_HARD_FAIL
    assert info["exit_code"] == 2
    assert info["run_outcome_reason"] == "hard_writer"


def test_preflight_auth_exit_codes():
    assert derive_run_outcome(
        ["AUTH BLOCK: denied"], hard_exit_code=3
    )["exit_code"] == 3
    assert derive_run_outcome(
        ["PREFLIGHT BLOCK: calendar"], hard_exit_code=4
    )["exit_code"] == 4


def test_dispatcher_outcome_keyed_skips_macos_for_soft(tmp_path, monkeypatch):
    report = tmp_path / "daily_20260722.json"
    report.write_text(
        json.dumps(
            {
                "date": "20260722",
                "run_outcome": OUTCOME_SOFT_WAITING,
                "sla_warn": True,
                "alert_flags": {"sla_warn": True},
                "sla_summary": {"n_alerts": 1, "stale_sources": ["tushare"]},
            }
        ),
        encoding="utf-8",
    )
    sent: list[str] = []

    class FakeMacOS:
        def __init__(self, config=None):
            self.config = config or {"enabled": True}

        @property
        def enabled(self):
            return bool(self.config.get("enabled", True))

        def send(self, subject, body, severity):
            sent.append("macos")
            return True

    class FakeEmail:
        def __init__(self, config=None):
            self.enabled = True

        def send(self, subject, body, severity):
            sent.append("email")
            return True

    monkeypatch.setattr(disp, "MacOSDriver", FakeMacOS)
    monkeypatch.setattr(disp, "EmailDriver", FakeEmail)
    monkeypatch.setattr(
        disp,
        "load_trigger_config",
        lambda _=None: {
            "default_channels": ["email", "macos"],
            "conditions": {
                "sla_warn": {"enabled": True, "severity": "warn", "channels": ["email", "macos"]},
                "kpi_anomaly": {"enabled": False},
                "leakage_red": {"enabled": False},
            },
        },
    )

    assert disp.dispatch_report(report) is True
    assert "email" in sent
    assert "macos" not in sent


def test_dispatcher_success_silent(tmp_path, monkeypatch):
    report = tmp_path / "daily_ok.json"
    report.write_text(
        json.dumps(
            {
                "date": "20260722",
                "run_outcome": OUTCOME_SUCCESS,
                "sla_warn": True,
                "alert_flags": {"sla_warn": True},
            }
        ),
        encoding="utf-8",
    )
    sent: list[str] = []

    class FakeMacOS:
        def __init__(self, config=None):
            self.config = config or {"enabled": True}

        @property
        def enabled(self):
            return bool(self.config.get("enabled", True))

        def send(self, subject, body, severity):
            sent.append("macos")
            return True

    monkeypatch.setattr(disp, "MacOSDriver", FakeMacOS)
    assert disp.dispatch_report(report) is False
    assert sent == []


def test_write_report_includes_run_outcome(tmp_path, monkeypatch):
    from services.pipeline import store as store_mod
    from services.pipeline.context import PipelineContext
    import services.pipeline.context as ctx_mod

    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    monkeypatch.setattr(store_mod, "REPO", tmp_path)
    monkeypatch.setattr(ctx_mod, "REPO", tmp_path)
    monkeypatch.setattr(ctx_mod, "DEGRADED_FLAG", tmp_path / "degraded.flag")

    ctx = PipelineContext(date="20260722", dry=True, skip_sync=True)
    ctx.degraded_msgs.append("pending_publish daily pre_available_after_zero_rows")
    ctx.degraded_msgs.append("continuity/integrity 审查 FAIL")

    # Avoid real osascript / dispatcher subprocess in unit test.
    monkeypatch.setattr(store_mod.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    out = store_mod.write_report_and_alert(ctx)
    path = tmp_path / "data" / "reports" / "daily_20260722.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["run_outcome"] == OUTCOME_SOFT_WAITING
    assert data["run_outcome_exit_code"] == 1
    assert out["run_outcome"] == OUTCOME_SOFT_WAITING
    ctx.close()
