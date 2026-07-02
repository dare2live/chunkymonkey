"""C4 pulse API 单测 — 7 端点结构 + 边界 (空表 / 未知 chain 400 / 未知 content_type 400)。

v2 (2026-07-02 第一批) 覆盖: heatmap content_type tab / rotation dc 资金流轮动+双龙头 /
sentiment 情绪周期+水位字段 / strongest 最强板块卡 / members 成分下钻 (dc 快照 + sw is_new)。

真库被回填写锁占用 → 全部走内存 DuckDB fixture (复用 test_market_pulse._fixture_conn
的源表 DDL + 数据, 扩 3 个 sw L1 + 2 个 dc 板块造 warnings/quiet 场景), FastAPI dependency
override 注入连接 (get_pulse_conn + get_members_conn 同一 fixture, 自带 tr schema)。
真库 TestClient 冒烟由主会话锁释放后跑。
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import market_pulse as mp
from routers import market_pulse as pulse_api
from test_market_pulse import CFG, D, _DDL, _fixture_conn
from conftest import duck_mem

# warnings 阈值测试用固定值 (monkeypatch 注入, 与生产 yaml 解耦; 生产键在场由 config 契约测试守)
WARN_CFG = {**CFG, "warning_quiet_outflow_days": 3}


def _api_conn():
    """扩展 fixture: 5 个 sw L1 (rank 1..5, 造"跌出 top3") + dc 连续悄悄流入/流出板块。"""
    c = _fixture_conn()
    c.executemany("INSERT INTO tr.raw_tushare_index_member_all VALUES (?, ?, ?, ?, ?)",
                  [("801150.SI", "医药生物", "600276.SH", "恒瑞医药", "Y"),
                   ("801200.SI", "商贸零售", "600415.SH", "小商品城", "Y"),
                   ("801300.SI", "基础化工", "600309.SH", "万华化学", "Y")])
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
    # BK0003: 5 日价稳 + 连续净流出 → 最新日 quiet_outflow_days=5 (>=3 进预警)
    c.executemany(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '概念', 'BK0003.DC', '光伏设备', 0.2, 100.0, -10.0, -5.0, '阳光电源')",
        [(d,) for d in D])
    # BK0004: 5 日价稳 + 连续净流入 → 最新日 quiet_inflow_days=5
    c.executemany(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '行业', 'BK0004.DC', '贵金属', 0.1, 100.0, 20.0, 10.0, '赤峰黄金')",
        [(d,) for d in D])
    mp.rebuild_all(conn=c, cfg=CFG)
    return c


def _make_client(conn) -> TestClient:
    app = FastAPI()
    app.include_router(pulse_api.router, prefix="/api/v3/pulse")
    app.dependency_overrides[pulse_api.get_pulse_conn] = lambda: conn
    # members 端点生产走独立 tushare_raw ATTACH 连接; 测试同一 fixture (自带 tr schema)
    app.dependency_overrides[pulse_api.get_members_conn] = lambda: conn
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
    """边界: 源表全空 → rebuild 产出空 pulse 表 → 5 端点必须 200 + 空结构 (不 500)。"""
    monkeypatch.setattr(pulse_api, "_load_cfg", lambda: WARN_CFG)
    c = duck_mem()
    c.executescript(_DDL)
    mp.rebuild_all(conn=c, cfg=CFG)
    try:
        yield _make_client(c)
    finally:
        c.close()


def test_heatmap_matrix_topn_and_days(client):
    # v2 缺口①契约: 默认 content_type=行业 → 只出行业板块 (BK0004 100 > BK0001 20)
    r = client.get("/api/v3/pulse/heatmap")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == mp.CHAIN_DC and body["content_type"] == "行业"
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


def test_heatmap_content_type_tab(client):
    """概念 tab: 只出概念板块 (BK0002 500 > BK0003 -50); 非法 content_type → 400。"""
    r = client.get("/api/v3/pulse/heatmap?content_type=概念")
    assert r.status_code == 200
    body = r.json()
    assert body["content_type"] == "概念"
    assert [s["sector_code"] for s in body["sectors"]] == ["BK0002.DC", "BK0003.DC"]
    assert body["sectors"][0]["total_net_amount"] == pytest.approx(500.0)
    r2 = client.get("/api/v3/pulse/heatmap?content_type=地域")
    assert r2.status_code == 400
    assert "unknown content_type" in r2.json()["detail"]


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
    """v2 dc 资金流轮动: rank_flow 迁移 + 双龙头 + 宽度 + content_type; top 截断。"""
    r = client.get(f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC}&lag=1")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == mp.CHAIN_DC
    assert body["latest_date"] == D[4] and body["prev_date"] == D[3]
    codes = [s["sector_code"] for s in body["sectors"]]
    # D4 net_amount: BK0002 (100) > BK0004 (20) > BK0001 (-3) > BK0003 (-10)
    assert codes == ["BK0002.DC", "BK0004.DC", "BK0001.DC", "BK0003.DC"]
    secs = {s["sector_code"]: s for s in body["sectors"]}
    bk1 = secs["BK0001.DC"]
    assert bk1["rank_flow"] == 3 and bk1["prev_rank_flow"] == 3
    assert bk1["content_type"] == "行业"
    assert bk1["leading"] == "云煤能源" and bk1["leading_pct"] == pytest.approx(9.98)
    assert bk1["flow_leader_stock"] == "云煤能源"
    assert bk1["inflow_breadth"] == pytest.approx(0.0)  # D4 成分 2 只全非流入 → 真 0
    bk2 = secs["BK0002.DC"]
    assert bk2["rank_flow"] == 1 and bk2["net_amount"] == pytest.approx(100.0)
    assert bk2["leading"] is None and bk2["flow_leader_stock"] == "万丰奥威"
    assert bk2["inflow_breadth"] is None  # 无成分快照 → 不知道≠0
    # top 截断
    r2 = client.get(f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC}&lag=1&top=2")
    assert [s["sector_code"] for s in r2.json()["sectors"]] == ["BK0002.DC", "BK0004.DC"]


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
    assert body["chain"] == mp.CHAIN_DC and body["as_of"] == D[4]
    assert [m["con_code"] for m in body["members"]] == ["600001.SH", "600002.SZ"]
    assert body["members"][0]["name"] == "甲"
    r2 = client.get(f"/api/v3/pulse/members?sector_code=801010.SI&chain={mp.CHAIN_SW}")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["as_of"] is None
    assert [m["con_code"] for m in body2["members"]] == ["000592.SZ", "002679.SZ"]
    assert all(m["con_code"] != "600265.SH" for m in body2["members"]), "is_new='N' 历史行必须排除"
    r3 = client.get("/api/v3/pulse/members?sector_code=BK9999.DC")
    assert r3.status_code == 200 and r3.json()["members"] == [] and r3.json()["as_of"] is None
    r4 = client.get("/api/v3/pulse/members?sector_code=BK0001.DC&chain=ths_industry")
    assert r4.status_code == 400


def test_sentiment_v2_fields(client):
    """情绪时序 v2 字段: 天梯/晋级率/秒板/两融/估值/龙虎榜按日透出, 缺源日 null。"""
    r = client.get("/api/v3/pulse/sentiment?days=5")
    assert r.status_code == 200
    days = {d["trade_date"]: d for d in r.json()["days"]}
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


def test_quiet_leaderboards(client):
    r = client.get("/api/v3/pulse/quiet")
    assert r.status_code == 200
    body = r.json()
    # 最新 dc 日 D4: BK0004 inflow streak 5; BK0003 outflow 5 > BK0001 outflow 1
    assert [x["sector_code"] for x in body["inflow"]] == ["BK0004.DC"]
    assert body["inflow"][0]["quiet_inflow_days"] == 5
    assert body["inflow"][0]["trade_date"] == D[4]
    assert [x["sector_code"] for x in body["outflow"]] == ["BK0003.DC", "BK0001.DC"]
    assert [x["quiet_outflow_days"] for x in body["outflow"]] == [5, 1]
    assert body["outflow"][0]["net_amount"] == pytest.approx(-10.0)
    # sw 链 quiet 恒 NULL → 不出现
    assert all(x["chain"] == mp.CHAIN_DC for x in body["inflow"] + body["outflow"])


def test_sentiment_series(client):
    r = client.get("/api/v3/pulse/sentiment?days=3")
    assert r.status_code == 200
    days = r.json()["days"]
    assert [d["trade_date"] for d in days] == D[2:]  # 最近 3 日, 升序
    d0105 = days[1]
    assert d0105["limit_up_total"] == 1 and d0105["zha_ban_rate"] == pytest.approx(0.5)
    # D4 limit 源整日缺失 → NULL (不知道≠0)
    assert days[2]["limit_up_total"] is None and days[2]["zha_ban_rate"] is None


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
        (f"/api/v3/pulse/rotation?chain={mp.CHAIN_DC}",
         {"latest_date": None, "prev_date": None, "sectors": []}),
        ("/api/v3/pulse/quiet", {"inflow": [], "outflow": []}),
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
