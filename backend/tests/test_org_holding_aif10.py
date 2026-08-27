"""单测 — 机构持仓明细 aif10 service (services.org_holding_aif10).

覆盖: PIT 披露日锚 (真金白银防穿越) / 报告期枚举 / 字段映射 (real-shape fixture, mythos §12) /
幂等 upsert / grain 唯一性。不调真 API (fixture = 真实形态值)。
"""
from __future__ import annotations

import duckdb
import pytest

from services import org_holding_aif10 as m


@pytest.fixture(autouse=True)
def _no_live_org_count_probe(monkeypatch):
    """Gap report must not hit Eastmoney in unit tests (count=0 → never source_ahead)."""
    monkeypatch.setattr(m, "_probe_period_count", lambda *_a, **_k: 0)
    monkeypatch.setattr(m, "_period_announcement_map", lambda *_a, **_k: {})


# ── PIT 披露日锚 (报告期 -> 法定披露截止, 监管硬上界) ──────────────────
def test_disclosure_deadline_quarter_mapping():
    # evidence: A股定期报告法定披露截止 (Q1/年报 04-30, H1 08-31, Q3 10-31)
    assert m.disclosure_deadline("2026-03-31") == "2026-04-30"   # Q1
    assert m.disclosure_deadline("2026-06-30") == "2026-08-31"   # H1
    assert m.disclosure_deadline("2026-09-30") == "2026-10-31"   # Q3
    assert m.disclosure_deadline("2025-12-31") == "2026-04-30"   # 年报 -> 次年


def test_disclosure_deadline_delegates_to_periodic_calendar():
    from services.data_sources.periodic_report_calendar import disclosure_deadline_iso

    assert m.disclosure_deadline("2026-06-30") == disclosure_deadline_iso("2026-06-30")
    assert m.disclosure_deadline("2026-06-30") == "2026-08-31"


def test_disclosure_deadline_never_before_report_period():
    # PIT 红线: 可用日必须 >= 报告期末 (绝不超前可见)
    for rd in ("2020-03-31", "2022-06-30", "2024-09-30", "2019-12-31"):
        assert m.disclosure_deadline(rd) > rd


def test_enumerate_quarter_ends_kline_aligned():
    qs = m.enumerate_quarter_ends("2018-12-31", "2019-12-31")
    assert qs == ["2018-12-31", "2019-03-31", "2019-06-30", "2019-09-30", "2019-12-31"]


# ── 增量 planner: 采集=报告期末, PIT/accept=公告日 ─────────────────────
def test_latest_plannable_is_ended_period_not_statutory_deadline():
    """org 采集闸随报告期末打开, 不是等到法定截止。

    2026-07-22 H1(06-30) 已结束 → plannable=06-30, 即使截止 08-31 未到。
    证伪「提前拉 / 截止前不是缺口」。accepted 在报告期末即可 (PIT=公告日)。
    """
    from datetime import date

    assert m.latest_plannable_report_date(date(2026, 6, 29)) == "2026-03-31"
    assert m.latest_plannable_report_date(date(2026, 6, 30)) == "2026-06-30"
    assert m.latest_plannable_report_date(date(2026, 7, 22)) == "2026-06-30"
    assert m.latest_plannable_report_date(date(2026, 8, 30)) == "2026-06-30"
    assert m.latest_plannable_report_date(date(2026, 8, 31)) == "2026-06-30"
    assert m.latest_plannable_report_date(date(2026, 10, 31)) == "2026-09-30"
    assert m.latest_plannable_report_date(date(2027, 4, 30)) == "2027-03-31"


def test_accept_unlocked_follows_ended_period_not_statutory_deadline():
    from datetime import date

    assert m.accept_unlocked("2026-06-30", date(2026, 6, 29)) is False
    assert m.accept_unlocked("2026-06-30", date(2026, 6, 30)) is True
    assert m.accept_unlocked("2026-06-30", date(2026, 7, 22)) is True
    assert m.latest_accept_unlocked_report_date(date(2026, 7, 22)) == "2026-06-30"
    assert m.latest_accept_unlocked_report_date(date(2026, 8, 31)) == "2026-06-30"


