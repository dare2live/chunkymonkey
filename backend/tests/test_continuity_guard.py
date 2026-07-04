"""continuity_guard 消费侧硬门单测 (red-green 证伪门)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.continuity_guard import ContinuityGapError, assert_domains_continuous
from conftest import duck_mem


def _conn(days_present):
    c = duck_mem()
    c.executescript("""
        CREATE SCHEMA tr; CREATE SCHEMA ref;
        CREATE TABLE ref.dim_trading_calendar (trade_date TEXT, is_trading BIGINT);
        CREATE TABLE tr.raw_tushare_moneyflow (trade_date TEXT);
    """)
    for d in ("2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"):
        c.execute("INSERT INTO ref.dim_trading_calendar VALUES (?, 1)", [d])
    for d in days_present:
        c.execute("INSERT INTO tr.raw_tushare_moneyflow VALUES (?)", [d])
    return c


def test_interior_gap_raises(monkeypatch):
    import services.continuity_guard as cg
    monkeypatch.setattr(cg, "_load_spec_for_test", None, raising=False)
    c = _conn(["20260601", "20260603", "20260605"])  # 0602/0604 中间空洞
    spec = {"moneyflow": {"batch_mode": "by_trade_date", "target_table": "raw_tushare_moneyflow",
                          "data_start": "20260601"}}
    monkeypatch.setattr(cg.yaml, "safe_load", lambda _: {"domains": spec})
    with pytest.raises(ContinuityGapError, match="2 个未豁免中间缺口"):
        assert_domains_continuous(["moneyflow"], c)
    c.close()


def test_clean_and_tombstone_pass(monkeypatch):
    import services.continuity_guard as cg
    c = _conn(["20260601", "20260602", "20260604", "20260605"])  # 0603 缺但有墓碑
    spec = {"moneyflow": {"batch_mode": "by_trade_date", "target_table": "raw_tushare_moneyflow",
                          "data_start": "20260601", "known_empty_days": ["20260603"]}}
    monkeypatch.setattr(cg.yaml, "safe_load", lambda _: {"domains": spec})
    out = assert_domains_continuous(["moneyflow"], c)
    assert out["moneyflow"]["gaps"] == []
    c.close()


def test_unregistered_domain_raises(monkeypatch):
    import services.continuity_guard as cg
    c = _conn(["20260601"])
    monkeypatch.setattr(cg.yaml, "safe_load", lambda _: {"domains": {}})
    with pytest.raises(ContinuityGapError, match="不在 sync_registry"):
        assert_domains_continuous(["ghost_domain"], c)
    c.close()
