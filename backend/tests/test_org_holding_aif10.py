"""单测 — 机构持仓明细 aif10 service (services.org_holding_aif10).

覆盖: PIT 披露日锚 (真金白银防穿越) / 报告期枚举 / 字段映射 (real-shape fixture, mythos §12) /
幂等 upsert / grain 唯一性。不调真 API (fixture = 真实形态值)。
"""
from __future__ import annotations

import duckdb
import pytest

from services import org_holding_aif10 as m


# ── PIT 披露日锚 (报告期 -> 法定披露截止, 监管硬上界) ──────────────────
def test_disclosure_deadline_quarter_mapping():
    # evidence: A股定期报告法定披露截止 (Q1/年报 04-30, H1 08-31, Q3 10-31)
    assert m.disclosure_deadline("2026-03-31") == "2026-04-30"   # Q1
    assert m.disclosure_deadline("2026-06-30") == "2026-08-31"   # H1
    assert m.disclosure_deadline("2026-09-30") == "2026-10-31"   # Q3
    assert m.disclosure_deadline("2025-12-31") == "2026-04-30"   # 年报 -> 次年


def test_disclosure_deadline_never_before_report_period():
    # PIT 红线: 可用日必须 >= 报告期末 (绝不超前可见)
    for rd in ("2020-03-31", "2022-06-30", "2024-09-30", "2019-12-31"):
        assert m.disclosure_deadline(rd) > rd


def test_enumerate_quarter_ends_kline_aligned():
    qs = m.enumerate_quarter_ends("2018-12-31", "2019-12-31")
    assert qs == ["2018-12-31", "2019-03-31", "2019-06-30", "2019-09-30", "2019-12-31"]


# ── 增量 planner 随披露日历自进 (非 hard-frozen; owner 2026-07-22) ──────
def test_latest_plannable_advances_across_disclosure_deadline():
    """org 增量 planner 不是「冻在某期」— 随法定披露截止日自动前移。

    2026-07-22 latest=Q1(03-31, 截止 04-30 已过); Q2(06-30) 截止 08-31 未到 →
    仍 03-31。跨 08-31 后自动前移到 06-30。证伪「hard-frozen to Mar 31」误读。
    """
    from datetime import date

    assert m.latest_plannable_report_date(date(2026, 7, 22)) == "2026-03-31"
    assert m.latest_plannable_report_date(date(2026, 8, 30)) == "2026-03-31"
    assert m.latest_plannable_report_date(date(2026, 8, 31)) == "2026-06-30"
    assert m.latest_plannable_report_date(date(2026, 10, 31)) == "2026-09-30"
    assert m.latest_plannable_report_date(date(2027, 4, 30)) == "2027-03-31"


def test_next_period_unlock_points_to_following_quarter_and_deadline():
    # 已 plannable Q1 → 下一期 Q2(06-30) 于其披露截止 08-31 解锁 (证明会前进)。
    assert m.next_period_unlock("2026-03-31") == ("2026-06-30", "2026-08-31")
    assert m.next_period_unlock("2026-06-30") == ("2026-09-30", "2026-10-31")
    assert m.next_period_unlock("2026-12-31") == ("2027-03-31", "2027-04-30")


def test_incremental_skip_message_shows_next_unlock(monkeypatch):
    """日常增量 skip 时日志须暴露「下一期何时解锁」, 不再像永久冻结。"""
    import asyncio
    from datetime import date

    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")

    class _Conn:
        def execute(self, sql, *a, **k):
            class _Cur:
                def fetchall(_self):
                    return [("2026-03-31",)]
                def fetchone(_self):
                    return (1,)
            return _Cur()

    monkeypatch.setattr(m, "ensure_tables", lambda _conn: None)
    out = asyncio.run(m.sync_org_holding_incremental(_Conn()))
    assert out["status"] == "skipped"
    assert out["next_period"] == "2026-06-30"
    assert out["next_period_unlock"] == "2026-08-31"
    assert "next period 2026-06-30 unlocks 2026-08-31" in out["message"]