def test_next_period_unlock_points_to_following_quarter_and_deadline():
    # 已 plannable Q1 → 下一期 Q2(06-30) 于其披露截止 08-31 解锁 (证明会前进)。
    assert m.next_period_unlock("2026-03-31") == ("2026-06-30", "2026-08-31")
    assert m.next_period_unlock("2026-06-30") == ("2026-09-30", "2026-10-31")
    assert m.next_period_unlock("2026-12-31") == ("2027-03-31", "2027-04-30")


def test_incremental_skip_message_shows_next_unlock(monkeypatch):
    """日常增量 skip 时日志须暴露「下一期何时解锁」, 不再像永久冻结。"""
    import asyncio

    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "services.org_holding_period_catchup.plan_older_org_period_fill",
        lambda *_a, **_k: {
            "fill_target_period": None,
            "older_remaining": 0,
            "missing_older_count": 0,
        },
    )

    class _Conn:
        def execute(self, sql, *a, **k):
            text = str(sql)
            if "canonical_org_holding" in text:
                raise RuntimeError("canonical unavailable in stub")

            class _Cur:
                def fetchall(_self):
                    return [("2026-03-31",)]

                def fetchone(_self):
                    return (1,)

            return _Cur()

    monkeypatch.setattr(m, "ensure_tables", lambda _conn: None)
    out = asyncio.run(m.sync_org_holding_incremental(_Conn()))
    assert out["status"] == "skipped"
    assert out["action"] == "skip_current"
    assert out["next_period"] == "2026-06-30"
    assert out["next_period_unlock"] == "2026-08-31"
    assert "acquire opens at period end" in out["message"]
    assert "bounded fill idle" in out["message"]


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
    rows = m._normalize_rows(
        _real_shape_raw(),
        announcement_by_stock={"600388": "2026-04-15"},
        land_date="2026-04-20",
        today="2026-04-20",
    )
    assert len(rows) == 2  # 缺主键的第3行剔除
    r0 = rows[0]
    assert r0["stock_code"] == "600388"
    assert r0["report_date"] == "2026-03-31"
    assert r0["available_date"] == "2026-04-15"  # 公告日, 不是法定截止 04-30
    assert r0["available_date"] != m.disclosure_deadline("2026-03-31")
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
    """Raw research table path = legacy_direct (formal_only _upsert_rows does not mirror)."""
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    rows = m._normalize_rows(_real_shape_raw())
    assert m._upsert_rows_legacy_direct(con, rows, as_mirror=False) == 2
    m._upsert_rows_legacy_direct(con, rows, as_mirror=False)  # 幂等重写
    n = con.execute("SELECT COUNT(*) FROM raw_org_holding_aif10").fetchone()[0]
    assert n == 2  # 不翻倍
    # 改值重写 -> 同 grain 覆盖 (非新增)
    rows[0]["total_shares"] = 999.0
    m._upsert_rows_legacy_direct(con, rows, as_mirror=False)
    n2 = con.execute("SELECT COUNT(*) FROM raw_org_holding_aif10").fetchone()[0]
    v = con.execute(
        "SELECT total_shares FROM raw_org_holding_aif10 WHERE holder_code='10010626'"
    ).fetchone()[0]
    assert n2 == 2 and v == pytest.approx(999.0)
    con.close()


def test_available_date_no_null_when_quarter_end():
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    m._upsert_rows_legacy_direct(
        con, m._normalize_rows(_real_shape_raw()), as_mirror=False
    )
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

    m._upsert_rows_legacy_direct(
        con, m._normalize_rows(_real_shape_raw()), as_mirror=False
    )
    ins = con.execute("SELECT MAX(fetched_at) FROM raw_org_holding_aif10").fetchone()[0]
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((ins - utc_now).total_seconds()) < tol, f"INSERT 路径 fetched_at 非 UTC: {ins}"

    # 冲突更新路径同口径
    rows2 = m._normalize_rows(_real_shape_raw())
    rows2[0]["total_shares"] = 123.0
    m._upsert_rows_legacy_direct(con, rows2, as_mirror=False)
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
    assert gap["action"] == "fetch_then_accept"
    assert "2026-03-31" in gap["missing_periods"]
    assert gap["completeness_due"] is True
    assert gap["status"] == "completeness_miss"
    assert "2025-12-31" in gap["completeness_miss_periods"]
    assert "2026-03-31" in gap["completeness_miss_periods"]
    con.close()


