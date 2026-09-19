"""backend/services/pipeline/chain_run.py — doctor 的 chain_run 节 (R1-R7)。

规格: sandbox/patch_loop_review_20260916/fable_review_chain_run_stop_rule.md 任务1;
sandbox/acceptance_cuts_20260918/spec_daily_availability.md (A1-A5, R7 期望日改按
daily 域自己声明的可用时刻算, 不再是写侧 15:05 口径)。
基线 + M1-M10 + A1-A5: 每条判据一条「其它条件全满足、只违反它」的隔离用例, 逐条变异
(改坏生产代码里对应那一处, 看红的是不是该用例) 记在提交信息 assertions 里，本文件
只锁行为。frontier/calendar/registry 全部注入，不开任何真实 DB。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
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


def test_runtime_check_json_out_paths_extracts_all_json_out_checks(tmp_path):
    """09-18 tier12 整层退役 (cut_tier12_retire): cutover_effective 那条 runtime_check
    连同其 --json-out 一并从登记表删除，清单自动少一条 —— 不许把这个数钉成常量
    (memory: scalar-cannot-represent-set-completeness)，改比对登记表自己数出的期望数。
    """
    registry = load_registry()
    rels = chain_run.runtime_check_json_out_paths(registry, date=BASE_DATE)
    expected = sum(1 for c in registry.runtime_checks if "--json-out" in c.args)
    assert len(rels) == expected
    assert expected > 0
    assert all(BASE_DATE in rel for rel in rels)
    assert all(rel.startswith("data/audit/") for rel in rels)


# ── A1-A5: R7 期望日改按 daily 域自己声明的可用时刻算 ───────────────────────────
# spec_daily_availability.md。可用时刻不再是写侧 15:05 口径 (那条问「bar 是否收盘
# 定稿」)，而是该域契约声明的可见时刻，从 sync_registry 该域条目的 available_after 读。


def _fake_calendar_lookup(trading_days: set[str]):
    """A1/A2 用的假日历查询函数：与 services.calendar.latest_completed_trade_date 同一
    契约 ((run_at, close_hour, close_minute) -> 'YYYYMMDD' | None)，但只查内存里的
    ``trading_days`` 集合，不开任何真实 DB。"""

    def _lookup(run_at, close_hour, close_minute):
        anchor = run_at.date()
        if (run_at.hour, run_at.minute) < (close_hour, close_minute):
            anchor -= timedelta(days=1)
        candidates = [d for d in trading_days if d <= anchor.strftime("%Y%m%d")]
        return max(candidates) if candidates else None

    return _lookup


# A1 — 可用时刻 17:30 边界：其它一切满足，只在 run_at 越过 17:30 那一刻切换。
def test_a1_availability_boundary_17_29_before_17_30_at(tmp_path):
    lookup = _fake_calendar_lookup({"20260917", "20260918"})
    before = chain_run._expected_trade_date_for_availability(
        datetime(2026, 9, 18, 17, 29), (17, 30), calendar_lookup=lookup
    )
    at = chain_run._expected_trade_date_for_availability(
        datetime(2026, 9, 18, 17, 30), (17, 30), calendar_lookup=lookup
    )
    assert before == "20260917"
    assert at == "20260918"


# A2 — 可用时刻从 sync_registry 读，不是字面量：假注册表把 daily 的 available_after
# 改成 16:00，run_at 16:30 (18:00/17:30 都不满足但 16:00 满足) 应期望当天。
def test_a2_available_after_read_from_registry_not_literal(tmp_path):
    fake_registry = {"domains": {"daily": {"available_after": "16:00"}}}
    parsed = chain_run._registered_available_after(registry_loader=lambda: fake_registry)
    assert parsed == (16, 0)

    lookup = _fake_calendar_lookup({"20260918"})
    result = chain_run._expected_trade_date_for_availability(
        datetime(2026, 9, 18, 16, 30), parsed, calendar_lookup=lookup
    )
    assert result == "20260918"


# A3 — 可用时刻不是 HH:MM (t+1 / 缺键) -> UNVERIFIED，reason 含 available_after，
# 不得 PASS 也不得 FAIL。先隔离测纯函数直接抛的异常，再端到端走 evaluate_chain_run
# 的 R7 确认真落到 UNVERIFIED。
@pytest.mark.parametrize("bad_available_after", [None, "t+1"])
def test_a3_non_hhmm_available_after_raises_mentioning_available_after(bad_available_after):
    lookup = _fake_calendar_lookup({"20260918"})
    with pytest.raises(chain_run.AvailabilityUnverified) as exc_info:
        chain_run._expected_trade_date_for_availability(
            datetime(2026, 9, 18, 20, 0), bad_available_after, calendar_lookup=lookup
        )
    assert "available_after" in str(exc_info.value)


def test_a3_missing_available_after_key_parses_to_none():
    """缺键 (registry 条目根本没有 available_after) 解析结果与 't+1' 同归 None 一侧。"""
    fake_registry = {"domains": {"daily": {}}}
    parsed = chain_run._registered_available_after(registry_loader=lambda: fake_registry)
    assert parsed is None


def test_a3_r7_unverified_end_to_end_when_available_after_not_hhmm(tmp_path):
    ctx = _baseline(tmp_path)
    lookup = _fake_calendar_lookup({BASE_DATE})

    def _bad_calendar(run_at):
        return chain_run._expected_trade_date_for_availability(
            run_at, "t+1", calendar_lookup=lookup
        )

    result = _evaluate(tmp_path, ctx, calendar=_bad_calendar)
    assert result["state"] == "UNVERIFIED"
    assert result["failed_rule"] == "R7"
    assert "available_after" in result["reason"]


# A4 — 真配置：唯一允许读真实 sync_registry.yaml 的用例。只断言不变量 (两份副本
# 相等且都是可解析的 HH:MM), 不把 17:30 这个取值钉成常量 —— 取值随实测调整时只改
# 配置一处。
def test_a4_real_registry_daily_availability_copies_agree_and_parse():
    from services.data_sources.nominal_ohlcv_schema import DOMAIN
    from services.data_sources.sync_runner import _parse_available_after, domain_spec
    from services.data_sources.sync_runner import load_registry as load_sync_registry

    spec = domain_spec(load_sync_registry(), DOMAIN.domain)
    assert spec["available_after"] == spec["availability_policy"]["at"]
    assert isinstance(_parse_available_after(spec), tuple)
    assert chain_run._registered_available_after() == _parse_available_after(spec)


# A1b — 默认日历函数的接线: 注册表声明的时分原样传给 services.calendar 的
# latest_completed_trade_date (A1 只证明纯函数把参数传给注入的查询函数; 这一条证明
# 生产默认实现传的是注册表的值, 且 now 就是 run_at)。
def test_a1b_default_calendar_fn_passes_registered_hhmm(monkeypatch):
    import services.calendar as calendar_mod

    seen = {}

    def _capture(*, now, close_hour, close_minute):
        seen.update(now=now, close_hour=close_hour, close_minute=close_minute)
        return "20260918"

    monkeypatch.setattr(chain_run, "_registered_available_after", lambda: (16, 5))
    monkeypatch.setattr(calendar_mod, "latest_completed_trade_date", _capture)
    run_at = datetime(2026, 9, 18, 16, 6)
    assert chain_run._default_calendar_fn(run_at) == "20260918"
    assert seen == {"now": run_at, "close_hour": 16, "close_minute": 5}


# A5 — chain_run 其余 R1-R6 行为不变: 由本文件其余既有用例(M1-M10 等)覆盖，本条只
# 确认 _default_calendar_fn 替换后仍然只在 R7 生效、不改变 evaluate_chain_run 的签名
# 与 R1-R6 分支 (baseline 走既有 M1-M10 用例即是回归锁)。
