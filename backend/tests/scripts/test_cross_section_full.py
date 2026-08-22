"""check_cross_section_full 单测 (全历史 dip 扫描 + CV 分层, 观测性质, 2026-08-22 接线)。

check_cross_section 只看近 60 交易日, 历史异常一旦滑出窗口就永久失查; check_cross_section_full
把已验收的两个纯函数 (_dip_scan.scan_full_history / _dip_severity.dip_signal_level) 接进日常
审查, 但状态一律 observe_*/skipped_* 前缀, 不产出 fail/warn —— 这是接线测试, 不重测那两个纯函数
自身的判据 (那些已在 test_dip_scan.py / test_dip_severity.py 覆盖)。全内存 DuckDB, 不碰真库。
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
        "known_empty_days": set(), "row_dip_tolerance": False,
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


# ── 1: row_dip_tolerance 域直接跳过 ──────────────────────────────────────

def test_row_dip_tolerance_domain_is_skipped():
    c = duck_mem()
    try:
        r = cci.check_cross_section_full(c, _mkspec(row_dip_tolerance=True))
        assert r["status"] == "skipped_row_dip_tolerance"
    finally:
        c.close()


# ── 2: 稳定域塌陷 -> observe_high_signal, detail 含塌陷日期 ─────────────────

def test_stable_domain_collapse_is_high_signal():
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


# ── 3: 高方差域塌陷 -> 不能是 high (low 或 clean 皆可) ───────────────────

def test_high_variance_domain_collapse_is_not_high_signal():
    days = _dates(21)
    dip_day = days[10]
    per_day = {}
    for i, d in enumerate(days):
        if d == dip_day:
            continue
        per_day[d] = 20 if i % 2 == 0 else 180
    per_day[dip_day] = 3
    c = duck_mem()
    try:
        _mktable(c, per_day)
        r = cci.check_cross_section_full(c, _mkspec())
        assert r["status"] in ("observe_low_signal", "observe_clean")
        assert r["status"] != "observe_high_signal"
    finally:
        c.close()


# ── 4: 无 dip -> observe_clean ──────────────────────────────────────────

def test_no_dip_is_clean():
    days = _dates(21)
    per_day = {d: 100 for d in days}
    c = duck_mem()
    try:
        _mktable(c, per_day)
        r = cci.check_cross_section_full(c, _mkspec())
        assert r["status"] == "observe_clean"
    finally:
        c.close()


# ── 5: known_empty_days 覆盖塌陷日 -> observe_clean ─────────────────────

def test_known_empty_day_suppresses_dip():
    days = _dates(22)
    dip_day = days[10]
    per_day = {d: 100 for d in days}
    per_day[dip_day] = 3
    c = duck_mem()
    try:
        _mktable(c, per_day)
        r = cci.check_cross_section_full(c, _mkspec(known_empty_days={dip_day}))
        assert r["status"] == "observe_clean"
    finally:
        c.close()


# ── 6: 状态前缀硬约束 -> 绝不 fail/warn ──────────────────────────────────

def test_status_never_starts_with_fail_or_warn():
    days = _dates(22)
    dip_day = days[10]
    per_day = {d: 100 for d in days}
    per_day[dip_day] = 3

    scenarios: list[dict] = []

    c1 = duck_mem()
    _mktable(c1, per_day)
    scenarios.append({"conn": c1, "spec": _mkspec()})

    c2 = duck_mem()
    scenarios.append({"conn": c2, "spec": _mkspec(table="missing_table")})

    c3 = duck_mem()
    c3.execute('CREATE TABLE "t" (ts_code TEXT)')  # 无可解析日期列
    scenarios.append({"conn": c3, "spec": _mkspec()})

    c4 = duck_mem()
    scenarios.append({"conn": c4, "spec": _mkspec(row_dip_tolerance=True)})

    try:
        for scenario in scenarios:
            r = cci.check_cross_section_full(scenario["conn"], scenario["spec"])
            assert r["status"].startswith(("observe_", "skipped_")), r
            assert not r["status"].startswith("fail")
            assert not r["status"].startswith("warn")
    finally:
        for scenario in scenarios:
            scenario["conn"].close()
