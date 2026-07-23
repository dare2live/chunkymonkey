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
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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

    2026-07-08 字段从 gap_tolerance 拆分(owner=analysis/gap_root_cause_20260708.md): stk_surv
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
    """known_group_gaps (2026-07-05 R4 修复): margin/ths_hot 型域某日源端确认只回部分组,
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
        "    available_after: '09:20'\n"
        "    gap_tolerance: annotate\n    known_empty_days: ['20240312']\n"
        "    min_rows_per_batch: 3\n    universe_filter: true\n"
        "    universe_filter_col: ts_code\n    universe_filter_prefixes: ['60', '00']\n"
        "  b:\n    target_table: t_b\n    grain: [trade_date, data_type]\n"
        "    batch_mode: by_trade_date\n    data_start: '20240101'\n"
        "    freshness_sla_trading_days: 2\n"
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
    b = next(s for s in specs if s["domain"] == "b")
    assert b["freshness_group_col"] == "data_type" and b["dead_groups"] == ["热基"]
    assert b["data_start_reviewed"] is True
    assert b["row_dip_tolerance"] is True
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
    margin = next(s for s in specs if s["domain"] == "margin")
    assert margin["accepted_margin"] is True
    assert margin["table"] == "canonical_margin_exchange_daily"
    assert margin["data_start"] == "20260717"
    assert margin["availability_policy"] == {
        "axis": "trading_day",
        "rule": "next_trading_session_at",
        "at": "09:00",
    }
    assert margin["execution_policy_mode"] == "enabled"
    assert margin["execution_policy_reason"] == "bounded_calendar_catchup"
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


# ── 检测 6: calendar_horizon (2026-07-06 从孤儿 data_quality.py 迁入) ─────

def test_calendar_horizon_red_green():
    """today 之后已登记交易日 < 60 = FAIL, >= 60 = PASS (阈值边界)。"""
    today = "20260701"
    # FAIL: today 之后只有 59 个交易日
    tds_short = _weekdays("20260401", 60) + _weekdays("20260702", 59)
    r = cci.check_calendar_horizon(sorted(tds_short), today)
    assert r["status"] == "fail" and r["check"] == "calendar_horizon"
    assert "59" in r["detail"]

    # PASS: today 之后有 61 个交易日
    tds_ok = _weekdays("20260401", 60) + _weekdays("20260702", 61)
    r2 = cci.check_calendar_horizon(sorted(tds_ok), today)
    assert r2["status"] == "pass"


def test_calendar_horizon_ignores_past_days():
    """today 及之前的交易日不计入前瞻余量 (bisect_right 语义: today 当天本身不算"之后")。"""
    today = "20260701"
    tds = _weekdays("20260401", 60) + [today] + _weekdays("20260702", 60)
    r = cci.check_calendar_horizon(sorted(tds), today)
    assert r["status"] == "pass"
    assert "60" in r["detail"]  # today 自己不计入 60 个未来交易日


def test_calendar_horizon_normalizes_iso_today_against_compact_days():
    """生产日历是 compact；ISO today 不能把全部历史日期误算成未来。"""
    past = _weekdays("20260101", 99)
    r = cci.check_calendar_horizon(past, "2026-07-15")
    assert r["status"] == "fail"
    assert "仅剩 0" in r["detail"]


def test_calendar_today_consistency_requires_raw_row_and_matching_dim_state():
    future = _weekdays("20260716", 61)
    missing = cci.check_calendar_today_consistency(future, "2026-07-15", None)
    assert missing["status"] == "fail"

    mismatch = cci.check_calendar_today_consistency(
        ["20260715", *future], "2026-07-15", 0
    )
    assert mismatch["status"] == "fail"

    open_ok = cci.check_calendar_today_consistency(
        ["20260715", *future], "2026-07-15", 1
    )
    closed_ok = cci.check_calendar_today_consistency(future, "2026-07-15", 0)
    assert open_ok["status"] == closed_ok["status"] == "pass"


def test_calendar_horizon_wired_into_run_checks_global_not_per_domain():
    """run_checks 里 calendar_horizon 全局跑一次 (不随 registry 域数量重复), domain 过滤器
    传入非 trade_cal 的具体域名时应跳过 (只对该域自己的检测负责)。"""
    tds = _weekdays("20260401", 5)

    def _fresh(alias):
        c = duck_mem()
        _mktable(c, {d: 3 for d in tds})
        return c

    specs = [_mkspec(domain="dom1"), _mkspec(domain="dom2")]
    results, _ = cci.run_checks(specs, _fresh, tds, tds[-1], only="calendar_horizon")
    assert len(results) == 1, "calendar_horizon 应全局只跑一次, 不随域数重复"
    assert results[0]["domain"] == "trade_cal"

    results2, _ = cci.run_checks(specs, _fresh, tds, tds[-1], only="calendar_horizon", domain="dom1")
    assert results2 == [], "显式指定非 trade_cal 的域时, 全局 calendar_horizon 应跳过"
