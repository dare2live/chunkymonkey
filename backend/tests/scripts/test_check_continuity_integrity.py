"""check_continuity_integrity 单测 (R1 根因 2/4/6 机械门, 2026-07-03).

五类检测各 >=2 测 + red-green: (1) 日历缺日 (中间空洞 FAIL / 尾部 SLA 内外 / 墓碑 / annotate);
(2) 横截面骤降 WARN + 分组缺失 FAIL (margin SSE-only 型); (3) 分组新鲜度断流 FAIL + dead_groups
墓碑 (分组子榜型); (4) 声明-实测错位 WARN + 深史稀疏年份 (income 型); (5) by_ts_code 断流
只 WARN 不 FAIL (stk_factor_pro 型)。另: registry 解析 (新键 + gap_tolerance 非法值报错) /
run_checks 编排 (only 过滤 / 库不可达 strict) / 告警 flag 写-自愈。全内存 DuckDB, 不碰真库。
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from conftest import duck_mem  # noqa: E402
from services.calendar_gate_rules import (  # noqa: E402
    CalendarGateRules,
    CalendarGateRulesError,
    load_calendar_gate_rules,
)
from services.data_sources.calendar_contract import calendar_contract_for_spec  # noqa: E402
from services.data_sources.sources.calendar_rule import (  # noqa: E402
    CalendarRuleError,
    derive_calendar_rows,
    load_holidays,
)
from services.data_sources.sync_runner import domain_spec, load_registry  # noqa: E402

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
            "gap_tolerance": "none", "freshness_group_col": None, "dead_groups": [],
            "known_group_gaps": {}, "row_dip_tolerance": False}
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


# ── calendar_horizon / calendar_next_year 夹具 (cut_calendar_horizon, 2026-09-25) ──────

def _rules(floor: str, warn: str, fail: str) -> CalendarGateRules:
    """floor: compact YYYYMMDD; warn/fail: 'MM-DD'。直接构造 (CalendarGateRules 是普通
    frozen dataclass, 不像 CalendarGenerationContract 那样禁止直接构造)。"""
    y, m, d = int(floor[:4]), int(floor[4:6]), int(floor[6:8])
    wm, wd = (int(x) for x in warn.split("-"))
    fm, fd = (int(x) for x in fail.split("-"))
    return CalendarGateRules(
        version=1,
        serve_projection_floor=date(y, m, d),
        next_year_warn_from=(wm, wd),
        next_year_fail_from=(fm, fd),
    )


def _contract():
    """读真注册表 (与 test_calendar_reader 同法), 不 monkeypatch。"""
    return calendar_contract_for_spec(domain_spec(load_registry(), "trade_cal"))


def _dim_from_rule(floor: date, year_end: date, holidays: dict) -> list[str]:
    """规则推导 [floor, year_end] 的开市日 compact 升序列表 (纯函数, 不经 fetch_raw)。"""
    rows, unconfirmed = derive_calendar_rows(floor, year_end, holidays)
    assert not unconfirmed, f"测试夹具自身配置不全, 缺年份 {unconfirmed}"
    return sorted(r["cal_date"] for r in rows if r["is_open"] == "1")


# 覆盖单年 2026 的最小夹具 (只一个哨兵节假日, 只测集合比对逻辑本身, 不掺节假日推导细节;
# 不能用空列表——2026-09-26 blocking 修复后空列表在窗口内被判"误录", 见
# test_serve_projection_current_year_empty_list_is_misrecorded_not_confirmed_fails)。
_NOW_2026 = datetime(2026, 6, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
_FLOOR_2026 = date(2026, 1, 1)
_YEAR_END_2026 = date(2026, 12, 31)
_HOLIDAYS_2026_ONLY: dict[int, set] = {2026: {date(2026, 5, 1)}}


def _forbidden_conn(alias: str):
    """calendar_horizon/calendar_next_year 不挂在任一域上, 不该碰 conn_for。"""
    raise AssertionError(f"conn_for 不应被调用 (alias={alias})")


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


def test_calendar_gaps_frozen_disabled_domain_observes_not_fail():
    """execution_policy=disabled → tail lag is observe_frozen_stale, not FAIL.

    Parallel to SLA FROZEN_STALE_OBSERVED: records local_max vs eligible_end,
    does not wash Continuity READY by deleting the check.
    """
    tds = _weekdays("20260601", 15)
    c = duck_mem()
    try:
        _mktable(c, {d: 3 for d in tds[:-4]})  # 尾部 4 日缺 > sla 1
        spec = _mkspec(
            sla=1,
            execution_policy_mode="disabled",
            execution_policy_reason="scope_blocked",
        )
        r = cci.check_calendar_gaps(c, spec, tds, tds[-1])
        assert r["status"] == "observe_frozen_stale"
        assert "frozen_observe" in r["detail"]
        assert "catchup_blocked=true" in r["detail"]
        assert "scope_blocked" in (r["fix_hint"] or "")
        # Enabled twin still FAILs (no blanket silence).
        r_fail = cci.check_calendar_gaps(
            c, _mkspec(sla=1, execution_policy_mode="enabled"), tds, tds[-1]
        )
        assert r_fail["status"] == "fail_stale_tail"
        assert cci.overall_status([r]) == "PASS"
        assert cci.overall_status([r_fail]) == "FAIL"
        summary = cci.summarize([r])
        assert summary["counts"].get("observe") == 1
        assert summary["counts"].get("fail", 0) == 0
    finally:
        c.close()


def test_calendar_gaps_formal_security_day_ignores_stale_raw():
    """daily/ST dual-path: accepted_partition at frontier PASS even if legacy raw lags."""
    tds = _weekdays("20260701", 10)
    c = duck_mem()
    try:
        c.execute(
            "CREATE TABLE accepted_partition ("
            "dataset_id TEXT, partition_value TEXT)"
        )
        c.executemany(
            "INSERT INTO accepted_partition VALUES (?, ?)",
            [("tier0.market_data.nominal_ohlcv_daily", d) for d in tds],
        )
        _mktable(c, {d: 2 for d in tds[:-3]}, table="raw_tushare_daily")
        spec = _mkspec(
            domain="daily",
            table="raw_tushare_daily",
            accepted_security_day=True,
            dataset_id="tier0.market_data.nominal_ohlcv_daily",
            data_start=tds[0],
            sla=1,
        )
        r = cci.check_calendar_gaps(c, spec, tds, tds[-1])
        assert r["status"] == "pass"
        assert "accepted_partition" in r["detail"]
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


def test_calendar_gaps_hk_holidays_typed_pass_and_residual_fail(tmp_path, monkeypatch):
    """hk_holidays: calendar-matched holes PASS; residual non-holiday holes FAIL."""
    tds = _weekdays("20260601", 12)
    holiday, real = tds[3], tds[7]
    cal = tmp_path / "hk.yaml"
    cal.write_text(f"schema_version: 1\ndays: ['{holiday}']\n", encoding="utf-8")
    monkeypatch.setattr(cci, "_HK_NORTHBOUND_CLOSED_CACHE", None)
    monkeypatch.setattr(cci, "HK_NORTHBOUND_CLOSED_PATH", cal)
    # bypass module cache by calling loader with explicit path via patched constant
    cci._HK_NORTHBOUND_CLOSED_CACHE = None

    c = duck_mem()
    try:
        _mktable(c, {d: 1 for d in tds if d not in {holiday, real}})
        # both holes present, only holiday in calendar → FAIL residual
        r_fail = cci.check_calendar_gaps(
            c, _mkspec(gap_tolerance="hk_holidays"), tds, tds[-1]
        )
        assert r_fail["status"] == "fail_interior_gaps"
        assert real in r_fail["detail"]
        assert holiday not in r_fail["detail"] or "非港股假期" in r_fail["detail"]

        # only holiday hole → typed PASS
        c.execute("INSERT INTO t VALUES ('c0', ?)", [real])
        cci._HK_NORTHBOUND_CLOSED_CACHE = None
        r_pass = cci.check_calendar_gaps(
            c, _mkspec(gap_tolerance="hk_holidays"), tds, tds[-1]
        )
        assert r_pass["status"] == "pass"
        assert "hk_holidays" in r_pass["detail"]
    finally:
        c.close()
        cci._HK_NORTHBOUND_CLOSED_CACHE = None


def test_calendar_gaps_event_sparse_typed_pass_keeps_tail_sla():
    """event_sparse: interior empty PASS; tail beyond SLA still FAIL."""
    tds = _weekdays("20260601", 12)
    hole = tds[4]
    c = duck_mem()
    try:
        # present through early days; hole interior; last 3 missing → tail
        present = {d: 2 for d in tds[:8] if d != hole}
        _mktable(c, present)
        r = cci.check_calendar_gaps(
            c, _mkspec(gap_tolerance="event_sparse", sla=1), tds, tds[-1]
        )
        assert r["status"] == "fail_stale_tail"
        assert "event_sparse" in r["detail"]

        # fill tail within SLA=5 → pass with event_sparse note
        for d in tds[-3:]:
            c.execute("INSERT INTO t VALUES ('c0', ?)", [d])
        r2 = cci.check_calendar_gaps(
            c, _mkspec(gap_tolerance="event_sparse", sla=5), tds, tds[-1]
        )
        assert r2["status"] == "pass"
        assert "event_sparse" in r2["detail"]
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


def test_margin_cross_section_requires_accepted_evidence(monkeypatch):
    from services.data_sources import margin_state

    c = duck_mem()
    try:
        c.execute(
            "CREATE TABLE canonical_margin_exchange_daily ("
            "trade_date DATE, exchange_id VARCHAR, ingest_batch_id VARCHAR)"
        )
        monkeypatch.setattr(
            margin_state,
            "accepted_margin_partitions",
            lambda _conn, **_kwargs: (),
        )
        spec = _mkspec(
            domain="margin",
            table="canonical_margin_exchange_daily",
            grain=["trade_date", "exchange_id"],
            accepted_margin=True,
        )

        result = cci.check_cross_section(c, spec, ["20260715"], "20260715")

        assert result["status"] == "fail_no_accepted_partitions"
    finally:
        c.close()


def test_margin_cross_section_excludes_orphan_canonical_rows(monkeypatch):
    from services.data_sources import margin_state

    days = _weekdays("20260701", 6)
    c = duck_mem()
    try:
        c.execute(
            "CREATE TABLE canonical_margin_exchange_daily ("
            "trade_date DATE, exchange_id VARCHAR, ingest_batch_id VARCHAR)"
        )
        accepted = []
        rows = []
        for index, day in enumerate(days):
            batch_id = f"accepted-{index}"
            accepted.append(
                SimpleNamespace(partition_value=day, batch_id=batch_id)
            )
            iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            rows.extend((iso, exchange, batch_id) for exchange in ("SSE", "SZSE", "BSE"))
        # A large unaccepted row set must not distort counts or establish coverage.
        orphan_day = f"{days[-1][:4]}-{days[-1][4:6]}-{days[-1][6:]}"
        rows.extend((orphan_day, f"ORPHAN-{index}", "unaccepted") for index in range(50))
        c.executemany(
            "INSERT INTO canonical_margin_exchange_daily VALUES (?, ?, ?)", rows
        )
        monkeypatch.setattr(
            margin_state,
            "accepted_margin_partitions",
                lambda _conn, **_kwargs: tuple(accepted),
        )
        spec = _mkspec(
            domain="margin",
            table="canonical_margin_exchange_daily",
            grain=["trade_date", "exchange_id"],
            accepted_margin=True,
            data_start=days[0],
        )

        result = cci.check_cross_section(c, spec, days, days[-1])

        assert result["status"] == "pass"
        assert result["detail"].startswith("6 观测日无骤降")
    finally:
        c.close()


def test_calendar_gaps_treats_incomplete_required_groups_as_missing():
    """日期虽存在但缺必需市场仍是缺口，不能被 DISTINCT(date) 洗白。"""
    tds = _weekdays("20260601", 10)
    partial = tds[5]
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (ts_code TEXT, trade_date TEXT, built_at TEXT)")
        rows = []
        for day in tds:
            rows.append(("600000.SH", day, "2026-06-30T00:00:00+00:00"))
            if day != partial:
                rows.append(("000001.SZ", day, "2026-06-30T00:00:00+00:00"))
        c.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
        spec = _mkspec(
            data_start=tds[0],
            min_rows_per_batch=2,
            min_rows_since="",
            min_rows_before=1,
            batch_completeness={
                "group_from": {"column": "ts_code", "transform": "exchange_suffix"},
                "required_groups": ["SH", "SZ"],
            },
        )

        result = cci.check_calendar_gaps(c, spec, tds, tds[-1])

        assert result["status"] == "fail_interior_gaps"
        assert partial in result["detail"]
    finally:
        c.close()


def test_margin_calendar_gaps_ignore_legacy_raw_and_require_accepted_pointer():
    """Legacy raw ahead cannot make the formal margin continuity gate green."""
    day = "20260715"
    c = duck_mem()
    try:
        c.execute("CREATE TABLE raw_tushare_margin(trade_date VARCHAR)")
        c.execute("INSERT INTO raw_tushare_margin VALUES ('20991231')")
        spec = _mkspec(
            domain="margin",
            table="canonical_margin_exchange_daily",
            grain=["trade_date", "exchange_id"],
            data_start=day,
            sla=0,
            accepted_margin=True,
        )

        result = cci.check_calendar_gaps(c, spec, [day], day)

        assert result["status"] == "fail_stale_tail"
        assert day in result["detail"]
    finally:
        c.close()


def test_calendar_gaps_treats_below_min_rows_day_as_missing_without_group_contract():
    """仅声明 min_rows 的域也不能让一行截断批被 DISTINCT(date) 洗绿。"""
    tds = _weekdays("20260601", 10)
    partial = tds[5]
    c = duck_mem()
    try:
        _mktable(c, {day: (1 if day == partial else 3) for day in tds})
        spec = _mkspec(
            data_start=tds[0],
            min_rows_per_batch=3,
            min_rows_since="",
            min_rows_before=1,
            batch_completeness={},
        )

        result = cci.check_calendar_gaps(c, spec, tds, tds[-1])

        assert result["status"] == "fail_interior_gaps"
        assert partial in result["detail"]
    finally:
        c.close()


def test_calendar_gaps_counts_full_landing_population_for_min_rows():
    """A4: landing gap gate counts BJ rows; serve filter is not a raw completeness gate."""
    day = "20260601"
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (ts_code TEXT, trade_date TEXT)")
        c.executemany(
            "INSERT INTO t VALUES (?, ?)",
            [("600000.SH", day), ("000001.SZ", day), ("830001.BJ", day)],
        )
        spec = _mkspec(
            data_start=day,
            min_rows_per_batch=3,
            min_rows_since="",
            min_rows_before=1,
            batch_completeness={},
            universe_filter=True,
            sla=0,
        )

        result = cci.check_calendar_gaps(c, spec, [day], day)

        assert result["status"] == "pass"
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


def test_cross_section_row_dip_tolerance_downgrades_to_pass():
    """report_rc/share_float 型: 已逐域单独审查的天然高方差域(row_dip_tolerance=true)骤降降 pass,
    未设域(false, 默认)仍照常 warn_row_dip (red-green 对照)。

    2026-07-08 字段从 gap_tolerance 拆分(owner=git log --grep gap_root_cause): stk_surv
    曾因日历稀疏理由(calendar_gaps 用途)被打 gap_tolerance, 若沿用旧的"gap_tolerance 连带抑制
    row_dip"逻辑, 会掩盖它同时存在的系统性 page_limit 截断 bug(丢 22%~87%)。row_dip 的容忍
    必须逐域单独声明, 不得从 gap_tolerance 继承——本测试改用独立的 row_dip_tolerance 字段。"""
    tds = _weekdays("20260401", 30)
    dip_day = tds[25]
    counts = {d: 100 for d in tds}
    counts[dip_day] = 10
    c = duck_mem()
    try:
        _mktable(c, counts)
        red = cci.check_cross_section(c, _mkspec(data_start=tds[0]), tds, tds[-1])
        assert red["status"] == "warn_row_dip"
        green = cci.check_cross_section(
            c, _mkspec(data_start=tds[0], row_dip_tolerance=True), tds, tds[-1])
        assert green["status"] == "pass" and dip_day in green["detail"]
        # gap_tolerance=annotate 单独设置(不带 row_dip_tolerance)不应再抑制 row_dip —— 这正是
        # 修正的盲区: 日历稀疏判断不该自动延伸到行数骤降判断。
        still_warn = cci.check_cross_section(
            c, _mkspec(data_start=tds[0], gap_tolerance="annotate"), tds, tds[-1])
        assert still_warn["status"] == "warn_row_dip"
    finally:
        c.close()


def test_cross_section_row_dip_known_empty_days_tombstone():
    """cyq_perf 20260615 型: 已墓碑的单日源端真异常不重报 dip, 未墓碑仍照常触发 (red-green)。"""
    tds = _weekdays("20260401", 30)
    dip_day = tds[25]
    counts = {d: 100 for d in tds}
    counts[dip_day] = 1
    c = duck_mem()
    try:
        _mktable(c, counts)
        red = cci.check_cross_section(c, _mkspec(data_start=tds[0]), tds, tds[-1])
        assert red["status"] == "warn_row_dip" and dip_day in red["detail"]
        green = cci.check_cross_section(
            c, _mkspec(data_start=tds[0], known_empty_days={dip_day}), tds, tds[-1])
        assert green["status"] == "pass"
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


def test_cross_section_known_group_gaps_tombstone_precise_date():
    """known_group_gaps (2026-07-05 R4 修复): margin 型域某日源端确认只回部分组,
    需按(日期,组)精确墓碑 — 不能用 dead_groups(永久整组豁免, 会致盲该组未来真断流) 或
    known_empty_days(只喂 calendar_gaps, 对 cross_section 的 fail_missing_groups 无效,
    2026-07-05 workflow 实测 patch known_empty_days 后 FAIL 原样复现)。"""
    tds = _weekdays("20260401", 20)
    bad_day = tds[15]
    other_day = tds[10]
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (trade_date TEXT, exchange_id TEXT)")
        for d in tds:
            c.execute("INSERT INTO t VALUES (?, 'SSE')", [d])
            if d != bad_day:
                c.execute("INSERT INTO t VALUES (?, 'SZSE')", [d])
            else:
                c.execute("INSERT INTO t VALUES (?, 'SSE')", [d])  # 行数持平, 只缺组不缺量, 隔离测 fail_missing_groups
        spec = _mkspec(grain=["trade_date", "exchange_id"], data_start=tds[0],
                        known_group_gaps={bad_day: {"SZSE"}})
        r = cci.check_cross_section(c, spec, tds, tds[-1])
        assert r["status"] == "pass", f"已墓碑的(日期,组)不应再 FAIL: {r}"

        # 精确匹配: 换一个未墓碑的日期缺同一组, 仍必须 FAIL (不能变成对 SZSE 整组永久放行)
        c.execute("DELETE FROM t WHERE trade_date = ? AND exchange_id = 'SZSE'", [other_day])
        r2 = cci.check_cross_section(c, spec, tds, tds[-1])
        assert r2["status"] == "fail_missing_groups"
        assert other_day in r2["detail"] and "SZSE" in r2["detail"]
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
    """分组子榜断流型: 组 B 落后 > SLA x 3 = FAIL; dead_groups 墓碑后 = pass (red-green)。"""
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


def test_declared_drift_reviewed_flag_suppresses_warn_red_green():
    """balancesheet/fina_indicator/stk_holdernumber 型: 已人工核实(coverage_note)的 drift 不该
    每次重报——data_start_reviewed=True 时降级 pass, 未设时仍照常 WARN (red-green 对照)。"""
    c = duck_mem()
    try:
        _mktable(c, {"20230111": 10, "20240110": 10, "20250110": 10})
        red = cci.check_declared_vs_actual(c, _mkspec(data_start="20050104"), today="20260703")
        assert red["status"] == "warn_declared_drift"
        green = cci.check_declared_vs_actual(
            c, _mkspec(data_start="20050104", data_start_reviewed=True), today="20260703")
        assert green["status"] == "pass"
        assert "已人工核实" in green["detail"]
    finally:
        c.close()


def test_accepted_margin_pre_coverage_retention_not_declared_drift():
    """margin v3: coverage_start=义务窗起点, 表内可保留更早 canonical 行 — 不得记 declared_drift。
    反向(actual_min > coverage_start)仍 WARN。"""
    c = duck_mem()
    try:
        _mktable(c, {"20190102": 2, "20250110": 2, "20260717": 2})
        retention = cci.check_declared_vs_actual(
            c,
            _mkspec(data_start="20260717", accepted_margin=True),
            today="20260723",
        )
        assert retention["status"] == "pass"
        assert "pre-coverage retention" in retention["detail"]
        under = cci.check_declared_vs_actual(
            c,
            _mkspec(data_start="20180101", accepted_margin=True),
            today="20260723",
        )
        assert under["status"] == "warn_declared_drift"
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


def test_declared_drift_reviewed_also_suppresses_sparse_history_relabel():
    """balancesheet 实况: 压掉 declared_drift 后同一现象不该从 sparse_history 分支重新冒出
    (同一份 coverage_note 覆盖两者); 未设 reviewed 时 sparse_history 仍照常触发。"""
    c = duck_mem()
    try:
        days_rows = {"20210105": 50}
        days_rows.update({f"2022{m:02d}10": 100 for m in range(1, 11)})
        days_rows.update({f"2023{m:02d}10": 100 for m in range(1, 11)})
        days_rows.update({f"2024{m:02d}10": 100 for m in range(1, 11)})
        days_rows.update({f"2025{m:02d}10": 120 for m in range(1, 11)})
        _mktable(c, days_rows)
        red = cci.check_declared_vs_actual(c, _mkspec(data_start="20210105"), today="20260703")
        assert red["status"] == "warn_sparse_history"
        green = cci.check_declared_vs_actual(
            c, _mkspec(data_start="20210105", data_start_reviewed=True), today="20260703")
        assert green["status"] == "pass"
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


def test_static_staleness_frozen_disabled_domain_observes_not_warn():
    """execution_policy=disabled (2026-09-18, project cut_frozen_domain_verdicts):

    冻结域没有源、不会再刷新 —— 陈旧仍如实记录(不删检查、不消音), 但不再混进
    "需要人工介入"的 WARN 队列, 判 observe_frozen_stalled。enabled 孪生同样陈旧仍 WARN,
    证明放宽只对冻结域生效 (red-green: 同一份数据两种域状态两种判定)。"""
    tds = _weekdays("20260401", 40)
    c = duck_mem()
    try:
        c.execute("CREATE TABLE t (ts_code TEXT, trade_date TEXT, built_at TIMESTAMP)")
        stale_day = tds[5]
        c.execute("INSERT INTO t VALUES ('c0', ?, ?)",
                  [stale_day, f"{stale_day[:4]}-{stale_day[4:6]}-{stale_day[6:8]} 18:00:00"])
        spec = _mkspec(
            batch_mode="by_ts_code", sla=1, data_start=tds[0],
            execution_policy_mode="disabled", execution_policy_reason="tushare_sunset_retire",
        )
        r = cci.check_static_staleness(c, spec, tds, tds[-1])
        assert r["status"] == "observe_frozen_stalled", r
        assert "frozen_observe" in r["detail"], r["detail"]
        assert "tushare_sunset_retire" in (r["fix_hint"] or ""), r
        # Enabled twin, identical staleness, still WARNs (no blanket silence).
        r_warn = cci.check_static_staleness(
            c, _mkspec(batch_mode="by_ts_code", sla=1, data_start=tds[0],
                       execution_policy_mode="enabled"),
            tds, tds[-1],
        )
        assert r_warn["status"] == "warn_stalled", r_warn
        assert cci.overall_status([r]) == "PASS"
        assert cci.overall_status([r_warn]) == "WARN"
        summary = cci.summarize([r])
        assert summary["counts"].get("observe") == 1
        assert summary["counts"].get("warn", 0) == 0
    finally:
        c.close()


def test_static_staleness_frozen_missing_table_is_still_skipped_missing_table():
    """表不存在时既有行为不能被冻结分支破坏 —— 表存在性检查必须排在冻结判定之前。"""
    c = duck_mem()
    tds = _weekdays("20260401", 5)
    try:
        spec = _mkspec(
            batch_mode="by_ts_code", sla=1,
            execution_policy_mode="disabled", execution_policy_reason="tushare_sunset_retire",
        )
        r = cci.check_static_staleness(c, spec, tds, tds[-1])
        assert r["status"] == "skipped_missing_table", r
    finally:
        c.close()


# ── registry 解析 / 编排 / flag ──────────────────────────────────────────

def test_load_domain_specs_new_keys_and_bad_gap_tolerance(tmp_path):
    """新键解析 (gap_tolerance/freshness_group_col/dead_groups/known_empty_days);
    gap_tolerance 非法值 = 立即报错不静默。

    watermark_table 透传 (2026-09-18 blocking finding, cut_frozen_domain_verdicts):
    此前所有涉及 watermark_table 的测试都是 _mkspec(watermark_table=...) 手工构造 spec,
    从未真的经过 load_domain_specs()/domain_spec() 这条生产装配路径 —— 把 check_continuity_
    integrity.py 里 "watermark_table": contract_spec.get("watermark_table") 改成
    "watermark_table": None 之后, 全部既有测试(含本文件其余 129 项)照样绿, 没有一条会变红。
    这里用 defaults 层与域级覆盖各挑一个**与生产值不同的哨兵字符串**(custom_wm_anchor /
    b_only_wm), 而不是复用生产真实的 mart_data_source_watermark —— 避免测试恰好因为字面量
    撞上生产默认值而"看起来测过"; 域 a 不覆盖 (验 defaults → contract_spec 这一层三层继承
    确实流进了这份白名单 spec), 域 b 显式覆盖 (验域级值不会被 defaults 层覆盖回去, 同一条
    透传两个方向都钉住)。"""
    p = tmp_path / "reg.yaml"
    p.write_text(
        "defaults:\n  target_db: rawdb\n  watermark_table: custom_wm_anchor\n"
        "domains:\n"
        "  a:\n    target_table: t_a\n    grain: [x]\n    batch_mode: by_trade_date\n"
        "    data_start: '20240101'\n    freshness_sla_trading_days: 2\n"
        "    available_after: '09:20'\n"
        "    gap_tolerance: annotate\n    known_empty_days: ['20240312']\n"
        "    min_rows_per_batch: 3\n    universe_filter: true\n"
        "    universe_filter_col: ts_code\n    universe_filter_prefixes: ['60', '00']\n"
        "  b:\n    target_table: t_b\n    grain: [trade_date, data_type]\n"
        "    batch_mode: by_trade_date\n    data_start: '20240101'\n"
        "    freshness_sla_trading_days: 2\n    watermark_table: b_only_wm\n"
        "    freshness_group_col: data_type\n    dead_groups: ['热基']\n"
        "    data_start_reviewed: true\n    row_dip_tolerance: true\n",
        encoding="utf-8")
    specs = cci.load_domain_specs(p)
    a = next(s for s in specs if s["domain"] == "a")
    assert a["gap_tolerance"] == "annotate" and a["known_empty_days"] == {"20240312"}
    assert a["data_start_reviewed"] is False   # 缺省 false
    assert a["row_dip_tolerance"] is False     # 缺省 false, 且不从 gap_tolerance 继承
    assert a["min_rows_per_batch"] == 3 and a["universe_filter"] is True
    assert a["universe_filter_col"] == "ts_code"
    assert a["universe_filter_prefixes"] == ["60", "00"]
    assert a["available_after"] == "09:20"
    assert a["watermark_table"] == "custom_wm_anchor"   # defaults 层透传, 域 a 未覆盖
    b = next(s for s in specs if s["domain"] == "b")
    assert b["freshness_group_col"] == "data_type" and b["dead_groups"] == ["热基"]
    assert b["data_start_reviewed"] is True
    assert b["row_dip_tolerance"] is True
    assert b["watermark_table"] == "b_only_wm"           # 域级覆盖赢 defaults
    p2 = tmp_path / "bad.yaml"
    p2.write_text(
        "domains:\n  c:\n    target_table: t_c\n    grain: [x]\n"
        "    gap_tolerance: whatever\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cci.load_domain_specs(p2)


def test_real_registry_excludes_retired_k3_domains():
    """生产 sync_registry 真解析非空; K3 退役域不得再登记; 无 data_type 分组列。"""
    specs = cci.load_domain_specs()
    assert len(specs) >= 30
    names = {s["domain"] for s in specs}
    retired = {"daily_info", "dc_daily", "hm_detail", "hm_list", "kpl_list", "ths_hot"}
    assert names.isdisjoint(retired), names & retired
    assert "dc_index" in names and "dc_member" in names
    assert "moneyflow_ind_dc" in names and "moneyflow_mkt_dc" in names
    assert "moneyflow_dc" in names and "top_list" in names
    assert not any(s.get("freshness_group_col") == "data_type" for s in specs)
    assert cci.CROSS_SECTION_GROUP_COLS == ("exchange_id",)
    margin = next(s for s in specs if s["domain"] == "margin")
    assert margin["accepted_margin"] is True
    assert margin["table"] == "canonical_margin_exchange_daily"
    assert margin["data_start"] == "20260717"
    assert margin["availability_policy"] == {
        "axis": "trading_day",
        "rule": "next_trading_session_at",
        "at": "09:00",
    }
    # watermark_table 透传的生产回归 (2026-09-18 blocking finding 修复, cut_frozen_domain_
    # verdicts 同一刀): test_load_domain_specs_new_keys_and_bad_gap_tolerance 已用合成哨兵值
    # 钉住 defaults/域级两层都能流进 load_domain_specs() 的白名单 spec; 这里补真实 registry
    # 这条腿, 证明生产装配路径(domain_spec 的三层继承 → load_domain_specs 的白名单透传)在
    # 真实 sync_registry.yaml 上确实接通。不重复硬编码 "mart_data_source_watermark" 第二份
    # 字面量副本(同一参数只定义一处) —— 直接从生产 YAML 自己的 defaults 节读出声明值来比对,
    # 而不是猜一个字符串; margin 域自己不覆盖 watermark_table, 这条断言因此实际检验的是
    # defaults 层那条继承链。
    real_raw = cci.yaml.safe_load(cci.REGISTRY_PATH.read_text(encoding="utf-8"))
    assert margin["watermark_table"] == real_raw["defaults"]["watermark_table"]
    # 2026-09-07 删两行。原写 == "enabled" / == "bounded_calendar_catchup", 钉的是
    # margin 当时的**运行时策略状态**, 与本测试的主题 (K3 退役域不得再登记 / 无 data_type
    # 分组列) 无关; margin 按 tushare_sunset 台账切成 freeze 后它必然假红。
    # 「registry 的 execution_policy 必须与 sunset 台账裁决对得上」这条不变量已有唯一
    # 计算点: check_tushare_sunset.py 的 validate_freeze_execution_disabled (检查 8),
    # 两个方向都反向验证过。在这里再抄一份 = 同一判据两处实现, 迟早一处改了另一处没改。
    assert all(s["gap_tolerance"] in cci.GAP_TOLERANCE_VALUES for s in specs)


def test_margin_continuity_weekend_frontier_uses_typed_contract(monkeypatch):
    from services.data_sources import margin_state

    spec = next(
        item for item in cci.load_domain_specs() if item["domain"] == "margin"
    )
    spec["sla"] = 0
    # v3 coverage_start=20260717 — obligations are generation-local.
    accepted_state = SimpleNamespace(
        dates=frozenset({"20260717", "20260720"}),
        partitions=(),
        batch_by_partition={},
    )
    seen = []
    monkeypatch.setattr(
        margin_state,
        "load_margin_accepted_state",
        lambda _conn, *, contract=None: seen.append(contract) or accepted_state,
    )
    conn = duck_mem()

    results, failures = cci.run_checks(
        [spec],
        lambda _alias: conn,
        ["20260717", "20260720", "20260721"],
        "20260720",
        only="calendar_gaps",
        now=datetime(2026, 7, 21, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert failures == []
    assert results[0]["status"] == "pass"
    assert "2 应有交易日全在库" in results[0]["detail"]
    assert len(seen) == 1
    assert seen[0] is spec["_margin_contract"]


def test_margin_continuity_reuses_one_contract_and_accepted_snapshot(monkeypatch):
    from services.data_sources import margin_state, sync_runner

    days = _weekdays("20260715", 6)
    spec = next(
        item for item in cci.load_domain_specs() if item["domain"] == "margin"
    )
    planned = spec["_margin_contract"]
    batches = {day: f"batch-{index}" for index, day in enumerate(days)}
    accepted_state = SimpleNamespace(
        dates=frozenset(days),
        partitions=tuple(
            SimpleNamespace(partition_value=day, batch_id=batch_id)
            for day, batch_id in batches.items()
        ),
        batch_by_partition=batches,
    )
    seen = []
    monkeypatch.setattr(
        margin_state,
        "load_margin_accepted_state",
        lambda _conn, *, contract=None: seen.append(contract) or accepted_state,
    )
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(
            eligible_end=days[-1], reason="published"
        ),
    )
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE canonical_margin_exchange_daily ("
        "trade_date DATE, exchange_id VARCHAR, ingest_batch_id VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO canonical_margin_exchange_daily VALUES (?, ?, ?)",
        [
            (
                f"{day[:4]}-{day[4:6]}-{day[6:]}",
                exchange,
                batches[day],
            )
            for day in days
            for exchange in ("SSE", "SZSE", "BSE")
        ],
    )

    results, _failures = cci.run_checks(
        [spec], lambda _alias: conn, days, days[-1]
    )

    assert {item["check"] for item in results} >= {
        "calendar_gaps",
        "cross_section",
    }
    assert len(seen) == 1
    assert seen[0] is planned


def test_run_checks_only_filter_and_unreachable_strict():
    """--only 只跑单类; 库不可达默认跳过, --strict 才 FAIL (写锁期语义)。"""
    tds = _weekdays("20260601", 10)

    def _boom(alias):
        raise RuntimeError("Conflicting lock is held")

    # domain="d1" 显式指名, 排除全局 calendar_horizon (它不挂在任一域上, 靠 wall-clock today
    # 判前瞻余量, 本测试合成的 10 天历史窗口跟真实"今天"无关, 混进来会让 calendar_horizon
    # 自己 FAIL 污染这条"db_unreachable 专属语义"断言——它有自己的专门测试)。
    specs = [_mkspec(domain="d1", db="locked")]
    results, failures = cci.run_checks(specs, _boom, tds, tds[-1], domain="d1")
    assert results[0]["status"] == "db_unreachable" and not failures
    _, failures = cci.run_checks(specs, _boom, tds, tds[-1], strict=True, domain="d1")
    assert len(failures) == 1

    def _fresh(alias):
        c = duck_mem()
        _mktable(c, {d: 3 for d in tds})
        return c

    results, _ = cci.run_checks([_mkspec()], _fresh, tds, tds[-1], only="calendar_gaps")
    assert {r["check"] for r in results} == {"calendar_gaps"}


def test_run_checks_uses_each_domains_available_after_frontier():
    """同一时刻早发布域应查今日，t+1 域仍只查前一交易日。"""
    tds = ["20260715", "20260716"]

    def _conn_with_only_yesterday(_alias):
        c = duck_mem()
        _mktable(c, {"20260715": 3})
        return c

    now = datetime(2026, 7, 16, 9, 21, tzinfo=ZoneInfo("Asia/Shanghai"))
    published = _mkspec(
        available_after="09:20",
        data_start=tds[0],
        sla=0,
    )
    pending = _mkspec(
        domain="t_plus_one",
        available_after="t+1",
        data_start=tds[0],
        sla=0,
    )

    published_results, _ = cci.run_checks(
        [published],
        _conn_with_only_yesterday,
        tds,
        tds[0],
        only="calendar_gaps",
        domain="dom",
        now=now,
    )
    pending_results, _ = cci.run_checks(
        [pending],
        _conn_with_only_yesterday,
        tds,
        tds[0],
        only="calendar_gaps",
        domain="t_plus_one",
        now=now,
    )

    assert published_results[0]["status"] == "fail_stale_tail"
    assert pending_results[0]["status"] == "pass"


def test_run_checks_skips_when_domain_has_no_eligible_partition_yet():
    """首个交易日尚未到域可用时点时，没有前一分区可查，不能回退全局 frontier 误报。"""
    day = "20260716"

    def _empty_table(_alias):
        c = duck_mem()
        _mktable(c, {})
        return c

    results, failures = cci.run_checks(
        [_mkspec(available_after="t+1", data_start=day, sla=0)],
        _empty_table,
        [day],
        day,
        only="calendar_gaps",
        domain="dom",
        now=datetime(2026, 7, 16, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert not failures
    assert results[0]["status"] == "skipped_not_yet_eligible"


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
    # domain="dom" 显式指名 (= spec 的域), 排除全局 calendar_horizon (它不挂在任一域上, 靠
    # wall-clock today 判前瞻余量, 与本测试合成的历史 tds 窗口无关——calendar_horizon 有自己
    # 的专门测试 test_calendar_horizon_*)。
    results, failures = cci.run_checks([spec], _fresh, tds, tds[-1], today="20260703", domain="dom")
    assert not failures
    assert {r["check"] for r in results} == {"calendar_gaps", "cross_section", "declared_vs_actual"}
    assert all(r["status"] == "pass" for r in results)


# ── 检测 6/7: calendar_horizon (换刀, 2026-09-25 cut_calendar_horizon) /
#    calendar_next_year (新增) ───────────────────────────────────────────
# 替换旧的"today 之后剩余交易日数"标量门。C1-C7 每条对应规格 §8 表格里"其它全满足只违反
# 它"的隔离用例; R1/R2 用真配置 (不 monkeypatch) 验证整体不误报/按期 FAIL。

def test_serve_projection_missing_one_interior_day_fails():
    """C1: dim 缺一个窗口中部的交易日 -> fail_serve_projection_drift, missing=1 extra=0。"""
    dim = _dim_from_rule(_FLOOR_2026, _YEAR_END_2026, _HOLIDAYS_2026_ONLY)
    hole = dim[len(dim) // 2]
    dim_missing_one = [d for d in dim if d != hole]
    rules = _rules("20260101", "11-15", "12-20")
    r = cci.check_calendar_serve_projection(
        dim_missing_one, _NOW_2026,
        rules=rules, contract=_contract(), holidays=_HOLIDAYS_2026_ONLY)
    assert r["status"] == "fail_serve_projection_drift" and r["check"] == "calendar_horizon"
    assert "missing=1 extra=0" in r["detail"]
    assert f"first_missing={hole}" in r["detail"]


def test_serve_projection_missing_plus_extra_same_count_fails():
    """C1 附加用例: 缺一天 + 多一天 (计数相等) 也必须红 —— 防止把判据错写成比 len()。"""
    dim = _dim_from_rule(_FLOOR_2026, _YEAR_END_2026, _HOLIDAYS_2026_ONLY)
    hole = dim[len(dim) // 2]
    weekend_extra = "20260103"  # 周六, 不在任何推导开市集合里
    assert weekend_extra not in dim
    dim_swapped = sorted([d for d in dim if d != hole] + [weekend_extra])
    rules = _rules("20260101", "11-15", "12-20")
    r = cci.check_calendar_serve_projection(
        dim_swapped, _NOW_2026,
        rules=rules, contract=_contract(), holidays=_HOLIDAYS_2026_ONLY)
    assert r["status"] == "fail_serve_projection_drift"
    assert "missing=1 extra=1" in r["detail"]


def test_serve_projection_extra_closed_day_fails():
    """C2: dim 多一个规则休市日 -> fail, missing=0 extra=1 (防判据只查子集不查反向)。"""
    dim = _dim_from_rule(_FLOOR_2026, _YEAR_END_2026, _HOLIDAYS_2026_ONLY)
    extra_day = "20260103"  # 周六
    dim_with_extra = sorted(dim + [extra_day])
    rules = _rules("20260101", "11-15", "12-20")
    r = cci.check_calendar_serve_projection(
        dim_with_extra, _NOW_2026,
        rules=rules, contract=_contract(), holidays=_HOLIDAYS_2026_ONLY)
    assert r["status"] == "fail_serve_projection_drift"
    assert "missing=0 extra=1" in r["detail"]
    assert f"first_extra={extra_day}" in r["detail"]


def test_serve_projection_tail_not_extended_fails_with_republish_hint():
    """C3: dim 尾部未延伸到观测年年底 -> fail, first_missing=年中截断次日, fix_hint 含"未延伸"。"""
    now = datetime(2026, 10, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    truncated_dim = _dim_from_rule(_FLOOR_2026, date(2026, 6, 30), _HOLIDAYS_2026_ONLY)
    rules = _rules("20260101", "11-15", "12-20")
    r = cci.check_calendar_serve_projection(
        truncated_dim, now,
        rules=rules, contract=_contract(), holidays=_HOLIDAYS_2026_ONLY)
    assert r["status"] == "fail_serve_projection_drift"
    assert "first_missing=20260701" in r["detail"]
    assert "未延伸" in r["fix_hint"]


def test_serve_projection_truncated_head_fails_against_declared_floor():
    """C4: dim 被削头 (从 2006 年起), floor 仍声明 2005-01-04 -> fail, first_missing=声明下界
    (不是 min(dim) 自指——floor 被削头时若拿 min(dim) 当下界, 门会看不见这个缺口)。"""
    # 每年一个哨兵节假日 (非空), 避免 2026-09-26 blocking 修复后的"空表=误录"判据抢先命中
    # (本用例要测的是削头, 不是未配置)。
    holidays_2005_2026 = {y: {date(y, 5, 1)} for y in range(2005, 2027)}
    full_dim = _dim_from_rule(date(2005, 1, 4), date(2026, 12, 31), holidays_2005_2026)
    truncated_dim = [d for d in full_dim if d >= "20060104"]
    rules = _rules("20050104", "11-15", "12-20")
    r = cci.check_calendar_serve_projection(
        truncated_dim, _NOW_2026,
        rules=rules, contract=_contract(), holidays=holidays_2005_2026)
    assert r["status"] == "fail_serve_projection_drift"
    assert "first_missing=20050104" in r["detail"]


def test_serve_projection_current_year_unconfirmed_fails_named_status():
    """C5: 当年 (今年) 未在 holidays 里配置 -> fail_year_unconfirmed (精确状态串, 不是
    startswith("fail")); 不落到集合比对分支 (dim 传空列表也不影响结果)。"""
    now = datetime(2026, 6, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    rules = _rules("20260101", "11-15", "12-20")
    holidays_missing_current_year = {2025: set()}  # 缺 2026
    r = cci.check_calendar_serve_projection(
        [], now, rules=rules, contract=_contract(), holidays=holidays_missing_current_year)
    assert r["status"] == "fail_year_unconfirmed"
    assert "2026" in r["detail"]


def test_serve_projection_current_year_empty_list_is_misrecorded_not_confirmed_fails():
    """C5b (2026-09-26 blocking 修复的隔离用例, 其它条件全满足只违反"当年空表不算配置"这
    一条): 当年在 holidays 里出现了 key 但值是空列表 (占位误录, 如 market_holidays.yaml
    写 "'2026':" 后面只有注释) —— derive_calendar_rows 自己的 unconfirmed 判据是
    `set(holidays)`, 空集合也算"已配置", 会把该年整年按周末规则推导成幻影交易日 (每年约
    19 个) 而不进 unconfirmed。本检测必须额外识别这种情形, 仍判 fail_year_unconfirmed,
    不落到集合比对分支拿幻影行去跟 dim 比。"""
    now = datetime(2026, 6, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    rules = _rules("20260101", "11-15", "12-20")
    holidays_current_year_empty = {2026: set()}  # key 存在, 值是空集合 (不是缺失年份)
    r = cci.check_calendar_serve_projection(
        [], now, rules=rules, contract=_contract(), holidays=holidays_current_year_empty)
    assert r["status"] == "fail_year_unconfirmed"
    assert "2026" in r["detail"]


def test_serve_projection_historic_confirmed_empty_year_before_today_is_not_misrecorded():
    """C5c (2026-09-26 第二轮 blocking 修复的隔离用例, 其它条件全满足只违反"空表误录守卫
    的年份范围应止于 floor.year 还是 today.year"这一条): market_holidays.yaml 里
    `'1990': []` 是文档承认的合法史实 (该年日历仅 12-19~12-31, 交易所自身没有节假日概念,
    见 market_holidays.yaml:32/45), 不是占位误录。calendar_gate.yaml:6 明写"若将来把 dim
    回填到 1990, 改这里 (floor)"——本用例复现那个将来: floor 改成 19901219, dim 也回填到
    1990-12-19 起。除 1990 外 [1991,2026] 每年都有一个非空哨兵节假日 (排除"当年/其它年未
    配置"路径抢先命中), dim 与规则推导逐日一致 (排除集合缺口路径)。旧写法 (misrecorded_empty
    范围 = [floor.year, year_end.year] = [1990, 2026]) 会把 1990 也纳入检查并判它"误录"
    (len(holidays[1990])==0) -> fail_year_unconfirmed, 与该空表的文档合法性矛盾, 且
    preflight 自修 (发布代际 + build_latest) 无法清掉这个 FAIL, daily_update 永久 hard fail。
    修复后范围止于 max(floor.year, today.year), 1990 早于 today.year (2026) 天然不受这条
    误录守卫约束 -> 应为 pass。"""
    floor = date(1990, 12, 19)
    year_end = date(2026, 12, 31)
    holidays = {1990: set()}
    holidays.update({y: {date(y, 5, 1)} for y in range(1991, 2027)})
    dim = _dim_from_rule(floor, year_end, holidays)
    rules = _rules("19901219", "11-15", "12-20")
    r = cci.check_calendar_serve_projection(
        dim, _NOW_2026, rules=rules, contract=_contract(), holidays=holidays)
    assert r["status"] == "pass"


def test_calendar_checks_invalid_holidays_config_fail_closed_not_crash():
    """C6: holidays 加载失败时调用方注入异常实例 (而非重新抛出); 两个桶都必须转成
    fail_rule_config_invalid, 不崩溃。"""
    rules = _rules("20260101", "11-15", "12-20")
    contract = _contract()
    bad_holidays = CalendarRuleError("boom: market_holidays.yaml 格式非法 (测试注入)")

    r1 = cci.check_calendar_serve_projection(
        [], _NOW_2026, rules=rules, contract=contract, holidays=bad_holidays)
    assert r1["status"] == "fail_rule_config_invalid"

    r2 = cci.check_calendar_next_year_entry(
        _NOW_2026, rules=rules, contract=contract, holidays=bad_holidays)
    assert r2["status"] == "fail_rule_config_invalid"


# C6 above only exercises the isinstance(x, Exception) branch inside the check_* functions —
# it hands them a pre-built exception instance directly, bypassing run_checks' own three
# try/except blocks entirely (contract_value / holidays_value / rules_value 各自的加载 +
# 捕获). 下面三条各自 monkeypatch 真正的加载函数使其 raise, 不传 calendar_rules/
# calendar_contract/calendar_holidays (留 None 走 run_checks 默认加载路径), 逐条隔离验证
# run_checks 自己的接线 (规格 §7.4/§8 C6 点名的这段此前无测试覆盖)。

def test_run_checks_calendar_contract_load_failure_fails_closed_not_crash(monkeypatch):
    """新增(2026-09-26 blocking 修复): 只让 contract 加载失败 (holidays/rules 走真配置正常
    加载), 断言 run_checks 自己的 try/except ValueError 接线把异常转成结果行, 不崩溃。"""
    def _boom(*_a, **_kw):
        raise ValueError("registry drift (injected)")

    monkeypatch.setattr(
        "services.data_sources.calendar_contract.calendar_contract_for_spec", _boom)
    results, _ = cci.run_checks(
        [], _forbidden_conn, [], "20260101", only=None, now=_NOW_2026)
    assert len(results) == 2
    assert all(r["status"] == "fail_contract_unavailable" for r in results), results


def test_run_checks_calendar_holidays_load_failure_fails_closed_not_crash(monkeypatch):
    """新增(2026-09-26 blocking 修复): 只让 holidays 加载失败, 断言 run_checks 自己的
    try/except CalendarRuleError 接线把异常转成结果行, 不崩溃。"""
    def _boom(*_a, **_kw):
        raise CalendarRuleError("boom (injected)")

    monkeypatch.setattr(
        "services.data_sources.sources.calendar_rule.load_holidays", _boom)
    results, _ = cci.run_checks(
        [], _forbidden_conn, [], "20260101", only=None, now=_NOW_2026)
    assert len(results) == 2
    assert all(r["status"] == "fail_rule_config_invalid" for r in results), results


def test_run_checks_calendar_rules_load_failure_fails_closed_not_crash(monkeypatch):
    """新增(2026-09-26 blocking 修复): 只让 calendar_gate.yaml 加载失败, 断言 run_checks
    自己的 try/except CalendarGateRulesError 接线把异常转成结果行, 不崩溃。"""
    def _boom(*_a, **_kw):
        raise CalendarGateRulesError("boom (injected)")

    monkeypatch.setattr(
        "services.calendar_gate_rules.load_calendar_gate_rules", _boom)
    results, _ = cci.run_checks(
        [], _forbidden_conn, [], "20260101", only=None, now=_NOW_2026)
    assert len(results) == 2
    assert all(r["status"] == "fail_rule_config_invalid" for r in results), results


_STD_RULES = _rules("20260101", "11-15", "12-20")


def test_next_year_before_warn_from_passes():
    """C7a: warn_from 前一天 -> pass。"""
    now = datetime(2026, 11, 14, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = cci.check_calendar_next_year_entry(
        now, rules=_STD_RULES, contract=_contract(), holidays={2026: set()})
    assert r["status"] == "pass" and r["check"] == "calendar_next_year"


def test_next_year_on_warn_from_warns():
    """C7b: warn_from 当天 -> warn_next_year_unconfigured。"""
    now = datetime(2026, 11, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = cci.check_calendar_next_year_entry(
        now, rules=_STD_RULES, contract=_contract(), holidays={2026: set()})
    assert r["status"] == "warn_next_year_unconfigured"


def test_next_year_day_before_fail_from_still_warn():
    """C7c: fail_from 前一天仍是 warn (不是 fail)。"""
    now = datetime(2026, 12, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = cci.check_calendar_next_year_entry(
        now, rules=_STD_RULES, contract=_contract(), holidays={2026: set()})
    assert r["status"] == "warn_next_year_unconfigured"


def test_next_year_on_fail_from_fails():
    """C7d: fail_from 当天 -> fail_next_year_unconfigured。"""
    now = datetime(2026, 12, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = cci.check_calendar_next_year_entry(
        now, rules=_STD_RULES, contract=_contract(), holidays={2026: set()})
    assert r["status"] == "fail_next_year_unconfigured"


def test_next_year_configured_passes_regardless_of_date():
    """C7e: 下一年已配置 (非空) -> pass, 即使 now = fail_from (忽略日期)。"""
    now = datetime(2026, 12, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = cci.check_calendar_next_year_entry(
        now, rules=_STD_RULES, contract=_contract(),
        holidays={2026: set(), 2027: {date(2027, 1, 1)}})
    assert r["status"] == "pass"
    assert "2027" in r["detail"]


def test_next_year_empty_list_is_not_configured():
    """C7f: 下一年配置为空列表 (`[]`) 不算已确认 -> now=fail_from 时仍 fail。"""
    now = datetime(2026, 12, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = cci.check_calendar_next_year_entry(
        now, rules=_STD_RULES, contract=_contract(),
        holidays={2026: set(), 2027: set()})
    assert r["status"] == "fail_next_year_unconfigured"


def test_next_year_january_does_not_alarm():
    """C7g: 1 月, next_year(=今年+1) 现实中尚未公布 (国务院历年 10~12 月才公布次年安排,
    1 月自然未配置) 也不该误报——(1,5) 早于 warn_from, 无论 next_year 算对与否日期比较都
    不会红; 用消息里的年份数字区分"算对了 today.year+1"与"算成 today.year+2"两种情形。"""
    now = datetime(2027, 1, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = cci.check_calendar_next_year_entry(
        now, rules=_STD_RULES, contract=_contract(), holidays={2026: set()})
    assert r["status"] == "pass"
    assert "2028" in r["detail"]
    assert "2029" not in r["detail"]


def test_calendar_checks_only_filter_separates_hard_gate_from_deadline():
    """C9: run_checks 里 --only calendar_horizon 恰 1 行 (硬门); --only calendar_next_year
    恰 1 行 (期限); only=None 恰 2 行; domain 指定非 trade_cal 时两者皆 0 行。specs=[] 且
    conn_for 断言不会被调用 (calendar 桶不挂在任一域上, 不该碰 conn_for)。"""
    dim = _dim_from_rule(_FLOOR_2026, _YEAR_END_2026, _HOLIDAYS_2026_ONLY)
    rules = _rules("20260101", "11-15", "12-20")
    contract = _contract()
    holidays = _HOLIDAYS_2026_ONLY

    r1, _ = cci.run_checks(
        [], _forbidden_conn, dim, dim[-1], only="calendar_horizon", now=_NOW_2026,
        calendar_rules=rules, calendar_contract=contract, calendar_holidays=holidays)
    assert len(r1) == 1 and r1[0]["check"] == "calendar_horizon"

    r2, _ = cci.run_checks(
        [], _forbidden_conn, dim, dim[-1], only="calendar_next_year", now=_NOW_2026,
        calendar_rules=rules, calendar_contract=contract, calendar_holidays=holidays)
    assert len(r2) == 1 and r2[0]["check"] == "calendar_next_year"

    r3, _ = cci.run_checks(
        [], _forbidden_conn, dim, dim[-1], only=None, now=_NOW_2026,
        calendar_rules=rules, calendar_contract=contract, calendar_holidays=holidays)
    assert len(r3) == 2
    assert {x["check"] for x in r3} == {"calendar_horizon", "calendar_next_year"}

    r4, _ = cci.run_checks(
        [], _forbidden_conn, dim, dim[-1], only="calendar_horizon", domain="dom1", now=_NOW_2026,
        calendar_rules=rules, calendar_contract=contract, calendar_holidays=holidays)
    assert r4 == [], "显式指定非 trade_cal 的域时, 全局 calendar 检测应跳过"

    r5, _ = cci.run_checks(
        [], _forbidden_conn, dim, dim[-1], only="calendar_next_year", domain="dom1", now=_NOW_2026,
        calendar_rules=rules, calendar_contract=contract, calendar_holidays=holidays)
    assert r5 == []


def test_legacy_raw_today_probe_removed_without_tombstone():
    """C10: 旧探针删干净, 不留 alias/stub。"""
    assert not hasattr(cci, "_load_raw_today_status")
    assert not hasattr(cci, "check_calendar_today_consistency")
    assert not hasattr(cci, "check_calendar_horizon")
    assert not hasattr(cci, "CALENDAR_HORIZON_MIN_TRADING_DAYS")
    assert "raw_today_is_open" not in inspect.signature(cci.run_checks).parameters
    assert "calendar_next_year" in cci.CHECK_IDS


def test_main_preflight_cli_contract_unchanged(monkeypatch, capsys):
    """C11: main() 的 --only calendar_horizon --domain trade_cal --strict --json 接口不变
    (两个调用方 preflight/sync_preconditions 与两份测试钉住的接口)。日历路径里不再直接
    开库 (旧 _load_raw_today_status 的那种 connect_ro), 靠断言它被调用就报错来证明。"""
    rules = load_calendar_gate_rules()
    holidays = load_holidays()
    contract = _contract()
    now = datetime.now(ZoneInfo(contract.timezone))
    year_end = contract.required_through(now)
    dim = _dim_from_rule(rules.serve_projection_floor, year_end, holidays)
    latest = dim[-1]

    monkeypatch.setattr(cci, "load_domain_specs", lambda: [])
    monkeypatch.setattr(cci, "_load_calendar", lambda: (dim, latest))

    def _raise_connect_ro(*_a, **_kw):
        raise AssertionError("connect_ro 不该再出现在 calendar_horizon 路径里")

    monkeypatch.setattr("services.data_access.resolver.connect_ro", _raise_connect_ro)

    rc = cci.main(["--only", "calendar_horizon", "--domain", "trade_cal", "--strict", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    # blocking 修复(2026-09-26): 此前只断言 checks[0] 是 calendar_horizon, 没断言"恰好 1 行"
    # ——把 run_checks(only=args.only, ...) 改成 only=None (等于删掉 --only 路由) 也能让
    # checks[0] 仍是 calendar_horizon (它排在 calendar_next_year 前面), 全量 78 个测试仍然
    # 全绿地让 --only 静默失效。len(...) == 1 把"--only 恰好只跑这一类"钉到 main() 这一层。
    assert len(payload["checks"]) == 1, payload["checks"]
    assert payload["checks"][0]["check"] == "calendar_horizon"
    assert payload["latest_expected"].isdigit() and len(payload["latest_expected"]) == 8

    broken_dim = [d for d in dim if d != dim[len(dim) // 2]]
    monkeypatch.setattr(cci, "_load_calendar", lambda: (broken_dim, broken_dim[-1]))
    rc2 = cci.main(["--only", "calendar_horizon", "--domain", "trade_cal", "--strict", "--json"])
    assert rc2 == 1


def test_real_config_2026_10_09_whole_calendar_check_passes():
    """R1: 真配置整条 PASS (不 monkeypatch) —— 读真 market_holidays.yaml / calendar_gate.yaml /
    注册表契约, dim 按同一套真配置推导, 门不该误报。"""
    rules = load_calendar_gate_rules()
    holidays = load_holidays()
    contract = _contract()
    now = datetime(2026, 10, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    year_end = contract.required_through(now)
    dim = _dim_from_rule(rules.serve_projection_floor, year_end, holidays)

    results, failures = cci.run_checks(
        [], _forbidden_conn, dim, dim[-1], only=None, now=now,
    )
    assert len(results) == 2
    assert all(r["status"] == "pass" for r in results), results
    assert cci.overall_status(results) == "PASS"
    horizon = next(r for r in results if r["check"] == "calendar_horizon")
    assert "[20050104,20261231]" in horizon["detail"]


def test_real_config_fail_from_without_next_year_fails():
    """R2: 真配置 + now = fail_from 且真配置里没有下一年 -> calendar_next_year FAIL, overall FAIL
    (不 monkeypatch)。Y = max(已配置年份), Y+1 按定义未配置, 随 YAML 演进仍成立。"""
    rules = load_calendar_gate_rules()
    holidays = load_holidays()
    contract = _contract()
    max_year = max(holidays)
    now = datetime(
        max_year, rules.next_year_fail_from[0], rules.next_year_fail_from[1],
        10, 0, tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    year_end = contract.required_through(now)
    dim = _dim_from_rule(rules.serve_projection_floor, year_end, holidays)

    results, _ = cci.run_checks(
        [], _forbidden_conn, dim, dim[-1], only=None, now=now,
    )
    horizon = next(r for r in results if r["check"] == "calendar_horizon")
    next_year = next(r for r in results if r["check"] == "calendar_next_year")
    assert horizon["status"] == "pass"
    assert next_year["status"] == "fail_next_year_unconfigured"
    assert cci.overall_status(results) == "FAIL"


# ── 2026-09-18 真实 continuity_20260918.json 形状 (project cut_frozen_domain_verdicts) ──
#
# 当天真实审计 37 项非 PASS 里, 4 项是判据问错了问题: moneyflow/daily_basic (completeness_ref
# fail_row_count_mismatch) 与 index_daily_benchmark/index_dailybasic (static_staleness
# warn_stalled) 全部 execution_policy.mode=disabled/没有源/永远不会再有新行, 而判据逐日拿它们
# 跟一张还在长的基准比 —— 这 4 项永远不会好。本测试用同一份形状建夹具(不读生产库), 证明
# 这 4 项从 FAIL/WARN 变成 observe_*, 而 stock_st 的 calendar_gaps 中间空洞(真缺口, 与冻结
# 无关)必须仍是 fail_interior_gaps —— 这套放宽不是把整套判据调绿。

def test_20260918_continuity_shapes_frozen_domains_observe_real_gap_still_fails():
    tds = _weekdays("20260801", 34)
    local_max = tds[-15]  # 冻结域最后一次真实覆盖的交易日 (之后 09-18 起连续断流)
    c = duck_mem()
    try:
        # 基准域(daily/canonical_nominal_ohlcv_daily): 天天在长, 不含 ts_code
        # (强制 completeness_ref 的标的集合差分支回落成纯行数比对, 与本测试无关)。
        c.execute("CREATE TABLE canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
        c.executemany(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES (?)",
            [(d,) for d in tds for _ in range(5)],
        )

        # moneyflow / daily_basic: 冻结前逐日与基准一致, 冻结后(local_max 之后) 0 行
        # —— 09-18 实测 20260831 起本域 0 vs daily 5,553 的真实形状。
        for table in ("t_moneyflow", "t_daily_basic"):
            _mktable(c, {d: 5 for d in tds if d <= local_max}, table=table)

        # index_daily_benchmark / index_dailybasic: 手动刷新域, MAX(built_at) 停在 local_max
        for table in ("t_index_daily_benchmark", "t_index_dailybasic"):
            c.execute(f"CREATE TABLE {table} (ts_code TEXT, built_at TIMESTAMP)")
            c.execute(
                f"INSERT INTO {table} VALUES ('c0', ?)",
                [f"{local_max[:4]}-{local_max[4:6]}-{local_max[6:8]} 18:00:00"],
            )

        # stock_st: 中间空洞(真缺口, 与冻结无关) —— enabled 域, 必须仍 FAIL。
        hole = tds[10]
        _mktable(c, {d: 2 for d in tds if d != hole}, table="t_stock_st")

        specs = [
            _mkspec(
                domain="moneyflow", table="t_moneyflow", grain=["trade_date"],
                data_start=tds[0],
                execution_policy_mode="disabled", execution_policy_reason="tushare_sunset_freeze",
                completeness_ref={"ref_domain": "daily", "tolerance": 0, "verified_since": tds[0]},
            ),
            _mkspec(
                domain="daily_basic", table="t_daily_basic", grain=["trade_date"],
                data_start=tds[0],
                execution_policy_mode="disabled",
                execution_policy_reason="tushare_sunset_replace_pending",
                completeness_ref={"ref_domain": "daily", "tolerance": 0, "verified_since": tds[0]},
            ),
            _mkspec(
                domain="index_daily_benchmark", table="t_index_daily_benchmark",
                batch_mode="by_code_list", sla=1, data_start=tds[0],
                execution_policy_mode="disabled",
                execution_policy_reason="tushare_sunset_replace_pending",
            ),
            _mkspec(
                domain="index_dailybasic", table="t_index_dailybasic",
                batch_mode="by_code_list", sla=1, data_start=tds[0],
                execution_policy_mode="disabled", execution_policy_reason="tushare_sunset_freeze",
            ),
            _mkspec(domain="stock_st", table="t_stock_st", sla=1, data_start=tds[0]),
        ]

        results, failures = cci.run_checks(
            specs, lambda alias: c, tds, tds[-1], today=tds[-1]
        )
        by_key = {(r["check"], r["domain"]): r for r in results}

        assert by_key[("completeness_ref", "moneyflow")]["status"] == "observe_frozen_window", \
            by_key[("completeness_ref", "moneyflow")]
        assert by_key[("completeness_ref", "daily_basic")]["status"] == "observe_frozen_window", \
            by_key[("completeness_ref", "daily_basic")]
        assert by_key[("static_staleness", "index_daily_benchmark")]["status"] == \
            "observe_frozen_stalled", by_key[("static_staleness", "index_daily_benchmark")]
        assert by_key[("static_staleness", "index_dailybasic")]["status"] == \
            "observe_frozen_stalled", by_key[("static_staleness", "index_dailybasic")]
        assert by_key[("calendar_gaps", "stock_st")]["status"] == "fail_interior_gaps", \
            by_key[("calendar_gaps", "stock_st")]

        # 真缺口不许被这套放宽连带放过: overall 仍必须是 FAIL, 不是全线转绿。
        assert cci.overall_status(results) == "FAIL"
        fail_keys = {(f["check"], f["domain"]) for f in failures}
        assert ("completeness_ref", "moneyflow") not in fail_keys
        assert ("completeness_ref", "daily_basic") not in fail_keys
        assert ("calendar_gaps", "stock_st") in fail_keys
    finally:
        c.close()


# ── 冻结锚点回退防线的生产接线 (2026-09-18 blocking finding 修复) ──────────────────────────
#
# 此前 run_checks 对 check_completeness_ref 的唯一生产调用点从不传 anchor_conn, 于是
# _frozen_watermark_anchor_max 的默认值分支(自己现开一条到 smartmoney 库的真实连接)是
# 生产实际唯一会走的路径, 却没有一条测试覆盖过 —— 两次独立变异(改坏 alias / 把整段
# 现开逻辑删掉)均存活。修法: anchor_conn 经 run_checks 自己已注入的 conn_for("smartmoney")
# 缓存传入, 与其它域的 db 连接同一套 DI; 本节测试因此直接走 run_checks 生产路径, 从不
# 手动向 check_completeness_ref 传 anchor_conn, 证明这条接线本身是通的。

def test_run_checks_wires_real_anchor_conn_via_smartmoney_alias():
    """生产接线: 只走 run_checks(经 conn_for), 不手动传 anchor_conn —— 证明 check_completeness_ref
    的冻结锚点回退防线在真实调用路径上确实生效, 不再是"自己现开一条没人测过的连接"。"""
    tds = _weekdays("20260801", 20)
    local_max = tds[-8]              # 域自己的表被静默削尾后现算出的 MAX
    poisoned_anchor = tds[-3]        # 冻结锚点记得的水位, 晚于 local_max —— 真损坏的证据

    mine_conn = duck_mem()
    anchor_conn = duck_mem()
    try:
        mine_conn.execute("CREATE TABLE canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
        mine_conn.executemany(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES (?)", [(d,) for d in tds]
        )
        _mktable(mine_conn, {d: 3 for d in tds if d <= local_max}, table="t_moneyflow")

        anchor_conn.execute(
            "CREATE TABLE mart_data_source_watermark "
            "(data_domain TEXT, source_name TEXT, source_tier SMALLINT, last_data_date TEXT)"
        )
        anchor_conn.execute(
            "INSERT INTO mart_data_source_watermark VALUES (?, ?, ?, ?)",
            ["sync:moneyflow", "tushare", 2, poisoned_anchor],
        )

        specs = [_mkspec(
            domain="moneyflow", db="tushare_raw", table="t_moneyflow", grain=["trade_date"],
            data_start=tds[0],
            execution_policy_mode="disabled", execution_policy_reason="tushare_sunset_freeze",
            watermark_table="mart_data_source_watermark",
            completeness_ref={"ref_domain": "daily", "tolerance": 0, "verified_since": tds[0]},
        )]

        def conn_for(alias):
            return {"tushare_raw": mine_conn, "smartmoney": anchor_conn}[alias]

        results, failures = cci.run_checks(specs, conn_for, tds, tds[-1], today=tds[-1])
        by_key = {(r["check"], r["domain"]): r for r in results}

        got = by_key[("completeness_ref", "moneyflow")]
        assert got["status"] == "fail_frozen_regression", got
        assert local_max in got["detail"] and poisoned_anchor in got["detail"], got["detail"]
        assert ("completeness_ref", "moneyflow") in {(f["check"], f["domain"]) for f in failures}
    finally:
        mine_conn.close()
        anchor_conn.close()


def test_run_checks_does_not_open_smartmoney_anchor_conn_for_enabled_domain():
    """隔离(其它全满足, 只有域未冻结这一条不满足): execution_policy.mode=enabled 的域声明
    completeness_ref 时, run_checks 不该多开一条它用不到的 smartmoney 锚点连接 —— 用一个
    记录被请求过哪些 alias 的假 conn_for 断言 smartmoney 从未出现在其中(不能靠让 conn_for
    抛异常来证明: run_checks 的 _conn 本身就会 except Exception 把任何异常吞成"库不可达",
    那样即使调用真的发生了, 断言也会因为异常被静默吞掉而误判通过)。"""
    tds = _weekdays("20260801", 12)
    mine_conn = duck_mem()
    requested_aliases: list[str] = []
    try:
        mine_conn.execute("CREATE TABLE canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
        mine_conn.executemany(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES (?)",
            [(d,) for d in tds for _ in range(3)],
        )
        _mktable(mine_conn, {d: 3 for d in tds}, table="t_enabled")

        specs = [_mkspec(
            domain="enabled_dom", db="tushare_raw", table="t_enabled", grain=["trade_date"],
            data_start=tds[0], execution_policy_mode="enabled",
            completeness_ref={"ref_domain": "daily", "tolerance": 0, "verified_since": tds[0]},
        )]

        def conn_for(alias):
            requested_aliases.append(alias)
            return {"tushare_raw": mine_conn}[alias]

        results, _ = cci.run_checks(specs, conn_for, tds, tds[-1], today=tds[-1])
        by_key = {(r["check"], r["domain"]): r for r in results}
        assert by_key[("completeness_ref", "enabled_dom")]["status"] == "pass", \
            by_key[("completeness_ref", "enabled_dom")]
        assert "smartmoney" not in requested_aliases, (
            "enabled 域不该请求 smartmoney 锚点连接, 实际请求过: " + repr(requested_aliases)
        )
    finally:
        mine_conn.close()


def test_run_checks_falls_back_when_smartmoney_conn_unreachable():
    """隔离(其它全满足, 只有锚点库本身不可达这一条不满足): 冻结域声明了 watermark_table,
    但 smartmoney 库不可达(写锁/缺文件, 走与其它域 db 连接完全相同的 _conn 缓存 + 异常捕获
    路径) —— 必须原样退化成"锚点不可用", 落回旧判据(observe_frozen_window), 不得让
    completeness_ref 因锚点连接失败而 crash 或误判。"""
    tds = _weekdays("20260801", 20)
    local_max = tds[-8]
    mine_conn = duck_mem()
    try:
        mine_conn.execute("CREATE TABLE canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
        mine_conn.executemany(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES (?)",
            [(d,) for d in tds for _ in range(3)],
        )
        _mktable(mine_conn, {d: 3 for d in tds if d <= local_max}, table="t_moneyflow")

        specs = [_mkspec(
            domain="moneyflow", db="tushare_raw", table="t_moneyflow", grain=["trade_date"],
            data_start=tds[0],
            execution_policy_mode="disabled", execution_policy_reason="tushare_sunset_freeze",
            watermark_table="mart_data_source_watermark",
            completeness_ref={"ref_domain": "daily", "tolerance": 0, "verified_since": tds[0]},
        )]

        def conn_for(alias):
            if alias == "smartmoney":
                raise RuntimeError("Conflicting lock is held")
            return {"tushare_raw": mine_conn}[alias]

        results, _ = cci.run_checks(specs, conn_for, tds, tds[-1], today=tds[-1])
        by_key = {(r["check"], r["domain"]): r for r in results}
        got = by_key[("completeness_ref", "moneyflow")]
        assert got["status"] == "observe_frozen_window", got
    finally:
        mine_conn.close()
