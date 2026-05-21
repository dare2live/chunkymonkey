import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import routers.institution as institution_router
import services.institution_read as institution_read


def test_load_tracked_institution_names_merges_aliases_and_ignores_bad_json():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT,
            aliases TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO inst_institutions VALUES (?, ?, ?)",
            [
                ("inst_a", "高瓴资本", '["高瓴", "Hillhouse"]'),
                ("inst_b", "景林资产", None),
                ("inst_c", "源码资本", 'not-json'),
            ],
        )
        conn.commit()

        tracked_names = institution_read.load_tracked_institution_names(conn)

        assert tracked_names == {"高瓴资本", "高瓴", "Hillhouse", "景林资产", "源码资本"}
    finally:
        conn.close()


def test_search_institution_candidates_requires_keywords(monkeypatch):
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT,
            aliases TEXT
        );
        CREATE TABLE _cache_holder_search (
            holder_name TEXT,
            holder_type TEXT,
            stock_count INTEGER,
            latest_notice TEXT
        );
        """
    )
    try:
        monkeypatch.setattr(institution_read, "_ensure_cache", lambda _conn: None)

        payload = institution_read.search_institution_candidates(conn, " , ， 、 ")

        assert payload == {"ok": False, "message": "请输入关键词"}
    finally:
        conn.close()


def test_search_institution_candidates_supports_or_and_and_alias_tracking(monkeypatch):
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT,
            aliases TEXT
        );
        CREATE TABLE _cache_holder_search (
            holder_name TEXT,
            holder_type TEXT,
            stock_count INTEGER,
            latest_notice TEXT
        );
        """
    )
    try:
        monkeypatch.setattr(institution_read, "_ensure_cache", lambda _conn: None)
        conn.executemany(
            "INSERT INTO inst_institutions VALUES (?, ?, ?)",
            [
                ("inst_a", "高瓴资本", '["高瓴 景林联合"]'),
                ("inst_b", "淡马锡", '["淡马锡控股"]'),
            ],
        )
        conn.executemany(
            "INSERT INTO _cache_holder_search VALUES (?, ?, ?, ?)",
            [
                ("高瓴 景林联合", "基金", 9, "2026-04-12"),
                ("淡马锡控股", "QFII", 7, "2026-04-11"),
                ("高瓴资本", "基金", 12, "2026-04-13"),
            ],
        )
        conn.commit()

        payload = institution_read.search_institution_candidates(conn, "高瓴 景林, 淡马锡")

        assert payload["ok"] is True
        assert payload["keywords"] == ["高瓴 景林", "淡马锡"]
        assert [row["holder_name"] for row in payload["data"]] == ["高瓴 景林联合", "淡马锡控股"]
        assert all(row["tracked"] is True for row in payload["data"])
    finally:
        conn.close()


def test_search_institution_candidates_filters_holder_type_on_cache_table(monkeypatch):
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT,
            aliases TEXT
        );
        CREATE TABLE _cache_holder_search (
            holder_name TEXT,
            holder_type TEXT,
            stock_count INTEGER,
            latest_notice TEXT
        );
        """
    )
    try:
        monkeypatch.setattr(institution_read, "_ensure_cache", lambda _conn: None)
        conn.executemany(
            "INSERT INTO _cache_holder_search VALUES (?, ?, ?, ?)",
            [
                ("淡马锡控股", "QFII", 7, "2026-04-11"),
                ("淡马锡基金", "基金", 6, "2026-04-10"),
            ],
        )
        conn.commit()

        payload = institution_read.search_institution_candidates(conn, "淡马锡", holder_type="QFII")

        assert payload["ok"] is True
        assert [row["holder_name"] for row in payload["data"]] == ["淡马锡控股"]
        assert payload["data"][0]["holder_type"] == "QFII"
    finally:
        conn.close()


def test_load_tracked_institutions_filters_and_sorts(monkeypatch):
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT,
            display_name TEXT,
            type TEXT,
            blacklisted INTEGER,
            enabled INTEGER,
            merged_into TEXT
        );
        CREATE TABLE inst_holdings (
            institution_id TEXT,
            stock_code TEXT,
            report_date TEXT,
            notice_date TEXT,
            hold_market_cap REAL
        );
        CREATE TABLE _cache_stock_latest_rd (
            stock_code TEXT,
            max_rd TEXT
        );
        """
    )
    try:
        monkeypatch.setattr(institution_read, "_ensure_cache", lambda _conn: None)
        conn.executemany(
            "INSERT INTO inst_institutions VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_active", "机构甲", "甲", "fund", 0, 1, None),
                ("inst_inactive", "机构乙", "乙", "insurance", 1, 1, None),
                ("inst_disabled", "机构丙", "丙", "broker", 0, 0, None),
            ],
        )
        conn.executemany(
            "INSERT INTO _cache_stock_latest_rd VALUES (?, ?)",
            [("600001", "2026-03-31"), ("600002", "2026-03-31"), ("600003", "2026-03-31")],
        )
        conn.executemany(
            "INSERT INTO inst_holdings VALUES (?, ?, ?, ?, ?)",
            [
                ("inst_active", "600001", "2026-03-31", "2026-04-10", 100.0),
                ("inst_active", "600002", "2026-03-31", "2026-04-11", 90.0),
                ("inst_inactive", "600003", "2026-03-31", "2026-04-09", 80.0),
            ],
        )
        conn.commit()

        active_rows = institution_read.load_tracked_institutions(conn, show="active")
        inactive_rows = institution_read.load_tracked_institutions(conn, show="inactive")

        assert [row["id"] for row in active_rows] == ["inst_active"]
        assert active_rows[0]["stock_count"] == 2
        assert active_rows[0]["latest_notice"] == "2026-04-11"
        assert {row["id"] for row in inactive_rows} == {"inst_inactive", "inst_disabled"}
    finally:
        conn.close()


