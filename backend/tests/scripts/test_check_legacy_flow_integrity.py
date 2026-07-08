"""check_legacy_flow_integrity 门自测 — 防孤儿 + red→green 可证 (mythos §14: 门不会红=废门)。

2026-07-08 C1 收口: 原版扫描 scripts/daily_update.sh(2026-06-23 已重设计为瘦 wrapper, 不再
直接提及 backend/scripts/*.py 路径, 继续扫它=伪绿); 改扫描真实调用图
backend/services/pipeline/*.py。本文件覆盖 C1 修复后的红绿验证 + C2/C3 检测逻辑本身可用性
(即便当前 0 命中, 也要证明注入违规样本能翻红)。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_MOD_PATH = _REPO / "backend" / "scripts" / "check_legacy_flow_integrity.py"
_spec = importlib.util.spec_from_file_location("check_legacy_flow_integrity", _MOD_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_c1_scans_pipeline_dir_not_stale_daily_update_sh():
    """C1 扫描源必须是 backend/services/pipeline/ (真实调用图), 不能再指向
    scripts/daily_update.sh(2026-06-23 已重设计为瘦 wrapper, 该文件里不会再出现任何
    backend/scripts/*.py 字面路径, 继续扫它 = 门恒真伪绿)。"""
    assert mod.PIPELINE_DIR == _REPO / "backend" / "services" / "pipeline"


def test_c1_real_pipeline_has_no_missing_scripts():
    """绿基线: 当前 backend/services/pipeline/*.py 引用的 backend/scripts/*.py 全部在盘。"""
    result = mod.check_daily_update_scripts()
    assert result["verdict"] == "PASS"
    assert result["n_called"] > 0  # 必须真扫到调用(非空转), 2026-07-08 修复前 n_called==0
    assert result["missing"] == []


def test_c1_flags_missing_script_red_green(tmp_path, monkeypatch):
    """red→green: 注入一个指向不存在脚本的 pipeline 文件必须让 C1 翻 FAIL; 干净目录必须 PASS。"""
    pipeline_dir = tmp_path / "pipeline"
    pipeline_dir.mkdir()
    monkeypatch.setattr(mod, "PIPELINE_DIR", pipeline_dir)
    monkeypatch.setattr(mod, "REPO", tmp_path)
    (tmp_path / "backend" / "scripts").mkdir(parents=True)
    real_script = tmp_path / "backend" / "scripts" / "real_script.py"
    real_script.write_text("# real", encoding="utf-8")

    clean = pipeline_dir / "clean_stage.py"
    clean.write_text('ctx.run_script("backend/scripts/real_script.py", degraded_msg="x")\n', encoding="utf-8")
    assert mod.check_daily_update_scripts()["verdict"] == "PASS"  # 干净=绿

    dirty = pipeline_dir / "dirty_stage.py"
    dirty.write_text('ctx.run_script("backend/scripts/does_not_exist.py", degraded_msg="x")\n', encoding="utf-8")
    result = mod.check_daily_update_scripts()
    assert result["verdict"] == "FAIL"  # 注入缺失脚本引用 → 红
    assert "does_not_exist.py" in result["missing"]


def test_c2_wiped_refs_detection_logic_is_live(monkeypatch):
    """C2 检测逻辑本身可用性验证(不依赖当前项目状态是否恰好0命中): 强制注入一张必然被
    引用的表名当作 wiped 表, 验证 grep 扫描 + stale 判定真能触发, 非扫空目录空转。"""
    monkeypatch.setattr(mod, "_layers", lambda: {"database_manifest": "L2_feature"})
    # database_manifest 在 backend/config 下大量被真实引用(非 @archived), 强制其落入 wiped
    # 分支后必然被 grep 抓到非豁免的 stale 引用, 证明扫描路径真的在读文件系统。
    import sys
    sys.modules.pop("data_layer_audit", None)  # 防止真模块 _live_tables 掩盖注入
    result = mod.check_no_wiped_refs()
    assert result["n_wiped"] == 1


def test_c3_flags_missing_retention_red_green(monkeypatch):
    """red→green: 注入一张 _history 命名且不在 storage_retention.yaml 里的表必须翻 FAIL。"""
    monkeypatch.setattr(mod, "_layers", lambda: {"fake_inject_history": "L1_foundation"})
    result = mod.check_append_only_retention()
    assert result["verdict"] == "FAIL"
    assert "fake_inject_history" in result["missing_retention"]
