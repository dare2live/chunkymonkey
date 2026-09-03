"""check_lineage_drift.py 门的机械锁 (#12, 2026-09-04 lineage_drift 三件修)。

**必须自带 fixture, 不许依赖当前仓库的真实 git/DB 状态** (feedback-test-must-carry-
its-own-fixture)。每个用例在 tmp_path 里建一个全新、自足的 git 仓库, 把真实的
services/lineage/{builder,model,query,__init__}.py + services/duck_adapter.py +
scripts/{check_lineage_drift,lineage_cli}.py 原样拷进去 (测的是真实逻辑, 不是 stub),
搭配一套微型 registry yaml (database_manifest/sync_registry/data_layers/data_access)。
这套 fixture 不含任何 .duckdb —— 正是 #12(i) 的要点: catalog=False 的提交门不需要它。

覆盖 lineage_drift 三件修 (§4 #12) 的验收 a/c/e 三例的机械版本 (b 在
test_lineage_cli.py 覆盖 --from-index 本身; d/G 需要真实 DuckDB RW 锁, 覆盖在
test_check_lineage_catalog_drift.py::test_fail_open_under_real_write_lock, 该测试
同时也拿真锁验证了 check_lineage_drift 的 catalog=False 路径不受锁影响)。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINEAGE_SRC = REPO_ROOT / "backend" / "services" / "lineage"
DUCK_ADAPTER_SRC = REPO_ROOT / "backend" / "services" / "duck_adapter.py"
CHECK_DRIFT_SRC = REPO_ROOT / "backend" / "scripts" / "check_lineage_drift.py"
LINEAGE_CLI_SRC = REPO_ROOT / "backend" / "scripts" / "lineage_cli.py"


def _run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), text=True, capture_output=True, check=False, **kwargs
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _init_git(repo: Path) -> None:
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "t"], repo)


def _build_fixture_repo(tmp_path: Path) -> Path:
    """微型自足仓库: 真实 lineage 代码 + 微型 registry, 无任何 .duckdb。"""
    repo = tmp_path / "repo"
    lineage_dst = repo / "backend" / "services" / "lineage"
    lineage_dst.mkdir(parents=True)
    for name in ("__init__.py", "builder.py", "model.py", "query.py"):
        (lineage_dst / name).write_text((LINEAGE_SRC / name).read_text(encoding="utf-8"), encoding="utf-8")

    services_dir = repo / "backend" / "services"
    (services_dir / "duck_adapter.py").write_text(DUCK_ADAPTER_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    (services_dir / "__init__.py").write_text("", encoding="utf-8")

    scripts_dir = repo / "backend" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "check_lineage_drift.py").write_text(CHECK_DRIFT_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    (scripts_dir / "lineage_cli.py").write_text(LINEAGE_CLI_SRC.read_text(encoding="utf-8"), encoding="utf-8")

    _write(repo / "backend" / "config" / "database_manifest.yaml", """
version: 1
databases:
  main:
    path: data/main.duckdb
  other:
    path: data/other.duckdb
    table_patterns:
      - dim_other_thing
      - raw_other_*
""")
    _write(repo / "backend" / "config" / "sync_registry.yaml", """
version: 1
defaults: {}
sources:
  vendor:
    target_db: other
domains:
  demo:
    source: vendor
    api: demo
    target_table: raw_other_demo
    grain: [id]
    pit_anchor: trade_date
""")
    _write(repo / "backend" / "config" / "data_layers.yaml", """
version: 1
tables:
  raw_other_demo: L0_source
  dim_main_thing: L1_foundation
