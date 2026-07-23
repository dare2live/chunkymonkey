"""C4 pulse API 单测 — 9 端点结构 + 边界 (空表 / 未知 namespace 400 / 非法 level 400)。

v2 (2026-07-02 第一批) 覆盖: heatmap taxonomy namespace / rotation dc 资金流轮动+双龙头 /
sentiment 情绪周期+水位字段 / strongest 最强板块卡 / members 成分下钻 (dc 快照 + sw is_new)。
v3 (2026-07-03) 覆盖: level 参数默认 L1 (rotation/heatmap 不破坏 v2 契约) / flow_board
资金流向榜 (regime 分组 + cum_net + stripe, 替代 /quiet) / drill 三层链下钻 (sw L1→L2→L3→
成分股叶子, 叶子字段齐: 近20日净流/实时 flow_regime/form/limit_times; dc 板块→叶子) /
flow_stripe mini 条纹序列。

真库被回填写锁占用 → 全部走内存 DuckDB fixture (复用 test_market_pulse._fixture_conn
的源表 DDL + 数据, 扩 3 个 sw L1 + 2 个 dc 板块造 warnings/flow_board 场景), FastAPI
dependency override 注入连接 (get_pulse_conn + get_members_conn + get_drill_conn 同一
fixture, 自带 tr schema)。真库 TestClient 冒烟由主会话锁释放后跑。
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import market_pulse as mp
from routers import market_pulse as pulse_api
from test_market_pulse import CFG, D, _fixture_conn

# warnings 阈值测试用固定值 (monkeypatch 注入, 与生产 yaml 解耦; 生产键在场由 config 契约测试守)
WARN_CFG = {**CFG, "warning_quiet_outflow_days": 3}


def _api_conn():
    """扩展 fixture: 5 个 sw L1 (rank 1..5, 造"跌出 top3") + dc 连续净流入/流出板块
    (v3: BK0003/BK0004 日 |pct|=0.1 → 5 日复利 <1% 落横盘带, 造 accum_*_silent)。"""
    c = _fixture_conn()
    c.executemany("INSERT INTO tr.raw_tushare_index_member_all VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        ("801150.SI", "医药生物", "801151.SI", "化学制药", "850151.SI", "原料药",
         "600276.SH", "恒瑞医药", "20111010", None, "Y"),
        ("801200.SI", "商贸零售", "801201.SI", "一般零售", "850201.SI", "百货",
         "600415.SH", "小商品城", "20111010", None, "Y"),
        ("801300.SI", "基础化工", "801301.SI", "化学原料", "850301.SI", "纯碱",
         "600309.SH", "万华化学", "20111010", None, "Y")])
    closes = {
        # w4=2 (CFG): rs@D3 = close[3]/close[1]-1, rs@D4 = close[4]/close[2]-1 (基准平盘)
        "801150.SI": [100.0, 105.0, 110.25, 115.76, 121.55],  # 恒 +10.25%
        "801200.SI": [100.0, 100.0, 108.0, 116.0, 100.0],     # D3 +16% (rank2) → D4 -7.4% (rank4)
        "801300.SI": [100.0, 101.0, 102.0, 103.0, 104.0],     # ~+2%
    }
    for code, cs in closes.items():
        c.executemany(
            f"INSERT INTO tr.raw_tushare_sw_daily VALUES ('{code}', ?, ?, 1.0, 50.0)",
            list(zip(D, cs)))
    # BK0003: 5 日价稳 + 连续净流出 → quiet_outflow_days=5 (>=3 进预警) + regime 横盘累积流出
    c.executemany(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '概念', 'BK0003.DC', '光伏设备', 0.1, 100.0, -10.0, -5.0, '阳光电源')",
        [(d,) for d in D])
    # BK0004: 5 日价稳 + 连续净流入 → quiet_inflow_days=5 + regime 横盘累积流入
    c.executemany(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '行业', 'BK0004.DC', '贵金属', 0.1, 100.0, 20.0, 10.0, '赤峰黄金')",
        [(d,) for d in D])
    c.executemany(
        "INSERT INTO tr.raw_tushare_dc_index VALUES "
        "('BK0003.DC', ?, '概念板块', '阳光电源', '300274.SZ', 1.0, 1, 1, 100.0, NULL)",
        [(d,) for d in D],
    )
    c.executemany(
        "INSERT INTO tr.raw_tushare_dc_index VALUES "
        "('BK0004.DC', ?, '行业板块', '赤峰黄金', '600988.SH', 1.0, 1, 1, 100.0, "
        "'东财一级行业')",
        [(d,) for d in D],
    )
    mp.rebuild_all(conn=c, cfg=CFG)
    return c


def _make_client(conn) -> TestClient:
    app = FastAPI()
    app.include_router(pulse_api.router, prefix="/api/v3/pulse")
    app.dependency_overrides[pulse_api.get_pulse_conn] = lambda: conn
    # members/drill 端点生产走独立 ATTACH 连接; 测试同一 fixture (自带 tr schema + 主表)
    app.dependency_overrides[pulse_api.get_members_conn] = lambda: conn
    app.dependency_overrides[pulse_api.get_drill_conn] = lambda: conn
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(pulse_api, "_load_cfg", lambda: WARN_CFG)
    c = _api_conn()
    try:
        yield _make_client(c)
    finally:
        c.close()


@pytest.fixture
def empty_client(monkeypatch):
    """边界: 已发布 mart 暂无行 → 端点必须 200 + 空结构 (不 500)。

    Writer 对空源应 fail closed；API 空读契约用已验证 schema 清空后的表测试，不能要求
    production rebuild 把 0 行当成功。
    """
    monkeypatch.setattr(pulse_api, "_load_cfg", lambda: WARN_CFG)
    c = _api_conn()
    c.execute(f"DELETE FROM {mp.MARKET_TABLE}")
    c.execute(f"DELETE FROM {mp.SECTOR_TABLE}")
    for table_name, table_type in c.execute("""
        SELECT table_name, table_type FROM information_schema.tables
        WHERE table_schema = 'tr'
    """).fetchall():
        if str(table_type).upper() == "VIEW":
            continue  # S7: v_sw_industry_pit is a view over membership raw
        c.execute(f'DELETE FROM tr."{table_name}"')
    c.execute("DELETE FROM dim_stock_segment_daily")
    c.execute("DELETE FROM fact_stock_form_daily")
    # B1/B2 publications live on main (not tr.*); clear serve-leaf facts too.
    for fact in (
        "fact_dc_member_daily",
        "fact_stock_moneyflow_daily",
        "fact_stock_moneyflow_dc_daily",
        "fact_stock_limit_daily",
        "fact_index_daily",
        "fact_top_inst_seat_daily",
    ):
        c.execute(f"DELETE FROM {fact}")
    try:
        yield _make_client(c)
    finally:
        c.close()


def test_heatmap_matrix_topn_and_days(client):
    # 默认 namespace=dc_industry → 只出行业板块 (BK0004 100 > BK0001 20)
    r = client.get("/api/v3/pulse/heatmap")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == mp.CHAIN_DC_INDUSTRY
    assert "content_type" not in body
    assert body["dates"] == D  # 5 日 <= 默认 20, 升序
    codes = [s["sector_code"] for s in body["sectors"]]
    assert codes == ["BK0004.DC", "BK0001.DC"]
    top = body["sectors"][0]
    assert top["total_net_amount"] == pytest.approx(100.0)
    assert len(top["values"]) == len(body["dates"])
    assert top["values"] == [pytest.approx(20.0)] * 5
    # top 截断
    r2 = client.get("/api/v3/pulse/heatmap?top=1")
    assert [s["sector_code"] for s in r2.json()["sectors"]] == ["BK0004.DC"]
    # days 截断: 只留最近 2 日
    r3 = client.get("/api/v3/pulse/heatmap?days=2")
    assert r3.json()["dates"] == D[-2:]


def test_heatmap_exposes_dc_industry_and_concept_as_separate_namespaces(client):
    industry = client.get(f"/api/v3/pulse/heatmap?chain={mp.CHAIN_DC_INDUSTRY}")
    concept = client.get(f"/api/v3/pulse/heatmap?chain={mp.CHAIN_DC_CONCEPT}")

    assert industry.status_code == 200 and concept.status_code == 200
    assert industry.json()["chain"] == mp.CHAIN_DC_INDUSTRY
    assert concept.json()["chain"] == mp.CHAIN_DC_CONCEPT
    assert [row["sector_code"] for row in industry.json()["sectors"]] == [
        "BK0004.DC", "BK0001.DC"
    ]
    assert [row["sector_code"] for row in concept.json()["sectors"]] == [
        "BK0002.DC", "BK0003.DC"
    ]
    assert "content_type" not in industry.json()
    assert "content_type" not in concept.json()


def test_heatmap_concept_namespace(client):
    """概念是独立 namespace，不是 dc_industry 内的 content_type tab。"""
    r = client.get(f"/api/v3/pulse/heatmap?chain={mp.CHAIN_DC_CONCEPT}")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == mp.CHAIN_DC_CONCEPT
    assert [s["sector_code"] for s in body["sectors"]] == ["BK0002.DC", "BK0003.DC"]
    assert body["sectors"][0]["total_net_amount"] == pytest.approx(500.0)


def test_heatmap_unknown_chain_400(client):
    r = client.get("/api/v3/pulse/heatmap?chain=ths_industry")
    assert r.status_code == 400
    assert "unknown chain" in r.json()["detail"]


def test_rotation_rank_migration(client):
    # lag=1: prev = 前一入库日 D3 (fixture 仅 5 日, 默认 lag=5 会兜底到最早日 rs 全 NULL)
    r = client.get("/api/v3/pulse/rotation?lag=1")
    assert r.status_code == 200
    body = r.json()
    assert body["latest_date"] == D[4] and body["prev_date"] == D[3]
    secs = {s["sector_code"]: s for s in body["sectors"]}
    assert len(secs) == 5
    # 排序 = 最新日 rs_rank_4w 升序
    assert [s["sector_code"] for s in body["sectors"]][:2] == ["801010.SI", "801150.SI"]
    mover = secs["801200.SI"]  # D3 rank2 → D4 rank4 (迁移箭头数据源)
    assert mover["prev_rs_rank_4w"] == 2 and mover["rs_rank_4w"] == 4
    assert mover["rs_4w"] == pytest.approx(100.0 / 108.0 * 100 - 100, abs=1e-6)
    assert mover["prev_rs_4w"] == pytest.approx(16.0)


def test_rotation_dc_chain_leaders_and_rank_flow(client):
    """东财行业与概念分别排名；任一 API 响应不得混入另一 namespace。"""
    r = client.get(f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC_INDUSTRY}&lag=1")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == mp.CHAIN_DC_INDUSTRY
    assert body["latest_date"] == D[4] and body["prev_date"] == D[3]
    codes = [s["sector_code"] for s in body["sectors"]]
    assert codes == ["BK0004.DC", "BK0001.DC"]
    secs = {s["sector_code"]: s for s in body["sectors"]}
    bk1 = secs["BK0001.DC"]
    assert bk1["rank_flow"] == 2 and bk1["prev_rank_flow"] == 2
    assert bk1["content_type"] == "行业"
    assert bk1["leading"] == "云煤能源" and bk1["leading_pct"] == pytest.approx(9.98)
    assert bk1["flow_leader_stock"] == "云煤能源"
    assert bk1["inflow_breadth"] == pytest.approx(0.0)  # D4 成分 2 只全非流入 → 真 0
    concept = client.get(
        f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC_CONCEPT}&lag=1"
    ).json()
    assert concept["chain"] == mp.CHAIN_DC_CONCEPT
    assert [s["sector_code"] for s in concept["sectors"]] == ["BK0002.DC", "BK0003.DC"]
    bk2 = concept["sectors"][0]
    assert bk2["rank_flow"] == 1 and bk2["net_amount"] == pytest.approx(100.0)
    assert bk2["leading"] == "万丰奥威" and bk2["flow_leader_stock"] == "万丰奥威"
    assert bk2["inflow_breadth"] is None  # 无成分快照 → 不知道≠0
    # top 截断
    r2 = client.get(f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC_CONCEPT}&lag=1&top=1")
    assert [s["sector_code"] for s in r2.json()["sectors"]] == ["BK0002.DC"]


def test_strongest_leaderboard(client):
    """最强板块卡: 取最近有榜日 (fixture 仅 D0), rank 升序, TI 码原样透出 (独立卡不 JOIN)。"""
    r = client.get("/api/v3/pulse/strongest")
    assert r.status_code == 200
    body = r.json()
    assert body["trade_date"] == D[0]
    assert [s["ts_code"] for s in body["sectors"]] == ["885700.TI", "885571.TI"]
    top = body["sectors"][0]
    assert top["name"] == "军工" and top["days"] == 1
    assert top["up_stat"] == "3天3板" and top["cons_nums"] == "2" and top["up_nums"] == 13
    assert top["rank"] == 1


def test_members_drilldown(client):
    """成分下钻: dc 取该板块最新快照日 (D4, 2 成分); sw 取 is_new='Y' 当前成分
    (历史 'N' 行排除); 未知板块 → 200 空; 未知 chain → 400。"""
    r = client.get("/api/v3/pulse/members?sector_code=BK0001.DC")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == mp.CHAIN_DC_INDUSTRY and body["as_of"] == D[4]
    assert [m["con_code"] for m in body["members"]] == ["600001.SH", "600002.SZ"]
    assert body["members"][0]["name"] == "甲"
    r2 = client.get(f"/api/v3/pulse/members?sector_code=801010.SI&chain={mp.CHAIN_SW}")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["as_of"] is None
    assert [m["con_code"] for m in body2["members"]] == [
        "000592.SZ",
        "002679.SZ",
        "600001.SH",
        "600003.SZ",
    ]
    assert all(m["con_code"] != "600265.SH" for m in body2["members"]), "is_new='N' 历史行必须排除"
    r3 = client.get("/api/v3/pulse/members?sector_code=BK9999.DC")
    assert r3.status_code == 200 and r3.json()["members"] == [] and r3.json()["as_of"] is None
    r4 = client.get("/api/v3/pulse/members?sector_code=BK0001.DC&chain=ths_industry")
    assert r4.status_code == 400


def test_sentiment_v2_fields(client):
    """情绪时序 v2 字段: 天梯/晋级率/秒板/两融/估值/龙虎榜按日透出, 缺源日 null。"""
    r = client.get("/api/v3/pulse/sentiment?days=5")
    assert r.status_code == 200
    body = r.json()
    days = {d["trade_date"]: d for d in body["days"]}
    d0 = days[D[0]]
    assert d0["rzrqye"] == pytest.approx(150.0) and d0["rzrqye_chg"] is None
    assert d0["mkt_pe"] == pytest.approx(14.42) and d0["mkt_turnover"] == pytest.approx(3.0)
    assert d0["lhb_count"] == 2 and d0["lhb_inst_net"] == pytest.approx(70.0)
    assert d0["max_limit_times"] is None  # limit 源 D0 整日缺失
    d3 = days[D[3]]
    assert d3["max_limit_times"] == 2 and d3["sec_board_n"] == 1
    assert d3["promotion_rate"] is None  # 昨日 (D2) 0 板 → 不除零
    assert d3["limit_times_dist_json"] == '{"2":1}'
    assert d3["avg_fd_amount"] == pytest.approx(3000.0) and d3["open_times_total"] == 3
    assert d3["rzrqye"] is None and d3["lhb_count"] is None
    # B-ext sidecar: trust markers without rewriting mart day values.
    assert body["cutover_allowed"] is False
    scope = body["population_scope"]
    # Fixture latest D[4]=20240108 is outside B-pit window + before margin
    # coverage → both typed EMPTY (normal absence), overall READY.
    assert scope["overall_status"] == "READY"
    assert scope["trade_date"] == D[4]
    by_field = {f["field"]: f for f in scope["fields"]}
    assert by_field["rzrqye"]["population_kind"] == "external_aggregate"
    # Fixture latest D[4]=20240108 is before margin v3 coverage_start → typed EMPTY.
    assert by_field["rzrqye"]["status"] == "EMPTY"
    assert "normal_absence_not_fail_closed" in by_field["rzrqye"]["reason"]
    # Latest fixture day has no accepted margin → shadow blocked, not PARITY/cutover.
    shadow = body["shadow_reconcile"]
    assert shadow["cutover_allowed"] is False
    assert shadow["verdict"] == "BLOCKED"
    assert "margin_core_venues_incomplete" in shadow["issues"]
    # F4 promote_gate: pre-coverage absence → EMPTY_OK (normal), not UNTRUSTED scare.
    gate = body["promote_gate"]
    assert gate["product_trust_would_be"] == "EMPTY"
    assert gate["status"] == "EMPTY_OK"
    assert gate["population_kind"] == "external_aggregate"
    assert gate["absence_kind"] == "not_expected"
    assert "typed_empty_not_expected" in gate["notes"]
    assert "no_silent_product_thaw" in gate["notes"]
    # Phase C: cutover ON (owner opt-in), but this fixture day (20240108) has no
    # accepted partition → resolver fails closed to legacy scaffold (BLOCKED).
    t12 = body["tier12_production_read"]
    assert t12["uses_legacy"] is True
    assert t12["cutover_allowed"] is False
    assert t12["status"] == "BLOCKED"
    assert any("missing_accept" in r for r in t12["reasons"])
    assert "pulse_ui_attestation" in t12["notes"]
    # B-pit mart cutover ON, but fixture day is outside the attested shadow
    # window [20260121, 20260722] → fail closed to legacy mart (BLOCKED).
    assert body["b_pit_mart_cutover_allowed"] is False
    bpit = body["b_pit_mart_production_read"]
    assert bpit["uses_legacy"] is True
    assert bpit["cutover_allowed"] is False
    assert bpit["status"] == "BLOCKED"
    assert bpit["source"] == "legacy_mart"
    assert any("trade_date_outside_shadow_window" in r for r in bpit["reasons"])
    assert "pulse_ui_attestation" in bpit["notes"]
    # Outside B-pit window → breadth typed EMPTY (normal), not UNTRUSTED scare.
    assert by_field["adv_dec_ratio"]["status"] == "EMPTY"
    assert "normal_absence_not_fail_closed" in by_field["adv_dec_ratio"]["reason"]
    assert "breadth_typed_empty_normal_absence" in scope["notes"]
    assert "breadth_untrusted_until_b_pit_mart_cutover" not in scope["notes"]


def test_flow_board_regime_groups_and_stripe(client):
    """资金形态榜按 namespace 隔离：行业和概念分别请求、分别排序。"""
    r = client.get("/api/v3/pulse/flow_board")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == mp.CHAIN_DC_INDUSTRY and body["trade_date"] == D[4]
    inflow = {x["sector_code"]: x for x in body["inflow"]}
    assert [x["sector_code"] for x in body["inflow"]] == ["BK0004.DC"]
    assert inflow["BK0004.DC"]["flow_regime"] == "accum_in_silent"
    assert inflow["BK0004.DC"]["flow_streak"] == 5
    assert [x["sector_code"] for x in body["outflow"]] == ["BK0001.DC"]
    out = {x["sector_code"]: x for x in body["outflow"]}
    assert out["BK0001.DC"]["flow_regime"] == "surge_out"
    assert out["BK0001.DC"]["cum_ratio_20d"] == pytest.approx(5.0 / 2e6 * 100)  # (7+1-3)/总市值
    # stripe: 与 stripe_dates 对齐的逐日净流序列
    assert body["stripe_dates"] == D
    assert out["BK0001.DC"]["stripe"] == [pytest.approx(v) for v in (10.0, 5.0, 7.0, 1.0, -3.0)]
    concept = client.get(f"/api/v3/pulse/flow_board?chain={mp.CHAIN_DC_CONCEPT}").json()
    assert [x["sector_code"] for x in concept["inflow"]] == ["BK0002.DC"]
    assert concept["inflow"][0]["flow_regime"] == "accum_in_driving"
    assert concept["inflow"][0]["cum_net"] == pytest.approx(300.0)
    assert [x["sector_code"] for x in concept["outflow"]] == ["BK0003.DC"]
    assert concept["outflow"][0]["flow_regime"] == "accum_out_silent"
    assert concept["outflow"][0]["cum_ratio_20d"] == pytest.approx(-0.003)
    # stripe_days=0 关闭条纹
    r2 = client.get("/api/v3/pulse/flow_board?stripe_days=0")
    assert r2.json()["stripe_dates"] == [] and r2.json()["inflow"][0]["stripe"] == []


def test_flow_board_sw_chain_level(client):
    """flow_board sw 链: level 默认 L1 — 801010 上行累积流入 (net=成分净流聚合); 801080 无流
    数据 regime NULL 不进榜; L2 参数切 801011。"""
    r = client.get(f"/api/v3/pulse/flow_board?chain={mp.CHAIN_SW}")
    assert r.status_code == 200
    body = r.json()
    assert [x["sector_code"] for x in body["inflow"]] == ["801010.SI"]
    row = body["inflow"][0]
    assert row["flow_regime"] == "accum_in_driving" and row["level"] == "L1"
    assert row["cum_net"] == pytest.approx(130000.0)  # cw=3: 5e4+5e4+3e4
    assert body["outflow"] == []
    # L2: 801011 最新日 (D4) 成员无流数据 → net/regime NULL → 不进榜 (不知道≠0, 不伪造)
    r2 = client.get(f"/api/v3/pulse/flow_board?chain={mp.CHAIN_SW}&level=L2")
    assert r2.status_code == 200
    assert r2.json()["inflow"] == [] and r2.json()["outflow"] == []
    r3 = client.get(f"/api/v3/pulse/flow_board?chain={mp.CHAIN_SW}&level=L9")
    assert r3.status_code == 400


def test_flow_stripe_series(client):
    r = client.get("/api/v3/pulse/flow_stripe?code=BK0001.DC&days=3")
    assert r.status_code == 200
    body = r.json()
    assert body["sector_code"] == "BK0001.DC" and body["sector_name"] == "煤炭行业"
    assert body["dates"] == D[2:]
    assert body["values"] == [pytest.approx(v) for v in (7.0, 1.0, -3.0)]
    # 未知码 → 200 空 (不猜); 未知 chain → 400
    r2 = client.get("/api/v3/pulse/flow_stripe?code=BK9999.DC")
    assert r2.status_code == 200 and r2.json()["dates"] == []
    r3 = client.get("/api/v3/pulse/flow_stripe?code=BK0001.DC&chain=ths_industry")
    assert r3.status_code == 400


def test_sentiment_series(client):
    r = client.get("/api/v3/pulse/sentiment?days=3")
    assert r.status_code == 200
    days = r.json()["days"]
    assert [d["trade_date"] for d in days] == D[2:]  # 最近 3 日, 升序
    d0105 = days[1]
    assert d0105["limit_up_total"] == 1 and d0105["zha_ban_rate"] == pytest.approx(0.5)
    # D4 limit 源整日缺失 → NULL (不知道≠0)
    assert days[2]["limit_up_total"] is None and days[2]["zha_ban_rate"] is None


def test_rotation_heatmap_level_param(client):
    """v3 level 参数: 默认 L1 保 v2 契约 (31 行语义 — fixture 5 L1, L2 不混入);
    level=L2 切出 801011; 非法 level → 400。heatmap sw 链 v3 起有净流值。"""
    r = client.get("/api/v3/pulse/rotation?lag=1")
    assert all(s["sector_code"].startswith("801") and s["sector_code"] != "801011.SI"
               for s in r.json()["sectors"])
    r2 = client.get("/api/v3/pulse/rotation?lag=1&level=L2")
    secs2 = r2.json()["sectors"]
    assert [s["sector_code"] for s in secs2] == ["801011.SI"]
    assert secs2[0]["rs_rank_4w"] == 1  # 同级分区: L2 独行 rank=1, 不与 L1 混排
    assert client.get("/api/v3/pulse/rotation?level=LX").status_code == 400
    # heatmap sw: level 默认 L1 (5 个 L1 全列, 无流的 4 个 total NULL 沉底);
    # 801010 窗口累计净流 = 5+5+5+5+3 万元×1e4
    r3 = client.get(f"/api/v3/pulse/heatmap?chain={mp.CHAIN_SW}")
    body3 = r3.json()
    codes3 = [s["sector_code"] for s in body3["sectors"]]
    assert len(codes3) == 5 and codes3[0] == "801010.SI" and "801011.SI" not in codes3
    assert body3["sectors"][0]["total_net_amount"] == pytest.approx(230000.0)
    r4 = client.get(f"/api/v3/pulse/heatmap?chain={mp.CHAIN_SW}&level=L2")
    assert [s["sector_code"] for s in r4.json()["sectors"]] == ["801011.SI"]
    assert client.get(f"/api/v3/pulse/heatmap?chain={mp.CHAIN_SW}&level=LX").status_code == 400


def test_drill_sw_three_level_chain(client):
    """drill 三层链 (sw): 根→L1 列表; L1→L2 (含 mart 行); L2→L3 (无行情码补名字行);
    面包屑逐层加长。"""
    r = client.get("/api/v3/pulse/drill")
    body = r.json()
    assert body["rows_level"] == "L1" and body["date"] == D[4] and body["breadcrumb"] == []
    codes = [x["sector_code"] for x in body["rows"]]
    assert codes[0] == "801010.SI" and len(codes) == 5  # rs_rank 升序, L2 不混入
    assert body["rows"][0]["flow_regime"] == "accum_in_driving"
    r2 = client.get("/api/v3/pulse/drill?code=801010.SI")
    b2 = r2.json()
    assert b2["rows_level"] == "L2"
    assert [x["code"] for x in b2["breadcrumb"]] == ["801010.SI"]
    assert b2["breadcrumb"][0]["name"] == "农林牧渔"
    assert [x["sector_code"] for x in b2["rows"]] == ["801011.SI", "801012.SI"]
    assert b2["rows"][0]["level"] == "L2" and b2["rows"][0]["rs_4w"] is not None
    r3 = client.get("/api/v3/pulse/drill?code=801011.SI")
    b3 = r3.json()
    assert b3["rows_level"] == "L3"
    assert [x["code"] for x in b3["breadcrumb"]] == ["801010.SI", "801011.SI"]
    assert [x["sector_code"] for x in b3["rows"]] == ["850111.SI"]
    # 850111 不在 sw_daily → 无行情行, 但成分在册必须列出 (名字 + 指标 NULL, 不隐藏)
    assert b3["rows"][0]["sector_name"] == "粮食种植" and b3["rows"][0]["net_amount"] is None
    # 未知码 → 200 空
    r4 = client.get("/api/v3/pulse/drill?code=X99999.SI")
    assert r4.status_code == 200 and r4.json()["rows"] == []


def test_drill_leaf_fields_complete(client):
    """叶子层字段齐 (v3.2 选股落点): 近20日净流 (cum_net) / 实时 flow_regime (窗口 SQL) /
    form_name+is_breakout_event (as-of 最新行, D2 旧行被 D3 覆盖) / limit_times (最新流日
    U 行) / PIT 排除 (600265 out_date 已过不出现)。"""
    r = client.get("/api/v3/pulse/drill?code=850111.SI")
    body = r.json()
    assert body["rows_level"] == "stock"
    assert [x["code"] for x in body["breadcrumb"]] == ["801010.SI", "801011.SI", "850111.SI"]
    rows = {x["ts_code"]: x for x in body["rows"]}
    assert set(rows) == {"600001.SH", "000592.SZ", "002679.SZ"}, "is_new='N' 已剔除股必须排除"
    lead = body["rows"][0]
    assert lead["ts_code"] == "600001.SH"  # cum_net 降序首位
    assert lead["stock_code"] == "600001"
    assert lead["trade_date"] == D[3]           # 该股最新流日 (D4 无流行)
    assert lead["net_amount"] == pytest.approx(20000.0)
    assert lead["cum_net"] == pytest.approx(60000.0)   # cw=3: 2e4×3
    assert lead["flow_streak"] == 4
    assert lead["flow_regime"] == "accum_in_driving"   # px=1.01^4-1=4.06%>=1
    assert lead["form_name"] == "低位横盘" and lead["is_breakout_event"] is True
    assert lead["limit_times"] == 2                    # D3 U 行 limit_times
    # 无流数据成员: 字段 NULL (不知道≠0), 仍在列表
    assert rows["000592.SZ"]["net_amount"] is None
    assert rows["000592.SZ"]["flow_regime"] is None
    assert rows["000592.SZ"]["form_name"] is None


def test_drill_dc_top_and_leaf(client):
    """drill 的东财行业/概念顶层由 namespace 分开，行业板块码可进入成分叶子。
    板块码→成分股叶子 (dc_member 最新快照 + moneyflow_dc 东财口径流)。"""
    r = client.get(f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_INDUSTRY}")
    body = r.json()
    assert body["rows_level"] == "sector"
    assert [x["sector_code"] for x in body["rows"]] == ["BK0004.DC", "BK0001.DC"]  # 行业 rank_flow 序
    assert body["rows"][0]["flow_regime"] == "accum_in_silent"
    r2 = client.get(f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_CONCEPT}")
    assert [x["sector_code"] for x in r2.json()["rows"]] == ["BK0002.DC", "BK0003.DC"]
    r3 = client.get(f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_INDUSTRY}&top=1")
    assert [x["sector_code"] for x in r3.json()["rows"]] == ["BK0004.DC"]
    # 叶子: BK0001 D4 快照成分 (600001/600002), moneyflow_dc 流 (万元→元)
    r4 = client.get(f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_INDUSTRY}&code=BK0001.DC")
    b4 = r4.json()
    assert b4["rows_level"] == "stock" and b4["member_as_of"] == D[4]
    assert b4["breadcrumb"][0]["name"] == "煤炭行业"
    assert [x["ts_code"] for x in b4["rows"]] == ["600001.SH", "600002.SZ"]  # cum_net 4e4 > -3e4
    leaf = b4["rows"][0]
    assert leaf["trade_date"] == D[4]
    assert leaf["net_amount"] == pytest.approx(-10000.0)
    assert leaf["cum_net"] == pytest.approx(40000.0)   # (5-1) 万元 ×1e4
    assert leaf["flow_streak"] == -1 and leaf["flow_regime"] == "neutral"
    assert leaf["form_name"] == "低位横盘"
    assert leaf["limit_times"] is None                 # D4 无涨停行 → NULL 不猜
    # 未知板块 → 200 空
    r5 = client.get(f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_INDUSTRY}&code=BK9999.DC")
    assert r5.status_code == 200 and r5.json()["rows"] == []


def test_warnings_dropout_and_quiet_outflow(client):
    r = client.get("/api/v3/pulse/warnings")
    assert r.status_code == 200
    body = r.json()
    assert body["thresholds"] == {"rank_top": 3, "quiet_outflow_days": 3}
    # 801200: D3 rank2 (<=3) → D4 rank4 (>3) = 跌出 top3
    assert [x["sector_code"] for x in body["rank_dropouts"]] == ["801200.SI"]
    drop = body["rank_dropouts"][0]
    assert drop["prev_rank"] == 2 and drop["latest_rank"] == 4
    assert drop["prev_date"] == D[3] and drop["latest_date"] == D[4]
    # BK0003 streak 5 >= 3 进预警; BK0001 streak 1 不进
    assert [x["sector_code"] for x in body["quiet_outflows"]] == ["BK0003.DC"]
    assert body["quiet_outflows"][0]["quiet_outflow_days"] == 5


def test_empty_tables_all_endpoints_200(empty_client):
    for path, empty_shape in [
        ("/api/v3/pulse/heatmap", {"dates": [], "sectors": []}),
        ("/api/v3/pulse/rotation", {"latest_date": None, "prev_date": None, "sectors": []}),
        (f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC_INDUSTRY}",
         {"latest_date": None, "prev_date": None, "sectors": []}),
        (f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC_CONCEPT}",
         {"latest_date": None, "prev_date": None, "sectors": []}),
        ("/api/v3/pulse/flow_board", {"trade_date": None, "stripe_dates": [],
                                      "inflow": [], "outflow": []}),
        (f"/api/v3/pulse/flow_board?chain={mp.CHAIN_SW}", {"inflow": [], "outflow": []}),
        ("/api/v3/pulse/flow_stripe?code=BK0001.DC", {"dates": [], "values": []}),
        ("/api/v3/pulse/drill", {"date": None, "breadcrumb": [], "rows": []}),
        (f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_INDUSTRY}", {"date": None, "rows": []}),
        (f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_CONCEPT}", {"date": None, "rows": []}),
        (f"/api/v3/pulse/drill?chain={mp.CHAIN_DC_INDUSTRY}&code=BK0001.DC",
         {"member_as_of": None, "rows": []}),
        ("/api/v3/pulse/drill?code=801010.SI", {"rows": []}),
        ("/api/v3/pulse/sentiment", {"days": []}),
        ("/api/v3/pulse/warnings", {"rank_dropouts": [], "quiet_outflows": []}),
        ("/api/v3/pulse/strongest", {"trade_date": None, "sectors": []}),
        ("/api/v3/pulse/members?sector_code=BK0001.DC", {"as_of": None, "members": []}),
    ]:
        r = empty_client.get(path)
        assert r.status_code == 200, path
        body = r.json()
        for k, v in empty_shape.items():
            assert body[k] == v, f"{path} .{k}"


def test_production_cfg_has_warning_keys():
    """生产 yaml 契约: warnings 端点消费的两个阈值键在场且类型合法 (值本身是真相源不冻结)。"""
    cfg = pulse_api._load_cfg()
    assert isinstance(cfg["top_n_sectors"], int) and cfg["top_n_sectors"] > 0
    assert isinstance(cfg["warning_quiet_outflow_days"], int) and cfg["warning_quiet_outflow_days"] > 0
