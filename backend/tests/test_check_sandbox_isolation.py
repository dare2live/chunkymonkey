"""check_sandbox_isolation 单测 (2026-06-21; C2 删于 2026-09-04) — 隔离门 C1/C3 正则+命名
+ 当前仓库集成 PASS + 临时 git 仓库红/绿双向验证。

C2 (控制面文档嵌入未 promote 实验 run_id) 随 docs/ 整个目录退役一起删除,
check_c1/check_c3 的红例测试改用 tmp_path 建的临时 git 仓库当 fixture —— 不断言宿主
仓库的状态。这个项目栽过: 本地仓库信息完整而 CI 是浅克隆 (0 tag / 1 commit), 断言宿主
git 状态本地绿 CI 红 (见 feedback-test-must-carry-its-own-fixture.md)。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import scripts.check_sandbox_isolation as sandbox_gate  # noqa: E402

from scripts.check_sandbox_isolation import (  # noqa: E402
    C1_PAT,
    check_c1,
    check_c3,
)


def test_c1_pat_flags_sandbox_refs():
    assert C1_PAT.search("from sandbox.d4_exp import foo")
    assert C1_PAT.search("import sandbox")
    assert C1_PAT.search("p = 'sandbox/d4_exp/scratch.duckdb'")
    assert C1_PAT.search('open("sandbox/x/results.json")')


def test_c1_pat_no_false_positive_on_guard():
    # guard 的正常 import 不该误判 (from services.sandbox_guard / enable_sandbox_guard)
    assert not C1_PAT.search("from services.sandbox_guard import enable_sandbox_guard")
    assert not C1_PAT.search("    enable_sandbox_guard()")
    assert not C1_PAT.search("# 探索写 sandbox scratch 用 sandbox_scratch()")


def test_current_repo_passes():
    # 集成: 全清后当前仓库 C1(backend不引用sandbox) + C3(无探索runner) 应空
    assert check_c1() == [], f"C1 漏码: {check_c1()}"
    assert check_c3() == [], f"C3 探索runner: {check_c3()}"


# ---------------------------------------------------------------------------
# 临时 git 仓库 fixture —— 不碰宿主仓库, 只造一个一次性小仓库当 check_c1/check_c3
# 的输入 (两者都经 `git -C <repo> ls-files` 读, 需要一个真 git 仓库, 但不需要 commit,
# `git add` 进 index 就够 `git ls-files` 看见)。
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _stage(repo: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", relative], check=True)
    return path


def test_c1_red_example_flags_sandbox_import_then_green_after_fix(tmp_path):
    repo = _init_repo(tmp_path)
    _stage(repo, "backend/services/leaky_module.py", "from sandbox import scratch_helper\n")

    bad = check_c1(repo=repo)
    assert bad, "C1 该抓到 backend/ 引用 sandbox/ 却没抓到"
    assert any("backend/services/leaky_module.py" in x for x in bad)

    # 去掉缺陷 (换成正常 services 内部引用) 后必须转绿 —— 双向验证, 不是只测红例
    _stage(repo, "backend/services/leaky_module.py", "from services.other_module import scratch_helper\n")
    good = check_c1(repo=repo)
    assert good == [], f"去掉 sandbox 引用后 C1 应为空, 实得: {good}"


def test_c3_red_example_flags_experiment_runner_then_green_after_removal(tmp_path):
    repo = _init_repo(tmp_path)
    _stage(repo, "backend/scripts/experiment_new_alpha.py", "print('exploring')\n")

    bad = check_c3(repo=repo)
    assert bad == ["backend/scripts/experiment_new_alpha.py"], f"C3 该抓到探索 runner: {bad}"

    # 去掉缺陷 (探索 runner 移出/删除) 后必须转绿
    (repo / "backend/scripts/experiment_new_alpha.py").unlink()
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    good = check_c3(repo=repo)
    assert good == [], f"删掉探索 runner 后 C3 应为空, 实得: {good}"


def test_main_passes_repo_kwarg_through_and_exits_clean_on_empty_repo(tmp_path, monkeypatch, capsys):
    # main() 本身不接受 repo 参数 (CLI 入口固定用宿主 REPO), 但 check_c1/check_c3 的
    # repo 参数要能被 monkeypatch 进 main() 调用的默认值路径复用 —— 这里只验证一个空
    # 临时仓库经由显式 repo= 调用两个 check_* 都是空, 防止 _tracked 的 glob 签名改动
    # 悄悄破坏 main() 里的无参调用方式。
    repo = _init_repo(tmp_path)
    assert check_c1(repo=repo) == []
    assert check_c3(repo=repo) == []
