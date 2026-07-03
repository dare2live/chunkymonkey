"""check_grain_uniqueness 单测 (R1 件2, 2026-07-03).

锁: (1) dup→FAIL / 清后→PASS red-green; (2) grain 列缺 = schema 漂移 FAIL; (3) 表缺 = skip
(注册未拉); (4) 豁免带到期日 (未到期降级 / 过期恢复 FAIL); (5) registry 解析 (默认库/同表去重
/ mart 映射并入); (6) 生产 registry 真解析非空。
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
