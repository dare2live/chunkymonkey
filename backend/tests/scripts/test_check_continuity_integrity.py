"""check_continuity_integrity 单测 (R1 根因 2/4/6 机械门, 2026-07-03).

五类检测各 >=2 测 + red-green: (1) 日历缺日 (中间空洞 FAIL / 尾部 SLA 内外 / 墓碑 / annotate);
(2) 横截面骤降 WARN + 分组缺失 FAIL (margin SSE-only 型); (3) 分组新鲜度断流 FAIL + dead_groups
墓碑 (ths_hot 子榜型); (4) 声明-实测错位 WARN + 深史稀疏年份 (income 型); (5) by_ts_code 断流
只 WARN 不 FAIL (stk_factor_pro 型)。另: registry 解析 (新键 + gap_tolerance 非法值报错) /
run_checks 编排 (only 过滤 / 库不可达 strict) / 告警 flag 写-自愈。全内存 DuckDB, 不碰真库。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from conftest import duck_mem  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_continuity_integrity", REPO / "backend" / "scripts" / "check_continuity_integrity.py")
cci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cci)


# ── fixtures ─────────────────────────────────────────────────────────────

def _weekdays(start: str, n: int) -> list[str]:
    """从 start (compact) 起的 n 个工作日 (合成交易日历, 单测无需真日历)。"""
    d = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def _mkspec(**kw) -> dict:
    base = {"domain": "dom", "db": "mem", "table": "t", "grain": ["ts_code", "trade_date"],
            "batch_mode": "by_trade_date", "data_start": "20260601", "sla": 1,
            "freshness_date_column": None, "date_param": None, "known_empty_days": set(),
            "gap_tolerance": "none", "freshness_group_col": None, "dead_groups": []}
    base.update(kw)
    return base


def _mktable(conn, days_rows: dict[str, int], iso: bool = False, table: str = "t"):
    """建表并按 {compact日: 行数} 填充 (iso=True 存 'YYYY-MM-DD' 混存归一路径)。"""
    conn.execute(f"CREATE TABLE {table} (ts_code TEXT, trade_date TEXT)")
    rows = []
    for d, n in days_rows.items():
        v = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if iso else d
        rows += [(f"c{i}", v) for i in range(n)]
    if rows:
        conn.executemany(f"INSERT INTO {table} VALUES (?, ?)", rows)


# ── 检测 1: calendar_gaps ────────────────────────────────────────────────

def test_calendar_gaps_interior_hole_red_green():
    """中间空洞 = FAIL (间歇空响应指纹); 补上该日 → PASS (red-green)。"""
    tds = _weekdays("20260601", 15)
    hole = tds[5]
    c = duck_mem()
    try:
        _mktable(c, {d: 3 for d in tds if d != hole})
        r = cci.check_calendar_gaps(c, _mkspec(), tds, tds[-1])
        assert r["status"] == "fail_interior_gaps" and hole in r["detail"]
        # green: 补上空洞日
        c.execute("INSERT INTO t VALUES ('c0', ?)", [hole])
        r2 = cci.check_calendar_gaps(c, _mkspec(), tds, tds[-1])
        assert r2["status"] == "pass"
    finally:
        c.close()


def test_calendar_gaps_tail_sla_ok_vs_fail():
    """尾部缺日: SLA 内 = pass (未到不算断流); 超 SLA = FAIL (stale tail)。"""
    tds = _weekdays("20260601", 15)
    c = duck_mem()
    try:
        _mktable(c, {d: 3 for d in tds[:-1]})     # 缺最后 1 日
        r = cci.check_calendar_gaps(c, _mkspec(sla=1), tds, tds[-1])
        assert r["status"] == "pass" and "尾部 1 日未到" in r["detail"]
        r2 = cci.check_calendar_gaps(c, _mkspec(sla=0), tds, tds[-1])
        assert r2["status"] == "fail_stale_tail"
    finally:
        c.close()


def test_calendar_gaps_known_empty_tombstone_and_annotate():
    """known_empty_days 墓碑排除中间空洞; gap_tolerance=annotate 空洞降 WARN 不 FAIL。"""
    tds = _weekdays("20260601", 15)
    hole = tds[5]
    c = duck_mem()
    try:
        _mktable(c, {d: 3 for d in tds if d != hole})
        r = cci.check_calendar_gaps(c, _mkspec(known_empty_days={hole}), tds, tds[-1])
        assert r["status"] == "pass"
        r2 = cci.check_calendar_gaps(c, _mkspec(gap_tolerance="annotate"), tds, tds[-1])
        assert r2["status"] == "warn_interior_gaps" and hole in r2["detail"]
    finally:
        c.close()


def test_calendar_gaps_iso_stored_dates_normalized():
    """表内 ISO 'YYYY-MM-DD' 存储与日历 compact 归一对齐 (dim_trading_calendar ISO 口径)。"""
    tds = _weekdays("20260601", 10)
    c = duck_mem()
    try:
        _mktable(c, {d: 2 for d in tds}, iso=True)
        r = cci.check_calendar_gaps(c, _mkspec(), tds, tds[-1])
        assert r["status"] == "pass"
    finally:
        c.close()


# ── 检测 2: cross_section ────────────────────────────────────────────────

def test_cross_section_row_dip_warn_red_green():
    """单日行数 < 近 20 观测日中位 x 0.6 = WARN; 补齐行数 → pass。"""
    tds = _weekdays("20260401", 30)
    dip_day = tds[25]
    counts = {d: 100 for d in tds}
    counts[dip_day] = 10
    c = duck_mem()
    try:
        _mktable(c, counts)
        r = cci.check_cross_section(c, _mkspec(data_start=tds[0]), tds, tds[-1])
        assert r["status"] == "warn_row_dip" and dip_day in r["detail"]
        # green: 补齐该日
        c.executemany("INSERT INTO t VALUES (?, ?)", [(f"x{i}", dip_day) for i in range(90)])
        r2 = cci.check_cross_section(c, _mkspec(data_start=tds[0]), tds, tds[-1])
        assert r2["status"] == "pass"
    finally:
        c.close()


def test_cross_section_missing_group_fail_margin_sse_only():
    """margin SSE-only 型: grain 含 exchange_id, 某日缺 SZSE 组 = FAIL (行在但横截面骤缺组)。"""
    tds = _weekdays("20260401", 20)
    bad_day = tds[15]
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (trade_date TEXT, exchange_id TEXT)")
        for d in tds:
            c.execute("INSERT INTO t VALUES (?, 'SSE')", [d])
            if d != bad_day:
                c.execute("INSERT INTO t VALUES (?, 'SZSE')", [d])
        spec = _mkspec(grain=["trade_date", "exchange_id"], data_start=tds[0])
        r = cci.check_cross_section(c, spec, tds, tds[-1])
        assert r["status"] == "fail_missing_groups"
        assert bad_day in r["detail"] and "SZSE" in r["detail"]
        # green: 补上缺组行
        c.execute("INSERT INTO t VALUES (?, 'SZSE')", [bad_day])
        r2 = cci.check_cross_section(c, spec, tds, tds[-1])
        assert r2["status"] == "pass"
    finally:
        c.close()


def test_cross_section_insufficient_history_skipped():
    """观测日不足 (新域首周) 不判骤降 — 防噪音。"""
    tds = _weekdays("20260601", 4)
    c = duck_mem()
    try:
        _mktable(c, {d: 5 for d in tds})
        r = cci.check_cross_section(c, _mkspec(), tds, tds[-1])
        assert r["status"] == "skipped_insufficient_history"
    finally:
        c.close()


# ── 检测 3: group_freshness ──────────────────────────────────────────────

def test_group_freshness_stalled_subboard_fail_and_dead_tombstone():
    """ths_hot 子榜断流型: 组 B 落后 > SLA x 3 = FAIL; dead_groups 墓碑后 = pass (red-green)。"""
    tds = _weekdays("20260401", 40)
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (trade_date TEXT, data_type TEXT)")
        for d in tds:
            c.execute("INSERT INTO t VALUES (?, '热股')", [d])
        c.execute("INSERT INTO t VALUES (?, '热基')", [tds[5]])   # 热基停更在窗口早期
        spec = _mkspec(freshness_group_col="data_type", sla=2, data_start=tds[0])
        r = cci.check_group_freshness(c, spec, tds, tds[-1])
        assert r["status"] == "fail_group_stalled" and "热基" in r["detail"]
        # green: 墓碑
        spec2 = {**spec, "dead_groups": ["热基"]}
        r2 = cci.check_group_freshness(c, spec2, tds, tds[-1])
        assert r2["status"] == "pass" and "墓碑" in r2["detail"]
    finally:
        c.close()


def test_group_freshness_all_fresh_pass():
    tds = _weekdays("20260601", 10)
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (trade_date TEXT, data_type TEXT)")
        for d in tds:
            c.execute("INSERT INTO t VALUES (?, 'A')", [d])
            c.execute("INSERT INTO t VALUES (?, 'B')", [d])
        spec = _mkspec(freshness_group_col="data_type", sla=2)
        assert cci.check_group_freshness(c, spec, tds, tds[-1])["status"] == "pass"
    finally:
        c.close()


# ── 检测 4: declared_vs_actual ───────────────────────────────────────────

def test_declared_drift_warn_with_suggestion_red_green():
    """dividend 型: 声明 20050104 实测 2023 起 = WARN 带建议修正值; 声明改齐 → pass。"""
    c = duck_mem()
    try:
        _mktable(c, {"20230111": 10, "20240110": 10, "20250110": 10})
        r = cci.check_declared_vs_actual(c, _mkspec(data_start="20050104"), today="20260703")
        assert r["status"] == "warn_declared_drift"
        assert "20230111" in r["fix_hint"]          # 建议修正值 = 实测 MIN
        r2 = cci.check_declared_vs_actual(c, _mkspec(data_start="20230111"), today="20260703")
        assert r2["status"] == "pass"
    finally:
        c.close()


def test_sparse_history_years_flagged():
    """income 型深史稀疏: 2021 年行数 < 参照完整年 x 0.3 = WARN 列年份; 正常年不列。"""
    c = duck_mem()
    try:
        days_rows = {"20210105": 50}                          # 稀疏年
        days_rows.update({f"2022{m:02d}10": 100 for m in range(1, 11)})   # 1000 行
        days_rows.update({f"2023{m:02d}10": 100 for m in range(1, 11)})
        days_rows.update({f"2024{m:02d}10": 100 for m in range(1, 11)})
        days_rows.update({f"2025{m:02d}10": 120 for m in range(1, 11)})   # 参照年 1200
        _mktable(c, days_rows)
        r = cci.check_declared_vs_actual(c, _mkspec(data_start="20210105"), today="20260703")
        assert r["status"] == "warn_sparse_history"
        assert "2021" in r["detail"] and "2022" not in r["detail"]
        assert "coverage_note" in r["fix_hint"]
    finally:
        c.close()


def test_declared_vs_actual_empty_table_skipped():
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (ts_code TEXT, trade_date TEXT)")
        r = cci.check_declared_vs_actual(c, _mkspec(), today="20260703")
        assert r["status"] == "skipped_empty_table"
    finally:
        c.close()


# ── 检测 5: static_staleness ─────────────────────────────────────────────

def test_static_staleness_warn_not_fail_red_green():
    """stk_factor_pro 型: MAX(built_at) 落后 > SLA x 5 = WARN (只警不 FAIL); 刷新后 pass。"""
    tds = _weekdays("20260401", 40)
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (ts_code TEXT, trade_date TEXT, built_at TIMESTAMP)")
        stale_day = tds[5]
        c.execute("INSERT INTO t VALUES ('c0', ?, ?)",
                  [stale_day, f"{stale_day[:4]}-{stale_day[4:6]}-{stale_day[6:8]} 18:00:00"])
        spec = _mkspec(batch_mode="by_ts_code", sla=1, data_start=tds[0])
        r = cci.check_static_staleness(c, spec, tds, tds[-1])
        assert r["status"] == "warn_stalled"          # 落后 34 交易日 > 1x5
        assert not r["status"].startswith("fail")     # 规格: 手动刷新域只警不 FAIL
        # green: 刷新 built_at 到最新
        last = tds[-1]
        c.execute("UPDATE t SET built_at = ?", [f"{last[:4]}-{last[4:6]}-{last[6:8]} 18:00:00"])
        r2 = cci.check_static_staleness(c, spec, tds, tds[-1])
        assert r2["status"] == "pass"
    finally:
        c.close()


def test_static_staleness_fallback_date_col_when_no_built_at():
    """无 built_at 列 → 回退日期列探测 (防御路径)。"""
    tds = _weekdays("20260401", 40)
    c = duck_mem()
    try:
        _mktable(c, {tds[0]: 1})
        spec = _mkspec(batch_mode="by_ts_code", sla=1)
        r = cci.check_static_staleness(c, spec, tds, tds[-1])
        assert r["status"] == "warn_stalled" and "trade_date" in r["detail"]
    finally:
        c.close()


# ── registry 解析 / 编排 / flag ──────────────────────────────────────────

def test_load_domain_specs_new_keys_and_bad_gap_tolerance(tmp_path):
    """新键解析 (gap_tolerance/freshness_group_col/dead_groups/known_empty_days);
    gap_tolerance 非法值 = 立即报错不静默。"""
    p = tmp_path / "reg.yaml"
    p.write_text(
        "defaults:\n  target_db: rawdb\n"
        "domains:\n"
        "  a:\n    target_table: t_a\n    grain: [x]\n    batch_mode: by_trade_date\n"
        "    data_start: '20240101'\n    freshness_sla_trading_days: 2\n"
        "    gap_tolerance: annotate\n    known_empty_days: ['20240312']\n"
        "  b:\n    target_table: t_b\n    grain: [trade_date, data_type]\n"
        "    batch_mode: by_trade_date\n    data_start: '20240101'\n"
        "    freshness_sla_trading_days: 2\n"
        "    freshness_group_col: data_type\n    dead_groups: ['热基']\n",
        encoding="utf-8")
    specs = cci.load_domain_specs(p)
    a = next(s for s in specs if s["domain"] == "a")
    assert a["gap_tolerance"] == "annotate" and a["known_empty_days"] == {"20240312"}
    b = next(s for s in specs if s["domain"] == "b")
    assert b["freshness_group_col"] == "data_type" and b["dead_groups"] == ["热基"]
    p2 = tmp_path / "bad.yaml"
    p2.write_text(
        "domains:\n  c:\n    target_table: t_c\n    grain: [x]\n"
        "    gap_tolerance: whatever\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cci.load_domain_specs(p2)


def test_real_registry_parses_with_ths_hot_group_col():
    """生产 sync_registry.yaml 真解析非空; ths_hot 示例键 freshness_group_col=data_type 在录。"""
    specs = cci.load_domain_specs()
    assert len(specs) >= 30
    th = next(s for s in specs if s["domain"] == "ths_hot")
    assert th["freshness_group_col"] == "data_type"
    assert all(s["gap_tolerance"] in cci.GAP_TOLERANCE_VALUES for s in specs)


def test_run_checks_only_filter_and_unreachable_strict():
    """--only 只跑单类; 库不可达默认跳过, --strict 才 FAIL (写锁期语义)。"""
    tds = _weekdays("20260601", 10)

    def _boom(alias):
        raise RuntimeError("Conflicting lock is held")

    specs = [_mkspec(domain="d1", db="locked")]
    results, failures = cci.run_checks(specs, _boom, tds, tds[-1])
    assert results[0]["status"] == "db_unreachable" and not failures
    _, failures = cci.run_checks(specs, _boom, tds, tds[-1], strict=True)
    assert len(failures) == 1

    def _fresh(alias):
        c = duck_mem()
        _mktable(c, {d: 3 for d in tds})
        return c

    results, _ = cci.run_checks([_mkspec()], _fresh, tds, tds[-1], only="calendar_gaps")
    assert {r["check"] for r in results} == {"calendar_gaps"}


def test_overall_status_and_alert_flag_write_selfheal(tmp_path):
    """FAIL 写 flag / 非 FAIL 自愈删 flag (与 /tmp/chunkymonkey_ALERT_*.flag 告警链同模式)。"""
    flag = tmp_path / "ALERT_continuity.flag"
    fail_results = [{"check": "calendar_gaps", "domain": "d", "db": "x", "table": "t",
                     "status": "fail_interior_gaps", "detail": "hole", "fix_hint": ""}]
    assert cci.overall_status(fail_results) == "FAIL"
    cci.write_alert_flag(flag, "FAIL", fail_results)
    assert flag.exists() and "fail_interior_gaps" in flag.read_text()
    ok_results = [{**fail_results[0], "status": "pass"}]
    assert cci.overall_status(ok_results) == "PASS"
    cci.write_alert_flag(flag, "PASS", ok_results)
    assert not flag.exists()
    warn = [{**fail_results[0], "status": "warn_row_dip"}]
    assert cci.overall_status(warn) == "WARN"   # WARN 不 exit 1, 不写 flag


def test_run_checks_full_pipeline_on_mem_domain():
    """端到端: by_trade_date 域跑 calendar+cross_section+declared 三类, 全 pass。"""
    tds = _weekdays("20260401", 30)

    def _fresh(alias):
        c = duck_mem()
        _mktable(c, {d: 50 for d in tds})
        return c

    spec = _mkspec(data_start=tds[0])
    results, failures = cci.run_checks([spec], _fresh, tds, tds[-1], today="20260703")
    assert not failures
    assert {r["check"] for r in results} == {"calendar_gaps", "cross_section", "declared_vs_actual"}
    assert all(r["status"] == "pass" for r in results)
