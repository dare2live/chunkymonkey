"""check_grain_uniqueness 单测 (R1 件2, 2026-07-03; grain 契约 S2 追加, 2026-09-11).

锁: (1) dup→FAIL / 清后→PASS red-green; (2) grain 列缺 = schema 漂移 FAIL; (3) 表缺 = skip
(注册未拉); (4) 豁免带到期日 (未到期降级 / 过期恢复 FAIL); (5) registry 解析 (默认库/同表去重
/ mart 映射并入); (6) 生产 registry 真解析非空; (7) multiplicity_index (grain 契约 S2):
类型非整数族 FAIL / 组内断号 FAIL / NULL 计为坏组 / 豁免覆盖断号不覆盖类型错 / registry
透传该键 / 未声明该键的表不受影响 (原 fail_duplicate_grain 路径零新字段)。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from conftest import duck_mem  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_grain_uniqueness", REPO / "backend" / "scripts" / "check_grain_uniqueness.py")
cgu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cgu)


def _conn_with_dups():
    c = duck_mem()
    c.execute("CREATE TABLE t (a TEXT, b TEXT, v DOUBLE)")
    c.executemany("INSERT INTO t VALUES (?, ?, ?)", [
        ("x", "1", 1.0), ("x", "1", 2.0), ("x", "1", 3.0),   # 1 dup 组, excess 2
        ("y", "2", 4.0),
    ])
    return c


def test_check_table_red_green():
    """造 dup → FAIL (dup_groups/excess 正确); 清后 → PASS。"""
    c = _conn_with_dups()
    try:
        r = cgu.check_table(c, "t", ["a", "b"])
        assert r["status"] == "fail_duplicate_grain"
        assert r["dup_groups"] == 1 and r["excess_rows"] == 2
        # 清 dup (keep 1) → green
        c.execute("DELETE FROM t WHERE a = 'x' AND v > 1.0")
        r2 = cgu.check_table(c, "t", ["a", "b"])
        assert r2 == {"status": "pass", "dup_groups": 0, "excess_rows": 0}
    finally:
        c.close()


def test_check_table_missing_grain_col_is_fail():
    """grain 列缺 = schema 漂移 → FAIL (与 sync_runner 缺 grain 列 raise 同语义, 不静默跳)。"""
    c = _conn_with_dups()
    try:
        r = cgu.check_table(c, "t", ["a", "quarter"])
        assert r["status"] == "fail_missing_grain_cols" and r["missing_cols"] == ["quarter"]
    finally:
        c.close()


def test_check_table_missing_table_is_skip():
    c = duck_mem()
    try:
        assert cgu.check_table(c, "nope", ["a"])["status"] == "skipped_missing_table"
    finally:
        c.close()


def test_run_checks_fail_and_exemption_lifecycle():
    """dup 未豁免 = FAIL; 豁免未到期 = 降级不 FAIL; 豁免过期 = 恢复 FAIL (豁免非永久白名单)。"""
    specs = [{"db": "mem", "table": "t", "grain": ["a", "b"], "origin": "test"}]
    today = "20260703"
    # 未豁免 → FAIL
    results, failures = cgu.run_checks(specs, lambda alias: _conn_with_dups(), today=today)
    assert len(failures) == 1 and failures[0]["status"] == "fail_duplicate_grain"
    # 豁免未到期 → 不 FAIL, 状态可见
    results, failures = cgu.run_checks(specs, lambda alias: _conn_with_dups(),
                                       exemptions={"t": "20260801"}, today=today)
    assert not failures and results[0]["status"] == "exempt_until_20260801"
    # 豁免过期 → FAIL
    results, failures = cgu.run_checks(specs, lambda alias: _conn_with_dups(),
                                       exemptions={"t": "20260702"}, today=today)
    assert len(failures) == 1 and failures[0]["status"] == "fail_exemption_expired"


def test_run_checks_unreachable_db_default_skip_strict_fail():
    """库不可达 (写锁): 默认跳过标记可见; --strict 才 FAIL。"""
    def _boom(alias):
        raise RuntimeError("Conflicting lock is held")

    specs = [{"db": "locked", "table": "t", "grain": ["a"], "origin": "test"}]
    results, failures = cgu.run_checks(specs, _boom)
    assert results[0]["status"] == "db_unreachable" and not failures
    _, failures = cgu.run_checks(specs, _boom, strict=True)
    assert len(failures) == 1


def test_load_registry_specs_defaults_and_dedup(tmp_path):
    """默认 target_db 合并; 同表同 grain 多域去重 (index_member_all/_hist 同表 MERGE 型);
    mart 映射并入。"""
    p = tmp_path / "reg.yaml"
    p.write_text(
        "defaults:\n  target_db: rawdb\n"
        "domains:\n"
        "  a: {target_table: t_a, grain: [x, y]}\n"
        "  a_hist: {target_table: t_a, grain: [x, y]}\n"
        "  b: {target_table: t_b, grain: [k], target_db: other}\n",
        encoding="utf-8")
    specs = cgu.load_registry_specs(p)
    reg_specs = [s for s in specs if s["origin"].startswith("sync_registry")]
    assert len(reg_specs) == 2   # t_a 去重成 1 + t_b
    ta = next(s for s in reg_specs if s["table"] == "t_a")
    assert ta["db"] == "rawdb" and ta["grain"] == ["x", "y"]
    tb = next(s for s in reg_specs if s["table"] == "t_b")
    assert tb["db"] == "other"
    marts = [s for s in specs if s["origin"] == "mart_grains"]
    assert {m["table"] for m in marts} >= {"mart_sector_pulse_daily", "mart_market_pulse_daily",
                                           "dim_stock_segment_daily", "fact_stock_form_daily"}


def test_real_registry_parses():
    """生产 sync_registry.yaml 真解析: 全部条目有 grain; 抽查 top_inst grain 含 side
    (grain 修复批 R0 之后的现状对账)。"""
    specs = cgu.load_registry_specs()
    reg_specs = [s for s in specs if s["origin"].startswith("sync_registry")]
    assert len(reg_specs) >= 30
    assert all(s["grain"] for s in reg_specs)
    ti = next(s for s in reg_specs if s["table"] == "raw_tushare_top_inst")
    assert "side" in ti["grain"]


def test_parse_exemptions_requires_expiry():
    assert cgu.parse_exemptions(["t:20260801"]) == {"t": "20260801"}
    with pytest.raises(SystemExit):
        cgu.parse_exemptions(["t"])          # 无到期日
    with pytest.raises(SystemExit):
        cgu.parse_exemptions(["t:soon"])     # 到期日非 YYYYMMDD


# ── multiplicity_index (grain 契约 S2, 2026-09-11) ──────────────────────────
# 表固定 grain=[a, b, seq]、index="seq"；a/b 是事件的"身份"轴 (grain − seq)，seq 是落地层
# 派生的到达顺序。行以 (a, b, seq) 传入。


def _conn_multiplicity(rows: list[tuple], seq_type: str = "INTEGER"):
    c = duck_mem()
    c.execute(f"CREATE TABLE t (a TEXT, b INTEGER, seq {seq_type})")
    c.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
    return c


def test_check_table_multiplicity_index_pass():
    """G1: seq INTEGER 且每个 (a,b) 组内恰为 1..n 连续 → pass。"""
    c = _conn_multiplicity([("x", 1, 1), ("x", 1, 2), ("y", 2, 1)])
    try:
        r = cgu.check_table(c, "t", ["a", "b", "seq"], "seq")
        assert r == {"status": "pass", "dup_groups": 0, "excess_rows": 0}
    finally:
        c.close()


def test_check_table_multiplicity_index_gap():
    """G2: 唯一性通过 (a,b,seq 全表唯一), 但 (x,1) 组 seq={1,3} 断号 (缺 2)
    → fail_multiplicity_index_gap, bad_groups=1。"""
    c = _conn_multiplicity([("x", 1, 1), ("x", 1, 3), ("y", 2, 1)])
    try:
        r = cgu.check_table(c, "t", ["a", "b", "seq"], "seq")
        assert r["status"] == "fail_multiplicity_index_gap"
        assert r["bad_groups"] == 1
    finally:
        c.close()


def test_check_table_multiplicity_index_null_counts_as_bad_group():
    """G3: seq NULL (旧契约行, 未走新落地路径) → COUNT(seq) < COUNT(*), 计为坏组
    → fail_multiplicity_index_gap (不是静默通过, 也不是 fail_missing_grain_cols)。"""
    c = _conn_multiplicity([("x", 1, None), ("y", 2, 1)])
    try:
        r = cgu.check_table(c, "t", ["a", "b", "seq"], "seq")
        assert r["status"] == "fail_multiplicity_index_gap"
        assert r["bad_groups"] == 1
    finally:
        c.close()


def test_check_table_multiplicity_index_wrong_type():
    """G4: 值本身连续 ('1','2'), 但列是 VARCHAR (字典序 MIN/MAX 会在 '10'<'2' 时假通过)
    → fail_multiplicity_index_type, 且必须先于连续性检查跑 (这条数据若走连续性检查会通过,
    只有类型检查能抓住它)。"""
    c = _conn_multiplicity([("x", 1, "1"), ("x", 1, "2")], seq_type="VARCHAR")
    try:
        r = cgu.check_table(c, "t", ["a", "b", "seq"], "seq")
        assert r["status"] == "fail_multiplicity_index_type"
    finally:
        c.close()


def test_run_checks_exemption_covers_gap_not_type():
    """G5: fail_multiplicity_index_gap (重落期过渡态) 受豁免覆盖降级；
    fail_multiplicity_index_type (schema bug) 豁免不覆盖, 仍 FAIL。"""
    today = "20260703"
    gap_specs = [{"db": "mem", "table": "t", "grain": ["a", "b", "seq"],
                  "multiplicity_index": "seq", "origin": "test"}]
    results, failures = cgu.run_checks(
        gap_specs,
        lambda alias: _conn_multiplicity([("x", 1, 1), ("x", 1, 3), ("y", 2, 1)]),
        exemptions={"t": "20260801"}, today=today)
    assert not failures and results[0]["status"] == "exempt_until_20260801"

    type_specs = [{"db": "mem", "table": "t", "grain": ["a", "b", "seq"],
                   "multiplicity_index": "seq", "origin": "test"}]
    results, failures = cgu.run_checks(
        type_specs,
        lambda alias: _conn_multiplicity([("x", 1, "1"), ("x", 1, "2")], seq_type="VARCHAR"),
        exemptions={"t": "20260801"}, today=today)
    assert len(failures) == 1 and failures[0]["status"] == "fail_multiplicity_index_type"


def test_load_registry_specs_multiplicity_index(tmp_path):
    """G6: registry 域声明 multiplicity_index 时原样透传; 未声明为 None。"""
    p = tmp_path / "reg.yaml"
    p.write_text(
        "defaults:\n  target_db: rawdb\n"
        "domains:\n"
        "  a: {target_table: t_a, grain: [x, y, seq], multiplicity_index: seq}\n"
        "  b: {target_table: t_b, grain: [k]}\n",
        encoding="utf-8")
    specs = cgu.load_registry_specs(p)
    reg_specs = {s["table"]: s for s in specs if s["origin"].startswith("sync_registry")}
    assert reg_specs["t_a"]["multiplicity_index"] == "seq"
    assert reg_specs["t_b"]["multiplicity_index"] is None


def test_check_table_no_multiplicity_index_is_unaffected():
    """G7 隔离: 不声明 multiplicity_index (默认 None) 的表即使有 grain 重复, 仍走原有
    fail_duplicate_grain 路径, 不带任何 multiplicity_index 相关新字段。"""
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (a TEXT, b INTEGER)")
        c.executemany("INSERT INTO t VALUES (?, ?)", [("x", 1), ("x", 1)])
        r = cgu.check_table(c, "t", ["a", "b"])
        assert r == {"status": "fail_duplicate_grain", "dup_groups": 1, "excess_rows": 1}
        assert "bad_groups" not in r and "multiplicity_index_type" not in r
    finally:
        c.close()
