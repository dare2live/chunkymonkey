"""backend/services/pipeline/chain_run.py — doctor 的 chain_run 节 (R1-R7)。

规格: sandbox/patch_loop_review_20260916/fable_review_chain_run_stop_rule.md 任务1.
基线 + M1-M10: 每条判据一条「其它条件全满足、只违反它」的隔离用例, 逐条变异
(改坏生产代码里对应那一处, 看红的是不是该用例) 记在提交信息 assertions 里，本文件
只锁行为。frontier/calendar/registry 全部注入，不开任何真实 DB。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from services.governance_gates import load_registry
from services.pipeline import chain_run
from services.pipeline.evidence_paths import load_pipeline_evidence_paths

BASE_DATE = "20260912"
BASE_RUN_AT = "2026-09-12T15:37:39+00:00"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _flag_parent(tmp_path: Path) -> Path:
    return tmp_path / "tmp-alert-dir"


def _baseline(tmp_path: Path, *, date: str = BASE_DATE) -> dict:
    """R1-R7 全满足的最小证据集: 1 份 daily 报告 + 8 个 sibling (2 watermark + 6 runtime-check)。"""
    paths = load_pipeline_evidence_paths()
    flag_parent = _flag_parent(tmp_path)
    report_path = tmp_path / paths.daily_report_rel(date=date)
    _write_json(
        report_path,
        {
            "date": date,
            "dry_run": 0,
            "skip_sync": 0,
            "log": str(flag_parent / f"chunkymonkey_daily_update_{date}.log"),
            "run_outcome": "success",
        },
    )
    sla_path = tmp_path / paths.watermark_sla_rel(date=date)
    _write_json(sla_path, {"run_at": BASE_RUN_AT, "n_alerts": 0, "sources": []})
    before_path = tmp_path / paths.watermark_sla_before_rel(date=date)
    _write_json(before_path, {"n_alerts": 0, "sources": []})

    registry = load_registry()
    sibling_rel = [
        paths.watermark_sla_before_rel(date=date),
        paths.watermark_sla_rel(date=date),
        *chain_run.runtime_check_json_out_paths(registry, date=date),
    ]
    for rel in sibling_rel[2:]:
        _write_json(tmp_path / rel, {"overall": "PASS"})

    return {
        "paths": paths,
        "flag_parent": flag_parent,
        "registry": registry,
        "report_path": report_path,
        "sibling_rel": sibling_rel,
    }


def _evaluate(tmp_path: Path, ctx: dict, *, date: str = BASE_DATE, frontier=None, calendar=None,
              registry_loader=None):
    return chain_run.evaluate_chain_run(
        date,
        repo=tmp_path,
        degraded_flag_parent=ctx["flag_parent"],
        frontier_fn=frontier or (lambda: "20260912"),
        calendar_fn=calendar or (lambda now: "2026-09-12"),
        registry_loader=registry_loader,
    )


# ── baseline ─────────────────────────────────────────────────────────────────

def test_baseline_all_seven_rules_pass(tmp_path):
    ctx = _baseline(tmp_path)
    result = _evaluate(tmp_path, ctx)
    assert result == {
        "date": BASE_DATE,
        "state": "PASS",
        "failed_rule": None,
        "reason": None,
    }


# ── R1: report exists, parses, content date == D ────────────────────────────

def test_m1_report_file_missing_fails_r1(tmp_path):
    ctx = _baseline(tmp_path)
    ctx["report_path"].unlink()
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R1"


def test_m2_report_content_date_mismatch_fails_r1(tmp_path):
    """改名骗: 文件名仍是 daily_20260912.json, 内容 date 却是 20260911。"""
    ctx = _baseline(tmp_path)
    payload = json.loads(ctx["report_path"].read_text())
    payload["date"] = "20260911"
    ctx["report_path"].write_text(json.dumps(payload))
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R1"


def test_r1_not_json_fails_r1(tmp_path):
    ctx = _baseline(tmp_path)
    ctx["report_path"].write_text("not json at all")
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R1"


# ── R2: dry_run == 0 ─────────────────────────────────────────────────────────

def test_m3_dry_run_one_fails_r2(tmp_path):
    ctx = _baseline(tmp_path)
    payload = json.loads(ctx["report_path"].read_text())
    payload["dry_run"] = 1
    ctx["report_path"].write_text(json.dumps(payload))
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R2"


# ── R3: log path signature ───────────────────────────────────────────────────

def test_m4_log_points_at_pytest_path_fails_r3(tmp_path):
    ctx = _baseline(tmp_path)
    payload = json.loads(ctx["report_path"].read_text())
    payload["log"] = str(tmp_path / "some" / "pytest" / "tmp" / "run.log")
    ctx["report_path"].write_text(json.dumps(payload))
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R3"


def test_r3_end_to_end_context_default_log_matches_evidence_paths_signature(
    monkeypatch, tmp_path
):
    """生产写日志 (context.py __post_init__ 默认路径) 与 R3 校验签名必须对得上。

    本用例走真实 PipelineContext (不传 log_path, 走默认计算) + 真实
    store.write_report_and_alert + 真实 evaluate_chain_run, 不手写任何路径字符串。
    ctx 的 dry/skip_sync 全默认 (=0), run_outcome 因 degraded_msgs 为空而是 success,
    所以 R1/R2/R4/R5 必然全过 —— 若仍卡在 R3, 说明写日志与校验签名两处已不是同一来源;
    卡在 R6 (D 日证据文件本来就没造齐) 才是这条用例的正常结局。
    """
    from services.pipeline import store as pipeline_store
    from services.pipeline.context import PipelineContext

    repo = tmp_path / "repo"
    monkeypatch.setattr(pipeline_store, "REPO", repo)

    date = "20260915"
    # 不传 log_path —— conftest._isolate_alert_flags 已把 context.DEGRADED_FLAG 隔离到
    # 本用例自己的 tmp_path, __post_init__ 会用它的 .parent 算出默认日志路径。
    ctx = PipelineContext(date=date)
    try:
        pipeline_store.write_report_and_alert(ctx)
    finally:
        ctx.close()

    result = chain_run.evaluate_chain_run(date, repo=repo)
    assert result["failed_rule"] not in ("R1", "R2", "R3"), (
        "context.py 硬编码的日志文件名字面量与 pipeline_evidence_paths.yaml 的 "
        f"daily_update_log_name_template 已经漂移: {result}"
    )


# ── R4: skip_sync == 0 (missing key fails closed) ───────────────────────────

def test_m5a_skip_sync_key_missing_fails_r4(tmp_path):
    ctx = _baseline(tmp_path)
    payload = json.loads(ctx["report_path"].read_text())
    del payload["skip_sync"]
    ctx["report_path"].write_text(json.dumps(payload))
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R4"


def test_m5b_skip_sync_one_fails_r4(tmp_path):
    ctx = _baseline(tmp_path)
    payload = json.loads(ctx["report_path"].read_text())
    payload["skip_sync"] = 1
    ctx["report_path"].write_text(json.dumps(payload))
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R4"


def test_r4_end_to_end_store_writes_and_chain_run_reads_skip_sync(monkeypatch, tmp_path):
    """把「store.py 真的写 skip_sync 这个键」和「chain_run R4 真的读这个键」焊死。

    上面 M5a/M5b 两条 R4 隔离用例全部走 _baseline() 手写合成 JSON, 从未真正调用
    ``store.write_report_and_alert`` —— 生产代码与 R4 判据之间缺一条端到端连线。
    本用例改用真实实现产出报告 (不 mock skip_sync 键本身), 分别覆盖 skip_sync=True
    /False 两个值: skip_sync=True 时 R4 必须真的 FAIL, skip_sync=False 时七条判据
    (含 R4) 必须真的全过 PASS。删掉 store.py 里 ``"skip_sync": int(ctx.skip_sync)``
    那一行, 本用例的 skip_sync=False 分支必须变红 (键缺失 -> R4 fail-closed)。
    """
    from services.pipeline import store as pipeline_store
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(pipeline_store, "REPO", tmp_path)

    for skip_sync, date in ((True, "20260913"), (False, "20260914")):
        ctx = _baseline(tmp_path, date=date)
        flag_parent = ctx["flag_parent"]
        flag_parent.mkdir(parents=True, exist_ok=True)
        run_ctx = PipelineContext(
            dry=False,
            skip_sync=skip_sync,
            date=date,
            log_path=flag_parent / f"chunkymonkey_daily_update_{date}.log",
        )
        try:
            written = pipeline_store.write_report_and_alert(run_ctx)
        finally:
            run_ctx.close()

        assert written["skip_sync"] == int(skip_sync)
        on_disk = json.loads(ctx["report_path"].read_text())
        assert on_disk["skip_sync"] == int(skip_sync)

        result = _evaluate(tmp_path, ctx, date=date)
        if skip_sync:
            assert result["state"] == "FAIL"
            assert result["failed_rule"] == "R4"
        else:
            assert result == {
                "date": date,
                "state": "PASS",
                "failed_rule": None,
                "reason": None,
            }


# ── R5: run_outcome == success ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "run_outcome", ["soft_waiting_clock", "integrity_observe", "hard_fail", "ok"]
)
def test_m6_non_success_run_outcome_fails_r5(tmp_path, run_outcome):
    ctx = _baseline(tmp_path)
    payload = json.loads(ctx["report_path"].read_text())
    payload["run_outcome"] = run_outcome
    ctx["report_path"].write_text(json.dumps(payload))
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R5"


# ── R6: D-day evidence entities all exist ───────────────────────────────────

def test_m7_each_of_eight_siblings_missing_fails_r6(tmp_path):
    """参数化删 8 个 sibling 各一; 每次只删一个, 其余 7 个仍在。"""
    ctx = _baseline(tmp_path)
    for rel in ctx["sibling_rel"]:
        # rebuild a fresh baseline each iteration so exactly one sibling is missing
        fresh_tmp = tmp_path / f"case-{rel.replace('/', '_')}"
        fresh_ctx = _baseline(fresh_tmp)
        target = fresh_tmp / rel
        assert target.is_file()
        target.unlink()
        result = _evaluate(fresh_tmp, fresh_ctx)
        assert result["state"] == "FAIL", f"sibling {rel} missing should FAIL"
        assert result["failed_rule"] == "R6", f"sibling {rel} missing should be R6"


def test_r6_registry_unavailable_is_unverified(tmp_path):
    ctx = _baseline(tmp_path)

    def _boom():
        from services.governance_gates import GatePolicyError

        raise GatePolicyError("registry unavailable (injected)")

    result = _evaluate(tmp_path, ctx, registry_loader=_boom)
    assert result["state"] == "UNVERIFIED"
    assert result["failed_rule"] == "R6"


# ── R7: accepted frontier >= expected trade date as of run_at ───────────────

def test_m8a_frontier_behind_expected_fails_r7(tmp_path):
    ctx = _baseline(tmp_path)
    result = _evaluate(tmp_path, ctx, frontier=lambda: "20260911", calendar=lambda now: "2026-09-12")
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R7"


def test_m8b_frontier_ahead_of_expected_passes(tmp_path):
    """后来的运行推高了前沿 —— actual 领先 expected 仍然 PASS。"""
    ctx = _baseline(tmp_path)
    result = _evaluate(tmp_path, ctx, frontier=lambda: "20260915", calendar=lambda now: "2026-09-12")
    assert result["state"] == "PASS"


def test_m10_frontier_exception_is_unverified_r7(tmp_path):
    ctx = _baseline(tmp_path)

    def _boom():
        raise RuntimeError("db unreachable (injected)")

    result = _evaluate(tmp_path, ctx, frontier=_boom)
    assert result["state"] == "UNVERIFIED"
    assert result["failed_rule"] == "R7"


def test_r7_calendar_exception_is_unverified(tmp_path):
    ctx = _baseline(tmp_path)

    def _boom(now):
        raise RuntimeError("calendar unreachable (injected)")

    result = _evaluate(tmp_path, ctx, calendar=_boom)
    assert result["state"] == "UNVERIFIED"
    assert result["failed_rule"] == "R7"


def test_r7_run_at_unreadable_is_unverified(tmp_path):
    ctx = _baseline(tmp_path)
    sla_path = tmp_path / ctx["paths"].watermark_sla_rel(date=BASE_DATE)
    payload = json.loads(sla_path.read_text())
    del payload["run_at"]
    sla_path.write_text(json.dumps(payload))
    result = _evaluate(tmp_path, ctx)
    assert result["state"] == "UNVERIFIED"
    assert result["failed_rule"] == "R7"


# ── M9: selection is by filename D, never by mtime ──────────────────────────

def test_m9_stray_newer_mtime_report_does_not_hijack_selection(tmp_path):
    ctx = _baseline(tmp_path)
    stray_date = "20260101"
    stray_path = tmp_path / ctx["paths"].daily_report_rel(date=stray_date)
    _write_json(
        stray_path,
        {
            "date": stray_date,
            "dry_run": 1,
            "skip_sync": 0,
            "log": str(ctx["flag_parent"] / f"chunkymonkey_daily_update_{stray_date}.log"),
            "run_outcome": "hard_fail",
        },
    )
    # Force the stray file to have a strictly newer mtime than the real D=20260912 report,
    # regardless of filesystem timestamp resolution.
    real_stat = ctx["report_path"].stat()
    newer = real_stat.st_mtime + 3600
    os.utime(stray_path, (newer, newer))
    assert stray_path.stat().st_mtime > ctx["report_path"].stat().st_mtime

    # D=20260912 must still PASS: selection reads the filename-addressed file, not "latest mtime".
    result_d = _evaluate(tmp_path, ctx)
    assert result_d["state"] == "PASS"

    # D=20260101 must FAIL/R2 on its own (dry_run=1) content, proving the stray file
    # really was read when explicitly addressed by date -- not ignored altogether.
    result_stray = _evaluate(tmp_path, ctx, date=stray_date)
    assert result_stray["state"] == "FAIL"
    assert result_stray["failed_rule"] == "R2"


def test_runtime_check_json_out_paths_extracts_exactly_six(tmp_path):
    registry = load_registry()
    rels = chain_run.runtime_check_json_out_paths(registry, date=BASE_DATE)
    assert len(rels) == 6
    assert all(BASE_DATE in rel for rel in rels)
    assert all(rel.startswith("data/audit/") for rel in rels)
