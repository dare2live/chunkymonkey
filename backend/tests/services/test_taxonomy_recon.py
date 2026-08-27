"""Four-chain taxonomy recon: set-diff, banned residuals, names ≠ identity."""
from __future__ import annotations

import pytest

from conftest import duck_mem
from services.data_sources.taxonomy_recon import (
    BANNED_DC_MEMBERSHIP,
    BANNED_SW_MEMBERSHIP,
    BANNED_THS_INTERVAL,
    DC_MEMBER_PUBLICATION,
    SW_PIT_PUBLICATION,
    compare_named_memberships,
    fuyao_catalog_rows,
    fuyao_constituent_codes,
    load_dc_industry_l1_memberships,
    load_sw_l1_memberships,
    member_set_diff,
    miaoxiang_dc_universe_status,
    publication_vs_landing_pairs,
    reject_banned_baseline,
    reject_tdx_block,
    reject_ths_interval_row,
    select_ths_sample,
)
from services.taxonomy_config import FOUR_CHAIN_NAMESPACES, load_taxonomy_config


def test_four_chain_yaml_keeps_ths_observation_and_bans_tdx_block():
    cfg = load_taxonomy_config()
    assert set(cfg["namespaces"]) == FOUR_CHAIN_NAMESPACES
    assert cfg["cross_namespace_fallback"] == "forbidden"
    assert cfg["namespaces"]["ths_industry"]["membership"] == "observation_snapshot"
    assert cfg["namespaces"]["ths_industry"]["pit_interval"] == "forbidden"
    assert cfg["namespaces"]["ths_concept"]["kind"] == "multi_label"
    assert "tdx_block" not in cfg["namespaces"]


def test_banned_membership_baselines_and_tdx_block_are_rejected():
    with pytest.raises(ValueError, match="raw_tushare_dc_member"):
        reject_banned_baseline(
            "raw_tushare_dc_member",
            banned=BANNED_DC_MEMBERSHIP,
            accepted=DC_MEMBER_PUBLICATION,
        )
    with pytest.raises(ValueError, match="v_dc_industry_pit"):
        reject_banned_baseline(
            "v_dc_industry_pit",
            banned=BANNED_DC_MEMBERSHIP,
            accepted=DC_MEMBER_PUBLICATION,
        )
    with pytest.raises(ValueError, match="raw_tushare_index_member_all"):
        reject_banned_baseline(
            "raw_tushare_index_member_all",
            banned=BANNED_SW_MEMBERSHIP,
            accepted=SW_PIT_PUBLICATION,
        )
    with pytest.raises(ValueError, match="raw_tushare_ths_member"):
        reject_banned_baseline(
            "raw_tushare_ths_member",
            banned=BANNED_THS_INTERVAL,
            accepted="fuyao observation snapshot",
        )
    with pytest.raises(ValueError, match="not one of the four"):
        reject_tdx_block("tdx_block")


def test_empty_recon_is_not_a_match():
    report = member_set_diff([], [])
    assert report["status"] == "empty_recon"
    assert report["jaccard"] is None
    assert report["identity"] is False
    assert report["intersection"] == 0


def test_name_collision_with_identical_sets_is_still_not_identity():
    left = {"银行": ["000001.SZ", "600000.SH"]}
    right = {"银行": ["000001.SZ", "600000.SH"]}
    report = compare_named_memberships(
        left, right, left_ns="dc_industry", right_ns="sw_industry"
    )
    assert report["colliding_names"] == 1
    assert report["identical_member_sets"] == 1
    assert report["divergent_member_sets"] == 0
    assert report["per_name"][0]["jaccard"] == 1.0
    assert report["per_name"][0]["identity"] is False
    assert report["per_name"][0]["relation"] == "name_collision_candidate"
    with pytest.raises(ValueError, match="distinct namespaces"):
        compare_named_memberships(left, right, left_ns="dc_industry", right_ns="dc_industry")


def test_name_collision_divergent_sets():
    report = compare_named_memberships(
        {"综合": ["000001.SZ", "000002.SZ"]},
        {"综合": ["000001.SZ", "600519.SH"]},
        left_ns="dc_industry",
        right_ns="sw_industry",
    )
    body = report["per_name"][0]
    assert body["intersection"] == 1
    assert body["only_left"] == 1
    assert body["only_right"] == 1
    assert body["only_left_sample"] == ["000002.SZ"]
    assert body["identity"] is False


def test_load_dc_l1_rejects_raw_member_and_reads_publication():
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE fact_dc_member_daily (
            trade_date VARCHAR, ts_code VARCHAR, con_code VARCHAR, name VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw_tushare_dc_index (
            trade_date VARCHAR, ts_code VARCHAR, name VARCHAR,
            idx_type VARCHAR, level VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO fact_dc_member_daily VALUES "
        "('20260825', 'BK1283.DC', '000001.SZ', '平安银行'), "
        "('20260825', 'BK1283.DC', '600000.SH', '浦发银行')"
    )
    con.execute(
        "INSERT INTO raw_tushare_dc_index VALUES "
        "('20260825', 'BK1283.DC', '银行', '行业板块', '东财一级行业')"
    )
    got = load_dc_industry_l1_memberships(con, "20260825")
    assert got == {"银行": {"000001.SZ", "600000.SH"}}
    with pytest.raises(ValueError, match="banned baseline"):
        load_dc_industry_l1_memberships(
            con, "20260825", member_table="raw_tushare_dc_member"
        )


def test_load_sw_pit_rejects_raw_index_member_all():
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE v_sw_industry_pit (
            ts_code VARCHAR, l1_name VARCHAR, in_date VARCHAR, out_date VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO v_sw_industry_pit VALUES "
        "('000001.SZ', '银行', '20140101', NULL), "
        "('000002.SZ', '银行', '20140101', '20200101')"
    )
    got = load_sw_l1_memberships(con, "20260825")
    assert got == {"银行": {"000001.SZ"}}
    with pytest.raises(ValueError, match="banned baseline"):
        load_sw_l1_memberships(con, "20260825", table="raw_tushare_index_member_all")


def test_publication_vs_landing_empty_and_mismatch():
    empty = publication_vs_landing_pairs([], [])
    assert empty["status"] == "empty_recon"
    body = publication_vs_landing_pairs(
        [("BK1.DC", "000001.SZ")],
        [("BK1.DC", "000001.SZ"), ("BK1.DC", "000002.SZ")],
    )
    assert body["only_landing"] == 1
    assert body["only_publication"] == 0
    assert "residual" in body["note"]


def test_ths_current_constituents_reject_invented_interval():
    rows = fuyao_catalog_rows(
        [{"thscode": "886001.TI", "name": "银行"}, {"thscode": "x", "name": ""}]
    )
    assert rows == [{"thscode": "886001.TI", "name": "银行"}]
    codes = fuyao_constituent_codes(
        [{"thscode": "000001.SZ", "name": "平安银行"}]
    )
    assert codes == ["000001.SZ"]
    with pytest.raises(ValueError, match="observation snapshot"):
        reject_ths_interval_row({"thscode": "000001.SZ", "in_date": "20200101"})
    sample = select_ths_sample(
        [{"thscode": "886001.TI", "name": "银行"}, {"thscode": "886002.TI", "name": "传媒"}],
        ["银行"],
        limit=1,
    )
    assert sample == [{"thscode": "886001.TI", "name": "银行"}]
    assert miaoxiang_dc_universe_status()["status"] == "blocked_no_universe_dump"