def test_org_holding_period_gap_accepts_after_period_end():
    """H1 报告期末已过: 采集跟源, 也可以 accept (PIT=公告日, 不等 8/31)."""
    from datetime import date

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    gap = m.org_holding_period_gap_report(
        con, today=date(2026, 7, 22), start_period="2026-03-31"
    )
    assert gap["plannable"] == "2026-06-30"
    assert gap["accept_unlocked"] is True
    assert gap["action"] == "fetch_then_accept"
    assert gap["completeness_due"] is False
    assert gap["completeness_class"] == "in_season"
    assert gap["status"] == "plannable_missing"
    assert "2026-03-31" in gap["completeness_miss_periods"]
    assert "2026-06-30" not in gap["completeness_miss_periods"]
    con.close()


def test_org_holding_period_gap_completeness_miss_all_period_types():
    """漏抓门: 四种定期报告各自截止日后本地缺 = completeness_miss, 截止前不是."""
    from datetime import date

    cases = (
        (date(2026, 4, 29), "2026-03-31", "2026-03-31", False),
        (date(2026, 4, 30), "2025-12-31", "2026-03-31", True),
        (date(2026, 8, 30), "2026-06-30", "2026-06-30", False),
        (date(2026, 8, 31), "2026-06-30", "2026-06-30", True),
        (date(2026, 10, 30), "2026-09-30", "2026-09-30", False),
        (date(2026, 10, 31), "2026-09-30", "2026-09-30", True),
    )
    for today, start, plannable, due in cases:
        con = duckdb.connect(":memory:")
        m.ensure_tables(con)
        gap = m.org_holding_period_gap_report(
            con, today=today, start_period=start
        )
        assert gap["plannable"] == plannable, today
        assert gap["action"] == "fetch_then_accept"
        assert gap["completeness_due"] is due, today
        if due:
            assert gap["status"] == "completeness_miss", today
            assert plannable in gap["completeness_miss_periods"]
            if today == date(2026, 4, 30):
                assert "2025-12-31" in gap["completeness_miss_periods"]
        else:
            assert gap["status"] == "plannable_missing", today
            assert plannable not in gap["completeness_miss_periods"]
        con.close()


def test_org_holding_period_gap_under_populated_canary(monkeypatch):
    """Partition exists + thin accepted population → under_populated_accepted."""
    from datetime import date

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    con.execute(
        """
        CREATE TABLE canonical_org_holding_detail_period (
            report_date VARCHAR,
            available_date VARCHAR,
            stock_code VARCHAR,
            holder_code VARCHAR,
            fund_derivecode VARCHAR
        )
        """
    )
    # canary: 2 stocks accepted while raw is dense
    for code in ("600519", "000001"):
        con.execute(
            "INSERT INTO canonical_org_holding_detail_period VALUES "
            "('2026-03-31', '20260430', ?, 'H1', '')",
            [code],
        )
    for i in range(1200):
        con.execute(
            "INSERT INTO raw_org_holding_aif10 "
            "(report_date, stock_code, holder_code, fund_derivecode) "
            "VALUES ('2026-03-31', ?, 'H1', '')",
            [f"{i:06d}"],
        )
    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)
    gap = m.org_holding_period_gap_report(
        con, today=date(2026, 5, 15), start_period="2025-12-31"
    )
    assert gap["action"] == "repair_accept_from_local_raw"
    assert gap["status"] == "under_populated_accepted"
    assert gap["population"]["under_populated"] is True
    assert gap["population"]["accepted_stocks"] == 2
    con.close()


