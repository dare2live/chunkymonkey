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


def test_backfill_hint_gives_a_command_that_the_domain_can_actually_run():
    """补拉提示必须对**这个域**真的能跑通, 而不是一句通用的 --drain。

    2026-08-17 实测: daily / stock_st 是授权短窗域, 结构上拒绝无参数 --drain
    (SyncWindowError: authorized short window requires explicit --start/--end),
    但门当时无条件建议 --drain —— 照着提示跑必然失败。一条跑不通的修复建议比不给
    更糟: 它会让人以为工具坏了, 而真正的缺口还在那里。

    同时提示必须带 env 与解释器: 裸 `python -m ...` 会得到
    authorization_blocked(missing_token) 或 package_missing, 因为 token 在 .env、
    依赖在 .venv —— 这两个坑 2026-08-17 都实际踩过。
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from check_continuity_integrity import _backfill_command

    from services.data_sources.sync_runner import AUTHORIZED_SHORT_WINDOW_DOMAINS

    assert AUTHORIZED_SHORT_WINDOW_DOMAINS, "短窗域集合为空, 这个用例就白跑了"

    for domain in sorted(AUTHORIZED_SHORT_WINDOW_DOMAINS):
        hint = _backfill_command(domain, ["20260812", "20260813"])
        assert "--drain" not in hint, f"{domain} 是短窗域, --drain 跑不通: {hint}"
        assert "--start 20260812" in hint and "--end 20260813" in hint, hint

    # 非短窗域仍然用 --drain 扫缺口
    other = _backfill_command("margin_detail", ["20260724"])
    assert "--drain" in other, other

    # 两类都要带上 env 与项目解释器
    for hint in (_backfill_command("daily", ["20260812"]), other):
        assert "source .env" in hint, hint
        assert ".venv/bin/python" in hint, hint


def test_report_points_at_the_object_actually_audited_not_the_legacy_raw_table():
    """输出的表名必须是**真正被查的对象**, 不是 registry 的 target_table。

    2026-08-21 实测代价: formal security-day 域(daily/stock_st)审计的是
    accepted_partition, 而输出默认打印 registry target_table = raw_tushare_daily。
    那张表在 legacy_raw_plane.yaml 里是 role=fill / write=forbidden 的停更表,
    照着它去查 max(trade_date) 得到的是一个月前的假象 —— 同一天内这个坑
    把我和一个调查 agent 各送进沟里多次。
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from check_continuity_integrity import _result

    spec = {"domain": "daily", "db": "tushare_raw", "table": "raw_tushare_daily"}

    plain = _result("calendar_gaps", spec, "pass", "ok")
    assert plain["table"] == "raw_tushare_daily", plain

    redirected = _result(
        "calendar_gaps", spec, "pass", "ok",
        audited="accepted_partition[tier0.market_data.nominal_ohlcv_daily]",
    )
    assert redirected["table"].startswith("accepted_partition["), redirected
    assert "raw_tushare_daily" not in redirected["table"], redirected
