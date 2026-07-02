"""market_pulse router — C4 市场感知 API (2026-07-02, 契约=analysis/market_pulse_design_20260702.md §3 + v2 增强设计第一批)

前端契约 (卡片↔API 一一对应, widget 独立取数):
  GET /api/v3/pulse/heatmap    资金热力图 (板块×近N日 net_amount 矩阵, dc 链默认;
                               v2: content_type 参数分 行业/概念 tab, 默认行业)
  GET /api/v3/pulse/rotation   板块轮动 (v2 分链: sw=RS 双窗排名迁移 [v1 原样];
                               dc=资金流排名迁移 + 涨幅/资金双龙头 + 流入宽度)
  GET /api/v3/pulse/quiet      悄悄流入/流出榜 (quiet_*_days > 0, 降序)
  GET /api/v3/pulse/sentiment  情绪温度时序 (涨跌停/炸板率/涨跌比 + v2 连板天梯/晋级率/
                               秒板/封单/两融/大盘PE换手/龙虎榜, mart_market_pulse_daily)
  GET /api/v3/pulse/warnings   退潮预警 (跌出 RS top-N + 连续悄悄流出 >= 阈值)
  GET /api/v3/pulse/strongest  最强板块榜 (limit_cpt_list 引擎快照; 885xxx.TI 码独立卡禁跨链)
  GET /api/v3/pulse/members    板块成分下钻 (dc=dc_member 最新快照; sw=index_member_all 当前成分)

红线: 感知层只描述现状, 零买卖暗示 (设计 §1); pulse 数据只读 services.market_pulse 产出的
两张 display 表 (smartmoney), 本 router 不做任何跨链混算。阈值走 config/market_pulse.yaml
(top_n_sectors / warning_quiet_outflow_days / dc_content_types), 不 hardcode。
例外: /members 直读 tushare_raw 成分表 (千万行级, 不值得复制进 mart; READ_ONLY ATTACH)。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from services import market_pulse as mp
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

router = APIRouter()


def get_pulse_conn():
    """read_only smartmoney 连接 (两张 pulse 表就在 smartmoney 库内, 无需 ATTACH)。
    测试经 app.dependency_overrides 注入内存 fixture 连接。"""
    con = duck_connect(str(get_database_manifest().path_for("smartmoney")), read_only=True)
    try:
        yield con
    finally:
        con.close()


def get_members_conn():
    """成分下钻连接: 内存主库 + READ_ONLY ATTACH tushare_raw AS tr — 与引擎/测试 fixture 同
    'tr.' 两部名解析 (测试 override 注入带 tr schema 的内存 fixture)。sync 写锁窗口内
    ATTACH 会拒连 → 前端按失败态展示重试, 不降级伪造数据。"""
    con = duck_connect(":memory:")
    try:
        raw_path = str(get_database_manifest().path_for("tushare_raw")).replace("'", "''")
        con.execute(f"ATTACH '{raw_path}' AS tr (READ_ONLY)")
        yield con
    finally:
        con.close()


def _load_cfg() -> dict[str, Any]:
    """阈值真相源 = config/market_pulse.yaml (测试 monkeypatch 本函数解耦生产值)。"""
    return mp._cfg()


def _require_chain(chain: str) -> None:
    if chain not in (mp.CHAIN_DC, mp.CHAIN_SW):
        raise HTTPException(
            status_code=400,
            detail=f"unknown chain: {chain!r} (expect {mp.CHAIN_DC!r}/{mp.CHAIN_SW!r})")


def _recent_dates(conn, chain: str, n: int) -> list[str]:
    """链内最近 n 个入库交易日, 升序返回。"""
    rows = conn.execute(
        f"SELECT DISTINCT trade_date FROM {mp.SECTOR_TABLE} WHERE chain = ? "
        "ORDER BY trade_date DESC LIMIT ?", [chain, n]).fetchall()
    return sorted(r[0] for r in rows)


@router.get("/heatmap")
def heatmap(chain: str = mp.CHAIN_DC,
            content_type: str = "行业",
            days: int = Query(default=20, ge=1, le=250),
            top: int = Query(default=40, ge=1, le=200),
            conn=Depends(get_pulse_conn)):
    """板块×近 N 日 net_amount 矩阵。板块按窗口内累计 net_amount 降序取 top 防爆载
    (dc 链 1000+ 板块)。sw 链 net_amount 恒 NULL (vendor 隔离), 前端默认只用 dc。
    v2 缺口①: content_type 分 行业/概念 tab (dc 链专用过滤, 白名单=yaml dc_content_types;
    sw 链行恒 '申万L1', 该参数不适用直接忽略)。"""
    _require_chain(chain)
    ct_filter = ""
    ct_params: list[Any] = []
    if chain == mp.CHAIN_DC:
        allowed = list(_load_cfg()["dc_content_types"])  # from yaml: dc_content_types
        if content_type not in allowed:
            raise HTTPException(status_code=400,
                                detail=f"unknown content_type: {content_type!r} (expect {allowed})")
        ct_filter = "AND content_type = ?"
        ct_params = [content_type]
    dates = _recent_dates(conn, chain, days)
    if not dates:
        return {"status": "ok", "chain": chain, "content_type": content_type,
                "dates": [], "sectors": []}
    ph = ",".join("?" * len(dates))
    rows = conn.execute(f"""
        WITH win AS (
            SELECT sector_code, sector_name, trade_date, net_amount
            FROM {mp.SECTOR_TABLE}
            WHERE chain = ? AND trade_date IN ({ph}) {ct_filter}
        ), tops AS (
            SELECT sector_code, SUM(net_amount) AS total_net
            FROM win GROUP BY 1
            ORDER BY total_net DESC NULLS LAST LIMIT ?
        )
        SELECT w.sector_code, w.sector_name, w.trade_date, w.net_amount, t.total_net
        FROM win w JOIN tops t USING (sector_code)
        ORDER BY t.total_net DESC NULLS LAST, w.sector_code, w.trade_date""",
        [chain, *dates, *ct_params, top]).fetchall()
    idx = {d: i for i, d in enumerate(dates)}
    sectors: list[dict[str, Any]] = []
    by_code: dict[str, dict[str, Any]] = {}
    for code, name, td, net, total in rows:
        sec = by_code.get(code)
        if sec is None:
            sec = {"sector_code": code, "sector_name": name,
                   "total_net_amount": total, "values": [None] * len(dates)}
            by_code[code] = sec
            sectors.append(sec)
        sec["values"][idx[td]] = net
    return {"status": "ok", "chain": chain, "content_type": content_type,
            "dates": dates, "sectors": sectors}


_ROTATION_COLS = ["sector_code", "sector_name", "trade_date", "rs_4w", "rs_12w", "rs_rank_4w"]
# dc 轮动列 (v2): 资金流排名迁移 + 双龙头 + 宽度; "leading" 是 DuckDB 保留字必须引号
_DC_ROTATION_COLS = ["sector_code", "sector_name", "content_type", "trade_date", "pct_change",
                     "net_amount", "rank_flow", "inflow_breadth",
                     '"leading"', "leading_pct", "flow_leader_stock"]


def _rotation_sw(conn, lag: int) -> dict[str, Any]:
    """sw 链 31 L1 的 RS 双窗 + 排名: 最新入库日 vs lag 个交易日前 (v1 原样)。"""
    dates = _recent_dates(conn, mp.CHAIN_SW, lag + 1)  # 升序; 末位=最新
    if not dates:
        return {"status": "ok", "chain": mp.CHAIN_SW,
                "latest_date": None, "prev_date": None, "sectors": []}
    latest = dates[-1]
    prev = dates[0] if len(dates) > 1 else None
    rows = conn.execute(f"""
        SELECT {', '.join(_ROTATION_COLS)} FROM {mp.SECTOR_TABLE}
        WHERE chain = '{mp.CHAIN_SW}' AND trade_date IN (?, ?)
        ORDER BY sector_code""", [latest, prev or latest]).fetchall()
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(zip(_ROTATION_COLS, r))
        code = d["sector_code"]
        sec = merged.setdefault(code, {
            "sector_code": code, "sector_name": d["sector_name"],
            "rs_4w": None, "rs_12w": None, "rs_rank_4w": None,
            "prev_rs_4w": None, "prev_rs_12w": None, "prev_rs_rank_4w": None})
        prefix = "" if d["trade_date"] == latest else "prev_"
        for k in ("rs_4w", "rs_12w", "rs_rank_4w"):
            sec[f"{prefix}{k}"] = d[k]
    sectors = sorted(merged.values(),
                     key=lambda s: (s["rs_rank_4w"] is None, s["rs_rank_4w"], s["sector_code"]))
    return {"status": "ok", "chain": mp.CHAIN_SW,
            "latest_date": latest, "prev_date": prev, "sectors": sectors}


def _rotation_dc(conn, lag: int, top: int) -> dict[str, Any]:
    """dc 链资金流轮动 (v2): rank_flow 迁移 + 涨幅龙头/资金龙头/流入宽度。rank_flow 是
    dc 全体 (行业+概念) 截面排名, 链内原生序 — 不做 RS (vendor 红线, rs_* dc 恒 NULL)。"""
    dates = _recent_dates(conn, mp.CHAIN_DC, lag + 1)
    if not dates:
        return {"status": "ok", "chain": mp.CHAIN_DC,
                "latest_date": None, "prev_date": None, "sectors": []}
    latest = dates[-1]
    prev = dates[0] if len(dates) > 1 else None
    rows = conn.execute(f"""
        SELECT {', '.join(_DC_ROTATION_COLS)} FROM {mp.SECTOR_TABLE}
        WHERE chain = '{mp.CHAIN_DC}' AND trade_date IN (?, ?)
        ORDER BY sector_code""", [latest, prev or latest]).fetchall()
    keys = [c.strip('"') for c in _DC_ROTATION_COLS]
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(zip(keys, r))
        code = d["sector_code"]
        sec = merged.setdefault(code, {
            "sector_code": code, "sector_name": d["sector_name"],
            "content_type": d["content_type"],
            "pct_change": None, "net_amount": None, "rank_flow": None, "prev_rank_flow": None,
            "inflow_breadth": None, "leading": None, "leading_pct": None,
            "flow_leader_stock": None})
        if d["trade_date"] == latest:
            for k in ("pct_change", "net_amount", "rank_flow", "inflow_breadth",
                      "leading", "leading_pct", "flow_leader_stock"):
                sec[k] = d[k]
        else:
            sec["prev_rank_flow"] = d["rank_flow"]
    sectors = sorted(merged.values(),
                     key=lambda s: (s["rank_flow"] is None, s["rank_flow"], s["sector_code"]))
    return {"status": "ok", "chain": mp.CHAIN_DC, "latest_date": latest, "prev_date": prev,
            "sectors": sectors[:top]}


@router.get("/rotation")
def rotation(chain: str = mp.CHAIN_SW,
             lag: int = Query(default=5, ge=1, le=60),
             top: int = Query(default=20, ge=1, le=200),
             conn=Depends(get_pulse_conn)):
    """板块轮动: 最新入库日 vs lag 个交易日前 (默认 5 ≈ 上周同字段), 供前端画排名迁移箭头。
    历史不足 lag 时取链内最早入库日兜底。chain=sw (默认, v1 契约不变) / dc (v2 资金流轮动,
    top 截断防 1000+ 板块爆载; top 参数 sw 链不适用 — 31 个 L1 全量返回)。"""
    _require_chain(chain)
    if chain == mp.CHAIN_SW:
        return _rotation_sw(conn, lag)
    return _rotation_dc(conn, lag, top)


_QUIET_COLS = ["chain", "sector_code", "sector_name", "trade_date", "pct_change",
               "net_amount", "quiet_inflow_days", "quiet_outflow_days"]


@router.get("/quiet")
def quiet(limit: int = Query(default=50, ge=1, le=500),
          conn=Depends(get_pulse_conn)):
    """悄悄流入/流出榜: 各链最新入库日 quiet_*_days > 0 的板块 (连续天数降序 + 净额)。
    quiet_* 为 dc 链专属列 (sw 恒 NULL, 天然不出现在榜内)。"""
    rows = conn.execute(f"""
        SELECT {', '.join(_QUIET_COLS)} FROM {mp.SECTOR_TABLE} p
        WHERE p.trade_date = (
            SELECT MAX(trade_date) FROM {mp.SECTOR_TABLE} WHERE chain = p.chain)
          AND (quiet_inflow_days > 0 OR quiet_outflow_days > 0)""").fetchall()
    recs = [dict(zip(_QUIET_COLS, r)) for r in rows]
    inflow = sorted((r for r in recs if (r["quiet_inflow_days"] or 0) > 0),
                    key=lambda r: (-r["quiet_inflow_days"], -(r["net_amount"] or 0)))[:limit]
    outflow = sorted((r for r in recs if (r["quiet_outflow_days"] or 0) > 0),
                     key=lambda r: (-r["quiet_outflow_days"], r["net_amount"] or 0))[:limit]
    return {"status": "ok", "inflow": inflow, "outflow": outflow}


_SENTIMENT_COLS = ["trade_date", "mkt_net_amount", "limit_up_total", "limit_down_total",
                   "zha_ban_rate", "adv_dec_ratio",
                   # v2 情绪周期/水位 (limit_list_d 族 + 两融 + 大盘估值换手 + 龙虎榜)
                   "max_limit_times", "limit_times_dist_json", "promotion_rate",
                   "sec_board_n", "avg_fd_amount", "open_times_total",
                   "rzrqye", "rzrqye_chg", "mkt_pe", "mkt_turnover",
                   "lhb_count", "lhb_inst_net"]


@router.get("/sentiment")
def sentiment(days: int = Query(default=120, ge=1, le=2000),
              conn=Depends(get_pulse_conn)):
    """全市场情绪温度时序 (最近 N 个入库日, 升序): 涨跌停家数/炸板率/涨跌比/大盘净流
    + v2 连板天梯/晋级率/秒板/封单/两融/大盘PE换手/龙虎榜。
    缺源日字段 = null (不知道≠0, 引擎口径), 前端按缺口断线展示。"""
    rows = conn.execute(f"""
        SELECT {', '.join(_SENTIMENT_COLS)} FROM (
            SELECT * FROM {mp.MARKET_TABLE} ORDER BY trade_date DESC LIMIT ?)
        ORDER BY trade_date ASC""", [days]).fetchall()
    return {"status": "ok", "days": [dict(zip(_SENTIMENT_COLS, r)) for r in rows]}


@router.get("/strongest")
def strongest(conn=Depends(get_pulse_conn)):
    """最强板块榜 (limit_cpt_list 引擎快照 strongest_sectors_json, rank 升序):
    取最近一个有榜的入库日 (冰点日源端无榜合法)。885xxx.TI 同花顺码 — 独立展示卡,
    禁与 dc/sw 任何链 JOIN (设计 v2 第 4 条红线)。"""
    row = conn.execute(f"""
        SELECT trade_date, strongest_sectors_json FROM {mp.MARKET_TABLE}
        WHERE strongest_sectors_json IS NOT NULL
        ORDER BY trade_date DESC LIMIT 1""").fetchone()
    if row is None:
        return {"status": "ok", "trade_date": None, "sectors": []}
    return {"status": "ok", "trade_date": row[0], "sectors": json.loads(row[1])}


@router.get("/members")
def members(sector_code: str,
            chain: str = mp.CHAIN_DC,
            conn=Depends(get_members_conn)):
    """板块成分下钻 (v2 第 6 条): dc = dc_member 该板块最新快照日成分;
    sw = index_member_all 当前成分 (is_new='Y' 快照 — 展示口径, 非 PIT; 特征侧另走
    v_sw_industry_pit, 本端点零入模)。未知板块码 → 200 + 空成分 (不猜)。"""
    _require_chain(chain)
    if chain == mp.CHAIN_DC:
        as_of_row = conn.execute(
            "SELECT MAX(trade_date) FROM tr.raw_tushare_dc_member WHERE ts_code = ?",
            [sector_code]).fetchone()
        as_of = as_of_row[0] if as_of_row else None
        if as_of is None:
            return {"status": "ok", "chain": chain, "sector_code": sector_code,
                    "as_of": None, "members": []}
        rows = conn.execute("""
            SELECT con_code, name FROM tr.raw_tushare_dc_member
            WHERE ts_code = ? AND trade_date = ? ORDER BY con_code""",
            [sector_code, as_of]).fetchall()
        return {"status": "ok", "chain": chain, "sector_code": sector_code, "as_of": as_of,
                "members": [{"con_code": r[0], "name": r[1]} for r in rows]}
    rows = conn.execute("""
        SELECT DISTINCT ts_code, name FROM tr.raw_tushare_index_member_all
        WHERE l1_code = ? AND is_new = 'Y' ORDER BY ts_code""", [sector_code]).fetchall()
    return {"status": "ok", "chain": chain, "sector_code": sector_code, "as_of": None,
            "members": [{"con_code": r[0], "name": r[1]} for r in rows]}


@router.get("/warnings")
def warnings(conn=Depends(get_pulse_conn)):
    """退潮预警 (描述性, 非操作建议):
    1) rank_dropouts: 前一入库日 rs_rank_4w <= top_n_sectors 而最新日 > top_n_sectors 的
       sw 板块 (跌出 RS top-N, aleabitoreddit 警示信号的 A股版);
    2) quiet_outflows: dc 链最新日 quiet_outflow_days >= warning_quiet_outflow_days 的板块。"""
    cfg = _load_cfg()
    rank_top = int(cfg["top_n_sectors"])                       # from yaml: top_n_sectors
    outflow_min = int(cfg["warning_quiet_outflow_days"])       # from yaml: warning_quiet_outflow_days
    sw_dates = _recent_dates(conn, mp.CHAIN_SW, 2)
    dropouts: list[dict[str, Any]] = []
    if len(sw_dates) == 2:
        prev_d, latest_d = sw_dates
        cols = ["sector_code", "sector_name", "prev_rank", "latest_rank", "rs_4w"]
        dropouts = [dict(zip(cols, r), prev_date=prev_d, latest_date=latest_d)
                    for r in conn.execute(f"""
            SELECT c.sector_code, c.sector_name, p.rs_rank_4w, c.rs_rank_4w, c.rs_4w
            FROM {mp.SECTOR_TABLE} c
            JOIN {mp.SECTOR_TABLE} p
              ON p.chain = c.chain AND p.sector_code = c.sector_code AND p.trade_date = ?
            WHERE c.chain = '{mp.CHAIN_SW}' AND c.trade_date = ?
              AND p.rs_rank_4w <= ? AND c.rs_rank_4w > ?
            ORDER BY p.rs_rank_4w, c.sector_code""",
            [prev_d, latest_d, rank_top, rank_top]).fetchall()]
    out_cols = ["sector_code", "sector_name", "trade_date", "quiet_outflow_days",
                "net_amount", "pct_change"]
    outflows = [dict(zip(out_cols, r)) for r in conn.execute(f"""
        SELECT sector_code, sector_name, trade_date, quiet_outflow_days, net_amount, pct_change
        FROM {mp.SECTOR_TABLE} p
        WHERE p.chain = '{mp.CHAIN_DC}'
          AND p.trade_date = (
              SELECT MAX(trade_date) FROM {mp.SECTOR_TABLE} WHERE chain = '{mp.CHAIN_DC}')
          AND quiet_outflow_days >= ?
        ORDER BY quiet_outflow_days DESC, net_amount ASC""", [outflow_min]).fetchall()]
    return {"status": "ok",
            "thresholds": {"rank_top": rank_top, "quiet_outflow_days": outflow_min},
            "rank_dropouts": dropouts, "quiet_outflows": outflows}
