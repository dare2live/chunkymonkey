"""verified_low_days 豁免的位置正确性 + 强制留证校验 (2026-08-23 实测缺陷修复回归测试)。

背景 (两处独立缺陷, 同一字段, 同一次实测发现):

  缺陷1 (功能性, 最严重) —— 豁免施加错了位置: verified_low_days 此前只在 observe-only、
  永不 FAIL/WARN 的 check_cross_section_full 里被排除 (见 tests/scripts/test_verified_low_days.py,
  它只测了 check_cross_section_full), 而真正会 WARN 的日常门 check_cross_section 只排除
  known_empty_days —— 豁免精确地施加在不需要豁免的地方, 缺失在需要豁免的地方。此前看不出来
  只因为 dc_member 那两天 (2025-10-29 / 2026-04-09) 早已滑出 60 交易日窗口; 将来任何落在近
  60 日内的已核证低值日都会被误报。本文件的 check_cross_section 两个测试是这处缺陷的直接回归。

  缺陷2 (设计未落实) —— "强制写理由"名不副实: verified_low_days 当初设计成
  dict(日期 -> 核证理由) 而非 list, 就是为了强制留下"凭什么豁免"的证据; 但加载时从不校验,
  传 list 也能跑通(反正只取 key)、理由写 "ok" 也能跑通, 等于没强制。本文件的
  load_domain_specs 三个测试覆盖: 传 list 拒绝 / 理由过短或为空拒绝 / 合法长理由正常加载。

全内存 DuckDB + tmp_path 临时 YAML, 不碰真库/真 registry。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

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
            "verified_low_days": set(), "gap_tolerance": "none", "freshness_group_col": None,
            "dead_groups": [], "known_group_gaps": {}, "row_dip_tolerance": False}
    base.update(kw)
    return base


def _mktable(conn, days_rows: dict[str, int], table: str = "t") -> None:
    conn.execute(f'CREATE TABLE "{table}" (ts_code TEXT, trade_date TEXT)')
    rows = []
    for d, n in days_rows.items():
        rows += [(f"c{i}", d) for i in range(n)]
    if rows:
        conn.executemany(f'INSERT INTO "{table}" VALUES (?, ?)', rows)


# ── 缺陷1 回归: check_cross_section (日常 WARN 门) 必须尊重 verified_low_days ──────

def test_check_cross_section_does_not_report_dip_for_verified_low_day():
    """近 60 日内的塌陷日一旦登记进 verified_low_days, 日常门必须不报——这是缺陷1的直接回归。

    此前 verified_low_days 只在 check_cross_section_full (observe-only) 里生效, 这个真正
    会 WARN 的日常门当时只排 known_empty_days, 不排 verified_low_days。
    """
    tds = _weekdays("20260601", 30)
    dip_day = tds[24]
    counts = {d: 100 for d in tds}
    counts[dip_day] = 10   # 100 * row_dip_ratio(0.6) = 60, 10 < 60 天然会被判塌陷
    c = duck_mem()
    try:
        _mktable(c, counts)
        got = cci.check_cross_section(c, _mkspec(verified_low_days={dip_day}), tds, tds[-1])
        assert got["status"] == "pass", got
        assert dip_day not in got["detail"], got["detail"]
    finally:
        c.close()


def test_check_cross_section_still_reports_dip_when_not_exempted():
    """同样的数据不登记进 verified_low_days → 照常报 dip, 证明缺陷1的修复没有连带关掉检测。"""
    tds = _weekdays("20260601", 30)
    dip_day = tds[24]
    counts = {d: 100 for d in tds}
    counts[dip_day] = 10
    c = duck_mem()
    try:
        _mktable(c, counts)
        got = cci.check_cross_section(c, _mkspec(), tds, tds[-1])
        assert got["status"] == "warn_row_dip", got
        assert dip_day in got["detail"], got["detail"]
    finally:
        c.close()


# ── 缺陷2 回归: load_domain_specs 必须强制 verified_low_days 带核证理由 ────────────

def _registry_with(tmp_path: Path, verified_low_days) -> Path:
    doc = {
        "defaults": {"target_db": "tushare_raw"},
        "domains": {
            "fake_dom": {
                "target_table": "fake_table",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "verified_low_days": verified_low_days,
            },
        },
    }
    path = tmp_path / "sync_registry.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_verified_low_days_as_list_is_refused(tmp_path):
    """传裸 list 必须报错 (反正只取 key 等于放弃了"强制留证"的设计意图); 错误信息须点出理由/核证。"""
    path = _registry_with(tmp_path, ["20260101", "20260102"])
    with pytest.raises(ValueError) as exc:
        cci.load_domain_specs(path)
    msg = str(exc.value)
    assert "理由" in msg or "核证" in msg, msg


@pytest.mark.parametrize("bad_reason", ["ok", "", "   "])
def test_verified_low_days_missing_or_too_short_reason_is_refused(tmp_path, bad_reason):
    """理由为空/纯空白/过短(如 "ok") → 拒绝: 敷衍或缺失的理由等于没核证。"""
    path = _registry_with(tmp_path, {"20260101": bad_reason})
    with pytest.raises(ValueError) as exc:
        cci.load_domain_specs(path)
    msg = str(exc.value)
    assert "理由" in msg or "核证" in msg, msg


def test_verified_low_days_valid_mapping_loads_cleanly(tmp_path):
    """value 是长度 >= 10 的合法理由 → 正常加载, 转成 spec 里的 compact 日期集合。"""
    path = _registry_with(
        tmp_path,
        {"2026-01-01": "已联系 vendor 核实为真实低值, 非我方采集缺口 (2026-08-23 核证)"},
    )
    specs = cci.load_domain_specs(path)
    assert len(specs) == 1, specs
    assert specs[0]["verified_low_days"] == {"20260101"}, specs[0]["verified_low_days"]
