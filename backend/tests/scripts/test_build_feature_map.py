from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_feature_map.py"
SPEC = importlib.util.spec_from_file_location("build_feature_map", SCRIPT_PATH)
fmap = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = fmap
SPEC.loader.exec_module(fmap)

REPO = Path(__file__).resolve().parents[3]


def test_write_re_matches_writer_statements() -> None:
    hits = [m.group(1) for m in fmap.WRITE_RE.finditer(
        'con.execute("CREATE TABLE IF NOT EXISTS fact_a (x INT)")\n'
        'con.execute("INSERT OR REPLACE INTO mart_b SELECT 1")\n'
        'con.execute("MERGE INTO raw_c USING df")\n'
        'con.execute("CREATE OR REPLACE TABLE dim_d AS SELECT 1")\n'
    )]
    assert hits == ["fact_a", "mart_b", "raw_c", "dim_d"]


def test_write_re_ignores_reads_and_maintenance() -> None:
    text = (
        'con.execute("SELECT * FROM fact_a JOIN mart_b USING (d)")\n'
        'con.execute("DELETE FROM fact_a WHERE 1=1")\n'
        'con.execute("DROP TABLE IF EXISTS mart_b")\n'
    )
    assert [m.group(1) for m in fmap.WRITE_RE.finditer(text)] == []


def test_scan_table_writers_excludes_tests_and_dynamic(tmp_path: Path) -> None:
    svc = tmp_path / "backend" / "services"
    tst = tmp_path / "backend" / "services" / "tests"
    scr = tmp_path / "backend" / "scripts"
    rts = tmp_path / "backend" / "routers"
    for d in (svc, tst, scr, rts):
        d.mkdir(parents=True)
    (svc / "w1.py").write_text('x("INSERT INTO fact_t VALUES (1)")', encoding="utf-8")
    (scr / "w2.py").write_text('x("CREATE TABLE fact_t (a INT)")', encoding="utf-8")
    (tst / "t.py").write_text('x("INSERT INTO fact_t VALUES (2)")', encoding="utf-8")
    (scr / "dyn.py").write_text(
        'x(f"INSERT INTO fact_{name} VALUES (3)")\ny(f"INSERT INTO {table} SELECT 1")',
        encoding="utf-8")
    writers, dynamic = fmap.scan_table_writers(tmp_path)
    assert writers == {"fact_t": [
        "backend/scripts/w2.py", "backend/services/w1.py",
    ]}
    # 部分动态 (fact_{) + 全动态 ({table}) 都必须显式计数, 不许静默漏
    assert dynamic == {"backend/scripts/dyn.py": 2}


def test_scan_table_writers_tracked_filter_drops_untracked(tmp_path: Path) -> None:
    svc = tmp_path / "backend" / "services"
    svc.mkdir(parents=True)
    (svc / "wip.py").write_text('x("INSERT INTO fact_ghost VALUES (1)")', encoding="utf-8")
    writers, _ = fmap.scan_table_writers(tmp_path, tracked=set())
    assert writers == {}


def test_scan_routes_real_repo_resolves_alias_prefixes() -> None:
    routes = fmap.scan_routes(REPO)
    assert routes, "routers 目录应扫出端点"
    non_empty = [r["prefix"] for r in routes.values() if r["prefix"]]
    # main.py 用 `router as <mod>_router` 别名注册 prefix — 修复前此处为全空 (对抗复审实锤)
    assert non_empty, "至少一个 router 必须解析出非空 prefix"
    assert any(p.startswith("/api") for p in non_empty)


def test_body_ignores_codegraph_stats_line() -> None:
    a = f"# H\n{fmap.CG_STATS_PREFIX} 节点 18,978 | calls 边 207,097\nbody\n"
    b = f"# H\n{fmap.CG_STATS_PREFIX} 节点 18,992 | calls 边 207,231\nbody\n"
    assert fmap._body(a) == fmap._body(b)


def test_body_ignores_snapshot_timestamp_line() -> None:
    a = "# H\n> Snapshot: 2026-06-11 18:00\nbody\n"
    b = "# H\n> Snapshot: 2026-06-12 09:30\nbody\n"
    c = "# H\n> Snapshot: 2026-06-12 09:30\nbody-changed\n"
    assert fmap._body(a) == fmap._body(b)
    assert fmap._body(a) != fmap._body(c)


def test_scan_chunkyctl_includes_map_subcommand() -> None:
    cmds = dict(fmap.scan_chunkyctl(REPO))
    assert "map" in cmds and "doctor" in cmds and "jobs" in cmds


def test_load_registry_real_repo_has_domains() -> None:
    rows = fmap.load_registry(REPO)
    domains = {r["domain"] for r in rows}
    assert "moneyflow" in domains
    assert all(r["table"].startswith("raw_") for r in rows)
