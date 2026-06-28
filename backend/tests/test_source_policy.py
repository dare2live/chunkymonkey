from services.source_policy import (
    get_capability_policy,
    load_source_policies,
)


def test_default_kline_policy_is_tushare_single_source():
    # 2026-06-28: tdxhub/akshare 退役, K线唯一源 = tushare (canonical = 前复权视图)
    policy = get_capability_policy("kline_daily")

    assert policy.primary == "tushare"
    assert policy.fallback == ()
    assert policy.canonical_relation == "market.v_price_kline_qfq"
    assert policy.allow_fallback_for_latest_gap is False
    assert policy.require_fallback_lineage is False


def test_source_policy_yaml_subset_loader(tmp_path):
    # 测 YAML subset 解析机制 (primary/fallback/标量), 用 tushare 单源 fixture
    cfg = tmp_path / "data_sources.yaml"
    cfg.write_text(
        """
version: 1
capabilities:
  kline_daily:
    primary: tushare
    fallback:
    canonical_relation: market.v_price_kline_qfq
    allow_fallback_for_latest_gap: false
    require_fallback_lineage: false
    max_primary_lag_trading_days: 2
""",
        encoding="utf-8",
    )

    policy = load_source_policies(cfg)["kline_daily"]

    assert policy.primary == "tushare"
    assert policy.fallback == ()
    assert policy.max_primary_lag_trading_days == 2
