"""daily_update 四阶段管线 smoke 测试 (2026-06-23 重设计)。

锁住: (1) 包/各阶段可 import (防 port 笔误回归); (2) PipelineContext degraded/log 机制;
(3) run.main 编排顺序 preflight→获取→清洗→加工→存储 (monkeypatch 阶段, 不碰真 DB/网络)。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_pipeline_package_imports():
    """各阶段模块 + 入口可 import (catch port 笔误 / 死引用)。"""
    from services.pipeline import acquire, clean, context, preflight, process, run, store
    assert hasattr(run, "main")
    assert hasattr(acquire, "run_acquire")
    assert hasattr(clean, "run_clean")
    assert hasattr(process, "run_process")
    assert hasattr(store, "run_store")
    assert hasattr(preflight, "run_preflight")


def test_context_degraded_and_log(tmp_path, monkeypatch):
    from services.pipeline import context
    from services.pipeline.context import PipelineContext
    # degraded() 写全局 DEGRADED_FLAG (/tmp/chunkymonkey_ALERT_daily_update_degraded.flag) —
    # 隔离到 tmp_path 防测试污染真实生产告警文件 (2026-06-29 批4发现: 未隔离时 pytest 全量跑
    # 会把测试字面量"步骤X失败"写进真实 alert flag, 误导下次 session 启动检查)。
    monkeypatch.setattr(context, "DEGRADED_FLAG", tmp_path / "alert.flag")
    ctx = PipelineContext(dry=True, date="20260101", log_path=tmp_path / "t.log")
    ctx.degraded("步骤X失败")
    assert "步骤X失败" in ctx.degraded_msgs
    ctx.log("普通日志")
    ctx.close()
    content = (tmp_path / "t.log").read_text()
    assert "步骤X失败" in content and "普通日志" in content
    assert "步骤X失败" in (tmp_path / "alert.flag").read_text()


def test_context_requires_date():
    from services.pipeline.context import PipelineContext
    with pytest.raises(ValueError):
        PipelineContext(date="")


def test_run_orchestration_order(monkeypatch, tmp_path):
    """run.main 按 preflight→获取→清洗→加工→存储 顺序调四阶段 (mock 不碰真 DB)。"""
    from services.pipeline import run as run_mod

    called = []
    for name in ("run_preflight", "run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx, _n=name: called.append(_n))
    # 把日志/flag 引到 tmp, 不污染 /tmp
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")

    rc = run_mod.main(["--dry", "--skip-sync", "--date", "20260101"])
    assert rc == 0
    assert called == ["run_preflight", "run_acquire", "run_clean", "run_process", "run_store"]


def test_run_no_flags_parses(monkeypatch, tmp_path):
    """无 flag (全量真实模式) 也能解析 — 防 bash wrapper 空参传成空字符串 arg 的回归
    (2026-06-23: wrapper 用 ${arr[@]:-} 在空数组时传 '' → argparse unrecognized arguments)。

    2026-07-10 修真库污染(全栈审计HIGH): 本测试 dry=False 只 patch 了 5 个阶段函数, 漏了
    状态记录与日志两条副作用路径 — run_and_record 经 _record_stage_best_effort→get_conn()
    真写生产 smartmoney.mart_pipeline_run_manifest(每次全量 pytest 灌 4 行亚秒级 check_pass,
    污染 stage_runner 的 upstream check_pass 门真相源, 真实 check_fail 被下一次 pytest 盖掉),
    且默认 log_path 写真实 /tmp/chunkymonkey_daily_update_20260101.log。2026-06-29 已修过
    同类 DEGRADED_FLAG 污染但漏了这两条 — 同一测试的多条副作用路径必须逐条隔离。"""
    from services.pipeline import run as run_mod
    from services.pipeline import stage_status as ss_mod
    from services.pipeline.context import PipelineContext
    for name in ("run_preflight", "run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx: None)
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    monkeypatch.setattr(run_mod, "PipelineContext",
                        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}))
    recorded = []
    monkeypatch.setattr(ss_mod, "_record_stage_best_effort",
                        lambda ctx, stage, status, gate_result=None: recorded.append(stage))
    assert run_mod.main(["--date", "20260101"]) == 0  # 无 --dry/--skip-sync
    assert recorded == ["acquire", "clean", "process", "store"], \
        "状态记录必须被 stub 捕获而非写真库 (捕获顺序同时验证阶段链)"
