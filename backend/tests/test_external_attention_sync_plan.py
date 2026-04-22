import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.updater import _collect_downstream_steps  # noqa: E402
from services.audit import (  # noqa: E402
    _classify_plannable_stale_kline_codes,
    _current_relationship_plan_reason,
    _external_attention_plan_reason,
    _needs_stock_score_recalc,
    _summarize_current_relationship_freshness,
    _summarize_external_attention,
    build_smart_plan,
)
from services.utils import latest_completed_trade_date  # noqa: E402


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_stock_attention_latest (
            stock_code TEXT,
            snapshot_date TEXT,
            comment_available INTEGER DEFAULT 0,
            survey_available INTEGER DEFAULT 0,
            comment_trade_date TEXT,
            last_survey_date TEXT
        );

        CREATE TABLE mart_current_relationship (
            stock_code TEXT,
            report_date TEXT
        );

        CREATE TABLE market_raw_holdings (
            stock_code TEXT,
            report_date TEXT
        );

        CREATE TABLE excluded_stocks (
            stock_code TEXT PRIMARY KEY
        );

        CREATE TABLE mart_stock_trend (
            stock_code TEXT,
            external_attention_score REAL,
            attention_comment_trade_date TEXT,
            external_attention_signal TEXT
        );
        """
    )
    return conn


def _make_plan_conn(*, with_turtle=True):
    conn = _make_conn()
    today = date.today()
    conn.executescript(
        """
        CREATE TABLE dim_financial_latest (
            stock_code TEXT
        );

        CREATE TABLE dim_stock_quality_latest (
            stock_code TEXT
        );

        CREATE TABLE raw_gpcw_financial (
            stock_code TEXT,
            report_date TEXT
        );

        CREATE TABLE fact_financial_indicator_ak (
            stock_code TEXT,
            report_date TEXT
        );

        CREATE TABLE dim_stock_stage_latest (
            stock_code TEXT
        );

        CREATE TABLE qlib_model_state (
            model_id TEXT,
            status TEXT,
            created_at TEXT
        );

        CREATE TABLE dim_stock_forecast_latest (
            stock_code TEXT,
            model_id TEXT
        );

        CREATE TABLE dim_sector_forecast_latest (
            sector_name TEXT,
            model_id TEXT
        );

        CREATE TABLE dim_stock_turtle_latest (
            stock_code TEXT,
            model_id TEXT
        );

        CREATE TABLE mart_stock_screening (
            screen_date TEXT
        );

        CREATE TABLE mart_sector_momentum (
            sector_name TEXT
        );

        CREATE TABLE dim_stock_industry_context_latest (
            stock_code TEXT
        );

        CREATE TABLE dim_trading_calendar (
            trade_date TEXT,
            is_trading INTEGER
        );

        CREATE TABLE step_status (
            step_id TEXT PRIMARY KEY,
            error TEXT,
            finished_at TEXT
        );

        CREATE TABLE financial_sync_state (
            stock_code TEXT PRIMARY KEY,
            history_rows INTEGER DEFAULT 0,
            last_report_date TEXT,
            last_snapshot_at TEXT,
            last_history_at TEXT,
            history_status TEXT,
            history_error TEXT,
            snapshot_status TEXT,
            snapshot_error TEXT,
            status TEXT DEFAULT 'pending',
            error TEXT,
            updated_at TEXT
        );

        CREATE TABLE raw_institution_surveys (
            institution_id TEXT,
            stock_code TEXT,
            notice_date TEXT
        );
        """
    )

    for offset in range(0, 5):
        conn.execute(
            "INSERT INTO dim_trading_calendar (trade_date, is_trading) VALUES (?, 1)",
            ((today - timedelta(days=offset)).isoformat(),),
        )

    latest_trade_date = latest_completed_trade_date(conn) or today.isoformat()
    previous_trade_date = (date.fromisoformat(latest_trade_date) - timedelta(days=1)).isoformat()

    conn.execute("INSERT INTO dim_financial_latest (stock_code) VALUES ('000001')")
    conn.execute("INSERT INTO dim_stock_quality_latest (stock_code) VALUES ('000001')")
    conn.execute("INSERT INTO dim_stock_stage_latest (stock_code) VALUES ('000001')")
    conn.execute(
        """
        INSERT INTO qlib_model_state (model_id, status, created_at)
        VALUES ('model-1', 'trained', '2026-04-15T00:00:00')
        """
    )
    conn.execute(
        "INSERT INTO dim_stock_forecast_latest (stock_code, model_id) VALUES ('000001', 'model-1')"
    )
    conn.execute(
        "INSERT INTO dim_sector_forecast_latest (sector_name, model_id) VALUES ('全市场', 'model-1')"
    )
    if with_turtle:
        conn.execute(
            "INSERT INTO dim_stock_turtle_latest (stock_code, model_id) VALUES ('000001', 'model-1')"
        )
    # 机构调研最新（避免 sync_surveys 触发 build_external_attention + calc_stock_scores）
    conn.execute(
        "INSERT INTO raw_institution_surveys (institution_id, stock_code, notice_date) VALUES (?, ?, ?)",
        ("inst_a", "000001", today.isoformat()),
    )
    conn.execute("INSERT INTO mart_stock_screening (screen_date) VALUES ('2026-04-15')")
    conn.execute("INSERT INTO mart_sector_momentum (sector_name) VALUES ('行业A')")
    conn.execute("INSERT INTO dim_stock_industry_context_latest (stock_code) VALUES ('000001')")
    conn.commit()
    return conn


def _make_audit(*, holdings_count=1, events_count=1, returns_count=1, returns_total=1):
    today = date.today()
    return {
        "layers": {
            "raw": {"latest_notice": today.strftime("%Y%m%d")},
            "holdings": {"count": holdings_count},
            "institutions": {"tracked": 1},
            "kline": {"missing": 0, "stale_stocks": 0},
            "events": {"count": events_count},
            "returns": {"count": returns_count, "total": returns_total},
            "industry": {"missing": 0, "count": 1},
            "current_relationship": {
                "count": 1,
                "stock_gap": 0,
                "institution_gap": 0,
                "row_gap": 0,
                "stale_stocks": 0,
            },
            "external_attention": {
                "latest_snapshot_date": today.isoformat(),
                "snapshot_rows": 1,
                "snapshot_lag_days": 0,
                "expected_stocks": 1,
                "missing_stocks": 0,
            },
        }
    }


class ExternalAttentionSyncPlanTests(unittest.TestCase):
    def test_classify_plannable_stale_kline_codes_uses_tracked_scope(self):
        conn = _make_conn()
        try:
            rows = [
                {"code": "000001", "max_date": "2026-04-01"},
                {"code": "000002", "max_date": "2026-04-01"},
                {"code": "000003", "max_date": "2026-04-01"},
            ]
            conn.execute("INSERT INTO excluded_stocks (stock_code) VALUES ('000003')")
            stale_count, suspended_count = _classify_plannable_stale_kline_codes(
                conn,
                rows,
                holding_codes={"000001", "000003"},
                suspended_codes={"000001"},
            )

            self.assertEqual(stale_count, 0)
            self.assertEqual(suspended_count, 1)
        finally:
            conn.close()

    def test_summary_marks_stale_snapshot_before_market_refresh(self):
        conn = _make_conn()
        today = date.today()
        snapshot_day = today - timedelta(days=2)

        conn.executemany(
            """
            INSERT INTO dim_stock_attention_latest (
                stock_code, snapshot_date, comment_available, survey_available,
                comment_trade_date, last_survey_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", snapshot_day.isoformat(), 1, 0, (today - timedelta(days=3)).isoformat(), (today - timedelta(days=2)).isoformat()),
                ("000002", snapshot_day.isoformat(), 1, 1, (today - timedelta(days=2)).isoformat(), (today - timedelta(days=1)).isoformat()),
            ],
        )
        conn.executemany(
            "INSERT INTO mart_current_relationship (stock_code) VALUES (?)",
            [("000001",), ("000002",), ("000003",)],
        )
        conn.executemany(
            """
            INSERT INTO mart_stock_trend (
                stock_code, external_attention_score, attention_comment_trade_date, external_attention_signal
            ) VALUES (?, ?, ?, ?)
            """,
            [
                ("000001", 72.0, snapshot_day.isoformat(), "外部确认增强"),
                ("000003", None, None, ""),
            ],
        )

        summary = _summarize_external_attention(
            conn,
            today.isoformat(),
            expected_stocks=3,
            expected_stock_codes={"000001", "000002", "000003"},
        )

        self.assertEqual(summary["latest_snapshot_date"], snapshot_day.isoformat())
        self.assertEqual(summary["snapshot_rows"], 2)
        self.assertEqual(summary["covered_stocks"], 2)
        self.assertEqual(summary["missing_stocks"], 1)
        self.assertEqual(summary["snapshot_lag_days"], 2)
        self.assertEqual(summary["trend_scored_stocks"], 1)
        self.assertEqual(_external_attention_plan_reason(summary), "外部关注快照滞后2天")

    def test_summary_marks_missing_current_stock_coverage_even_when_snapshot_is_fresh(self):
        conn = _make_conn()
        today = date.today()

        conn.executemany(
            """
            INSERT INTO dim_stock_attention_latest (
                stock_code, snapshot_date, comment_available, survey_available,
                comment_trade_date, last_survey_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", today.isoformat(), 1, 0, today.isoformat(), today.isoformat()),
                ("000002", today.isoformat(), 1, 1, today.isoformat(), today.isoformat()),
            ],
        )
        conn.executemany(
            "INSERT INTO mart_current_relationship (stock_code) VALUES (?)",
            [("000001",), ("000002",), ("000003",)],
        )

        summary = _summarize_external_attention(
            conn,
            today.isoformat(),
            expected_stocks=3,
            expected_stock_codes={"000001", "000002", "000003"},
        )

        self.assertEqual(summary["snapshot_lag_days"], 0)
        self.assertEqual(summary["missing_stocks"], 1)
        self.assertEqual(_external_attention_plan_reason(summary), "1只当前股票缺外部关注覆盖")

    def test_summary_uses_expected_stock_codes_instead_of_current_relationship_join(self):
        conn = _make_conn()
        today = date.today()

        conn.executemany(
            """
            INSERT INTO dim_stock_attention_latest (
                stock_code, snapshot_date, comment_available, survey_available,
                comment_trade_date, last_survey_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", today.isoformat(), 1, 0, today.isoformat(), today.isoformat()),
                ("000002", today.isoformat(), 1, 1, today.isoformat(), today.isoformat()),
                ("000003", today.isoformat(), 1, 0, today.isoformat(), today.isoformat()),
            ],
        )
        conn.executemany(
            "INSERT INTO mart_current_relationship (stock_code) VALUES (?)",
            [("000001",), ("000002",)],
        )

        summary = _summarize_external_attention(
            conn,
            today.isoformat(),
            expected_stocks=3,
            expected_stock_codes={"000001", "000002", "000003"},
        )

        self.assertEqual(summary["covered_stocks"], 3)
        self.assertEqual(summary["missing_stocks"], 0)
        self.assertIsNone(_external_attention_plan_reason(summary))

    def test_current_relationship_summary_marks_per_stock_report_date_staleness(self):
        conn = _make_conn()

        conn.executemany(
            "INSERT INTO market_raw_holdings (stock_code, report_date) VALUES (?, ?)",
            [
                ("000001", "2026-03-31"),
                ("000002", "2026-03-31"),
            ],
        )
        conn.executemany(
            "INSERT INTO mart_current_relationship (stock_code, report_date) VALUES (?, ?)",
            [
                ("000001", "2026-03-31"),
                ("000002", "2025-09-30"),
            ],
        )

        summary = _summarize_current_relationship_freshness(conn)

        self.assertEqual(summary["latest_raw_report_date"], "2026-03-31")
        self.assertEqual(summary["latest_current_report_date"], "2026-03-31")
        self.assertEqual(summary["stale_stocks"], 1)
        self.assertEqual(summary["sample_stock_code"], "000002")
        self.assertEqual(summary["sample_expected_report_date"], "2026-03-31")
        self.assertEqual(summary["sample_current_report_date"], "2025-09-30")
        self.assertEqual(_current_relationship_plan_reason({"count": 2, **summary}), "1只股票当前关系落后于最新报告期")

    def test_current_relationship_plan_reason_detects_gap_mismatches(self):
        self.assertEqual(
            _current_relationship_plan_reason({"count": 10, "stock_gap": -2}),
            "当前关系少2只最新股票",
        )
        self.assertEqual(
            _current_relationship_plan_reason({"count": 10, "institution_gap": 3}),
            "当前关系多3家非最新机构",
        )
        self.assertEqual(
            _current_relationship_plan_reason({"count": 10, "row_gap": -5}),
            "当前关系少5条最新持仓关系",
        )

    def test_build_external_attention_cascades_to_stock_scores(self):
        step_ids = _collect_downstream_steps("build_external_attention")

        self.assertIn("build_external_attention", step_ids)
        self.assertIn("calc_stock_scores", step_ids)

    def test_build_smart_plan_recalc_scores_for_event_driven_trend_refresh(self):
        conn = _make_plan_conn()

        plan = build_smart_plan(
            conn,
            audit=_make_audit(events_count=0),
            use_cache=False,
        )

        self.assertIn("gen_events", plan["steps"])
        self.assertIn("build_trends", plan["steps"])
        self.assertIn("calc_stock_scores", plan["steps"])

    def test_build_smart_plan_skips_turtle_standalone_refresh(self):
        """build_turtle_features 已迁出智能更新（手动触发），缺 turtle 数据也不重算."""
        conn = _make_plan_conn(with_turtle=False)

        plan = build_smart_plan(conn, audit=_make_audit(), use_cache=False)

        self.assertNotIn("build_trends", plan["steps"])
        self.assertNotIn("build_stage_features", plan["steps"])
        self.assertNotIn("build_forecast_features", plan["steps"])
        self.assertNotIn("build_turtle_features", plan["steps"])
        self.assertNotIn("calc_stock_scores", plan["steps"])
        self.assertEqual(
            plan["skip_reasons"].get("build_turtle_features"),
            "已迁出智能更新，请用工作台·选股扫描手动触发",
        )

    def test_build_smart_plan_skips_score_recalc_when_everything_is_fresh(self):
        conn = _make_plan_conn(with_turtle=True)

        plan = build_smart_plan(conn, audit=_make_audit(), use_cache=False)

        self.assertNotIn("build_turtle_features", plan["steps"])
        self.assertNotIn("calc_stock_scores", plan["steps"])
        self.assertEqual(plan["skip_reasons"].get("calc_stock_scores"), "上游未变更，无需重算")

    def test_build_smart_plan_skips_sync_financial_when_only_recent_history_gaps_remain(self):
        conn = _make_plan_conn()
        try:
            conn.execute("INSERT INTO mart_stock_trend (stock_code) VALUES ('000001')")
            conn.executemany(
                "INSERT INTO raw_gpcw_financial (stock_code, report_date) VALUES (?, ?)",
                [
                    ("000001", "2025-09-30"),
                    ("000001", "2025-12-31"),
                ],
            )
            conn.executemany(
                "INSERT INTO fact_financial_indicator_ak (stock_code, report_date) VALUES (?, ?)",
                [
                    ("000001", "2024-03-31"),
                    ("000001", "2024-06-30"),
                    ("000001", "2024-09-30"),
                    ("000001", "2024-12-31"),
                    ("000001", "2025-03-31"),
                    ("000001", "2025-06-30"),
                    ("000001", "2025-09-30"),
                    ("000001", "2025-12-31"),
                ],
            )
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO financial_sync_state (
                    stock_code, history_rows, last_report_date, last_history_at,
                    history_status, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("000001", 2, "2025-12-31", now, "failed", "ok", now),
            )
            conn.commit()

            plan = build_smart_plan(conn, audit=_make_audit(), use_cache=False)

            self.assertNotIn("sync_financial", plan["steps"])
            self.assertEqual(
                plan["skip_reasons"].get("sync_financial"),
                "1 只研究股票财务历史缺口刚重试，等待冷却后继续",
            )
        finally:
            conn.close()

    def test_stock_scores_recalc_when_trends_rebuilt(self):
        self.assertTrue(_needs_stock_score_recalc(["build_trends"]))

    def test_stock_scores_do_not_recalc_when_only_turtle_features_rebuilt(self):
        """turtle 已迁出智能更新；单独重建 turtle 不再触发股票评分重算."""
        self.assertFalse(_needs_stock_score_recalc(["build_turtle_features"]))

    def test_stock_scores_skip_when_no_upstream_changes(self):
        self.assertFalse(_needs_stock_score_recalc(["calc_screening"]))


if __name__ == "__main__":
    unittest.main()