def test_sync_org_holding_incremental_skips_when_plannable_present(monkeypatch):
    import asyncio

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)
    for q in m.enumerate_quarter_ends(m.DEFAULT_START_PERIOD, "2026-03-31"):
        con.execute(
            "INSERT INTO raw_org_holding_aif10 "
            "(report_date, stock_code, holder_code, fund_derivecode) "
            "VALUES (?, '600000', 'H1', '')",
            [q],
        )
    con.commit()
    fetched = {"called": False}
    accepted = {"called": False}

    def _boom(*_a, **_k):
        fetched["called"] = True
        raise AssertionError("must not mass-fetch when plannable present")

    def _accept_boom(*_a, **_k):
        accepted["called"] = True
        raise AssertionError("must not re-accept when accepted present")

    monkeypatch.setattr(m, "sync_period", _boom)
    monkeypatch.setattr(m, "_accept_plannable_from_local_raw", _accept_boom)
    result = asyncio.run(m.sync_org_holding_incremental(con))
    assert result["status"] == "skipped"
    assert result["action"] == "skip_current"
    assert fetched["called"] is False
    assert accepted["called"] is False
    assert "raw=present accepted=present" in result["message"]
    con.close()


def test_sync_org_holding_incremental_accepts_when_raw_present_unaccepted(monkeypatch):
    """Raw already has plannable but accepted missing → accept_from_local_raw (no re-fetch)."""
    import asyncio

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: False)
    con.execute(
        "INSERT INTO raw_org_holding_aif10 "
        "(report_date, available_date, stock_code, holder_code, fund_derivecode) "
        "VALUES ('2026-03-31', '2026-04-30', '600000', 'H1', '')"
    )
    con.commit()
    fetched = {"called": False}

    def _boom(*_a, **_k):
        fetched["called"] = True
        raise AssertionError("must not re-fetch when raw already has plannable")

    monkeypatch.setattr(m, "sync_period", _boom)
    monkeypatch.setattr(
        m,
        "_accept_plannable_from_local_raw",
        lambda _c, rd: {
            "status": "accepted",
            "report_date": rd,
            "available_date": "20260430",
            "canonical_rows": 1,
        },
    )
    result = asyncio.run(m.sync_org_holding_incremental(con))
    assert fetched["called"] is False
    assert result["action"] == "accept_from_local_raw"
    assert result["status"] == "completed"
    assert result["accept"]["status"] == "accepted"
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


def test_sync_period_allows_fetch_when_sibling_shares_available_partition(monkeypatch):
    """Older quarter missing while sibling populates same available_date partition."""
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    con.execute(
        "INSERT INTO raw_org_holding_aif10 "
        "(report_date, stock_code, holder_code, fund_derivecode) "
        "VALUES ('2019-03-31', '600000', 'H1', '')"
    )
    con.commit()
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)
    fetched: list[str] = []

    def _fake_fetch(period: str):
        fetched.append(period)
        return {
            "rows": [
                {
                    "REPORT_DATE": "20181231",
                    "SECURITY_CODE": "600000",
                    "HOLDER_CODE": "H1",
                    "FUND_DERIVECODE": "",
                }
            ],
            "provider_count": 1,
            "fetched_rows": 1,
            "truncated": False,
            "shard_count": 1,
            "land_reasons": [],
        }

    monkeypatch.setattr(m, "_fetch_period", _fake_fetch)
    monkeypatch.setattr(
        "services.data_sources.disclosure_dual_write.write_org_holding_formal_then_mirror",
        lambda _c, rows, **kwargs: type(
            "Outcome",
            (),
            {
                "canonical_rows": len(rows),
                "partitions": ["20190430"],
                "legacy_rows_written": len(rows),
            },
        )(),
    )
    out = m.sync_period(con, "2018-12-31", allow_existing_refresh=False)
    assert fetched == ["2018-12-31"]
    assert out["status"] in {"ok", "empty"}
    con.close()


