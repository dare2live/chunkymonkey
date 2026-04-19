import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.institution as institution_router
from services import quote_snapshot_client, setup_tracker


def _make_biz_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_trading_calendar (
            trade_date TEXT PRIMARY KEY,
            is_trading INTEGER DEFAULT 1
        );

        CREATE TABLE fact_setup_snapshot (
            snapshot_date TEXT,
            stock_code TEXT,
            setup_tag TEXT,
            setup_inst_id TEXT,
            entry_trade_date TEXT,
            entry_price REAL,
            current_trade_date TEXT,
            current_price REAL,
            gain_to_now REAL,
            gain_10d REAL,
            gain_30d REAL,
            gain_60d REAL,
            max_drawdown_10d REAL,
            max_drawdown_30d REAL,
            max_drawdown_60d REAL,
            matured_10d INTEGER DEFAULT 0,
            matured_30d INTEGER DEFAULT 0,
            matured_60d INTEGER DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (snapshot_date, stock_code, setup_tag, setup_inst_id)
        );
        """
    )
    conn.executemany(
        "INSERT INTO dim_trading_calendar (trade_date, is_trading) VALUES (?, ?)",
        [
            ("2026-04-10", 1),
            ("2026-04-11", 0),
            ("2026-04-13", 1),
            ("2026-04-14", 1),
            ("2026-04-15", 1),
        ],
    )
    conn.execute(
        "INSERT INTO fact_setup_snapshot (snapshot_date, stock_code, setup_tag, setup_inst_id, entry_trade_date, entry_price) VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-04-15", "600036", "A", "inst_1", "2026-04-10", 10.0),
    )
    conn.commit()
    return conn


def _make_market_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE price_kline (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            freq TEXT NOT NULL DEFAULT 'daily',
            adjust TEXT NOT NULL DEFAULT 'qfq',
            close REAL,
            PRIMARY KEY (code, date, freq, adjust)
        );
        """
    )
    conn.executemany(
        "INSERT INTO price_kline (code, date, freq, adjust, close) VALUES (?, ?, 'daily', 'qfq', ?)",
        [
            ("600036", "2026-04-10", 10.0),
            ("600036", "2026-04-13", 10.8),
            ("600036", "2026-04-14", 11.0),
        ],
    )
    conn.commit()
    return conn


def test_fetch_stock_spot_batch_falls_back_to_next_server_and_normalizes_price():
    frame = pd.DataFrame(
        [
            {"code": "600036", "price": 38.98, "last_close": 39.21, "open": 39.13, "high": 39.16, "low": 38.92, "vol": 525144, "amount": 2047706880.0, "servertime": "14:59:47.051"},
            {"code": "000001", "price": 11.07, "last_close": 11.09, "open": 11.05, "high": 11.09, "low": 11.03, "vol": 406104, "amount": 449056800.0, "servertime": "15:32:55.164"},
        ]
    )

    with mock.patch.object(
        quote_snapshot_client,
        "call_tdx_quotes_with_retry",
        return_value=(frame, "tdxhub_2.2.2.2:7709"),
    ):
        result = quote_snapshot_client.fetch_stock_spot_batch(["600036", "000001"])

    assert sorted(result.keys()) == ["000001", "600036"]
    assert result["600036"]["price"] == 38.98
    assert result["600036"]["source"] == "tdxhub_2.2.2.2:7709"
    assert result["000001"]["volume"] == 406104.0


def test_refresh_setup_snapshot_returns_prefers_live_quotes_when_available():
    biz_conn = _make_biz_conn()
    market_conn = _make_market_conn()
    try:
        with mock.patch.object(setup_tracker, "get_market_conn", return_value=market_conn), mock.patch.object(
            setup_tracker,
            "fetch_stock_spot_batch",
            return_value={"600036": {"price": 12.5, "source": "tdxhub_test"}},
        ), mock.patch.object(setup_tracker, "_today_str", return_value="2026-04-15"):
            refreshed = setup_tracker.refresh_setup_snapshot_returns(biz_conn, snapshot_date="2026-04-15")

        assert refreshed == 1
        row = biz_conn.execute(
            "SELECT current_trade_date, current_price, gain_to_now FROM fact_setup_snapshot WHERE stock_code = '600036'"
        ).fetchone()
        assert row["current_trade_date"] == "2026-04-15"
        assert row["current_price"] == 12.5
        assert row["gain_to_now"] == 25.0
    finally:
        try:
            market_conn.close()
        except Exception:
            pass
        biz_conn.close()


