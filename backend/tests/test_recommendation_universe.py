from services.recommendation_universe import (
    RecommendationUniversePolicy,
    explain_universe_exclusions,
    filter_investable_records,
    load_recommendation_universe_policy,
)
from conftest import duck_mem


def test_load_recommendation_universe_policy_excludes_risk_warning_names():
    policy = load_recommendation_universe_policy()

    assert policy.policy_id == "production_a_share_investable_v1"
    assert policy.require_stock_name is True
    assert any("ST" in pattern for pattern in policy.exclude_name_regex)


def test_explain_universe_exclusions_uses_configured_rules_and_explicit_table():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE dim_active_a_stock (
                stock_code TEXT PRIMARY KEY,
                stock_name TEXT
            );
            CREATE TABLE excluded_stocks (
                stock_code TEXT,
                category TEXT,
                reason TEXT
            );
            INSERT INTO dim_active_a_stock VALUES
                ('000001', '平安银行'),
                ('000002', '*ST测试'),
                ('000003', '退市测试'),
                ('000004', NULL),
                ('000005', '显式排除');
            INSERT INTO excluded_stocks VALUES
                ('000005', 'manual', 'policy');
            """
        )
        policy = RecommendationUniversePolicy(
            policy_id="test",
            require_stock_name=True,
            respect_excluded_stocks_table=True,
            exclude_name_regex=(r"(^|\s|\*)ST", "退"),
        )

        exclusions = explain_universe_exclusions(
            conn,
            ["000001", "000002", "000003", "000004", "000005"],
            policy=policy,
        )

        assert "000001" not in exclusions
        assert exclusions["000002"].startswith("name_regex:")
        assert exclusions["000003"] == "name_regex:退"
        assert exclusions["000004"] == "missing_stock_name"
        assert exclusions["000005"].startswith("excluded_stocks:manual")


def test_filter_investable_records_returns_summary():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE dim_active_a_stock (
                stock_code TEXT PRIMARY KEY,
                stock_name TEXT
            );
            INSERT INTO dim_active_a_stock VALUES
                ('000001', '平安银行'),
                ('000002', 'ST测试');
            """
        )
        policy = RecommendationUniversePolicy(
            policy_id="test",
            exclude_name_regex=(r"(^|\s|\*)ST",),
        )

        filtered, summary = filter_investable_records(
            conn,
            [{"stock_code": "000001"}, {"stock_code": "000002"}],
            policy=policy,
        )

        assert filtered == [{"stock_code": "000001"}]
        assert summary["input_rows"] == 2
        assert summary["kept_rows"] == 1
        assert summary["excluded_rows"] == 1
