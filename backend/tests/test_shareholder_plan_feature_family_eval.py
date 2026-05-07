from __future__ import annotations

from conftest import duck_mem
from services.shareholder_plan_feature_family_eval import (
    EVAL_TABLE,
    build_shareholder_plan_feature_family_eval,
)


def _seed_panel_and_sources(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE fact_feature_panel (
            stock_code TEXT,
            date TEXT,
            follow_net_return_60d DOUBLE
        );
        INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-02', 0.10),
            ('000002', '2026-01-02', -0.03),
            ('000001', '2026-01-05', 0.12),
            ('000002', '2026-01-05', -0.02),
            ('000001', '2026-01-06', 0.08),
            ('000002', '2026-01-06', 0.01);

        CREATE TABLE fact_shareholder_plan_tdx_f10 (
            stock_code TEXT,
            source_available_date TEXT,
            direction TEXT,
            progress TEXT,
            target_amount_min DOUBLE,
            target_amount_max DOUBLE
        );
        INSERT INTO fact_shareholder_plan_tdx_f10 VALUES
            ('000001', '2026-01-05', '增持', '完成', 100.0, 200.0),
            ('000002', '2026-01-06', '减持', '进行中', 50.0, 80.0);

        CREATE TABLE mart_shareholder_plan_initial_event (
            stock_code TEXT,
            source_available_date TEXT,
            direction TEXT,
            target_amount_min DOUBLE,
            target_amount_max DOUBLE
        );
        INSERT INTO mart_shareholder_plan_initial_event VALUES
            ('000001', '2026-01-02', '增持', 100.0, 200.0),
            ('000002', '2026-01-06', '减持', 50.0, 80.0);
        """
    )


def test_shareholder_plan_feature_family_eval_compares_latest_and_initial_families() -> None:
    with duck_mem() as conn:
        _seed_panel_and_sources(conn)

        result = build_shareholder_plan_feature_family_eval(
            conn,
            run_id="shareholder_plan_family_unit",
            labels=["follow_net_return_60d"],
            min_daily_count=1,
        )

        assert result["status"] == "completed"
        assert result["panel_rows"] == 6
        assert result["inserted_rows"] == 13
        assert {item["source_family"] for item in result["family_evidence"]} == {
            "latest_state",
            "initial_event",
        }

        rows = conn.execute(
            f"""
            SELECT source_family, feature_name, label_name, total_rows,
                   event_rows, nondefault_rows, feature_purpose
              FROM {EVAL_TABLE}
             WHERE run_id = 'shareholder_plan_family_unit'
             ORDER BY source_family, feature_name
            """
        ).fetchall()
        by_key = {(row["source_family"], row["feature_name"]): row for row in rows}

        assert by_key[("latest_state", "shareholder_plan_completed_count_180d")]["event_rows"] == 1
        assert ("initial_event", "shareholder_plan_completed_count_180d") not in by_key
        assert by_key[("initial_event", "shareholder_plan_increase_count_180d")]["nondefault_rows"] == 3
        assert by_key[("latest_state", "shareholder_plan_increase_count_180d")]["nondefault_rows"] == 2
        assert by_key[("initial_event", "days_since_shareholder_plan_increase")][
            "feature_purpose"
        ] == "initial_notice_capital_attention_candidate"


def test_shareholder_plan_feature_family_eval_records_missing_source_family() -> None:
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                follow_net_return_60d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 0.10);
            """
        )

        result = build_shareholder_plan_feature_family_eval(
            conn,
            run_id="shareholder_plan_family_missing_unit",
            labels=["follow_net_return_60d"],
            min_daily_count=1,
        )

        assert result["inserted_rows"] == 0
        assert {item["status"] for item in result["family_evidence"]} == {"missing_source_table"}
