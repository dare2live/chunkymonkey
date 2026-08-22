"""verified_low_days 单测 (源端核证低值日豁免, 2026-08-22 接线)。

verified_low_days 与 known_empty_days 互补: 后者是源端真空墓碑, 前者是源端核证低值
(非缺陷)。两者都应被排除于 dip 扫描，以避免幻影缺口。全内存 DuckDB, 不碰真库。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from conftest import duck_mem  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_continuity_integrity", REPO / "backend" / "scripts" / "check_continuity_integrity.py")
cci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cci)


# ── fixtures ─────────────────────────────────────────────────────────────

def _dates(n: int) -> list[str]:
    return [f"202601{d:02d}" for d in range(1, n + 1)]


def _mkspec(**kw) -> dict:
    base = {
        "domain": "dom", "db": "mem", "table": "t",
        "freshness_date_column": None, "date_param": None,
        "known_empty_days": set(), "verified_low_days": set(),
        "row_dip_tolerance": False,
        "accepted_security_day": False, "dataset_id": None,
    }
    base.update(kw)
    return base


def _mktable(conn, days_rows: dict[str, int], table: str = "t") -> None:
    conn.execute(f'CREATE TABLE "{table}" (ts_code TEXT, trade_date TEXT)')
    rows = []
    for d, n in days_rows.items():
        rows += [(f"c{i}", d) for i in range(n)]
    if rows:
        conn.executemany(f'INSERT INTO "{table}" VALUES (?, ?)', rows)


# ── 1: verified_low_days 覆盖塌陷日 -> observe_clean ───────────────────────

def test_verified_low_day_suppresses_dip():
    days = _dates(22)
    dip_day = days[10]
    per_day = {d: 100 for d in days}
    per_day[dip_day] = 3
    c = duck_mem()
    try:
        _mktable(c, per_day)
        r = cci.check_cross_section_full(c, _mkspec(verified_low_days={dip_day}))
        assert r["status"] == "observe_clean"
    finally:
        c.close()


# ── 2: 无 verified_low_days 豁免时，相同塌陷日 -> observe_high_signal ────

def test_dip_without_verified_low_day_is_high_signal():
    days = _dates(22)
    dip_day = days[10]
    per_day = {d: 100 for d in days}
    per_day[dip_day] = 3
    c = duck_mem()
    try:
        _mktable(c, per_day)
        r = cci.check_cross_section_full(c, _mkspec())
        assert r["status"] == "observe_high_signal"
        assert dip_day in r["detail"]
    finally:
        c.close()


# ── 3: verified_low_days 与 known_empty_days 同时存在，都排除塌陷 ─────────

def test_both_verified_low_and_known_empty_suppress_dips():
    days = _dates(23)
    dip_day1 = days[10]
    dip_day2 = days[15]
    per_day = {d: 100 for d in days}
    per_day[dip_day1] = 3
    per_day[dip_day2] = 5
    c = duck_mem()
    try:
        _mktable(c, per_day)
        r = cci.check_cross_section_full(
            c,
            _mkspec(
                known_empty_days={dip_day1},
                verified_low_days={dip_day2}
            )
        )
        assert r["status"] == "observe_clean"
    finally:
        c.close()


# ── 4: verified_low_days 只豁免指定日，其它塌陷日仍被观测到 ──────────────

def test_verified_low_day_only_suppresses_specified_day():
    days = _dates(22)
    dip_day1 = days[10]
    per_day = {d: 100 for d in days}
    per_day[dip_day1] = 3  # 单一 dip
    c = duck_mem()
    try:
        _mktable(c, per_day)
        # 无豁免时，该 dip 被观测为 high_signal
        r_no_exempt = cci.check_cross_section_full(c, _mkspec())
        assert r_no_exempt["status"] == "observe_high_signal"
        assert dip_day1 in r_no_exempt["detail"]

        # 创建另一个表（测试 verified_low_days 豁免该日期时的行为）
        c2 = duck_mem()
        _mktable(c2, per_day)
        # 用 verified_low_days 豁免该日期
        r_exempt = cci.check_cross_section_full(c2, _mkspec(verified_low_days={dip_day1}))
        # 豁免后应该是 clean（该 dip 被排除）
        assert r_exempt["status"] == "observe_clean"
        c2.close()
    finally:
        c.close()