# ── 字段映射 (real-shape fixture: MAIN_ORGHOLDDETAIL 真实字段名+形态) ──
def _real_shape_raw():
    return [
        {
            "SECURITY_CODE": "600388", "SECUCODE": "600388.SH",
            "SECURITY_NAME_ABBR": "龙净环保", "REPORT_DATE": "2026-03-31 00:00:00",
            "ORG_TYPE": "07", "F9_ORGTYPE_NAME": "非金融类上市公司",
            "HOLDER_CODE": "10010626", "HOLDER_NAME": "紫金矿业集团股份有限公司",
            "FUND_CODE": None, "FUND_DERIVECODE": None,
            "TOTAL_SHARES": 267764576, "HOLD_VALUE": 4763531807.04,
            "TOTALSHARES_RATIO": 21.08305638, "FREESHARES_RATIO": 21.08305638,
            "FREE_MARKET_CAP": 4763531807.04, "FREE_SHARES": 267764576,
            "FSR_CHANGE": 0.0, "FSR_RATE_CHANGE": 0.0, "CHANGE_TYPE": "不变",
        },
        {
            "SECURITY_CODE": "600388", "SECUCODE": "600388.SH",
            "SECURITY_NAME_ABBR": "龙净环保", "REPORT_DATE": "2026-03-31 00:00:00",
            "ORG_TYPE": "05", "F9_ORGTYPE_NAME": "基金",
            "HOLDER_CODE": "70030012", "HOLDER_NAME": "某基金",
            "FUND_CODE": "000001", "FUND_DERIVECODE": "000001.OF",
            "TOTAL_SHARES": 1000000, "TOTALSHARES_RATIO": 0.08,
            "CHANGE_TYPE": "增加",
        },
        # 缺主键行 (HOLDER_CODE 空) -> 应被剔除
        {"SECURITY_CODE": "600388", "REPORT_DATE": "2026-03-31", "HOLDER_CODE": None},
    ]


def test_normalize_field_mapping_and_pit_anchor():
    rows = m._normalize_rows(_real_shape_raw())
    assert len(rows) == 2  # 缺主键的第3行剔除
    r0 = rows[0]
    assert r0["stock_code"] == "600388"
    assert r0["report_date"] == "2026-03-31"
    assert r0["available_date"] == "2026-04-30"   # PIT 锚 (季报 -> 披露截止)
    assert r0["org_type_name"] == "非金融类上市公司"
    assert r0["holder_name"] == "紫金矿业集团股份有限公司"
    assert r0["total_shares"] == pytest.approx(267764576.0)
    assert r0["total_shares_ratio"] == pytest.approx(21.08305638)
    assert r0["fund_derivecode"] == ""            # null fund_derivecode -> '' (grain 稳定)
    assert r0["source"] == "miaoxiang"
    assert rows[1]["fund_derivecode"] == "000001.OF"


def test_normalize_empty():
    assert m._normalize_rows([]) == []
    assert m._normalize_rows(None) == []


# ── 幂等 upsert + grain ───────────────────────────────────────────────
def test_upsert_idempotent_and_grain():
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    rows = m._normalize_rows(_real_shape_raw())
    assert m._upsert_rows(con, rows) == 2
    m._upsert_rows(con, rows)  # 幂等重写
    n = con.execute("SELECT COUNT(*) FROM raw_org_holding_aif10").fetchone()[0]
    assert n == 2  # 不翻倍
    # 改值重写 -> 同 grain 覆盖 (非新增)
    rows[0]["total_shares"] = 999.0
    m._upsert_rows(con, rows)
    n2 = con.execute("SELECT COUNT(*) FROM raw_org_holding_aif10").fetchone()[0]
    v = con.execute(
        "SELECT total_shares FROM raw_org_holding_aif10 WHERE holder_code='10010626'"
    ).fetchone()[0]
    assert n2 == 2 and v == pytest.approx(999.0)
    con.close()


def test_available_date_no_null_when_quarter_end():
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    m._upsert_rows(con, m._normalize_rows(_real_shape_raw()))
    miss = con.execute(
        "SELECT COUNT(*) FROM raw_org_holding_aif10 WHERE available_date IS NULL"
    ).fetchone()[0]
    assert miss == 0  # 标准季度末报告期 PIT 锚全覆盖
    con.close()


