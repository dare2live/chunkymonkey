"""CX-2 state sensors — ST/holder/delist detection + process_plan force reasons."""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_membership_diff_detects_enter_exit_and_attr():
    from services.pipeline.state_sensors import membership_diff

    prev = [
        ("000001.SZ", "*ST平安", "ST", "风险警示板"),
        ("000002.SZ", "万科A", "ST", "风险警示板"),
    ]
    curr = [
        ("000002.SZ", "ST万科", "ST", "风险警示板"),  # attr change name
        ("000003.SZ", "ST新进", "ST", "风险警示板"),
    ]
    out = membership_diff(prev, curr)
    assert out["changed"] is True
    assert out["entered_n"] == 1
    assert out["exited_n"] == 1
    assert out["attr_changed_n"] == 1
    assert out["tier0_write"] is False
    assert out["entered_sample"][0]["ts_code"] == "000003.SZ"
    assert out["exited_sample"][0]["ts_code"] == "000001.SZ"


def test_membership_diff_unchanged():
    from services.pipeline.state_sensors import membership_diff

    rows = [("000001.SZ", "ST甲", "ST", "风险警示板")]
    out = membership_diff(rows, list(rows))
    assert out["changed"] is False
    assert out["entered_n"] == 0
    assert out["exited_n"] == 0