def test_refresh_setup_snapshot_returns_falls_back_to_latest_close_when_quotes_missing():
    biz_conn = _make_biz_conn()
    market_conn = _make_market_conn()
    try:
        with mock.patch.object(setup_tracker, "get_market_conn", return_value=market_conn), mock.patch.object(
            setup_tracker,
            "fetch_stock_spot_batch",
            return_value={},
        ), mock.patch.object(setup_tracker, "_today_str", return_value="2026-04-15"):
            refreshed = setup_tracker.refresh_setup_snapshot_returns(biz_conn, snapshot_date="2026-04-15")

        assert refreshed == 1
        row = biz_conn.execute(
            "SELECT current_trade_date, current_price, gain_to_now FROM fact_setup_snapshot WHERE stock_code = '600036'"
        ).fetchone()
        assert row["current_trade_date"] == "2026-04-14"
        assert row["current_price"] == 11.0
        assert row["gain_to_now"] == 10.0
    finally:
        try:
            market_conn.close()
        except Exception:
            pass
        biz_conn.close()


def test_backfill_setup_snapshot_industry_uses_shared_industry_alias_map(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fact_setup_snapshot (
            snapshot_date TEXT,
            stock_code TEXT,
            setup_tag TEXT,
            setup_inst_id TEXT,
            snapshot_tdx_l1 TEXT,
            snapshot_tdx_l2 TEXT,
            snapshot_tdx_l3 TEXT,
            updated_at TEXT,
            PRIMARY KEY (snapshot_date, stock_code, setup_tag, setup_inst_id)
        );
        """
    )
    try:
        conn.execute(
            "INSERT INTO fact_setup_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-04-15", "600036", "A", "inst_1", "", "", "", None),
        )
        conn.commit()
        monkeypatch.setattr(
            setup_tracker,
            "load_industry_map",
            lambda _conn: {
                "600036": {
                    "tdx_l1": "T01",
                    "tdx_l2": "T0101",
                    "tdx_l3": "T010101",
                }
            },
        )

        updated = setup_tracker.backfill_setup_snapshot_industry(conn, snapshot_date="2026-04-15")

        row = conn.execute(
            "SELECT snapshot_tdx_l1, snapshot_tdx_l2, snapshot_tdx_l3 FROM fact_setup_snapshot WHERE stock_code = '600036'"
        ).fetchone()
        assert updated == 1
        assert row["snapshot_tdx_l1"] == "T01"
        assert row["snapshot_tdx_l2"] == "T0101"
        assert row["snapshot_tdx_l3"] == "T010101"
    finally:
        conn.close()


def test_list_setup_tracking_snapshots_returns_quality_provenance():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fact_setup_snapshot (
            snapshot_date TEXT,
            stock_code TEXT,
            stock_name TEXT,
            setup_tag TEXT,
            setup_priority INTEGER,
            setup_reason TEXT,
            setup_confidence TEXT,
            setup_inst_name TEXT,
            latest_report_date TEXT,
            discovery_score REAL,
            company_quality_score REAL,
            company_quality_score_source TEXT,
            quality_feature_snapshot_date TEXT,
            stage_score REAL,
            forecast_score REAL,
            composite_priority_score REAL,
            priority_pool TEXT,
            stock_archetype TEXT,
            score_highlights TEXT,
            score_risks TEXT,
            crowding_bucket TEXT,
            crowding_fit_raw REAL,
            crowding_fit_grade INTEGER,
            gain_to_now REAL,
            gain_10d REAL,
            gain_30d REAL,
            gain_60d REAL,
            max_drawdown_10d REAL,
            max_drawdown_30d REAL,
            max_drawdown_60d REAL,
            matured_10d INTEGER,
            matured_30d INTEGER,
            matured_60d INTEGER,
            setup_score_raw REAL,
            PRIMARY KEY (snapshot_date, stock_code, setup_tag)
        );
        """
    )
    try:
        conn.executemany(
            """
            INSERT INTO fact_setup_snapshot (
                snapshot_date, stock_code, stock_name, setup_tag, setup_priority,
                setup_reason, setup_confidence, setup_inst_name, latest_report_date,
                discovery_score, company_quality_score, company_quality_score_source,
                quality_feature_snapshot_date, stage_score, forecast_score,
                composite_priority_score, priority_pool, stock_archetype, score_highlights,
                score_risks, crowding_bucket, crowding_fit_raw, crowding_fit_grade,
                gain_to_now, gain_10d, gain_30d, gain_60d, max_drawdown_10d,
                max_drawdown_30d, max_drawdown_60d, matured_10d, matured_30d,
                matured_60d, setup_score_raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "2026-04-15", "600036", "招行", "industry_expert_entry", 1,
                    "等待突破", "high", "测试机构", "2025-12-31",
                    75.0, 66.0, None,
                    None, 58.0, 61.0,
                    72.0, "B池", "成长型", "亮点",
                    "风险", "中等", 4.0, 2,
                    3.2, 1.1, 2.2, 3.3, 0.8,
                    1.6, 2.4, 1, 1,
                    0, 80.0
                ),
                (
                    "2026-04-14", "000001", "平安", "industry_expert_entry", 2,
                    "财报对齐", "medium", "另一家机构", "2025-12-31",
                    70.0, 71.0, "quality_feature_v1",
                    "2025-12-31", 62.0, 65.0,
                    76.0, "A池", "高质量稳健型", "亮点2",
                    "风险2", "中等", 5.0, 1,
                    4.2, 1.5, 2.6, 3.7, 1.0,
                    1.8, 2.9, 1, 1,
                    1, 82.0
                ),
            ],
        )
        conn.commit()

        rows = setup_tracker.list_setup_tracking_snapshots(conn, limit=10)

        latest = rows[0]
        older = rows[1]
        assert latest["company_quality_score_source"] == "stock_scoring_v2"
        assert latest["quality_feature_snapshot_date"] is None
        assert older["company_quality_score_source"] == "quality_feature_v1"
        assert older["quality_feature_snapshot_date"] == "2025-12-31"
    finally:
        conn.close()


def test_list_watchlist_returns_quality_provenance(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE stock_watchlist (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            added_date TEXT,
            added_price REAL,
            added_reason TEXT,
            source_institution TEXT,
            source_event_type TEXT,
            status TEXT,
            updated_at TEXT,
            gain_since_added REAL,
            max_gain REAL,
            max_drawdown REAL
        );
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            setup_tag TEXT,
            setup_priority INTEGER,
            setup_reason TEXT,
            setup_confidence TEXT,
            discovery_score REAL,
            company_quality_score REAL,
            company_quality_score_source TEXT,
            quality_feature_snapshot_date TEXT,
            stage_score REAL,
            forecast_score REAL,
            raw_composite_priority_score REAL,
            composite_priority_score REAL,
            priority_pool TEXT,
            priority_pool_reason TEXT,
            composite_cap_reason TEXT,
            external_attention_score REAL,
            external_crowding_penalty REAL,
            external_attention_signal TEXT,
            score_highlights TEXT,
            score_risks TEXT
        );
        """
    )
    try:
        conn.execute(
            """
            INSERT INTO stock_watchlist (
                stock_code, stock_name, added_date, added_price, added_reason,
                source_institution, source_event_type, status, updated_at,
                gain_since_added, max_gain, max_drawdown
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("600036", "招行", "2026-04-10", 10.0, "手工加入", "测试机构", "increase", "active", "2026-04-15T10:00:00", 5.2, 8.4, 1.3),
        )
        conn.execute(
            """
            INSERT INTO mart_stock_trend (
                stock_code, setup_tag, setup_priority, setup_reason, setup_confidence,
                discovery_score, company_quality_score, company_quality_score_source,
                quality_feature_snapshot_date, stage_score, forecast_score,
                raw_composite_priority_score, composite_priority_score, priority_pool,
                priority_pool_reason, composite_cap_reason, external_attention_score,
                external_crowding_penalty, external_attention_signal, score_highlights,
                score_risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "600036", "industry_expert_entry", 1, "等待突破", "high",
                75.0, 71.0, None,
                None, 58.0, 62.0,
                77.0, 74.0, "B池",
                "阶段未完全打开", "阶段封顶", 68.0,
                3.0, "关注度抬升", "亮点", "风险"
            ),
        )
        conn.commit()

        monkeypatch.setattr(institution_router, "get_conn", lambda *args, **kwargs: conn)

        payload = asyncio.run(institution_router.list_watchlist())

        assert payload["ok"] is True
        assert payload["total"] == 1
        item = payload["data"][0]
        assert item["company_quality_score_source"] == "stock_scoring_v2"
        assert item["quality_feature_snapshot_date"] is None
    finally:
        conn.close()