# ── fetched_at 全路径 UTC 口径 (2026-07-10 修双时区漂移: 原 INSERT 落 DDL 默认北京墙钟/
#    UPDATE 落 now() 北京墙钟, _normalize_rows 算好的 UTC 被 _INSERT_COLS 漏列静默丢弃) ──
def test_fetched_at_utc_on_both_insert_and_update_paths():
    from datetime import datetime, timezone
    con = duckdb.connect(":memory:")
    # 钉死 session TZ: 旧 bug 路径 (DDL DEFAULT/now() 落墙钟) 在 UTC 主机 (GitHub CI 默认)
    # 与 UTC 无差 → 不钉则红面只在 Asia/Shanghai 开发机成立, CI 上永久假绿 (对抗复审实测)。
    con.execute("SET TimeZone='Asia/Shanghai'")
    m.ensure_tables(con)
    tol = 600  # 秒; 北京墙钟误写会偏 8h=28800s, 远超容差

    m._upsert_rows(con, m._normalize_rows(_real_shape_raw()))
    ins = con.execute("SELECT MAX(fetched_at) FROM raw_org_holding_aif10").fetchone()[0]
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((ins - utc_now).total_seconds()) < tol, f"INSERT 路径 fetched_at 非 UTC: {ins}"

    # 冲突更新路径同口径
    rows2 = m._normalize_rows(_real_shape_raw())
    rows2[0]["total_shares"] = 123.0
    m._upsert_rows(con, rows2)
    upd = con.execute("SELECT MAX(fetched_at) FROM raw_org_holding_aif10").fetchone()[0]
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((upd - utc_now).total_seconds()) < tol, f"UPDATE 路径 fetched_at 非 UTC: {upd}"
    con.close()


# ── Owner 2026-07-21 Q3: check plannable vs local; never silent mass dump ──
def test_org_holding_period_gap_report_detects_missing_plannable():
    from datetime import date

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    # today past Q1 2026 disclosure deadline (04-30) → plannable includes 2026-03-31
    gap = m.org_holding_period_gap_report(
        con, today=date(2026, 5, 15), start_period="2025-12-31"
    )
    assert gap["plannable"] == "2026-03-31"
    assert gap["local_has_plannable"] is False
    assert "2026-03-31" in gap["missing_periods"]
    assert gap["status"] == "plannable_missing"
    con.close()


def test_sync_org_holding_incremental_skips_when_plannable_present(monkeypatch):
    import asyncio

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")
    con.execute(
        "INSERT INTO raw_org_holding_aif10 "
        "(report_date, stock_code, holder_code, fund_derivecode) "
        "VALUES ('2026-03-31', '600000', 'H1', '')"
    )
    con.commit()
    fetched = {"called": False}

    def _boom(*_a, **_k):
        fetched["called"] = True
        raise AssertionError("must not mass-fetch when plannable present")

    monkeypatch.setattr(m, "sync_period", _boom)
    result = asyncio.run(m.sync_org_holding_incremental(con))
    assert result["status"] == "skipped"
    assert fetched["called"] is False
    assert "local=present" in result["message"]
    con.close()


def test_sync_period_refuses_mass_refresh_when_period_present(monkeypatch):
    """Fail-closed: existing period must not trigger ~830k provider crawl."""
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    con.execute(
        "INSERT INTO raw_org_holding_aif10 "
        "(report_date, stock_code, holder_code, fund_derivecode) "
        "VALUES ('2026-03-31', '600000', 'H1', '')"
    )
    con.commit()
    fetched = {"called": False}

    def _boom(*_a, **_k):
        fetched["called"] = True
        return []

    monkeypatch.setattr(m, "_fetch_period", _boom)
    with pytest.raises(m.OrgHoldingMassRefreshForbidden, match="refuse mass refresh"):
        m.sync_period(con, "2026-03-31")
    assert fetched["called"] is False
    con.close()


def test_pipeline_acquire_org_path_is_incremental_only():
    """daily_update acquire must wire incremental, never org backfill."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "pipeline"
        / "acquire.py"
    ).read_text(encoding="utf-8")
    assert "sync_org_holding_incremental" in src
    assert "org_holding_period_gap_report" in src
    # Hard ban: pipeline must not call org backfill / unbounded refresh.
    assert "org_holding_aif10 import (\n        backfill" not in src
    assert "backfill(" not in src.split("def _sync_org_holding")[1].split("def ")[0]
    assert "allow_existing_refresh=True" not in src