""")
    _write(repo / "backend" / "config" / "data_access.yaml", "version: 1\nentities: {}\n")
    _write(repo / "backend" / "services" / "consumer_demo.py",
           "# consumes raw_other_demo and dim_main_thing\n")
    _write(repo / "assets" / ".keep", "")
    _write(repo / "scripts" / ".keep", "")
    (repo / "data" / "lineage").mkdir(parents=True)

    _init_git(repo)
    assert _run(["git", "add", "-A"], repo).returncode == 0
    assert _run(["git", "commit", "-q", "-m", "fixture init"], repo).returncode == 0
    return repo


def _py(repo: Path, args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    env = kwargs.pop("env", None) or {}
    import os
    full_env = dict(os.environ)
    full_env["PYTHONPATH"] = str(repo / "backend")
    full_env.update(env)
    return subprocess.run(
        [sys.executable, *args], cwd=str(repo), text=True, capture_output=True,
        check=False, env=full_env, **kwargs,
    )


def _build(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return _py(repo, [str(repo / "backend" / "scripts" / "lineage_cli.py"), "build", *extra])


def _drift(repo: Path, *, real_repo: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {"CHUNKYMONKEY_REAL_REPO": str(real_repo)} if real_repo else {}
    return _py(repo, [str(repo / "backend" / "scripts" / "check_lineage_drift.py")], env=env)


def _stage_graph(repo: Path) -> None:
    assert _run(["git", "add", "data/lineage/graph.json"], repo).returncode == 0


def _checkout_index_snapshot(repo: Path, tmp_root: Path) -> Path:
    """与 scripts/safe_commit.sh Step 3.96 同一招: 导出暂存索引到独立目录并给它一个
    可 git-grep 的 throwaway repo, 隔离"活工作树"与"门实际读到的东西"。每次调用给
    独立子目录名 (同一条用例里可能要多次导出前后两个快照)。"""
    snap = Path(tempfile.mkdtemp(dir=str(tmp_root)))
    assert _run(["git", "checkout-index", "--all", f"--prefix={snap}/"], repo).returncode == 0
    _init_git(snap)
    assert _run(["git", "add", "-f", "-A"], snap).returncode == 0
    return snap


# ── 基础: 自足 fixture 能建出图, 门认得住 ──────────────────────────────

def test_fixture_builds_and_gate_passes_on_consistent_state(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    build = _build(repo)
    assert build.returncode == 0, build.stdout + build.stderr
    _stage_graph(repo)
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    result = _drift(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_missing_graph_json_is_exit_3(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    assert not (repo / "data" / "lineage" / "graph.json").exists()  # fixture never builds one
    result = _drift(repo)
    assert result.returncode == 3
    assert "缺失" in result.stderr


# ── 验收 a: 暂存 registry 改动 + 旧 graph.json → FAIL 且输出含新增节点 id ────

def test_staged_registry_change_with_stale_graph_fails_with_node_diagnostics(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    build = _build(repo)
    assert build.returncode == 0
    _stage_graph(repo)
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    sync_registry = repo / "backend" / "config" / "sync_registry.yaml"
    sync_registry.write_text(
        sync_registry.read_text(encoding="utf-8") + (
            "  new_domain_a:\n"
            "    source: vendor\n"
            "    api: new_domain_a\n"
            "    target_table: raw_other_newdomain\n"
            "    grain: [id]\n"
        ),
        encoding="utf-8",
    )
    assert _run(["git", "add", "backend/config/sync_registry.yaml"], repo).returncode == 0

    result = _drift(repo)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "漂移" in result.stderr
    assert "table:other.raw_other_newdomain" in result.stderr
    assert "source:vendor.new_domain_a" in result.stderr


# ── 验收 b (机械化子集; 完整 --from-index 覆盖见 test_lineage_cli.py) ──────

def test_from_index_rebuild_then_stage_makes_gate_pass(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    build = _build(repo)
    assert build.returncode == 0
    _stage_graph(repo)
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    sync_registry = repo / "backend" / "config" / "sync_registry.yaml"
    sync_registry.write_text(
        sync_registry.read_text(encoding="utf-8") + (
            "  new_domain_a:\n"
            "    source: vendor\n"
            "    api: new_domain_a\n"
            "    target_table: raw_other_newdomain\n"
            "    grain: [id]\n"
        ),
        encoding="utf-8",
    )
    assert _run(["git", "add", "backend/config/sync_registry.yaml"], repo).returncode == 0
    assert _drift(repo).returncode == 2  # still stale before rebuild

    rebuilt = _build(repo, "--from-index")
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    _stage_graph(repo)

    assert _drift(repo).returncode == 0


# ── 验收 c: 工作树未暂存改动 + 暂存 graph.json 与暂存树一致 → PASS ─────────

def test_unstaged_worktree_edit_does_not_affect_gate_when_run_isolated(tmp_path: Path) -> None:
    """门必须经由 checkout-index 隔离跑 (scripts/safe_commit.sh 的真实用法) 才谈得上
    "工作树未暂存改动不影响门"——直接裸跑会读到真实磁盘上的工作树内容 (见下一条对照
    用例), 那不是 safe_commit.sh 实际调用门的方式。"""
    repo = _build_fixture_repo(tmp_path)
    build = _build(repo)
    assert build.returncode == 0
    _stage_graph(repo)
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    # 工作树里追加一个新 domain, 但不 stage 它。
    sync_registry = repo / "backend" / "config" / "sync_registry.yaml"
    sync_registry.write_text(
        sync_registry.read_text(encoding="utf-8") + (
            "  unstaged_wip:\n"
            "    source: vendor\n"
            "    api: unstaged_wip\n"
            "    target_table: raw_other_wip\n"
            "    grain: [id]\n"
        ),
        encoding="utf-8",
    )
    assert _run(["git", "status", "--short", "backend/config/sync_registry.yaml"], repo).stdout.startswith(" M")

    snap = _checkout_index_snapshot(repo, tmp_path)
    result = _drift(snap, real_repo=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_bare_invocation_against_live_worktree_does_see_unstaged_edits(tmp_path: Path) -> None:
    """对照组: 不经 checkout-index 隔离、直接对工作树跑门, 会读到未暂存的改动而 FAIL
    —— 证明"c 必须经隔离跑"不是我随口加的限定, 是这个门(和 safe_commit.sh 的用法)
    本来就依赖 checkout-index 才成立的isolation 边界。"""
    repo = _build_fixture_repo(tmp_path)
    build = _build(repo)
    assert build.returncode == 0
    _stage_graph(repo)
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    sync_registry = repo / "backend" / "config" / "sync_registry.yaml"
    sync_registry.write_text(
        sync_registry.read_text(encoding="utf-8") + (
            "  unstaged_wip:\n"
            "    source: vendor\n"
            "    api: unstaged_wip\n"
            "    target_table: raw_other_wip\n"
            "    grain: [id]\n"
        ),
        encoding="utf-8",
    )
    result = _drift(repo)  # no isolation: reads the real worktree file directly
    assert result.returncode == 2


# ── 场景 F: 血缘输入 (backend/config 下任意 tracked 文件) 改了没暂存, 只暂存一个
# 无关文件 → 门仍 PASS (2026-09-03 夜里协调方被挡的精确复现)。────────────────

def test_unrelated_lineage_input_edit_unstaged_plus_unrelated_staged_file_passes(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    build = _build(repo)
    assert build.returncode == 0
    _stage_graph(repo)
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    # 另一个"域"的 A3 式 WIP: 编辑一个血缘输入范围内、但与本次改动语义无关的文件,
    # 且刻意不 stage 它 (它不是这次提交的对象)。
    unrelated_input = repo / "backend" / "config" / "data_layers.yaml"
    unrelated_input.write_text(
        unrelated_input.read_text(encoding="utf-8") + "  # wip comment, not staged\n",
        encoding="utf-8",
    )

    unrelated_file = repo / "UNRELATED.txt"
    unrelated_file.write_text("unrelated\n", encoding="utf-8")
    assert _run(["git", "add", "UNRELATED.txt"], repo).returncode == 0

    snap = _checkout_index_snapshot(repo, tmp_path)
    result = _drift(snap, real_repo=repo)
    assert result.returncode == 0, result.stdout + result.stderr


# ── 诊断 (iii-b): index≠worktree 的血缘输入清单, 定位到具体文件 ────────────

def test_diagnostics_name_the_unstaged_input_file_when_plain_build_leaks_worktree_state(
    tmp_path: Path,
) -> None:
    """完整故障复现: 有人在工作树跑了 plain `lineage_cli.py build` (未走 --from-index),
    这一步会把工作树里"未暂存"的血缘输入内容缝进 graph.json; 只 stage 了 graph.json,
    没 stage 那个输入文件本身。门经 checkout-index 隔离重生时只看到 index 版本的输入,
    与 staged 的 graph.json 不一致 —— FAIL, 且诊断必须点名是哪个文件。"""
    repo = _build_fixture_repo(tmp_path)
    build = _build(repo)
    assert build.returncode == 0
    _stage_graph(repo)
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    data_layers = repo / "backend" / "config" / "data_layers.yaml"
    data_layers.write_text(
        data_layers.read_text(encoding="utf-8") + "  dim_wip_only_in_worktree: L1_foundation\n",
        encoding="utf-8",
    )
    # Plain build (无隔离) 直接读工作树, 会缝进 dim_wip_only_in_worktree。
    rebuilt = _build(repo)
    assert rebuilt.returncode == 0
    _stage_graph(repo)  # 只 stage 结果, 不 stage data_layers.yaml 本身

    snap = _checkout_index_snapshot(repo, tmp_path)
    result = _drift(snap, real_repo=repo)
    assert result.returncode == 2
    assert "index≠worktree" in result.stderr
    assert "backend/config/data_layers.yaml" in result.stderr
    assert "chunkyctl lineage build --from-index" in result.stderr

    # 用 --from-index 才是这条故障的真正修法 (而不是 plain build)。
    rebuilt2 = _build(repo, "--from-index")
    assert rebuilt2.returncode == 0
    _stage_graph(repo)
    snap2 = _checkout_index_snapshot(repo, tmp_path)
    assert _drift(snap2, real_repo=repo).returncode == 0
