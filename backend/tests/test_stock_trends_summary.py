import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.industry as industry_service
import services.screening_read as screening_read
import services.stock_trends_read as stock_trends_read


def test_build_stock_trends_summary_matches_frontend_semantics():
    rows = [
        {
            "priority_pool": "A池",
            "stock_gate": "follow",
            "setup_tag": "A1",
            "setup_priority": 1,
            "setup_industry_name": "半导体",
            "display_inst_name": "机构甲",
            "_dual_confirm": True,
            "external_attention_signal": "关注抬升",
            "turtle_setup_state": "S1突破触发",
        },
        {
            "priority_pool": "B池",
            "stock_gate": "watch",
            "setup_industry_name": "半导体",
            "setup_inst_name": "机构乙",
            "external_attention_signal": "热度拥挤",
            "turtle_setup_state": "S2待突破",
        },
        {
            "priority_pool": "C池",
            "stock_gate": "observe",
            "industry_level1": "汽车",
            "external_attention_score": 0.42,
        },
    ]

    summary = stock_trends_read.build_stock_trends_summary(rows)

    assert summary["total"] == 3
    assert summary["abTotal"] == 2
    assert summary["followTotal"] == 1
    assert summary["dualConfirm"] == 1
    assert summary["setupTotal"] == 1
    assert summary["pools"] == {"A池": 1, "B池": 1, "C池": 1}
    assert summary["gates"] == {"follow": 1, "watch": 1, "observe": 1, "avoid": 0}
    assert summary["signals"] == {"A1": 1}
    assert summary["industries"]["半导体"] == 2
    assert summary["sources"] == {"机构甲": 1, "机构乙": 1}
    assert summary["attentionCovered"] == 3
    assert summary["attentionBoosted"] == 1
    assert summary["attentionCrowded"] == 1
    assert summary["attentionSignals"] == {"关注抬升": 1, "热度拥挤": 1}
    assert summary["turtleCovered"] == 2
    assert summary["turtleBreakout"] == 1
    assert summary["turtleWatch"] == 1
    assert summary["turtleExit"] == 0
    assert summary["topIndustries"][0] == {"key": "半导体", "count": 2}
    assert summary["topSignals"] == [{"key": "A1", "count": 1}]
    assert len(summary["topSources"]) == 2
    assert summary["topAttentionSignals"][0]["count"] == 1


def test_apply_stock_trend_gate_uses_backend_pool_rules():
    item = {
        "priority_pool": "B池",
        "composite_priority_score": 82.5,
        "priority_pool_reason": "综合评分 82.5，进入 B池",
    }

    stock_trends_read.apply_stock_trend_gate(item)

    assert item["stock_gate"] == "watch"
    assert "进入 B池" in (item["stock_gate_reason"] or "")
    assert "持续跟踪池" in (item["stock_gate_reason"] or "")