def test_detect_stock_st_from_accepted_partitions(tmp_path):
    from datetime import date

    from services.pipeline.state_sensors import detect_stock_st_state_changes

    conn = duckdb.connect(str(tmp_path / "st.duckdb"))
    conn.execute(
        """
        CREATE TABLE accepted_partition (
            dataset_id VARCHAR, partition_value VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE canonical_stock_st_daily (
            trade_date DATE, ts_code VARCHAR, name VARCHAR,
            type VARCHAR, type_name VARCHAR, st_origin VARCHAR NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO accepted_partition VALUES (?, ?), (?, ?)",
        [
            "tier0.security_identity.stock_st_daily",
            "20260720",
            "tier0.security_identity.stock_st_daily",
            "20260721",
        ],
    )
    conn.execute(
        """
        INSERT INTO canonical_stock_st_daily VALUES
          (?, '002656.SZ', '*ST摩登', 'ST', '风险警示板', 'derived_name_prefix'),
          (?, '000001.SZ', 'ST甲', 'ST', '风险警示板', 'derived_name_prefix'),
          (?, '000001.SZ', 'ST甲', 'ST', '风险警示板', 'derived_name_prefix')
        """,
        [date(2026, 7, 20), date(2026, 7, 20), date(2026, 7, 21)],
    )
    out = detect_stock_st_state_changes(conn)
    assert out["status"] == "ok"
    assert out["changed"] is True
    assert out["exited_n"] == 1
    assert out["as_of"] == "20260721"
    assert out["baseline"] == "20260720"
    assert out["tier0_write"] is False
    assert out["coverage_exchanges_compared"] == ["BJ", "SH", "SZ"]
    conn.close()


def test_detect_stock_st_coverage_intersection_excludes_bj_only_code(tmp_path):
    """两分区 origins 分别 derived_name_prefix (前一分区, 覆盖沪深京) /
    provider_baostock_isst (当前分区, 只覆盖沪深) —— 一只 .BJ 只在前者出现时,
    不得被判成"退出"(它压根不在两分区共同覆盖范围内), coverage_exchanges_compared
    必须收窄成 ["SH", "SZ"]。"""
    from datetime import date

    from services.pipeline.state_sensors import detect_stock_st_state_changes

    conn = duckdb.connect(str(tmp_path / "st_coverage.duckdb"))
    conn.execute(
        "CREATE TABLE accepted_partition (dataset_id VARCHAR, partition_value VARCHAR)"
    )
    conn.execute(
        """
        CREATE TABLE canonical_stock_st_daily (
            trade_date DATE, ts_code VARCHAR, name VARCHAR,
            type VARCHAR, type_name VARCHAR, st_origin VARCHAR NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO accepted_partition VALUES (?, ?), (?, ?)",
        [
            "tier0.security_identity.stock_st_daily",
            "20260916",
            "tier0.security_identity.stock_st_daily",
            "20260917",
        ],
    )
    conn.execute(
        """
        INSERT INTO canonical_stock_st_daily VALUES
          (?, '000001.SZ', 'ST甲', 'ST', '风险警示板', 'derived_name_prefix'),
          (?, '830001.BJ', 'ST北交', 'ST', '风险警示板', 'derived_name_prefix'),
          (?, '000001.SZ', NULL, 'ST', '风险警示板', 'provider_baostock_isst')
        """,
        [date(2026, 9, 16), date(2026, 9, 16), date(2026, 9, 17)],
    )
    out = detect_stock_st_state_changes(conn)
    assert out["status"] == "ok"
    assert out["coverage_prev"] == ["BJ", "SH", "SZ"]
    assert out["coverage_curr"] == ["SH", "SZ"]
    assert out["coverage_exchanges_compared"] == ["SH", "SZ"]
    # .BJ 只在前一分区出现, 但不在两分区共同覆盖范围内 -> 不进 entered/exited
    assert out["exited_n"] == 0
    assert out["entered_n"] == 0
    assert not any(item["ts_code"] == "830001.BJ" for item in out["exited_sample"])
    # name 一边 None (baostock 路径) 一边有值 (名称路径) -> 不计 attr 变化
    assert out["attr_changed_n"] == 0
    assert out["changed"] is False
    conn.close()


def test_holders_ratio_diff_same_rank():
    from services.pipeline.state_sensors import holders_ratio_diff

    # same rank, ratio changed
    prev = [("600000", "float", 1, 1, 10.0, "基金A")]
    curr = [("600000", "float", 1, 1, 12.5, "基金A")]
    out = holders_ratio_diff(curr, prev)
    assert out["changed"] is True
    assert out["ratio_changed_n"] == 1
    assert out["sample"][0]["prev_ratio"] == 10.0
    assert out["sample"][0]["curr_ratio"] == 12.5


def test_detect_holders_ratio_state_changes(tmp_path):
    from services.pipeline.state_sensors import detect_holders_state_changes

    conn = duckdb.connect(str(tmp_path / "h.duckdb"))
    conn.execute(
        """
        CREATE TABLE accepted_partition (
            dataset_id VARCHAR, partition_value VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, holder_set VARCHAR,
            holder_rank INTEGER, row_seq INTEGER, holder_name VARCHAR,
            hold_ratio_float DOUBLE, notice_date VARCHAR, is_exit_row BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO accepted_partition VALUES
          ('tier0.disclosure.top10_float_holders_period', '20260723')
        """
    )
    conn.execute(
        """
        INSERT INTO canonical_top10_float_holders_period VALUES
          ('600000','20260331','float',1,1,'基金A',10.0,'20260430', FALSE),
          ('600000','20260630','float',1,1,'基金A',11.0,'20260723', FALSE),
          ('600001','20260630','float',1,1,'基金B',5.0,'20260723', FALSE),
          ('600346','20260630','float',1,1,'退出户',0.5,'20260723', TRUE)
        """
    )
    out = detect_holders_state_changes(conn)
    assert out["status"] == "ok"
    assert out["as_of"] == "20260723"
    assert out["changed"] is True
    assert out["ratio_changed_n"] == 1
    assert out["exit_n"] == 1
    assert out["detection"] == "canonical_holders_notice_delta"
    assert out["tier0_write"] is False
    assert out["sample"]["exit"][0]["stock_code"] == "600346"
    conn.close()


def test_holders_notice_diff_rank_and_exit():
    from services.pipeline.state_sensors import holders_notice_diff

    prev = [("600000", "float", 1, 1, 10.0, "基金A")]
    curr = [("600000", "float", 2, 1, 10.0, "基金A")]  # rank remap, ratio same
    exits = [("600001", "float", 1, 0.5, "退出户")]
    out = holders_notice_diff(curr_active=curr, prev_active=prev, exit_rows=exits)
    assert out["changed"] is True
    assert out["ratio_changed_n"] == 0
    assert out["rank_changed_n"] == 1
    assert out["exit_n"] == 1
    assert out["sample"]["rank"][0]["prev_rank"] == 1
    assert out["sample"]["rank"][0]["curr_rank"] == 2


def test_holders_without_accepted_partition_fail_closed(tmp_path):
    from services.pipeline.state_sensors import detect_holders_state_changes

    conn = duckdb.connect(str(tmp_path / "h2.duckdb"))
    conn.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, holder_set VARCHAR,
            holder_rank INTEGER, row_seq INTEGER, holder_name VARCHAR,
            hold_ratio_float DOUBLE, notice_date VARCHAR, is_exit_row BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO canonical_top10_float_holders_period VALUES
          ('600000','20260630','float',1,1,'基金A',11.0,'20260723', FALSE)
        """
    )
    # No accepted_partition table → fail closed (no MAX(notice_date) invent).
    out = detect_holders_state_changes(conn)
    assert out["changed"] is False
    assert out["status"] in {"skipped_no_accepted", "unavailable"}
    assert out["tier0_write"] is False
    conn.close()


def test_delist_diff_and_as_of_roundtrip(tmp_path):
    from services.pipeline.state_sensors import (
        delist_diff,
        detect_delist_state_changes,
        read_dim_active_as_of,
    )

    out = delist_diff({"000001", "000002"}, {"000001", "000003"})
    assert out["changed"] is True
    assert out["removed_n"] == 1
    assert out["added_n"] == 1
    assert "000002" in out["removed_sample"]

    path = tmp_path / "dim_as_of.json"
    first = detect_delist_state_changes(
        before_codes=None,
        after_codes={"000001", "000002"},
        as_of_path=path,
        persist_after=True,
    )
    assert first["status"] == "skipped_no_baseline"
    assert read_dim_active_as_of(path)["code_count"] == 2

    second = detect_delist_state_changes(
        before_codes=None,
        after_codes={"000001"},
        as_of_path=path,
        persist_after=True,
    )
    assert second["changed"] is True
    assert second["removed_n"] == 1


def test_plan_process_steps_cites_state_changes_and_keeps_pulse():
    from services.pipeline.delta_manifest import decide_dc_action, plan_process_steps

    dc = decide_dc_action(
        current_frontier="20260721",
        previous_frontier="20260721",
        advanced_partitions=[],
    )
    state_changes = {
        "stock_st": {"changed": True, "entered_n": 1, "exited_n": 0},
        "holders": {"changed": False},
        "delist": {"changed": False},
    }
    plan = plan_process_steps(dc_decision=dc, state_changes=state_changes)
    assert plan["dc_industry_view"]["action"] == "skip"
    assert plan["market_pulse"]["action"] == "run"
    assert plan["market_pulse"]["reason"] == "late_window_mandatory"
    assert plan["segments"]["action"] == "run"
    assert "state_change:stock_st" in plan["segments"]["reason"]
    assert plan["any_state_changed"] is True
    assert "state_change:stock_st" in plan["state_change_force"]
    assert "holders_consumers" not in plan


def test_plan_holders_force_does_not_break_pulse_or_invent_step():
    from services.pipeline.delta_manifest import plan_process_steps

    plan = plan_process_steps(
        dc_decision={"action": "skip", "reason": "dc_frontier_unchanged"},
        state_changes={
            "stock_st": {"changed": False},
            "holders": {
                "changed": True,
                "ratio_changed_n": 3,
                "exit_n": 2,
                "rank_changed_n": 1,
            },
            "delist": {"changed": False},
        },
    )
    assert "holders_consumers" not in plan
    assert "state_change:holders" in plan["state_change_force"]
    assert plan["market_pulse"]["reason"] == "late_window_mandatory"
    # holders alone do not rewrite segment reason (serve reads canonical)
    assert plan["segments"]["reason"] == "build_latest_idempotent"


def test_sensors_never_claim_tier0_write():
    from services.pipeline.state_sensors import (
        delist_diff,
        empty_state_changes,
        holders_ratio_diff,
        membership_diff,
    )

    for block in (
        membership_diff([], []),
        holders_ratio_diff([], []),
        delist_diff(set(), set()),
        empty_state_changes()["stock_st"],
    ):
        assert block.get("tier0_write") is False
