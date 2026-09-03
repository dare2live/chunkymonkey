"""check_lineage_catalog_drift.py 的机械锁 (#12(i) runtime 雏形, 2026-09-04)。

自足 fixture (feedback-test-must-carry-its-own-fixture): 真实 services/lineage/* +
scripts/check_lineage_catalog_drift.py 原样拷进 tmp 仓库, 微型 registry + 真实
(临时) .duckdb 文件 —— 这条门本来就要连库, fixture 不能像 check_lineage_drift 那样
完全绕开 DuckDB。

覆盖: PASS(一致) / DEGRADED(有 ghost/orphan, 退出码 1) / --json-out 落盘 /
fail-open(某库不可达时 UNVERIFIED 退出 0, 不当阻断)。fail-open 用**真实** RW 写锁
复现 (backend/scripts/check_lineage_catalog_drift.py docstring 点名: 2026-08-11 被撤销
的旧检查这条路径从未被测过, 这次补上), 不是 mock RuntimeError —— mock 只能证明代码
分支存在, 证明不了 DuckDB 锁语义真的触发它。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LINEAGE_SRC = REPO_ROOT / "backend" / "services" / "lineage"
DUCK_ADAPTER_SRC = REPO_ROOT / "backend" / "services" / "duck_adapter.py"
CHECK_SCRIPT_SRC = REPO_ROOT / "backend" / "scripts" / "check_lineage_catalog_drift.py"


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
    (scripts_dir / "check_lineage_catalog_drift.py").write_text(
        CHECK_SCRIPT_SRC.read_text(encoding="utf-8"), encoding="utf-8"
    )

    _write(repo / "backend" / "config" / "database_manifest.yaml", f"""
version: 1
databases:
  main:
    path: data/main.duckdb