def test_org_gap_provider_truncated_signature(monkeypatch):
    """~200k rows with low stock mass → merge_period (not ok skip)."""
    from datetime import date

    from services.org_holding_population import decide_org_gap_action, population_for_period

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    for i in range(200_000):
        con.execute(
            "INSERT INTO raw_org_holding_aif10 "
            "(report_date, stock_code, holder_code, fund_derivecode) "
            "VALUES ('2025-12-31', ?, ?, '')",
            [f"{i % 800:06d}", f"H{i}"],
        )
    monkeypatch.setattr(
        "services.org_holding_population.max_accepted_stocks_across_partitions",
        lambda _c: 5520,
    )
    pop = population_for_period(
        con,
        report_date="2025-12-31",
        local_has=True,
        accepted_has=True,
    )
    assert pop["provider_truncated"] is True
    action, status = decide_org_gap_action(
        accepted_has=True,
        local_has=True,
        population=pop,
    )
    assert action == "merge_period"
    assert status == "provider_truncated"
    con.close()


def test_fetch_period_sharded_contract(monkeypatch):
    """_fetch_period returns metrics dict consumed by sync_period."""
    captured = {}

    def _fake_sharded(**kwargs):
        captured.update(kwargs)
        return {
            "rows": [{"SECURITY_CODE": "600000", "REPORT_DATE": "2025-12-31", "HOLDER_CODE": "1"}],
            "provider_count": 1,
            "fetched_rows": 1,
            "truncated": False,
            "shard_count": 2,
            "land_reasons": [],
        }

    monkeypatch.setattr(
        "aif10_scraper.fetch_all_pages_sharded",
        lambda *a, **k: _fake_sharded(**k),
    )
    out = m._fetch_period("2025-12-31")
    assert out["fetched_rows"] == 1
    assert out["truncated"] is False
    assert captured.get("max_pages_per_query") == m.EASTMONEY_MAX_PAGES


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


def test_source_count_ahead_uses_page_margin():
    from services.org_holding_population import source_count_ahead

    assert source_count_ahead(
        local_rows=100_000, source_count=100_500, page_size=2000
    ) is False
    assert source_count_ahead(
        local_rows=100_000, source_count=102_000, page_size=2000
    ) is True
    assert source_count_ahead(
        local_rows=100_000,
        source_count=102_000,
        last_reconciled_count=102_000,
        page_size=2000,
    ) is False
    assert source_count_ahead(
        local_rows=100_000,
        source_count=104_100,
        last_reconciled_count=102_000,
        page_size=2000,
    ) is True


def test_decide_org_gap_merge_when_source_ahead():
    from services.org_holding_population import decide_org_gap_action

    action, status = decide_org_gap_action(
        accepted_has=True,
        local_has=True,
        population={
            "under_populated": False,
            "raw_stocks": 5520,
            "raw_rows": 111_000,
            "provider_truncated": False,
        },
        source_count=200_000,
    )
    assert action == "merge_period"
    assert status == "source_ahead"


def test_decide_org_gap_skip_when_source_within_margin():
    from services.org_holding_population import decide_org_gap_action

    action, status = decide_org_gap_action(
        accepted_has=True,
        local_has=True,
        population={
            "under_populated": False,
            "raw_stocks": 5520,
            "raw_rows": 111_000,
            "provider_truncated": False,
        },
        source_count=111_400,
    )
    assert action == "skip_current"
    assert status == "ok"


def test_source_count_ahead_catches_annual_late_filings():
    """Live 2026-08-27 年报 +1545 必须触发 MERGE, 不能被 2000 页长吃掉."""
    from services.org_holding_population import source_count_ahead

    assert source_count_ahead(local_rows=832_907, source_count=834_452) is True
    assert source_count_ahead(local_rows=111_931, source_count=112_106) is False


def test_sync_period_merge_skips_fetch_when_probe_not_ahead(monkeypatch):
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
        return {}

    monkeypatch.setattr(m, "_probe_period_count", lambda *_a, **_k: 1)
    monkeypatch.setattr(m, "_fetch_period", _boom)
    out = m.sync_period(con, "2026-03-31", merge_grains=True)
    assert out["status"] == "skipped_probe_current"
    assert fetched["called"] is False
    con.close()


