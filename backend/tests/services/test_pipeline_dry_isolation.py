"""cut_dry_isolation_20260926 — dry-run 不碰真实运行的证据/告警/通知。

目的卡: sandbox/cut_dry_isolation_20260926/card.md (版本 1，冻结)。

覆盖 A2-A5 (executor=workflow; A1 是 executor=main_loop, 只能主循环在真实仓库上跑
`bash scripts/daily_update.sh --dry --date 20260925`, 本文件不模拟) 与 K1-K6 每条一个
隔离用例 (其它条件全满足只它为假)。K 的点名变异补丁存
`sandbox/cut_dry_isolation_20260926/mutations/`，由外部脚本 git apply 重放并核对
red_node/red_reason，见卡 §2 与 acceptance_standard.md §4.2。

夹具规则 (§4.3): 不开生产库、不联网；本文件自己的 autouse fixture 把
services.pipeline.{context,store,run}.REPO 与 context.DEGRADED_FLAG 全部隔离到
tmp_path，比 test_pipeline.py 的既有 fixture 更严格地统一三处 REPO —— dry_path()
不持有模块级 REPO (K1/A2 的核心不变量)，调用点用哪个 REPO 决定证据落在哪，三处不一致
会让"真实路径不受影响"这条断言失去意义。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

DATE = "20260925"


@pytest.fixture(autouse=True)
def _isolated_pipeline_runtime_paths(tmp_path, monkeypatch):
    from services.pipeline import context, run as run_mod, store
    from services.writer_lock import WRITER_LOCK_PATH_ENV

    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "pipeline-writer.lock"))
    monkeypatch.setattr(context, "DEGRADED_FLAG", tmp_path / "pipeline-alert.flag")
    monkeypatch.setattr(context, "REPO", tmp_path)
    monkeypatch.setattr(store, "REPO", tmp_path)
    monkeypatch.setattr(run_mod, "REPO", tmp_path)


def _dry_root(tmp_path: Path, date: str = DATE) -> Path:
    """字面量算出的 dry 根 —— 不经 evidence_paths 新代码计算 (§1.2 第1条)。"""
    return tmp_path / "data" / "scratch" / "dry_run" / date


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── A2: dry 路径改写 —— 报告/SLA前后/runtime check参数/日志/写者锁报告，字面量预期 ──


def test_a2_dry_path_does_not_hold_module_repo(tmp_path):
    """dry_path() 不持有/不 import 任何模块级 REPO —— 换一个 repo 参数结果跟着换。"""
    from services.pipeline.evidence_paths import dry_path

    other_repo = tmp_path / "another_repo"
    a = dry_path("data/reports/daily_20260925.json", repo=tmp_path, date=DATE)
    b = dry_path("data/reports/daily_20260925.json", repo=other_repo, date=DATE)
    assert a.is_relative_to(tmp_path)
    assert b.is_relative_to(other_repo)
    assert a != b


def test_a2_report_and_sla_paths_dry_false_are_literal(tmp_path, monkeypatch):
    """dry=False: 报告/SLA before/SLA after 落在与本刀之前逐字节相同的字面量路径。"""
    from services.pipeline.context import PipelineContext
    from services.pipeline.store import write_report_and_alert

    ctx = PipelineContext(dry=False, skip_sync=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        write_report_and_alert(ctx)
    finally:
        ctx.close()

    # 字面量预期 (与判据不共用 loader 代码路径, §1.2 第1条)。
    assert (tmp_path / "data/reports/daily_20260925.json").is_file()
    report = json.loads((tmp_path / "data/reports/daily_20260925.json").read_text())
    assert report["dry_run"] == 0
    # before/after 本次未产出 (没调 preflight/run_watermark_sla_check), phase_status 反映 ERR,
    # 但路径本身若产出也必须是这两个字面量 —— 用 sla_evidence 字段核对签名。
    assert report["sla_evidence"]["preflight"] == str(
        tmp_path / "data/audit/watermark_sla_before_20260925.json"
    )
    assert report["sla_evidence"]["post_acquire"] == str(
        tmp_path / "data/audit/watermark_sla_20260925.json"
    )


def test_a2_report_and_sla_paths_dry_true_under_dry_root(tmp_path, monkeypatch):
    """dry=True: 同三条证据全部落 <tmp REPO>/data/scratch/dry_run/<D>/ 下。"""
    from services.pipeline.context import PipelineContext
    from services.pipeline.store import write_report_and_alert

    ctx = PipelineContext(dry=True, skip_sync=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        output = write_report_and_alert(ctx)
    finally:
        ctx.close()

    dry_root = _dry_root(tmp_path)
    report_path = dry_root / "data/reports/daily_20260925.json"
    assert report_path.is_file()
    assert output["dry_run"] == 1
    assert Path(output["sla_evidence"]["preflight"]).is_relative_to(dry_root)
    assert Path(output["sla_evidence"]["post_acquire"]).is_relative_to(dry_root)
    # 真实路径完全不存在 —— dry 从没写过它们。
    assert not (tmp_path / "data/reports/daily_20260925.json").exists()
    assert not (tmp_path / "data/audit/watermark_sla_20260925.json").exists()


def test_a2_runtime_check_args_redirect_json_out_and_alert_flag(tmp_path):
    """--json-out / --json-output / --alert-flag 三种 flag 在 dry 下改写, 其它参数原样。"""
    from services.pipeline.evidence_paths import redirect_runtime_check_args

    args = [
        "--alert-flag",
        "/tmp/chunkymonkey_ALERT_db_invariants.flag",
        "--json-out",
        "data/audit/db_invariants_20260925.json",
        "--other-flag",
        "unchanged-value",
    ]
    out = redirect_runtime_check_args(args, repo=tmp_path, date=DATE)
    dry_root = _dry_root(tmp_path)
    assert out[0] == "--alert-flag"
    assert Path(out[1]).is_relative_to(dry_root)
    assert out[2] == "--json-out"
    assert Path(out[3]) == dry_root / "data/audit/db_invariants_20260925.json"
    assert out[4:] == ["--other-flag", "unchanged-value"]
    # dry=False 场景: 同一份 args 原样透传 (store.py 的 _run 只在 ctx.dry 时才调本函数)。
    assert args == [
        "--alert-flag",
        "/tmp/chunkymonkey_ALERT_db_invariants.flag",
        "--json-out",
        "data/audit/db_invariants_20260925.json",
        "--other-flag",
        "unchanged-value",
    ]


def test_a2_default_log_path_dry_false_is_literal_dry_true_under_dry_root(tmp_path):
    """log_path 未显式传入: dry=False 字面量文件名, dry=True 落 dry 根 tmp/ 子目录。"""
    from services.pipeline.context import DEGRADED_FLAG, PipelineContext

    real = PipelineContext(dry=False, date=DATE)
    try:
        assert real.log_path == DEGRADED_FLAG.parent / "chunkymonkey_daily_update_20260925.log"
    finally:
        real.close()

    dry = PipelineContext(dry=True, date=DATE)
    try:
        assert dry.log_path.is_relative_to(_dry_root(tmp_path))
        assert dry.log_path.name == "chunkymonkey_daily_update_20260925.log"
        assert dry.log_path != real.log_path
    finally:
        dry.close()


def test_a2_degraded_flag_and_soft_banner_marker_dry_paths(tmp_path):
    """降级旗标与 soft banner marker 的 dry 路径都在 dry 根的 tmp/ 子目录下。"""
    from services.pipeline.context import PipelineContext
    from services.pipeline.store import _soft_banner_marker

    ctx = PipelineContext(dry=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        assert ctx.degraded_flag_path().is_relative_to(_dry_root(tmp_path))
        assert _soft_banner_marker(ctx).is_relative_to(_dry_root(tmp_path))
    finally:
        ctx.close()


def test_a2_writer_block_report_dry_and_real_paths(tmp_path):
    """写者锁忙时的最小报告: dry=True 落 dry 根 dry_run=1, dry=False 落真实路径 dry_run=0。"""
    from services.pipeline.run import _write_writer_block_report
    from services.writer_lock import WriterLockBusyError

    exc = WriterLockBusyError("pipeline writer busy: owner=other pid=999 path=/tmp/x.lock")

    dry_path_out = _write_writer_block_report("20260925", exc, dry=True)
    assert dry_path_out.is_relative_to(_dry_root(tmp_path))
    assert json.loads(dry_path_out.read_text())["dry_run"] == 1

    real_path_out = _write_writer_block_report("20260926", exc, dry=False)
    assert real_path_out == tmp_path / "data/reports/daily_20260926.json"
    assert json.loads(real_path_out.read_text())["dry_run"] == 0


def test_a2_stage_runner_dry_entry_inherits_same_dry_root(tmp_path, monkeypatch):
    """chunkyctl pipeline store --dry 是第二个 dry 入口, 走同一个 PipelineContext +
    write_report_and_alert, 不持有另一套路径逻辑 —— 报告落同一个 dry 根。"""
    from services.pipeline import stage_runner
    from services.pipeline.store import write_report_and_alert

    monkeypatch.setattr(stage_runner, "_upstream_refusal", lambda stage: None)

    def _fake_store_stage(ctx):
        write_report_and_alert(ctx)

    monkeypatch.setitem(stage_runner.STAGES, "store", _fake_store_stage)

    rc = stage_runner.run_stage("store", dry=True, date=DATE, force=True)
    assert rc in (0, 1)
    report_path = _dry_root(tmp_path) / "data/reports/daily_20260925.json"
    assert report_path.is_file()
    assert json.loads(report_path.read_text())["dry_run"] == 1
    assert not (tmp_path / "data/reports/daily_20260925.json").exists()


# ── A3: dry 证据不可能被 chain_run 当成真实运行 (FAIL R1) ──────────────────────


def test_a3_dry_evidence_fails_chain_run_with_r1(tmp_path):
    from services.pipeline import chain_run
    from services.pipeline.context import PipelineContext
    from services.pipeline.store import write_report_and_alert

    ctx = PipelineContext(dry=True, skip_sync=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        write_report_and_alert(ctx)
    finally:
        ctx.close()

    result = chain_run.evaluate_chain_run(DATE, repo=tmp_path)
    assert result["state"] == "FAIL"
    assert result["failed_rule"] == "R1"


# ── A4: writer 锁忙 + dry=True 不覆盖真实 daily_D.json ──────────────────────────


def test_a4_writer_lock_busy_dry_report_does_not_touch_real_path(tmp_path, monkeypatch):
    from services import writer_lock as lock_mod
    from services.pipeline import run as run_mod

    real_report = tmp_path / "data/reports/daily_20260925.json"
    real_report.parent.mkdir(parents=True)
    sentinel = json.dumps({"date": "20260925", "dry_run": 0, "run_outcome": "success"})
    real_report.write_text(sentinel)
    sentinel_sha = _sha256(real_report)

    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("context must not start")),
    )
    with lock_mod.writer_lock(owner="other"):
        rc = run_mod.main(["--dry", "--date", "20260925"])
    assert rc == 2
    # 真实路径哨兵完全不变 (核心断言)。
    assert _sha256(real_report) == sentinel_sha

    dry_report = _dry_root(tmp_path) / "data/reports/daily_20260925.json"
    payload = json.loads(dry_report.read_text())
    assert payload["dry_run"] == 1
    assert payload["run_outcome"] == "hard_fail"

    # dry=False 同场景: 报告照旧写在真实路径 (覆盖旧内容), dry_run=0。
    with lock_mod.writer_lock(owner="other"):
        rc2 = run_mod.main(["--date", "20260926"])
    assert rc2 == 2
    real_report_2 = tmp_path / "data/reports/daily_20260926.json"
    assert json.loads(real_report_2.read_text())["dry_run"] == 0


# ── A5 / K6: dry 不发真实通知 (dispatcher 与 osascript 调用次数 = 0) ────────────


def _fake_subprocess_counter(monkeypatch, module):
    calls: list[list[str]] = []

    def _fake_run(cmd, *_a, **_k):
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    return calls


def test_a5_dry_skips_dispatcher_and_osascript_subprocess_calls(tmp_path, monkeypatch):
    """K6: dry=True 且 sla_warn=True / outcome=soft 时, dispatcher 与 osascript 调用次数
    必须为 0, 只留日志一行「dry: 通知跳过」。dry=False 同场景下两个 subprocess 调用都要发生
    (与本刀之前逐字节相同的调用次数)。"""
    from services.pipeline import store
    from services.pipeline.context import PipelineContext
    from services.pipeline.evidence_paths import dry_path

    # dry 场景: 预置 dry 根下的 SLA-after 证据 (n_alerts>0) 让 sla_warn=True。
    sla_after = dry_path("data/audit/watermark_sla_20260925.json", repo=tmp_path, date=DATE)
    sla_after.write_text(
        json.dumps({"n_updates": 1, "n_alerts": 1, "sources": [{"source_name": "x", "alert": True}]})
    )
    calls = _fake_subprocess_counter(monkeypatch, store)

    ctx = PipelineContext(dry=True, skip_sync=True, date=DATE, log_path=tmp_path / "p.log")
    ctx.degraded_msgs.append("sync_registry drain 有残余缺口或域错误 (见 log)")
    try:
        output = store.write_report_and_alert(ctx)
    finally:
        ctx.close()

    assert output["sla_warn"] is True
    assert calls == []
    log_text = (tmp_path / "p.log").read_text()
    assert "dry: 通知跳过 (dispatcher)" in log_text
    assert "dry: 通知跳过 (osascript)" in log_text

    # dry=False 同场景: 两个 subprocess 调用都要真的发生。
    real_sla_after = tmp_path / "data/audit/watermark_sla_20260925.json"
    real_sla_after.parent.mkdir(parents=True, exist_ok=True)
    real_sla_after.write_text(
        json.dumps({"n_updates": 1, "n_alerts": 1, "sources": [{"source_name": "x", "alert": True}]})
    )
    calls.clear()
    ctx2 = PipelineContext(dry=False, skip_sync=True, date=DATE, log_path=tmp_path / "p2.log")
    ctx2.degraded_msgs.append("sync_registry drain 有残余缺口或域错误 (见 log)")
    try:
        output2 = store.write_report_and_alert(ctx2)
    finally:
        ctx2.close()
    assert output2["sla_warn"] is True
    # dispatcher (非 macos 通道) + osascript 横幅各一次。
    assert len(calls) == 2


# ── K1: dry 路径映射 (相对路径证据 + log_path 默认值), 成立条件 dry=True 且未传 log_path ──


def test_k1_dry_path_mapping_relative_evidence_and_default_log(tmp_path):
    """隔离用例: 其它条件全满足 (skip_sync=True, 无 degraded), 只 dry=True 且未传
    log_path 为真。写错 (映射原样返回输入) 会让证据落回真实路径, 用 is_relative_to
    断言抓住。"""
    from services.pipeline.context import PipelineContext
    from services.pipeline.store import write_report_and_alert

    ctx = PipelineContext(dry=True, skip_sync=True, date=DATE)  # 不传 log_path
    try:
        output = write_report_and_alert(ctx)
    finally:
        ctx.close()

    dry_root = _dry_root(tmp_path)
    assert ctx.log_path.is_relative_to(dry_root)
    assert Path(output["log"]).is_relative_to(dry_root)
    report_path = dry_root / "data/reports/daily_20260925.json"
    assert report_path.is_file()
    assert not (tmp_path / "data/reports/daily_20260925.json").exists()


# ── K2a: 降级旗标 reset 只动 dry 根 (成立条件 dry=True) ─────────────────────────


def test_k2a_reset_degraded_flag_dry_only_touches_dry_root(tmp_path):
    from services.pipeline.context import DEGRADED_FLAG, PipelineContext

    DEGRADED_FLAG.parent.mkdir(parents=True, exist_ok=True)
    DEGRADED_FLAG.write_text("real-pre-existing-degraded\n")
    real_sha = _sha256(DEGRADED_FLAG)

    ctx = PipelineContext(dry=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        ctx.reset_degraded_flag()
    finally:
        ctx.close()

    assert DEGRADED_FLAG.exists()
    assert _sha256(DEGRADED_FLAG) == real_sha


# ── K2b: 降级旗标追加写只写 dry 根 (成立条件 dry=True) ──────────────────────────


def test_k2b_degraded_append_write_dry_only_writes_dry_root(tmp_path):
    from services.pipeline.context import DEGRADED_FLAG, PipelineContext

    DEGRADED_FLAG.parent.mkdir(parents=True, exist_ok=True)
    DEGRADED_FLAG.write_text("real-pre-existing-degraded\n")
    real_sha = _sha256(DEGRADED_FLAG)

    ctx = PipelineContext(dry=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        ctx.degraded("synthetic dry degraded message")
    finally:
        ctx.close()

    assert _sha256(DEGRADED_FLAG) == real_sha, "真实旗标不能混进 dry 的降级条目"
    dry_flag = ctx.degraded_flag_path()
    assert dry_flag.is_relative_to(_dry_root(tmp_path))
    assert "synthetic dry degraded message" in dry_flag.read_text()


# ── K2c: soft banner marker 读/写/删只动 dry 根 (成立条件 dry=True) ─────────────


def test_k2c_soft_banner_marker_dry_isolated_from_real_marker(tmp_path):
    from services.pipeline.context import DEGRADED_FLAG, PipelineContext
    from services.pipeline.store import _soft_banner_marker

    real_marker = DEGRADED_FLAG.parent / f"chunkymonkey_soft_banner_{DATE}.marker"
    real_marker.parent.mkdir(parents=True, exist_ok=True)
    real_marker.write_text("real-signature-from-a-genuine-run")

    ctx = PipelineContext(dry=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        marker = _soft_banner_marker(ctx)
    finally:
        ctx.close()

    assert marker != real_marker
    assert marker.is_relative_to(_dry_root(tmp_path))
    assert real_marker.read_text() == "real-signature-from-a-genuine-run"


# ── K3: runtime check 告警旗标 (绝对路径), 成立条件是输入为绝对路径 ─────────────


def test_k3_absolute_alert_flag_redirect_and_writable(tmp_path):
    """隔离用例: 其它条件全满足 (dry=True, 相对路径参数原样透传), 只输入是绝对路径这一
    条件为真时才需要去前导 / 映射。同时验证映射保证父目录存在 —— FAIL 分支真的能把
    旗标写出 (目录不存在会 FileNotFoundError, 与检查 exit 非 0 分不清)。"""
    from services.pipeline.evidence_paths import dry_path, redirect_runtime_check_args

    absolute_flag = "/tmp/chunkymonkey_ALERT_out_of_scope_rows.flag"
    args = ["--alert-flag", absolute_flag, "--json-out", "data/audit/out_of_scope_rows_20260925.json"]
    out = redirect_runtime_check_args(args, repo=tmp_path, date=DATE)
    mapped_flag = Path(out[1])
    assert mapped_flag.is_relative_to(_dry_root(tmp_path))
    assert mapped_flag != Path(absolute_flag)

    # 模拟 FAIL 分支真的写旗标文件 (governance 脚本自己的 write_alert_flag 逻辑简化版)。
    mapped_flag.write_text("[..] out_of_scope_rows 非 PASS\n", encoding="utf-8")
    assert mapped_flag.is_file()

    # 直接用 dry_path() 复核同一条绝对路径映射到同一处 (幂等，唯一实现)。
    assert dry_path(absolute_flag, repo=tmp_path, date=DATE) == mapped_flag


def test_k3d_wiring_run_system_health_checks_redirects_dry_true_literal_dry_false(
    tmp_path, monkeypatch
):
    """K3 (d) 返修 (card:K3): 上面的用例只测 redirect_runtime_check_args() 本身, 从没有
    一条用例驱动**生产入口** store.run_system_health_checks -> 内部 `_run` 闭包这条接线——
    在 scratch 拷贝里删掉 store.py 里 `if ctx.dry: args = redirect_runtime_check_args(...)`
    这行接线, 144 例仍全绿 (mutations/K3d.patch, 见 review0_conflict/mut_wire.txt)。

    本用例只在 subprocess 边界 (context.py 的 subprocess.run) 打桩, 不 mock
    redirect_runtime_check_args 也不 mock store._run/run_system_health_checks, 使用真实
    governance_gates.yaml 登记表; 桩模拟 db_invariants (skip_when_dry=false, dry 下仍真跑)
    FAIL 时把旗标真的写到子进程收到的 --alert-flag 路径——顺带核对 dry_path() 的
    parent.mkdir 保证在真实调用链下仍成立 (卡 K3 要求的『另跑一次子进程 FAIL 分支，
    断言 dry 根下 flag 真的写出』, 不是测试自己 write_text)。"""
    from services.pipeline import context as context_mod
    from services.pipeline.context import PipelineContext
    from services.pipeline.store import run_system_health_checks

    calls: list[list[str]] = []

    def _fake_subprocess_run(cmd, **_kwargs):
        calls.append(list(cmd))
        if "check_db_invariants.py" in cmd[1]:
            alert_flag = Path(cmd[cmd.index("--alert-flag") + 1])
            # 只在落进本用例的 tmp 夹具时才真的写 (dry 根或本用例的假"真实"根都在
            # tmp_path 下); 绝不能因为 dry=False 分支拿到字面量 /tmp 路径就去碰宿主机
            # 真实的 /tmp/chunkymonkey_ALERT_db_invariants.flag。
            if alert_flag.is_relative_to(tmp_path):
                alert_flag.write_text("[..] db_invariants 非 PASS\n", encoding="utf-8")
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(context_mod.subprocess, "run", _fake_subprocess_run)

    dry_root = _dry_root(tmp_path)
    ctx = PipelineContext(dry=True, skip_sync=True, date=DATE, log_path=tmp_path / "p.log")
    try:
        rows = run_system_health_checks(ctx)
    finally:
        ctx.close()

    db_call = next(c for c in calls if "check_db_invariants.py" in c[1])
    alert_val = Path(db_call[db_call.index("--alert-flag") + 1])
    json_val = Path(db_call[db_call.index("--json-out") + 1])
    assert alert_val.is_relative_to(dry_root)
    assert json_val.is_relative_to(dry_root)
    assert alert_val.is_file()  # FAIL 分支经真实子进程调用链写出, 目录由 dry_path() 保证存在
    db_row = next(r for r in rows if r["id"] == "db_invariants")
    assert db_row["status"] == "fail"

    calls.clear()
    ctx2 = PipelineContext(dry=False, skip_sync=True, date=DATE, log_path=tmp_path / "p2.log")
    try:
        run_system_health_checks(ctx2)
    finally:
        ctx2.close()
    db_call2 = next(c for c in calls if "check_db_invariants.py" in c[1])
    alert_val2 = db_call2[db_call2.index("--alert-flag") + 1]
    json_val2 = db_call2[db_call2.index("--json-out") + 1]
    # dry=False: 原样字面量透传, 不经过 dry_path (与本刀之前逐字节相同)。
    assert alert_val2 == "/tmp/chunkymonkey_ALERT_db_invariants.flag"
    assert json_val2 == f"data/audit/db_invariants_{DATE}.json"


# ── K4: dry=False 时全部路径与本刀之前逐字节相同 (最危险的一条) ─────────────────


def test_k4_dry_false_paths_are_byte_identical_to_pre_cut_literals(tmp_path):
    """隔离用例: dry=False (唯一变化的条件), 报告/SLA/日志文件名必须是硬编码字面量,
    不得意外套上 dry 前缀。"""
    from services.pipeline.context import DEGRADED_FLAG, PipelineContext
    from services.pipeline.store import write_report_and_alert

    ctx = PipelineContext(dry=False, skip_sync=True, date=DATE)  # 不传 log_path
    try:
        assert ctx.log_path == DEGRADED_FLAG.parent / "chunkymonkey_daily_update_20260925.log"
        write_report_and_alert(ctx)
    finally:
        ctx.close()

    expected_report = tmp_path / "data/reports/daily_20260925.json"
    assert expected_report.is_file()
    assert not expected_report.is_relative_to(_dry_root(tmp_path))
    payload = json.loads(expected_report.read_text())
    assert payload["dry_run"] == 0


def test_k4b_run_watermark_sla_check_wiring_dry_false_literal_dry_true_dry_root(
    tmp_path, monkeypatch
):
    """K4 返修 (card:K4, 另一落点): 实现没有唯一一个带 dry 参数的映射入口, 每个调用点
    各写一个 `if ctx.dry:` 分支, 所以 K4 变异有多处落点——把
    preflight.run_watermark_sla_check 里的 `if ctx.dry:` (preflight.py:73) 改成
    `if True:` 之前 182 例仍全绿 (mutations/K4b.patch): 所有既有测试要么整个
    monkeypatch 掉 preflight.run_watermark_sla_check (test_pipeline.py 五处), 要么只经
    write_report_and_alert (它自己独立算 sla_evidence 字符串, 从不调用这个函数), 从没有
    一条测试真正驱动到这个函数体内部的 dry 判断。

    本用例直接调用生产入口 (不 monkeypatch 掉它), 只在 subprocess 边界打桩, 捕获真正
    传给 --json-output 的取值。"""
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    captured: list[list[str]] = []

    def _fake_subprocess_run(cmd, **kwargs):
        captured.append(list(cmd))
        # 真实子进程以 cwd=REPO 跑, 相对路径 (dry=False 分支的字面量 output_rel) 靠这个
        # cwd 解析——本桩在同一进程内跑, 必须自己补这一步, 否则会把相对路径解析到测试
        # 进程自己的当前工作目录 (worktree 根), 而不是 tmp 夹具。
        raw_out = cmd[cmd.index("--json-output") + 1]
        out_path = Path(raw_out)
        if not out_path.is_absolute():
            out_path = Path(kwargs.get("cwd") or ".") / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"n_alerts": 0, "sources": []}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # run_watermark_sla_check 函数体内部 `import subprocess` 是局部名字, 但绑定的是同一个
    # sys.modules["subprocess"] 对象——patch 全局 subprocess.run 即可拦到它。
    import subprocess as subprocess_mod

    monkeypatch.setattr(subprocess_mod, "run", _fake_subprocess_run)

    output_rel = "data/audit/watermark_sla_before_20260925.json"

    real_ctx = PipelineContext(dry=False, skip_sync=True, date=DATE, log_path=tmp_path / "r.log")
    try:
        rc = preflight.run_watermark_sla_check(real_ctx, output_rel=output_rel)
    finally:
        real_ctx.close()
    assert rc == 0
    real_json_out = captured[-1][captured[-1].index("--json-output") + 1]
    # dry=False: 与本刀之前逐字节相同的字面量相对路径, 不经过 dry_path。
    assert real_json_out == output_rel
    assert "--dry-run" not in captured[-1]
    assert (tmp_path / output_rel).is_file()

    captured.clear()
    dry_ctx = PipelineContext(dry=True, skip_sync=True, date=DATE, log_path=tmp_path / "d.log")
    try:
        rc2 = preflight.run_watermark_sla_check(dry_ctx, output_rel=output_rel)
    finally:
        dry_ctx.close()
    assert rc2 == 0
    assert "--dry-run" in captured[-1]
    dry_json_out = Path(captured[-1][captured[-1].index("--json-output") + 1])
    assert dry_json_out.is_relative_to(_dry_root(tmp_path))
    # 真实证据文件 (第一次 dry=False 调用写下的) 完全不受这次 dry 调用影响。
    assert (tmp_path / output_rel).is_file()


# ── K5: writer-block 报告接 dry, 路径走 loader (成立条件: dry 与 writer_lock 持有者并发) ──


def test_k5_writer_block_report_dry_flag_and_loader_path(tmp_path):
    from services.pipeline.evidence_paths import load_pipeline_evidence_paths
    from services.pipeline.run import _write_writer_block_report
    from services.writer_lock import WriterLockBusyError

    exc = WriterLockBusyError("pipeline writer busy: owner=other pid=1 path=/tmp/x.lock")
    path = _write_writer_block_report(DATE, exc, dry=True)

    expected_rel = load_pipeline_evidence_paths().daily_report_rel(date=DATE)
    assert path.is_relative_to(_dry_root(tmp_path))
    assert path.name == Path(expected_rel).name
    payload = json.loads(path.read_text())
    assert payload["dry_run"] == 1
    assert payload["date"] == DATE


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(pytest.main([__file__, "-v"] + sys.argv[1:]))
