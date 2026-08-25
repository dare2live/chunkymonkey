"""institution_profile 读侧 API 单测 (合成 feature_store, mock _ro_conn)。"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import institution_profile as inst_router
from services import institution_profile as ip


@pytest.fixture
def mem(monkeypatch):
    c = duck_mem()
    c.executescript("""
        CREATE TABLE mart_inst_profile (holder TEXT, holder_type TEXT, n_closed INT,
            median_alpha DOUBLE, avg_alpha DOUBLE, win_rate_alpha DOUBLE, median_ret DOUBLE,
            avg_hold_days DOUBLE, low_sample BOOLEAN, n_episodes INT, n_holding INT,
            is_passive_holder BOOLEAN, metrics_status TEXT);
        CREATE TABLE mart_inst_profile_dim (holder TEXT, dim_type TEXT, dim_value TEXT,
            n_closed INT, median_alpha DOUBLE, win_rate_alpha DOUBLE, low_sample BOOLEAN);
        CREATE TABLE fact_inst_episode (holder TEXT, stock TEXT, open_date TEXT,
            open_notice TEXT, close_date TEXT, status TEXT, ret_c1 DOUBLE, alpha_c1 DOUBLE,
            n_adds INT, n_trims INT, sw_l1_at_open TEXT, seeded BOOLEAN, is_passive BOOLEAN,
            holder_type TEXT);
    """)
    c.execute(
        "INSERT INTO mart_inst_profile VALUES "
        "('牛散A','个人',50,0.15,0.2,0.7,0.12,200,false,51,1,false,'ranked')"
    )
    c.execute(
        "INSERT INTO mart_inst_profile VALUES "
        "('小散B','个人',3,0.5,0.5,1.0,0.5,100,true,3,0,false,'low_sample')"
    )
    c.execute("INSERT INTO mart_inst_profile_dim VALUES ('牛散A','industry_pit','煤炭',5,0.6,1.0,true)")
    c.execute("INSERT INTO fact_inst_episode VALUES "
              "('牛散A','600000','20990101','20990102',NULL,'holding',NULL,NULL,0,0,'银行',false,false,'个人')")
    monkeypatch.setattr(ip, "_ro_conn", lambda: c)
    # _ro_conn 每函数会 close — 内存库 close 后 fixture 失效, 用 no-op close 包装
    c.close_real = c.close
    c.close = lambda: None
    yield c
    c.close_real()


def test_research_envelope_labels_disclosure_nonconforming(mem, monkeypatch):
    """E0: unavailable shadow → NONCONFORMING + cutover false + read policy."""
    from services.data_sources.disclosure_shadow_compare import empty_disclosure_shadow

    monkeypatch.setattr(
        inst_router,
        "_disclosure_shadow_sidecar",
        lambda: empty_disclosure_shadow(reason="unit_test_fixture").as_dict(),
    )
    body = inst_router._research_envelope(profiles=[{"holder": "牛散A"}])
    assert body["status"] == "ok"
    assert body["surface_status"] == inst_router.SURFACE_STATUS
    assert body["profiles"] == [{"holder": "牛散A"}]
    assert body["cutover_allowed"] is False
    conf = body["disclosure_conformity"]
    assert conf["overall_status"] == "NONCONFORMING"
    assert conf["cutover_allowed"] is False
    assert conf["e0_phase"] == "in_progress"
    assert {d["domain"] for d in conf["domains"]} == {
        "holders_top10",
        "org_holding",
        "stk_holdertrade",
    }
    shadow = body["disclosure_shadow"]
    assert shadow["cutover_allowed"] is False
    assert shadow["overall_status"] == "UNAVAILABLE"
    policy = body["disclosure_read_policy"]
    assert policy["cutover_allowed"] is False
    assert policy["feature_store_profiles_status"] == "ACCEPTED"
    assert {d["domain"] for d in shadow["domains"]} == {
        "holders_top10",
        "org_holding",
        "stk_holdertrade",
    }


def test_research_envelope_allows_cutover_on_three_domain_match(mem, monkeypatch):
    """Three-domain MATCH → cutover_allowed; profiles ACCEPTED after E0-HIST."""
    from services.data_sources.disclosure_shadow_compare import (
        DisclosureDomainShadowReport,
        DisclosureShadowCompareReport,
    )

    def _d(name: str, partition: str) -> DisclosureDomainShadowReport:
        return DisclosureDomainShadowReport(
            domain=name,
            partition=partition,
            status="MATCH",
            legacy_row_count=2,
            canonical_row_count=2,
            compared_fields=("stock_code", "hold_ratio_float"),
            rows_match=True,
            mismatch_count=0,
            sample_mismatches=(),
            issues=("disclosure_shadow_compare_only",),
        )

    matched = DisclosureShadowCompareReport(
        overall_status="MATCH",
        cutover_allowed=True,
        domains=(
            _d("holders_top10", "20260717"),
            _d("org_holding", "20190430"),
            _d("stk_holdertrade", "20260706"),
        ),
        notes=("disclosure_shadow_compare_sidecar", "cutover_allowed_true"),
    )
    monkeypatch.setattr(
        inst_router, "_disclosure_shadow_sidecar", lambda: matched.as_dict()
    )
    body = inst_router._research_envelope(profiles=[{"holder": "牛散A", "n_closed": 50}])
    assert body["profiles"] == [{"holder": "牛散A", "n_closed": 50}]
    assert body["cutover_allowed"] is True
    assert body["disclosure_shadow"]["overall_status"] == "MATCH"
    assert body["disclosure_read_policy"]["cutover_allowed"] is True
    assert body["disclosure_conformity"]["overall_status"] == "ACCEPTED"
    assert body["disclosure_conformity"]["e0_phase"] == "gate_closed_canary"
    by_domain = {
        d["domain"]: d for d in body["disclosure_read_policy"]["domains"]
    }
    assert by_domain["holders_top10"]["source"] == "canonical"
    assert by_domain["holders_top10"]["conformity"] == "ACCEPTED"


def test_list_profiles_filters_low_sample(mem):
    out = ip.list_profiles(min_episodes=10)
    assert [p["holder"] for p in out] == ["牛散A"]  # 小散B n=3 被 min_episodes 滤掉


def test_list_profiles_order_by_whitelist(mem):
    with pytest.raises(ValueError, match="order_by"):
        ip.list_profiles(order_by="1; DROP TABLE x")  # SQL 注入防线


def test_get_profile_full_contract(mem):
    p = ip.get_profile("牛散A")
    assert p["n_closed"] == 50 and len(p["dims"]) == 1 and len(p["episodes"]) == 1
    assert p["research_identity"]["trend_layers"]["national_team_stabilizer"] is False
    assert ip.get_profile("不存在") is None


def test_recent_signals_star_holder_only(mem):
    sigs = ip.recent_signals(days=36500, min_holder_episodes=10)  # 20990101 远未来, 窗口放大覆盖
    assert len(sigs) == 1 and sigs[0]["holder"] == "牛散A"
    assert sigs[0]["holder_median_alpha"] == pytest.approx(0.15)


def test_recent_signals_anchors_on_notice_date_not_report_date(mem):
    """PIT 红绿测试 (2026-07-08 修复, 实测 notice_date 中位滞后 report_date 31天):
    real-world 真实场景是"季报期末(report_date)后, 隔了一段时间才披露(notice_date)"——
    notice_date >= report_date 恒成立。默认 30 天窗口下:
      迟披露D: report_date=40天前(超出30天窗口) + notice_date=5天前(真实刚披露, 在窗口内)
        → 必须出现(市场是5天前才知道这次建仓, 是真的"最新信号"); 旧代码按 open_date 过滤
          会因为 report_date 已经"过期"而漏掉这条明明才刚公开的真实新信号(假阴性)。
      未披露E: report_date=10天前(在30天窗口内) + notice_date=45天前(早披露过, 已过期)
        → 必须被排除(不是"最新"信号, 只是report_date凑巧新); 旧代码按 open_date 过滤
          会误当"最新"展示一条其实早就公开过的旧新闻(时间锚概念本身错位)。
    """
    now = datetime.now(timezone.utc)
    late_report = (now - timedelta(days=40)).strftime("%Y%m%d")
    late_notice = (now - timedelta(days=5)).strftime("%Y%m%d")
    stale_report = (now - timedelta(days=10)).strftime("%Y%m%d")
    stale_notice = (now - timedelta(days=45)).strftime("%Y%m%d")
    mem.execute(
        "INSERT INTO fact_inst_episode VALUES "
        f"('迟披露D','600001','{late_report}','{late_notice}',NULL,'holding',NULL,NULL,0,0,'银行',"
        "false,false,'个人'), "
        f"('未披露E','600002','{stale_report}','{stale_notice}',NULL,'holding',NULL,NULL,0,0,'银行',"
        "false,false,'个人')"
    )
    mem.execute(
        "INSERT INTO mart_inst_profile VALUES "
        "('迟披露D','个人',20,0.3,0.3,0.8,0.3,150,false,20,1,false,'ranked'), "
        "('未披露E','个人',20,0.3,0.3,0.8,0.3,150,false,20,1,false,'ranked')"
    )
    sigs = ip.recent_signals(days=30, min_holder_episodes=10)
    holders = {s["holder"] for s in sigs}
    assert "迟披露D" in holders    # notice_date 5天前(窗口内) → 真的是最新信号, 该出现
    assert "未披露E" not in holders  # notice_date 45天前(窗口外) → 不是最新信号, 该排除