def test_load_institution_profiles_uses_live_display_name_and_type():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_institution_profile (
            institution_id TEXT PRIMARY KEY,
            institution_name TEXT,
            display_name TEXT,
            inst_type TEXT,
            win_rate_30d REAL
        );
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            display_name TEXT,
            type TEXT,
            enabled INTEGER,
            blacklisted INTEGER,
            merged_into TEXT
        );
        -- load_institution_profiles 的 CTE exit_stats 依赖 fact_institution_event
        CREATE TABLE fact_institution_event (
            institution_id TEXT,
            stock_code TEXT,
            report_date TEXT,
            event_type TEXT,
            gain_30d REAL,
            gain_60d REAL,
            gain_120d REAL
        );
        """
    )
    try:
        conn.execute(
            "INSERT INTO mart_institution_profile VALUES (?, ?, ?, ?, ?)",
            ("inst_a", "机构甲", "旧简称", "old_type", 62.0),
        )
        conn.execute(
            "INSERT INTO inst_institutions VALUES (?, ?, ?, ?, ?, ?)",
            ("inst_a", "新简称", "fund", 1, 0, None),
        )
        conn.commit()

        rows = institution_read.load_institution_profiles(conn)

        assert len(rows) == 1
        assert rows[0]["display_name"] == "新简称"
        assert rows[0]["inst_type"] == "fund"
    finally:
        conn.close()


def test_load_institution_profile_detail_appends_exits_and_builds_industry_summary(monkeypatch):
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_institution_industry_stat (
            institution_id TEXT,
            industry_level TEXT,
            industry_name TEXT,
            avg_gain_30d REAL,
            win_rate_30d REAL
        );
        """
    )
    try:
        monkeypatch.setattr(
            institution_read,
            "get_inst_current_holdings",
            lambda _conn, _inst_id: [
                {"stock_code": "600001", "stock_name": "样本一", "event_type": "new_entry"},
                {"stock_code": "600002", "stock_name": "样本二", "event_type": "increase"},
            ],
        )
        monkeypatch.setattr(
            institution_read,
            "get_inst_exits",
            lambda _conn, _inst_id: [{"stock_code": "600003", "stock_name": "样本三", "exit_report_date": "2026-03-31"}],
        )
        monkeypatch.setattr(
            institution_read,
            "load_industry_map",
            lambda _conn: {
                "600001": {"tdx_l1": "电子", "tdx_l2": "半导体", "tdx_l3": "芯片设计"},
                "600002": {"tdx_l1": "电子", "tdx_l2": "半导体", "tdx_l3": "封装测试"},
            },
        )
        conn.executemany(
            "INSERT INTO mart_institution_industry_stat VALUES (?, ?, ?, ?, ?)",
            [
                ("inst_a", "level1", "电子", 8.5, 66.0),
                ("inst_a", "level2", "半导体", 10.2, 70.0),
            ],
        )
        conn.commit()

        payload = institution_read.load_institution_profile_detail(conn, "inst_a")

        assert payload["total"] == 3
        assert payload["data"][-1]["event_type"] == "exit"
        assert payload["data"][-1]["change_pct"] == -100.0
        assert len(payload["industry_summary"]) == 1
        assert payload["industry_summary"][0]["level1"] == "电子"
        assert payload["industry_summary"][0]["avg_gain_30d"] == 8.5
        assert payload["industry_summary"][0]["children"][0]["level2"] == "半导体"
        assert payload["industry_summary"][0]["children"][0]["win_rate_30d"] == 70.0
        assert len(payload["industry_summary"][0]["children"][0]["children"]) == 2
    finally:
        conn.close()


def test_router_load_industry_stat_map_batches_stats():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_institution_industry_stat (
            institution_id TEXT,
            industry_level TEXT,
            industry_name TEXT,
            avg_gain_30d REAL,
            win_rate_30d REAL
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO mart_institution_industry_stat VALUES (?, ?, ?, ?, ?)",
            [
                ("inst_a", "level1", "电子", 8.5, 66.0),
                ("inst_a", "level2", "半导体", 10.2, 70.0),
                ("inst_b", "level2", "银行", -1.0, 30.0),
            ],
        )
        conn.commit()

        stat_map = institution_router._load_industry_stat_map(conn, "inst_a")

        assert set(stat_map) == {("level1", "电子"), ("level2", "半导体")}
        assert stat_map[("level2", "半导体")]["avg_gain_30d"] == pytest.approx(10.2)
        assert stat_map[("level1", "电子")]["win_rate_30d"] == pytest.approx(66.0)
    finally:
        conn.close()


def test_load_institution_returns_history_calculates_extremes():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE fact_institution_event (
            institution_id TEXT,
            report_date TEXT,
            notice_date TEXT,
            event_type TEXT,
            gain_10d REAL,
            gain_30d REAL,
            gain_60d REAL,
            gain_120d REAL,
            max_drawdown_30d REAL
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "2026-03-31", "2026-04-01", "new_entry", 2.0, 3.0, 8.0, 10.0, 4.5),
                ("inst_a", "2026-03-31", "2026-04-03", "increase", 1.0, 2.5, 12.4, 16.0, 6.0),
                ("inst_a", "2026-03-31", "2026-04-05", "decrease", 0.0, None, None, None, 1.0),
            ],
        )
        conn.commit()

        payload = institution_read.load_institution_returns_history(conn, "inst_a")

        assert len(payload["data"]) == 2
        assert payload["max_gain"] == 12.4
        assert payload["max_drawdown"] == -6.0
    finally:
        conn.close()