""")
    _write(repo / "backend" / "config" / "sync_registry.yaml", "version: 1\ndefaults: {}\nsources: {}\ndomains: {}\n")
    _write(repo / "backend" / "config" / "data_layers.yaml", "version: 1\ntables:\n  dim_registered: L1_foundation\n")
    _write(repo / "backend" / "config" / "data_access.yaml", "version: 1\nentities: {}\n")
    (repo / "data").mkdir(parents=True, exist_ok=True)

    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "t"], repo)
    return repo


def _py(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "backend")
    return subprocess.run([sys.executable, *args], cwd=str(repo), text=True,
                           capture_output=True, check=False, env=env)


def _check(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _py(repo, [str(repo / "backend" / "scripts" / "check_lineage_catalog_drift.py"), *args])


def _make_main_db(repo: Path, tables: list[str]) -> None:
    db_path = repo / "data" / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    for t in tables:
        conn.execute(f"CREATE TABLE {t} (id INTEGER)")
    conn.close()


def test_pass_when_registry_matches_live_catalog(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    _make_main_db(repo, ["dim_registered"])
    result = _check(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_degraded_reports_ghosts_and_orphans_and_writes_json(tmp_path: Path) -> None:
    repo = _build_fixture_repo(tmp_path)
    _make_main_db(repo, ["dim_registered", "dim_ghost_nobody_declared"])
    # dim_registered 的物理表不建 -> orphan; dim_ghost_nobody_declared 无登记 -> ghost
    db_path = repo / "data" / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("DROP TABLE dim_registered")
    conn.close()

    out_json = repo / "out.json"
    result = _check(repo, "--json-out", str(out_json))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DEGRADED" in result.stdout
    assert "table:main.dim_ghost_nobody_declared" in result.stdout
    assert "table:main.dim_registered" in result.stdout

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["live_catalog_reachable"] is True
    assert payload["ghosts"] == ["table:main.dim_ghost_nobody_declared"]
    assert payload["orphans"] == ["table:main.dim_registered"]


def test_db_file_missing_is_reported_as_orphan_not_fail_open(tmp_path: Path) -> None:
    """活库文件根本不存在(还没建过) —— _live_tables_by_db 直接 skip 该库 (既有行为,
    不 raise), 所以登记表里声明的表在这个(不存在的)库里天然算"孤儿"; 这与"库存在但
    被写锁挡住"是两回事 (后者才该 fail-open, 见 test_fail_open_under_real_write_lock)
    —— "库还没建过" 不该被悄悄吞掉, 它本身就是一条真实、值得报的完整性观测。"""
    repo = _build_fixture_repo(tmp_path)
    # 不建 data/main.duckdb
    result = _check(repo)
    assert result.returncode == 1
    assert "DEGRADED" in result.stdout
    assert "table:main.dim_registered" in result.stdout


# ── fail-open under a REAL RW lock (2026-08-11 撤销的旧检查明确说这条路径没测过) ──

@pytest.fixture
def _rw_lock_holder(tmp_path):
    """在独立子进程里对给定 duckdb 文件开 read_write 连接并一直持有, 直到测试结束或
    显式释放。用哨兵文件同步 (不用 sleep(N) 掐时间, 规避子进程调度抖动)。"""
    held: dict[str, subprocess.Popen] = {}

    def _hold(db_path: Path) -> None:
        held_flag = tmp_path / "lock_held.flag"
        release_flag = tmp_path / "lock_release.flag"
        held_flag.unlink(missing_ok=True)
        release_flag.unlink(missing_ok=True)
        script = (
            "import duckdb, time, sys, os\n"
            f"conn = duckdb.connect(r'{db_path}', read_only=False)\n"
            f"open(r'{held_flag}', 'w').close()\n"
            f"while not os.path.exists(r'{release_flag}'):\n"
            "    time.sleep(0.1)\n"
            "conn.close()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        held["proc"] = proc
        held["release_flag"] = release_flag
        deadline = time.time() + 10
        while not held_flag.exists():
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError("lock holder subprocess never signalled LOCK_HELD in time")
            if proc.poll() is not None:
                raise RuntimeError(f"lock holder subprocess died early: {proc.stdout.read()}")
            time.sleep(0.05)

    yield _hold

    proc = held.get("proc")
    if proc is not None and proc.poll() is None:
        release_flag = held["release_flag"]
        release_flag.write_text("release", encoding="utf-8")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_fail_open_under_real_write_lock(tmp_path: Path, _rw_lock_holder) -> None:
    """核心验收 (G): 活库被另一个进程 RW 持锁时 (如形态面全量重建), 本检查必须
    UNVERIFIED + exit 0 (fail-open), 不能报 FAIL/崩溃 —— 这正是 2026-08-11 被撤出
    runtime_checks 的旧检查被点名"写锁期的降级路径从未验证"的那条路径。"""
    repo = _build_fixture_repo(tmp_path)
    _make_main_db(repo, ["dim_registered"])

    db_path = repo / "data" / "main.duckdb"
    _rw_lock_holder(db_path)

    # 独立确认: 常规只读连接此刻确实会被拒 (证明锁真的生效, 不是空气锁)。
    with pytest.raises(Exception, match="[Ll]ock"):
        duckdb.connect(str(db_path), read_only=True)

    result = _check(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNVERIFIED" in result.stdout
    assert "fail-open" in result.stdout


def test_check_lineage_drift_gate_unaffected_by_the_same_lock(tmp_path: Path, _rw_lock_holder) -> None:
    """对照: 同一把锁下, 提交门 check_lineage_drift.py (catalog=False) 完全不受影响
    ——它压根不摸这个数据库。用真实 check_lineage_drift.py + lineage_cli.py 搭一个
    最小的自足 repo 来证明 (与 test_check_lineage_drift.py 的 fixture 同款, 这里只加
    一个真实 .duckdb 文件和锁)。"""
    repo = tmp_path / "gate_repo"
    lineage_dst = repo / "backend" / "services" / "lineage"
    lineage_dst.mkdir(parents=True)
    for name in ("__init__.py", "builder.py", "model.py", "query.py"):
        (lineage_dst / name).write_text((LINEAGE_SRC / name).read_text(encoding="utf-8"), encoding="utf-8")
    services_dir = repo / "backend" / "services"
    (services_dir / "duck_adapter.py").write_text(DUCK_ADAPTER_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    (services_dir / "__init__.py").write_text("", encoding="utf-8")
    scripts_dir = repo / "backend" / "scripts"
    scripts_dir.mkdir(parents=True)
    check_drift_src = REPO_ROOT / "backend" / "scripts" / "check_lineage_drift.py"
    (scripts_dir / "check_lineage_drift.py").write_text(check_drift_src.read_text(encoding="utf-8"), encoding="utf-8")
    lineage_cli_src = REPO_ROOT / "backend" / "scripts" / "lineage_cli.py"
    (scripts_dir / "lineage_cli.py").write_text(lineage_cli_src.read_text(encoding="utf-8"), encoding="utf-8")

    _write(repo / "backend" / "config" / "database_manifest.yaml",
           "version: 1\ndatabases:\n  main:\n    path: data/main.duckdb\n")
    _write(repo / "backend" / "config" / "sync_registry.yaml", "version: 1\ndefaults: {}\nsources: {}\ndomains: {}\n")
    _write(repo / "backend" / "config" / "data_layers.yaml", "version: 1\ntables:\n  dim_registered: L1_foundation\n")
    _write(repo / "backend" / "config" / "data_access.yaml", "version: 1\nentities: {}\n")
    _write(repo / "assets" / ".keep", "")
    _write(repo / "scripts" / ".keep", "")
    (repo / "data").mkdir(parents=True, exist_ok=True)
    db_path = repo / "data" / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE dim_registered (id INTEGER)")
    conn.close()

    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "t"], repo)
    assert _run(["git", "add", "-A"], repo).returncode == 0
    assert _run(["git", "commit", "-q", "-m", "init"], repo).returncode == 0

    build = _py(repo, [str(scripts_dir / "lineage_cli.py"), "build"])
    assert build.returncode == 0, build.stdout + build.stderr
    assert _run(["git", "add", "data/lineage/graph.json"], repo).returncode == 0
    assert _run(["git", "commit", "-q", "-m", "graph"], repo).returncode == 0

    _rw_lock_holder(db_path)

    result = _py(repo, [str(scripts_dir / "check_lineage_drift.py")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
