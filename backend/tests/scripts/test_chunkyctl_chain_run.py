"""chunkyctl doctor chain_run 节接线: --run-date 有/无 -> WARN/UNVERIFIED vs 判 R1-R7。

规格: sandbox/patch_loop_review_20260916/fable_review_chain_run_stop_rule.md 任务1 M11
(接线) + M10 后半 (verdict 映射: 给 run-date 时 UNVERIFIED -> FAIL, 不给 -> WARN)。
R1-R7 本体的判定测试在 backend/tests/test_pipeline_chain_run.py。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import chunkyctl  # noqa: E402


def _stub_other_sections(monkeypatch):
    """doctor 的其余 8 节不是本文件要测的东西; 桩成恒 PASS 避免真跑 subprocess/moth。"""
    monkeypatch.setattr(chunkyctl, "_moth_gate", lambda _repo: {"name": "tooling_gate", "verdict": "PASS"})
    monkeypatch.setattr(
        chunkyctl, "audit_automation_surface", lambda _repo: {"name": "automation_surface", "verdict": "PASS", "mode": "manual_only", "findings": []},
    )
    monkeypatch.setattr(chunkyctl, "collect_alert_flags", lambda: {"verdict": "PASS", "count": 0, "flags": []})
    monkeypatch.setattr(
        chunkyctl,
        "_run_command",
        lambda command, **_kwargs: {
            "cmd": command,
            "returncode": 0,
            "stdout": (
                '{"verdict":"PASS","source_count":1,"formal_dataset_count":1,'
                '"scope_counts":{"external_aggregate":1},"live_readiness":"READY"}'
                if any("check_universe_filter.py" in part for part in command)
                else (
                    '{"verdict":"PASS","orphan_feature_blocks":[],'
                    '"orphan_type_b_tables":[],"violations":[],'
                    '"l2_count":1,"l3_count":1,"type_b_count":1}'
                    if any("check_brick_registry.py" in part for part in command)
                    else (
                        '{"gate":"foundation_done","verdict":"PASS",'
                        '"phase_closure_ready":true,"criteria":[],'
                        '"summary":{"PASS":10,"PARTIAL":0,"FAIL":0}}'
                        if any("check_foundation_done.py" in part for part in command)
                        else '{"verdict":"PASS","summary":{"total":1}}'
                    )
                )
            ),
            "stderr": "",
        },
    )


# ── _chain_run_section: unit-level verdict mapping ──────────────────────────

def test_chain_run_section_no_run_date_is_warn_unverified(tmp_path):
    section = chunkyctl._chain_run_section(None, repo=tmp_path)
    assert section["name"] == "chain_run"
    assert section["verdict"] == "WARN"
    assert section["state"] == "UNVERIFIED"


def test_chain_run_section_pass_state_maps_to_pass_verdict(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.pipeline.chain_run.evaluate_chain_run",
        lambda run_date, **_kw: {"date": run_date, "state": "PASS", "failed_rule": None, "reason": None},
    )
    section = chunkyctl._chain_run_section("20260912", repo=tmp_path)
    assert section["verdict"] == "PASS"
    assert section["state"] == "PASS"
    assert "failed_rule" not in section


def test_chain_run_section_fail_state_maps_to_fail_verdict_with_failed_rule(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.pipeline.chain_run.evaluate_chain_run",
        lambda run_date, **_kw: {
            "date": run_date, "state": "FAIL", "failed_rule": "R5", "reason": "run_outcome=hard_fail",
        },
    )
    section = chunkyctl._chain_run_section("20260912", repo=tmp_path)
    assert section["verdict"] == "FAIL"
    assert section["state"] == "FAIL"
    assert section["failed_rule"] == "R5"


def test_chain_run_section_unverified_with_run_date_maps_to_fail_verdict(monkeypatch, tmp_path):
    """M10 后半: 传了 run-date 但判据算不出 -> 聚合按 FAIL (fail-closed), 不是 WARN。"""
    monkeypatch.setattr(
        "services.pipeline.chain_run.evaluate_chain_run",
        lambda run_date, **_kw: {
            "date": run_date, "state": "UNVERIFIED", "failed_rule": "R7", "reason": "frontier lookup failed",
        },
    )
    section = chunkyctl._chain_run_section("20260912", repo=tmp_path)
    assert section["verdict"] == "FAIL"
    assert section["state"] == "UNVERIFIED"


# ── M11: run_doctor wiring ───────────────────────────────────────────────────

def test_run_doctor_includes_chain_run_section_and_reflects_run_date(monkeypatch, tmp_path, capsys):
    _stub_other_sections(monkeypatch)
    monkeypatch.setattr(
        "services.pipeline.chain_run.evaluate_chain_run",
        lambda run_date, **_kw: {"date": run_date, "state": "PASS", "failed_rule": None, "reason": None},
    )

    rc = chunkyctl.run_doctor(argparse.Namespace(repo=str(tmp_path), run_date="20260912"))
    report = json.loads(capsys.readouterr().out)

    names = [section["name"] for section in report["sections"]]
    assert "chain_run" in names
    chain_section = next(s for s in report["sections"] if s["name"] == "chain_run")
    assert chain_section["verdict"] == "PASS"
    assert chain_section["state"] == "PASS"
    assert chain_section["date"] == "20260912"
    assert rc == 0
    assert report["verdict"] == "PASS"


def test_run_doctor_without_run_date_is_unverified_warn_and_does_not_fail_doctor(
    monkeypatch, tmp_path, capsys
):
    _stub_other_sections(monkeypatch)
    # run_date is simply absent from the namespace, matching a real argparse.Namespace
    # built without --run-date (default=None per the doctor subparser).
    rc = chunkyctl.run_doctor(argparse.Namespace(repo=str(tmp_path), run_date=None))
    report = json.loads(capsys.readouterr().out)

    chain_section = next(s for s in report["sections"] if s["name"] == "chain_run")
    assert chain_section["verdict"] == "WARN"
    assert chain_section["state"] == "UNVERIFIED"
    assert rc == 0
    assert report["verdict"] == "WARN"


def test_run_doctor_run_date_given_but_unverified_fails_doctor(monkeypatch, tmp_path, capsys):
    _stub_other_sections(monkeypatch)
    monkeypatch.setattr(
        "services.pipeline.chain_run.evaluate_chain_run",
        lambda run_date, **_kw: {
            "date": run_date, "state": "UNVERIFIED", "failed_rule": "R6",
            "reason": "runtime-check registry unavailable (injected)",
        },
    )
    rc = chunkyctl.run_doctor(argparse.Namespace(repo=str(tmp_path), run_date="20260912"))
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert report["verdict"] == "FAIL"


def test_doctor_argparser_accepts_run_date_flag():
    """CLI 接线: `doctor --run-date YYYYMMDD` 必须真的被解析进 args.run_date。

    不重新声明一份 subparser (会和 chunkyctl.main 里那份各自漂移) —— 直接跑
    main() 的真实解析路径, 用 monkeypatch 截获它传给 run_doctor 的 Namespace。
    """
    ns = _parse_via_main(["doctor", "--run-date", "20260912"])
    assert ns.run_date == "20260912"

    ns_default = _parse_via_main(["doctor"])
    assert ns_default.run_date is None


def _parse_via_main(argv):
    """Reach into chunkyctl.main's own argparse setup without duplicating it."""
    captured = {}

    def _capture(args):
        captured["args"] = args
        return 0

    orig = chunkyctl.run_doctor
    chunkyctl.run_doctor = _capture
    try:
        chunkyctl.main(argv)
    finally:
        chunkyctl.run_doctor = orig
    return captured["args"]
