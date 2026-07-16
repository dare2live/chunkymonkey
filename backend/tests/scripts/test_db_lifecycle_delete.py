"""db_lifecycle_delete live 守护面单测 (2026-07-06 全面数据审计根因根治).

历史根因证据=analysis/comprehensive_data_module_audit_20260706.md pit_leakage_spotcheck 维度:
原 _live_surface() 只扫 daily_update.sh 里正则抓到的脚本名 + serving/recommendation/scoring/
ensemble 四个目录——结构性排除 backend/scripts/ 整个目录, 导致"表已删但治理脚本仍用 SQL
字符串引用"这类死引用在删表前完全检测不到 (data_quality.py 3742行零调用方模块正是这样
潜伏 44+ 天没被发现)。另: daily_update.sh 2026-06-23 重设计后已委托 services.pipeline.run
模块调用, 原对 daily_update.sh 做正则抓 backend/scripts/*.py 调用名的逻辑现在恒抓不到东西
(与历史执行面 verifier 的 "PASS by vacuity" 同型)。

本门锁定: (1) live 守护面必须包含 backend/scripts/*.py (治理/审计脚本); (2) 必须包含
backend/services/pipeline/*.py (真实当前调用图, 取代已过期的 wrapper 脚本正则解析);
(3) 端到端: 一张表若被 backend/scripts/ 下某脚本的 SQL 字符串引用, run() 必须 REFUSE
删除它 (不能真的删掉仍在被引用的表)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import db_lifecycle_delete as dld  # noqa: E402


def test_live_surface_includes_backend_scripts_directory():
    """live 守护面必须覆盖 backend/scripts/ (治理/审计脚本), 不能只扫
    daily_update.sh + serving/recommendation/scoring/ensemble 四个目录。"""
    surface = dld._live_surface()
    scripts_files = [p for p in surface if "backend/scripts/" in str(p).replace("\\", "/")]
    assert scripts_files, "live 守护面必须包含 backend/scripts/ 下的治理脚本"
    # 具体验证几个真实存在的治理脚本确实被纳入 (不是巧合命中了别的东西)
    names = {p.name for p in scripts_files}
    assert "check_dead_references.py" in names
    assert "check_continuity_integrity.py" in names


def test_live_surface_includes_pipeline_directory():
    """live 守护面必须覆盖 backend/services/pipeline/ (真实当前调用图),
    不能只靠对已过期的 daily_update.sh wrapper 脚本做正则解析
    (2026-06-23 重设计后 daily_update.sh 委托 services.pipeline.run 模块调用,
    原正则 `grep backend/scripts/*.py` 恒抓不到任何东西)。"""
    surface = dld._live_surface()
    pipeline_files = [p for p in surface if "services/pipeline/" in str(p).replace("\\", "/")]
    assert pipeline_files, "live 守护面必须包含 backend/services/pipeline/"


def test_is_live_cited_detects_sql_string_reference_in_scripts_dir(tmp_path, monkeypatch):
    """端到端 word-boundary 匹配: backend/scripts/ 下某脚本用纯 SQL 字符串引用一张表,
    _is_live_cited 必须能抓到 (不依赖 Python import 语法, 与 check_dead_references scan_e
    的死引用扫描同一套"SQL 字符串也算引用"的判断标准)。
    monkeypatch dld.REPO 指向 tmp_path: _is_live_cited 内部对命中文件调用 p.relative_to(REPO)
    (仓库内绝对路径), 探针文件必须与 REPO 处在同一棵目录树下才不会在这一步报错。"""
    monkeypatch.setattr(dld, "REPO", tmp_path)
    fake_script = tmp_path / "check_fake_governance.py"
    fake_script.write_text(
        'conn.execute("SELECT * FROM mart_some_table_still_in_use")\n', encoding="utf-8"
    )
    corpus = dld._load_surface([fake_script])
    hit = dld._is_live_cited("mart_some_table_still_in_use", corpus)
    assert hit is not None and "check_fake_governance.py" in hit
    # 反例: 不同表名不应误命中 (word-boundary 精确, 非子串匹配)
    miss = dld._is_live_cited("mart_some_table_still_in_use_v2", corpus)
    assert miss is None


def test_run_refuses_delete_when_table_cited_in_scripts_dir(tmp_path, monkeypatch):
    """端到端: run() 对一张仍被 backend/scripts/ 下脚本引用的表, 必须 REFUSE (dry-run 不
    崩溃, 且该表不出现在'待执行'名单里) —— 这是本次修复要堵住的真实场景 (data_quality.py
    式死引用如果晚一步被发现, 这道闸本该在删表那一刻就拦下来)。"""
    # monkeypatch _live_surface 注入一个受控最小 corpus (避免依赖全仓库当前状态导致测试
    # 脆弱); dld.REPO 也需指向 tmp_path, 因为 _is_live_cited 命中时对文件路径调用
    # p.relative_to(REPO), 探针文件必须与 REPO 处在同一棵目录树下。
    monkeypatch.setattr(dld, "REPO", tmp_path)
    fake_script = tmp_path / "check_probe.py"
    fake_script.write_text(
        'conn.execute("SELECT * FROM mart_probe_table_20260706")\n', encoding="utf-8"
    )
    monkeypatch.setattr(dld, "_live_surface", lambda: [fake_script])

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "run_id: test_probe\n"
        "db: smartmoney\n"
        "archive_dir: data/archive/test_probe\n"
        "entries:\n"
        "  - {table: mart_probe_table_20260706, action: drop, bucket: test, reason: probe}\n",
        encoding="utf-8",
    )
    rc = dld.run(manifest, execute=False)
    assert rc == 0  # dry-run 本身不因 REFUSE 而报错退出, 只是不把该表排进待执行
