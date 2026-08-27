from services.taxonomy_config import (
    FOUR_CHAIN_NAMESPACES,
    current_snapshot_quality_floor,
    load_taxonomy_config,
    source_content_type,
    source_index_type,
    source_level_map,
)


def test_taxonomy_namespaces_are_distinct_and_cross_fallback_is_forbidden():
    cfg = load_taxonomy_config()
    assert cfg["version"] == 2
    assert cfg["cross_namespace_fallback"] == "forbidden"
    assert set(cfg["namespaces"]) == FOUR_CHAIN_NAMESPACES
    assert cfg["namespaces"]["ths_industry"]["pit_interval"] == "forbidden"
    assert cfg["namespaces"]["ths_concept"]["membership"] == "observation_snapshot"
    assert cfg["namespaces"]["dc_concept"]["kind"] == "multi_label"
    assert source_content_type("dc_concept") == "概念"
    assert source_content_type("dc_industry") == "行业"
    assert source_content_type("dc_concept") != source_content_type("dc_industry")
    assert source_index_type("dc_industry") == "行业板块"
    assert source_index_type("dc_concept") == "概念板块"
    assert cfg["namespaces"]["dc_industry"]["canonical"] is False


def test_dc_source_level_map_uses_real_vendor_values():
    assert source_level_map("dc_industry") == {
        "东财一级行业": "L1",
        "东财二级行业": "L2",
        "东财三级行业": "L3",
    }


def test_dc_snapshot_quality_floors_are_config_owned_and_cover_both_namespaces():
    industry = current_snapshot_quality_floor("dc_industry")
    concept = current_snapshot_quality_floor("dc_concept")

    assert industry["measured_trade_date"] == "20260716"
    assert set(industry["min_nodes_by_level"]) == {"L1", "L2", "L3"}
    assert concept["min_memberships"] > concept["min_nodes"] > 0
