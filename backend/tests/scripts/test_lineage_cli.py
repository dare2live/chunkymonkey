"""lineage_cli.py 的机械锁, 聚焦 --from-index / --with-catalog (#12(ii), 2026-09-04)。

自足 fixture (feedback-test-must-carry-its-own-fixture): 真实 services/lineage/* +
scripts/{lineage_cli,check_lineage_drift}.py 原样拷进 tmp 仓库, 微型 registry yaml。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINEAGE_SRC = REPO_ROOT / "backend" / "services" / "lineage"
DUCK_ADAPTER_SRC = REPO_ROOT / "backend" / "services" / "duck_adapter.py"
CHECK_DRIFT_SRC = REPO_ROOT / "backend" / "scripts" / "check_lineage_drift.py"
LINEAGE_CLI_SRC = REPO_ROOT / "backend" / "scripts" / "lineage_cli.py"


def _run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False, **kwargs)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_fixture_repo(tmp_path: Path) -> Path:
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
    _write(repo / "backend" / "services" / "consumer_demo.py", "# consumes raw_other_demo\n")
    _write(repo / "assets" / ".keep", "")
    _write(repo / "scripts" / ".keep", "")
    (repo / "data" / "lineage").mkdir(parents=True)

    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "t"], repo)
    assert _run(["git", "add", "-A"], repo).returncode == 0
    assert _run(["git", "commit", "-q", "-m", "fixture init"], repo).returncode == 0
    return repo


def _py(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "backend")
    return subprocess.run([sys.executable, *args], cwd=str(repo), text=True,
                           capture_output=True, check=False, env=env)


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _py(repo, [str(repo / "backend" / "scripts" / "lineage_cli.py"), *args])


def test_build_default_is_registry_only(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    result = _cli(repo, "build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "registry-only" in result.stdout
    payload = json.loads((repo / "data" / "lineage" / "graph.json").read_text(encoding="utf-8"))
    assert payload["meta"]["node_count"] > 0
    assert any(n["id"] == "table:other.raw_other_demo" for n in payload["nodes"])


def test_build_with_catalog_flag_labels_mode(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    # 没有真实 .duckdb, 活库枚举会得到空集合, 但不应报错 (manifest 里的库 path 不存在时
    # _live_tables_by_db 直接 skip, builder.py:69 既有行为)。
    result = _cli(repo, "build", "--with-catalog")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "(catalog)" in result.stdout


def test_from_index_and_with_catalog_are_mutually_exclusive(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    result = _cli(repo, "build", "--from-index", "--with-catalog")
    assert result.returncode == 1
    assert "不能同用" in result.stderr


def test_from_index_matches_direct_registry_build_on_clean_tree(tmp_path: Path) -> None:
    """干净树上 (无未暂存改动), --from-index 与普通 build 必须产出逐字相同的图 ——
    这是它俩共用同一个 catalog=False builder 的直接验证。"""
    repo = _build_fixture_repo(tmp_path)
    direct = _cli(repo, "build")
    assert direct.returncode == 0
    direct_payload = (repo / "data" / "lineage" / "graph.json").read_text(encoding="utf-8")

    from_index = _cli(repo, "build", "--from-index")
    assert from_index.returncode == 0, from_index.stdout + from_index.stderr
    assert "请 git add" in from_index.stdout
    from_index_payload = (repo / "data" / "lineage" / "graph.json").read_text(encoding="utf-8")

    d1 = json.loads(direct_payload)
    d2 = json.loads(from_index_payload)
    del d1["meta"]["generated_at"]
    del d2["meta"]["generated_at"]
    assert d1 == d2


def test_from_index_ignores_unstaged_worktree_edit(tmp_path: Path) -> None:
    """--from-index 的整个存在理由: 工作树里有未暂存的改动时, 它必须只看 index 版本,
    不能把工作树内容缝进去 (否则跟普通 build 没有区别, 这个新命令就白造了)。"""
    repo = _build_fixture_repo(tmp_path)
    baseline = _cli(repo, "build", "--from-index")
    assert baseline.returncode == 0
    baseline_payload = json.loads((repo / "data" / "lineage" / "graph.json").read_text(encoding="utf-8"))

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
    # NOT staged.
    from_index = _cli(repo, "build", "--from-index")
    assert from_index.returncode == 0, from_index.stdout + from_index.stderr
    after_payload = json.loads((repo / "data" / "lineage" / "graph.json").read_text(encoding="utf-8"))

    del baseline_payload["meta"]["generated_at"]
    del after_payload["meta"]["generated_at"]
    assert baseline_payload == after_payload  # unstaged edit invisible to --from-index

    # But a plain (non-isolated) build DOES see it -- proving the isolation is real,
    # not just "nothing changed by coincidence".
    plain = _cli(repo, "build")
    assert plain.returncode == 0
    plain_payload = json.loads((repo / "data" / "lineage" / "graph.json").read_text(encoding="utf-8"))
    assert plain_payload["meta"]["node_count"] > baseline_payload["meta"]["node_count"]


def test_impact_provenance_dead_still_work_on_registry_only_graph(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    assert _cli(repo, "build").returncode == 0

    imp = _cli(repo, "impact", "raw_other_demo")
    assert imp.returncode == 0
    payload = json.loads(imp.stdout)
    assert payload["exists"] is True
    assert "backend/services/consumer_demo.py" in payload.get("consumers_by_type", {}).get("service", [])

    prov = _cli(repo, "provenance", "raw_other_demo")
    assert json.loads(prov.stdout)["acquired"] is True

    dead = _cli(repo, "dead")
    assert dead.returncode == 0
    json.loads(dead.stdout)  # just needs to parse