def test_sync_period_merge_passes_only_new_grains(monkeypatch):
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    con.execute(
        "INSERT INTO raw_org_holding_aif10 "
        "(report_date, stock_code, holder_code, fund_derivecode) "
        "VALUES ('2026-03-31', '600000', 'H1', '')"
    )
    con.commit()
    monkeypatch.setattr(m, "_probe_period_count", lambda *_a, **_k: 5000)

    def _fake_fetch(_period):
        return {
            "rows": [
                {
                    "REPORT_DATE": "20260331",
                    "SECURITY_CODE": "600000",
                    "HOLDER_CODE": "H1",
                    "FUND_DERIVECODE": "",
                },
                {
                    "REPORT_DATE": "20260331",
                    "SECURITY_CODE": "600000",
                    "HOLDER_CODE": "H2",
                    "FUND_DERIVECODE": "",
                },
            ],
            "provider_count": 5000,
            "fetched_rows": 2,
            "truncated": False,
            "shard_count": 1,
            "land_reasons": [],
        }

    captured: dict = {}

    def _fake_write(_c, rows, **kwargs):
        captured["rows"] = list(rows)
        captured["merge_grains"] = kwargs.get("merge_grains")
        return type(
            "Outcome",
            (),
            {
                "canonical_rows": len(rows),
                "partitions": ["20260430"],
                "legacy_rows_written": len(rows),
            },
        )()

    monkeypatch.setattr(m, "_fetch_period", _fake_fetch)
    monkeypatch.setattr(
        "services.data_sources.disclosure_dual_write.write_org_holding_formal_then_mirror",
        _fake_write,
    )
    out = m.sync_period(con, "2026-03-31", merge_grains=True)
    assert out["status"] == "merged"
    assert captured["merge_grains"] is True
    assert {row["holder_code"] for row in captured["rows"]} == {"H2"}
    con.close()


def test_probe_period_count_is_page_one(monkeypatch):
    from services import org_holding_fetch as fetch

    captured: dict = {}

    class _Client:
        def __init__(self, **_k):
            pass

        def get_v1(self, *_a, **kwargs):
            captured.update(kwargs)
            return {"count": 111931, "pages": 1, "data": []}

    monkeypatch.setattr("aif10_scraper.client.AIF10Client", _Client)
    assert fetch.probe_period_count("2026-03-31") == 111931
    assert captured.get("page") == 1
    assert captured.get("page_size") == 1


def test_daily_incremental_never_sets_allow_existing_refresh_true():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "org_holding_aif10.py"
    ).read_text(encoding="utf-8")
    fn = src.split("async def sync_org_holding_incremental")[1]
    fn = fn.split("\nasync def ")[0].split("\ndef ")[0]
    assert "allow_existing_refresh=False" in fn
    assert "allow_existing_refresh=True," not in fn
    assert "merge_grains=merge" in fn
    assert "raw_only=raw_only" in fn


def test_sync_period_raw_only_writes_legacy_not_canonical(monkeypatch):
    """raw_only hatch: land raw, do not formal-accept."""
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    formal = {"called": False}

    def _fake_fetch(_period):
        return {
            "rows": [
                {
                    "REPORT_DATE": "20260630",
                    "SECURITY_CODE": "600000",
                    "HOLDER_CODE": "H1",
                    "FUND_DERIVECODE": "",
                }
            ],
            "provider_count": 1,
            "fetched_rows": 1,
            "truncated": False,
            "shard_count": 1,
            "land_reasons": [],
        }

    def _boom_formal(*_a, **_k):
        formal["called"] = True
        raise AssertionError("raw_only must not formal-accept")

    monkeypatch.setattr(m, "_fetch_period", _fake_fetch)
    monkeypatch.setattr(
        "services.data_sources.disclosure_dual_write.write_org_holding_formal_then_mirror",
        _boom_formal,
    )
    out = m.sync_period(con, "2026-06-30", raw_only=True)
    assert formal["called"] is False
    assert out["status"] == "ok_raw"
    assert out["accepted_partitions"] == []
    assert con.execute(
        "SELECT COUNT(*) FROM raw_org_holding_aif10 WHERE report_date = '2026-06-30'"
    ).fetchone()[0] == 1
    con.close()

