from services.source_policy import (
    get_capability_policy,
    load_source_policies,
    normalize_kline_write_source,
)


def test_default_kline_policy_prefers_tdxhub():
    policy = get_capability_policy("kline_daily")

    assert policy.primary == "tdxhub"
    assert policy.fallback == ("akshare_multi_source",)
    assert policy.canonical_relation == "market.v_price_kline_qfq"
    assert policy.allow_fallback_for_latest_gap is True
    assert policy.require_fallback_lineage is True


def test_source_policy_yaml_subset_loader(tmp_path):
    cfg = tmp_path / "data_sources.yaml"
    cfg.write_text(
        """
version: 1
capabilities:
  kline_daily:
    primary: tdxhub
    fallback:
      - akshare_multi_source
      - local_cache
    canonical_relation: market.v_price_kline_qfq
    allow_fallback_for_latest_gap: true
    require_fallback_lineage: true
    max_primary_lag_trading_days: 2
""",
        encoding="utf-8",
    )

    policy = load_source_policies(cfg)["kline_daily"]

    assert policy.primary == "tdxhub"
    assert policy.fallback == ("akshare_multi_source", "local_cache")
    assert policy.max_primary_lag_trading_days == 2


def test_normalize_kline_write_source_preserves_tdxhub_primary():
    assert normalize_kline_write_source("tdxhub") == "tdxhub"
    assert normalize_kline_write_source("tdxhub_1.2.3.4:7709") == "tdxhub_1.2.3.4:7709"
    assert normalize_kline_write_source("eastmoney") == "akshare_eastmoney"
    assert normalize_kline_write_source("akshare_sina") == "akshare_sina"
    assert normalize_kline_write_source("") == "akshare_unknown"