def test_build_stock_trends_payload_enriches_rows_and_blacklist_fallbacks():
    payload = stock_trends_read.build_stock_trends_payload(
        [
            {
                "stock_code": "600001",
                "stock_name": "样本A",
                "setup_inst_name": "机构甲",
                "priority_pool": "A池",
                "composite_priority_score": 86.0,
                "priority_pool_reason": "综合评分 86，进入 A池",
                "setup_tag": "A3",
                "setup_priority": 3,
                "setup_score_raw": 80.0,
                "discovery_score": 74.0,
                "forecast_20d_score": 63.0,
                "forecast_60d_excess_score": 58.0,
                "external_attention_signal": "关注抬升",
                "turtle_setup_state": "S1待突破",
            }
        ],
        blacklist_map={
            "600999": {"stock_code": "600999", "stock_name": "拉黑样本"}
        },
        coverage_map={
            "600001": {
                "holder_total": 4,
                "holder_follow_count": 2,
                "holder_watch_count": 1,
                "holder_observe_count": 1,
                "holder_avoid_count": 0,
            }
        },
        industry_map={
            "600001": {"sw_l1": "T10", "sw_l2": "T1001", "sw_l3": "T100101"},
            "600999": {"sw_l1": "T40", "sw_l2": "T4001", "sw_l3": "T400101"},
        },
        screening_map={"600001": {"formula": "f1", "hits": 2}},
        dual_confirm_map={"600001": {"dual_confirm_count": 2, "dual_confirm_latest_report_date": "2026-04-15"}},
    )

    rows = payload["data"]
    assert len(rows) == 2

    active = rows[0]
    assert active["stock_code"] == "600001"
    assert active["sw_l2"] == "T1001"
    assert active["holder_follow_count"] == 2
    assert active["forecast_cross_section_score"] == 63.0
    assert active["forecast_industry_relative_score"] == 58.0
    assert active["stock_gate"] == "follow"
    assert active["dual_confirm_count"] == 2
    assert active["dual_confirm_latest_report_date"] == "2026-04-15"
    assert active["_screen"] == {"formula": "f1", "hits": 2}

    blacklisted = rows[1]
    assert blacklisted["stock_code"] == "600999"
    assert blacklisted["stock_gate"] is None
    assert blacklisted["stock_gate_reason"] == "已拉黑"
    assert blacklisted["sw_l2"] == "T4001"

    summary = payload["summary"]
    assert summary["total"] == 2
    assert summary["abTotal"] == 1
    assert summary["attentionBoosted"] == 1


def test_load_stock_trends_payload_queries_and_assembles(monkeypatch):
    class _DummyCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _DummyConn:
        def execute(self, sql, params=()):
            if "FROM excluded_stocks e" in sql:
                return _DummyCursor([
                    {"stock_code": "600999", "stock_name": "拉黑样本", "reason": "manual", "created_at": "2026-04-16"}
                ])
            if "FROM mart_stock_trend t" in sql:
                return _DummyCursor([
                    {
                        "stock_code": "600001",
                        "stock_name": "样本A",
                        "setup_inst_name": "机构甲",
                        "priority_pool": "A池",
                        "composite_priority_score": 86.0,
                        "priority_pool_reason": "综合评分 86，进入 A池",
                        "setup_tag": "A3",
                        "setup_priority": 3,
                        "setup_score_raw": 80.0,
                        "discovery_score": 74.0,
                        "forecast_20d_score": 63.0,
                        "forecast_60d_excess_score": 58.0,
                        "external_attention_signal": "关注抬升",
                        "turtle_setup_state": "S1待突破",
                        "display_inst_name": "机构甲",
                    }
                ])
            if "FROM mart_current_relationship" in sql:
                return _DummyCursor([
                    {
                        "stock_code": "600001",
                        "holder_total": 4,
                        "holder_follow_count": 2,
                        "holder_watch_count": 1,
                        "holder_observe_count": 1,
                        "holder_avoid_count": 0,
                    }
                ])
            raise AssertionError(sql)

    monkeypatch.setattr(industry_service, "load_industry_map", lambda conn: {
        "600001": {"sw_l1": "T10", "sw_l2": "T1001", "sw_l3": "T100101"},
        "600999": {"sw_l1": "T40", "sw_l2": "T4001", "sw_l3": "T400101"},
    })
    monkeypatch.setattr(screening_read, "load_screening_snapshot_map", lambda conn: {"600001": {"formula": "f1", "hits": 2}})
    monkeypatch.setattr(screening_read, "load_dual_confirm_snapshot_map", lambda conn: {"600001": {"dual_confirm_count": 2, "dual_confirm_latest_report_date": "2026-04-15"}})

    payload = stock_trends_read.load_stock_trends_payload(_DummyConn())

    rows = payload["data"]
    assert len(rows) == 2
    assert rows[0]["stock_code"] == "600001"
    assert rows[0]["sw_l2"] == "T1001"
    assert rows[0]["holder_follow_count"] == 2
    assert rows[0]["forecast_cross_section_score"] == 63.0
    assert rows[0]["stock_gate"] == "follow"
    assert rows[1]["stock_code"] == "600999"
    assert rows[1]["stock_gate_reason"] == "已拉黑"
    assert payload["summary"]["total"] == 2