"""market_pulse router — Tier2 市场感知 API。

现行边界: docs/MASTER_TOPLEVEL_DESIGN.md；历史设计证据: git log --grep market_pulse_design。

前端契约 (卡片↔API 一一对应, widget 独立取数):
  GET /api/v3/pulse/heatmap     资金热力图 (板块×近N日 net_amount 矩阵, dc_industry 默认;
                                DC 行业/概念由 chain namespace 分开; level 参数默认 L1 —
                                sw 链 v3 起有 net_amount, L2/L3 需显式 level)
  GET /api/v3/pulse/rotation    板块轮动 (sw=RS 双窗排名迁移, v3 加 level 默认 L1 保 v2 契约;
                                dc=资金流排名迁移 + 涨幅/资金双龙头 + 流入宽度)
  GET /api/v3/pulse/flow_board  资金流向榜 (v3, 替代 v1 /quiet): 最新日 flow_regime 非 neutral
                                板块分 流入形态/流出形态 两组, 行带 近cum_window日累计净额 +
                                cum_ratio_20d + mini stripe (逐日净流序列)
  GET /api/v3/pulse/drill       统一层级下钻 (v3): code 空=顶层 (sw L1 / 所选 DC namespace);
                                sw L1→L2→L3→成分股叶子; dc 板块→成分股叶子。叶子行带
                                近20日净流入 / 实时 flow_regime (窗口 SQL, 与引擎共用
                                _flow_annotate_sql) / form_name+is_breakout_event / limit_times。
                                响应带 breadcrumb。
  GET /api/v3/pulse/flow_stripe mini 温度条纹数据 (v3): 单板块近 N 日逐日净流入序列
  GET /api/v3/pulse/sentiment   情绪温度时序 (涨跌停/炸板率/涨跌比 + v2 连板天梯/晋级率/
                                秒板/封单/两融/大盘PE换手/龙虎榜, mart_market_pulse_daily);
                                旁路 population_scope / shadow_reconcile / cutover_allowed
                                + tier12_production_read（Phase C）+ b_pit_mart_production_read
                                （B-pit mart 门；cutover ON→MART_CUTOVER，窗外/缺影 fail-closed→LEGACY mart；
                                产品信任：窗外 EMPTY / 窗内缺 UNTRUSTED / 窗内有证 READY；不改 days 数值）
  GET /api/v3/pulse/warnings    退潮预警 (跌出 RS top-N [v3 锁 L1] + 连续静默流出 >= 阈值)
  GET /api/v3/pulse/strongest   最强板块榜 (limit_cpt_list 引擎快照; 885xxx.TI 码独立卡禁跨链)
  GET /api/v3/pulse/members     板块成分下钻 (dc=dc_member 最新快照; sw=index_member_all 当前成分)

红线: 感知层只描述现状, 零买卖暗示 (设计 §1); pulse 数据只读 services.market_pulse 产出的
两张 display 表 (smartmoney), 本 router 不做任何跨链混算 (drill 叶子层的 form/limit 是
个股市场事实, 非板块 vendor 数据, 两链均可挂; 资金流严格按链: sw 叶=moneyflow,
dc 叶=moneyflow_dc)。阈值走 config/market_pulse.yaml, 不 hardcode。

S6: drill/members/margin leaf 走 ``market_pulse_serve_read`` (DataAccess registry +
resolver ATTACH); form/sentiment 走 production_read resolvers。本文件禁内联 raw 表名。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from services import market_pulse as mp
from services import market_pulse_serve_read as pulse_serve
from services.duck_adapter import connect as duck_connect
from services.data_access import resolver
from services.market_pulse_scope import (
    NORMAL_BREADTH_ABSENCE_KINDS,
    attest_market_pulse_scope,
    classify_breadth_day_absence,
)
from services.market_pulse_shadow_reconcile import reconcile_market_pulse_shadow
from services.margin_pulse_promote_gate import (
    NORMAL_ABSENCE_KINDS,
    classify_margin_day_absence,
    evaluate_margin_pulse_promote_gate,
    load_margin_pulse_promote_config,
)
from services.b_pit_mart_cutover import (
    BPitMartCutoverConfig,
    load_b_pit_mart_cutover_config,
)
from services.market_pulse_b_pit_read import attest_pulse_b_pit_mart_production_read
from services.market_pulse_tier12_read import attest_pulse_tier12_production_read

router = APIRouter()

# Test hooks: production keeps default artifact/config paths.
_TIER12_ARTIFACT_ROOT = None
_TIER12_CUTOVER_CONFIG = None
_TIER12_CONFIG_PATH = None
_B_PIT_ARTIFACT_ROOT = None
_B_PIT_CUTOVER_CONFIG = None
_B_PIT_CONFIG_PATH = None
# F4: test overrides; production loads margin_pulse_promote.yaml (fail-closed defaults).
_MARGIN_PROMOTE_CFG = None


def _margin_promote_cfg():
    if _MARGIN_PROMOTE_CFG is not None:
        return _MARGIN_PROMOTE_CFG
    return load_margin_pulse_promote_config()


def _b_pit_cutover_cfg() -> BPitMartCutoverConfig:
    """Typed B-pit window bounds for breadth EMPTY vs UNTRUSTED classification."""
    if _B_PIT_CUTOVER_CONFIG is not None:
        if isinstance(_B_PIT_CUTOVER_CONFIG, BPitMartCutoverConfig):
            return _B_PIT_CUTOVER_CONFIG
        if isinstance(_B_PIT_CUTOVER_CONFIG, dict):
            raw = _B_PIT_CUTOVER_CONFIG
            if "mart_cutover" not in raw:
                raw = {"mart_cutover": raw}
            return BPitMartCutoverConfig.from_mapping(raw)
    return load_b_pit_mart_cutover_config(_B_PIT_CONFIG_PATH)


def _shadow_reconcile_for_day(conn, trade_date: str) -> dict[str, Any]:
    """Read-only shadow for one day; fail-closed if margin L0 is unavailable.

    When serve cutover is on, shadow uses accepted SSE+SZSE (the serve surface)
    so empty raw no longer blocks promote. Never rewrites mart numbers.
    """
    day = str(trade_date or "").replace("-", "")
    if len(day) != 8 or not day.isdigit():
        return reconcile_market_pulse_shadow(str(trade_date)).as_dict()
    cfg = _margin_promote_cfg()
    if cfg.pulse_source_accepted:
        margin_rows, issue = pulse_serve.load_accepted_margin_rows_for_shadow(
            conn, day, contract_version=cfg.contract_version
        )
        if issue is None and not margin_rows:
            issue = "accepted_margin_empty_for_day"
    else:
        margin_rows, issue = pulse_serve.load_margin_rows_for_shadow(conn, day)
    report = reconcile_market_pulse_shadow(day, margin_rows=margin_rows)
    payload = report.as_dict()
    if issue:
        issues = list(payload["issues"])
        if issue not in issues:
            issues.append(issue)
        payload["issues"] = issues
    return payload


def _margin_coverage_start() -> str:
    """v3 accepted obligation floor from sync_registry (fail-open to far future)."""
    try:
        from services.data_sources import sync_runner

        registry = sync_runner.load_registry()
        spec = sync_runner.domain_spec(registry, "margin")
        contract = (spec.get("dataset_contract") or {}) if isinstance(spec, dict) else {}
        start = str(contract.get("coverage_start") or "").replace("-", "")
        if len(start) == 8 and start.isdigit():
            return start
    except Exception:  # noqa: BLE001 — pulse API must not crash on registry miss
        pass
    return "99999999"


def _promote_gate_for_day(conn, trade_date: str, shadow: dict[str, Any]) -> dict[str, Any]:
    """Product-visible promote gate vs accepted SSE+SZSE; never invents TRUSTED.

    Missing accepted rows are classified: pre-coverage / not-yet-eligible /
    confirmed empty → EMPTY_OK; eligible expected day → UNTRUSTED (real gap).
    """
    day = str(trade_date or "").replace("-", "")
    cfg = _margin_promote_cfg()
    accepted_rows, acc_issue = pulse_serve.load_accepted_margin_rows_for_shadow(
        conn, day, contract_version=cfg.contract_version
    )
    absence_kind = None
    if not accepted_rows:
        # Pulse days are nominal trading days; coverage floor is the main
        # not_expected signal (fixture pre-coverage days → typed EMPTY).
        absence_kind = classify_margin_day_absence(
            day,
            coverage_start=_margin_coverage_start(),
            is_trading_day=True,
        )
    # promote_allowed only when accepted evidence exists (unexpected gap stays closed).
    promote_allowed = bool(cfg.promote_allowed) and bool(accepted_rows)
    report = evaluate_margin_pulse_promote_gate(
        day,
        shadow=shadow,
        accepted_margin_rows=accepted_rows,
        pulse_source_accepted=bool(cfg.pulse_source_accepted),
        promote_allowed=promote_allowed,
        absence_kind=absence_kind,
    )
    payload = report.as_dict()
    if absence_kind is not None:
        payload["absence_kind"] = absence_kind
    if acc_issue:
        notes = list(payload.get("notes") or [])
        if acc_issue not in notes:
            notes.append(acc_issue)
        payload["notes"] = notes
    if cfg.promote_allowed and not accepted_rows:
        notes = list(payload.get("notes") or [])
        if absence_kind in NORMAL_ABSENCE_KINDS:
            notes.append("accepted_absent_typed_empty_normal")
        else:
            notes.append("accepted_missing_on_eligible_day_untrusted")
        payload["notes"] = notes
    return payload


def get_pulse_conn():
    """read_only smartmoney 连接 (两张 pulse 表就在 smartmoney 库内, 无需 ATTACH)。
    测试经 app.dependency_overrides 注入内存 fixture 连接。"""
    con = duck_connect(resolver.db_path("smartmoney"), read_only=True)
    try:
        yield con
    finally:
        con.close()


# Re-export serve-read conn helpers so TestClient dependency_overrides keep working.
get_members_conn = pulse_serve.open_members_conn
get_drill_conn = pulse_serve.open_drill_conn


def _load_cfg() -> dict[str, Any]:
    """阈值真相源 = config/market_pulse.yaml (测试 monkeypatch 本函数解耦生产值)。"""
    return mp._cfg()


_LEVELS = ("L1", "L2", "L3")   # 申万层级词汇 (表内数据词汇, 同表名级常量非阈值)
_REGIMES_IN = ("surge_in", "accum_in_silent", "accum_in_driving")    # 流入形态标签 (v3.1 分类学词汇)
_REGIMES_OUT = ("surge_out", "accum_out_silent", "accum_out_driving")  # 流出形态标签


def _require_chain(chain: str) -> None:
    if chain not in mp.PULSE_CHAINS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown chain: {chain!r} (expect one of {mp.PULSE_CHAINS!r})")


def _require_level(level: str) -> None:
    if level not in _LEVELS:
        raise HTTPException(status_code=400,
                            detail=f"unknown level: {level!r} (expect {list(_LEVELS)})")


def _recent_dates(conn, chain: str, n: int) -> list[str]:
    """链内最近 n 个入库交易日, 升序返回。"""
    rows = conn.execute(
        f"SELECT DISTINCT trade_date FROM {mp.SECTOR_TABLE} WHERE chain = ? "
        "ORDER BY trade_date DESC LIMIT ?", [chain, n]).fetchall()
    return sorted(r[0] for r in rows)


@router.get("/heatmap")
def heatmap(chain: str = mp.CHAIN_DC_INDUSTRY,
            level: str = "L1",
            days: int = Query(default=20, ge=1, le=250),
            top: int = Query(default=40, ge=1, le=200),
            conn=Depends(get_pulse_conn)):
    """板块×近 N 日 net_amount 矩阵。板块按窗口内累计 net_amount 降序取 top 防爆载
    (dc 链 1000+ 板块)。v3: sw 链 net_amount 不再恒 NULL (成分个股全单净流聚合, 与 dc 主力
    口径并列不可比); level 参数默认 L1 (仅 sw 链生效 — 不滤则 L2/L3 行混入破坏 v2 契约;
    dc namespace 无申万层级, 该参数忽略)。东财行业/概念直接由 chain 选择，禁止再用
    content_type 在同一 chain 内二次分流；SW 链按 level 输出申万L1/L2/L3。"""
    _require_chain(chain)
    ct_filter = ""
    ct_params: list[Any] = []
    if chain == mp.CHAIN_SW:
        _require_level(level)
        ct_filter = "AND level = ?"
        ct_params = [level]
    dates = _recent_dates(conn, chain, days)
    if not dates:
        return {"status": "ok", "chain": chain, "dates": [], "sectors": []}
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
    return {"status": "ok", "chain": chain, "dates": dates, "sectors": sectors}


_ROTATION_COLS = ["sector_code", "sector_name", "trade_date", "rs_4w", "rs_12w", "rs_rank_4w"]
# dc 轮动列 (v2): 资金流排名迁移 + 双龙头 + 宽度; "leading" 是 DuckDB 保留字必须引号
_DC_ROTATION_COLS = ["sector_code", "sector_name", "content_type", "trade_date", "pct_change",
                     "net_amount", "rank_flow", "inflow_breadth",
                     '"leading"', "leading_pct", "flow_leader_stock"]


def _rotation_sw(conn, lag: int, level: str) -> dict[str, Any]:
    """sw 链 RS 双窗 + 排名: 最新入库日 vs lag 个交易日前 (v1 原样)。
    v3: level 过滤 (默认 L1 = v2 契约 31 行不变; rs_rank_4w 引擎侧已按同级分区)。"""
    dates = _recent_dates(conn, mp.CHAIN_SW, lag + 1)  # 升序; 末位=最新
    if not dates:
        return {"status": "ok", "chain": mp.CHAIN_SW, "level": level,
                "latest_date": None, "prev_date": None, "sectors": []}
    latest = dates[-1]
    prev = dates[0] if len(dates) > 1 else None
    rows = conn.execute(f"""
        SELECT {', '.join(_ROTATION_COLS)} FROM {mp.SECTOR_TABLE}
        WHERE chain = '{mp.CHAIN_SW}' AND level = ? AND trade_date IN (?, ?)
        ORDER BY sector_code""", [level, latest, prev or latest]).fetchall()
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
    return {"status": "ok", "chain": mp.CHAIN_SW, "level": level,
            "latest_date": latest, "prev_date": prev, "sectors": sectors}


def _rotation_dc(conn, chain: str, lag: int, top: int) -> dict[str, Any]:
    """单个 DC namespace 资金流轮动；行业与概念各自在自己的截面内排名。"""
    dates = _recent_dates(conn, chain, lag + 1)
    if not dates:
        return {"status": "ok", "chain": chain,
                "latest_date": None, "prev_date": None, "sectors": []}
    latest = dates[-1]
    prev = dates[0] if len(dates) > 1 else None
    rows = conn.execute(f"""
        SELECT {', '.join(_DC_ROTATION_COLS)} FROM {mp.SECTOR_TABLE}
        WHERE chain = ? AND trade_date IN (?, ?)
        ORDER BY sector_code""", [chain, latest, prev or latest]).fetchall()
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
    return {"status": "ok", "chain": chain, "latest_date": latest, "prev_date": prev,
            "sectors": sectors[:top]}


@router.get("/rotation")
def rotation(chain: str = mp.CHAIN_SW,
             level: str = "L1",
             lag: int = Query(default=5, ge=1, le=60),
             top: int = Query(default=20, ge=1, le=200),
             conn=Depends(get_pulse_conn)):
    """板块轮动: 最新入库日 vs lag 个交易日前 (默认 5 ≈ 上周同字段), 供前端画排名迁移箭头。
    历史不足 lag 时取链内最早入库日兜底。chain=sw (默认; v3 加 level 默认 L1 — 不滤则 v3
    新增的 L2/L3 行破坏 v2 的 31 行契约) / dc (v2 资金流轮动, top 截断防 1000+ 板块爆载;
    top/level 参数 dc 链不适用)。"""
    _require_chain(chain)
    if chain == mp.CHAIN_SW:
        _require_level(level)
        return _rotation_sw(conn, lag, level)
    return _rotation_dc(conn, chain, lag, top)


# 资金流向榜行列 (v3): flow_regime 形态 + 量级 (cum_net/cum_ratio) + 当日值
_BOARD_COLS = ["chain", "sector_code", "sector_name", "level", "content_type", "trade_date",
               "pct_change", "net_amount", "flow_z", "flow_streak", "cum_ratio_20d",
               "flow_regime", "cum_net"]


@router.get("/flow_board")
def flow_board(chain: str = mp.CHAIN_DC_INDUSTRY,
               level: str = "L1",
               limit: int = Query(default=20, ge=1, le=500),
               stripe_days: int = Query(default=60, ge=0, le=250),
               conn=Depends(get_pulse_conn)):
    """资金流向榜 (v3, 替代 v1 /quiet 榜): 链内最新入库日 flow_regime 非 neutral 的板块,
    分 流入形态 (surge_in/accum_in_*) / 流出形态 (镜像) 两组。
    行带: 近 cum_window 日累计净额 (cum_net, 元 — 窗口 SUM 就地算, 不满窗按已有行和) +
    cum_ratio_20d (满窗才有, 引擎口径) + mini stripe (近 stripe_days 日逐日净流序列,
    与响应级 stripe_dates 对齐; stripe_days=0 关闭)。
    排序: 流入按 cum_ratio 降序 (NULL 沉底, 次序 cum_net 降序); 流出镜像升序。
    level 仅 sw 链生效 (默认 L1); dc 链忽略。"""
    _require_chain(chain)
    cfg = _load_cfg()
    cw = int(cfg["cum_window"])                      # from yaml: cum_window
    lvl_filter, params = "", [chain]
    if chain == mp.CHAIN_SW:
        _require_level(level)
        lvl_filter = "AND level = ?"
        params.append(level)
    rows = conn.execute(f"""
        WITH hist AS (
            SELECT chain, sector_code, sector_name, level, content_type, trade_date,
                   pct_change, net_amount, flow_z, flow_streak, cum_ratio_20d, flow_regime,
                   SUM(net_amount) OVER (PARTITION BY sector_code ORDER BY trade_date
                       ROWS BETWEEN {cw - 1} PRECEDING AND CURRENT ROW) AS cum_net
            FROM {mp.SECTOR_TABLE} WHERE chain = ? {lvl_filter}
        )
        SELECT {', '.join(_BOARD_COLS)} FROM hist
        WHERE trade_date = (SELECT MAX(trade_date) FROM hist)
          AND flow_regime IS NOT NULL AND flow_regime != 'neutral'""", params).fetchall()
    recs = [dict(zip(_BOARD_COLS, r)) for r in rows]
    latest = recs[0]["trade_date"] if recs else None
    nulls_last = lambda v, desc: (v is None, (-v if desc else v) if v is not None else 0)  # noqa: E731
    inflow = sorted((r for r in recs if r["flow_regime"] in _REGIMES_IN),
                    key=lambda r: (*nulls_last(r["cum_ratio_20d"], True),
                                   *nulls_last(r["cum_net"], True)))[:limit]
    outflow = sorted((r for r in recs if r["flow_regime"] in _REGIMES_OUT),
                     key=lambda r: (*nulls_last(r["cum_ratio_20d"], False),
                                    *nulls_last(r["cum_net"], False)))[:limit]
    stripe_dates: list[str] = []
    if stripe_days > 0 and (inflow or outflow):
        stripe_dates = _recent_dates(conn, chain, stripe_days)
        codes = [r["sector_code"] for r in inflow + outflow]
        ph_c, ph_d = ",".join("?" * len(codes)), ",".join("?" * len(stripe_dates))
        idx = {d: i for i, d in enumerate(stripe_dates)}
        stripes: dict[str, list[Any]] = {c: [None] * len(stripe_dates) for c in codes}
        for code, td, net in conn.execute(f"""
            SELECT sector_code, trade_date, net_amount FROM {mp.SECTOR_TABLE}
            WHERE chain = ? AND sector_code IN ({ph_c}) AND trade_date IN ({ph_d})""",
                [chain, *codes, *stripe_dates]).fetchall():
            stripes[code][idx[td]] = net
        for r in inflow + outflow:
            r["stripe"] = stripes[r["sector_code"]]
    else:
        for r in inflow + outflow:
            r["stripe"] = []
    return {"status": "ok", "chain": chain, "trade_date": latest,
            "stripe_dates": stripe_dates, "inflow": inflow, "outflow": outflow}


@router.get("/flow_stripe")
def flow_stripe(code: str,
                chain: str = mp.CHAIN_DC_INDUSTRY,
                days: int = Query(default=60, ge=1, le=250),
                conn=Depends(get_pulse_conn)):
    """mini 温度条纹数据 (v3.3): 单板块近 N 日逐日净流入序列 (升序; 红入绿出由前端着色)。
    未知板块码 → 200 + 空序列 (不猜)。"""
    _require_chain(chain)
    rows = conn.execute(f"""
        SELECT trade_date, net_amount, sector_name FROM (
            SELECT trade_date, net_amount, sector_name FROM {mp.SECTOR_TABLE}
            WHERE chain = ? AND sector_code = ? ORDER BY trade_date DESC LIMIT ?)
        ORDER BY trade_date ASC""", [chain, code, days]).fetchall()
    return {"status": "ok", "chain": chain, "sector_code": code,
            "sector_name": rows[-1][2] if rows else None,
            "dates": [r[0] for r in rows], "values": [r[1] for r in rows]}


# ── v3 统一层级下钻 ─────────────────────────────────────────────────────────

# 板块层下钻行列 (mart 直读; dc/sw 共 schema, 不适用列 NULL)
_DRILL_SECTOR_COLS = ["sector_code", "sector_name", "level", "content_type", "trade_date",
                      "pct_change", "net_amount", "rank_flow", "rs_4w", "rs_12w", "rs_rank_4w",
                      "flow_z", "flow_streak", "cum_ratio_20d", "flow_regime"]


def _mart_asof(conn, chain: str, as_of: str) -> str | None:
    r = conn.execute(
        f"SELECT MAX(trade_date) FROM {mp.SECTOR_TABLE} WHERE chain = ? AND trade_date <= ?",
        [chain, as_of]).fetchone()
    return r[0] if r else None


def _drill_sector_rows(conn, chain: str, date: str | None, codes: list[str] | None,
                       ct_filter: str = "", ct_params: list[Any] | None = None,
                       top: int | None = None) -> tuple[str | None, list[dict[str, Any]]]:
    """mart 板块行 (下钻某一层): codes=None 取整层 (调用方经 ct_filter/level 限定), 否则限定码集。"""
    d = _mart_asof(conn, chain, date or "99999999")
    if d is None:
        return None, []
    where, params = f"chain = ? AND trade_date = ? {ct_filter}", [chain, d, *(ct_params or [])]
    if codes is not None:
        if not codes:
            return d, []
        where += " AND sector_code IN (%s)" % ",".join("?" * len(codes))
        params += codes
    order = ("ORDER BY (rank_flow IS NULL), rank_flow, sector_code" if chain in mp.DC_CHAINS
             else "ORDER BY (rs_rank_4w IS NULL), rs_rank_4w, sector_code")
    lim = f"LIMIT {int(top)}" if top else ""
    rows = conn.execute(f"""
        SELECT {', '.join(_DRILL_SECTOR_COLS)} FROM {mp.SECTOR_TABLE}
        WHERE {where} {order} {lim}""", params).fetchall()
    return d, [dict(zip(_DRILL_SECTOR_COLS, r)) for r in rows]


@router.get("/drill")
def drill(chain: str = mp.CHAIN_SW,
          code: str | None = None,
          date: str | None = None,
          top: int = Query(default=100, ge=1, le=1000),
          conn=Depends(get_drill_conn)):
    """统一层级下钻 (v3.2): code 空 = 顶层 (sw L1 列表 / 单个 DC namespace 板块列表);
    sw L1 码 → L2 行列表 → L3 行列表 → 成分股叶子 (index_member_all as-of PIT);
    dc 板块码 → 成分股叶子 (dc_member 最新快照)。响应带 breadcrumb (根→当前节点)。
    vendor 红线: sw 叶资金流 = moneyflow (tushare 全单口径), dc 叶 =
    moneyflow_dc (东财主力口径), 禁跨; form/limit 为个股市场事实两链共用。
    未知码 → 200 + 空行 (不猜)。top 只作用于顶层列表 (dc 概念 500+ 防爆载)。"""
    _require_chain(chain)
    cfg = _load_cfg()
    # Keep serve-read tier12 hooks in sync with router test overrides.
    pulse_serve._TIER12_ARTIFACT_ROOT = _TIER12_ARTIFACT_ROOT
    pulse_serve._TIER12_CUTOVER_CONFIG = _TIER12_CUTOVER_CONFIG
    pulse_serve._TIER12_CONFIG_PATH = _TIER12_CONFIG_PATH
    as_of = date or "99999999"
    if chain in mp.DC_CHAINS:
        if code is None:
            d, rows = _drill_sector_rows(conn, chain, date, None, top=top)
            return {"status": "ok", "chain": chain, "date": d, "rows_level": "sector",
                    "breadcrumb": [], "rows": rows}
        snap = pulse_serve.dc_member_snap(conn, code, as_of)
        name_r = conn.execute(
            f"SELECT sector_name FROM {mp.SECTOR_TABLE} WHERE chain = ? AND sector_code = ? LIMIT 1",
            [chain, code]).fetchone()
        crumb = [{"code": code, "name": name_r[0] if name_r else None, "level": "sector"}]
        if snap is None:
            return {"status": "ok", "chain": chain, "date": None, "rows_level": "stock",
                    "breadcrumb": crumb, "member_as_of": None, "rows": []}
        rows = pulse_serve.drill_leaf_rows(
            conn, cfg,
            mem_sql=pulse_serve.dc_member_mem_sql(),
            mem_params=[code, snap],
            flow_sql=pulse_serve.dc_flow_sql(as_of),
            as_of=as_of)
        return {"status": "ok", "chain": chain, "date": snap, "rows_level": "stock",
                "breadcrumb": crumb, "member_as_of": snap, "rows": rows}
    # ── sw 链 ──
    if code is None:
        d, rows = _drill_sector_rows(conn, chain, date, None, "AND level = 'L1'", [], top)
        return {"status": "ok", "chain": chain, "date": d, "rows_level": "L1",
                "breadcrumb": [], "rows": rows}
    lvl, names = pulse_serve.sw_code_level(conn, code)
    if lvl is None:
        return {"status": "ok", "chain": chain, "date": None, "rows_level": None,
                "breadcrumb": [], "rows": []}
    crumb = [{"code": names[f"{c}_code"], "name": names[f"{c}_name"], "level": c.upper()}
             for c in ("l1", "l2", "l3") if c.upper() <= lvl and names.get(f"{c}_code")]
    if lvl in ("L1", "L2"):
        child = "L2" if lvl == "L1" else "L3"
        kids = pulse_serve.sw_child_codes(conn, code, lvl)
        kid_codes = [k[0] for k in kids]
        d, rows = _drill_sector_rows(conn, chain, date, kid_codes)
        have = {r["sector_code"] for r in rows}
        # 无行情行的子节点也列出 (成分在册但 sw_daily 无该码 → 指标 NULL, 不隐藏)
        rows += [dict(zip(_DRILL_SECTOR_COLS,
                          [k[0], k[1], child] + [None] * (len(_DRILL_SECTOR_COLS) - 3)))
                 for k in kids if k[0] not in have]
        return {"status": "ok", "chain": chain, "date": d, "rows_level": child,
                "breadcrumb": crumb, "rows": rows}
    rows = pulse_serve.drill_leaf_rows(
        conn, cfg,
        mem_sql=pulse_serve.sw_member_mem_sql(),
        mem_params=[code, as_of, as_of],
        flow_sql=pulse_serve.sw_flow_sql(as_of),
        as_of=as_of)
    return {"status": "ok", "chain": chain, "date": _mart_asof(conn, chain, as_of),
            "rows_level": "stock", "breadcrumb": crumb, "rows": rows}


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
    缺源日字段 = null (不知道≠0, 引擎口径), 前端按缺口断线展示。

    B-ext: 旁路 ``population_scope`` + ``shadow_reconcile``。F4: ``promote_gate``
    对 accepted SSE+SZSE 影子+门标准；serve cutover + promote 消费后 rzrqye 可
    READY（仍 external_aggregate，禁 project_universe / 假 TRUSTED）。breadth
    仅在 B-pit ``MART_CUTOVER`` 证据下 READY（project_universe_pit）；窗外/未到期
    → typed EMPTY（正常空，同 rzrqye 覆盖前）；窗内应有却缺 → UNTRUSTED。
    Phase C: ``tier12_production_read`` 经 ``resolve_tier12_production_read``；
    cutover ON → ACCEPTED_CUTOVER，缺 accept fail-closed → LEGACY。B-pit:
    ``b_pit_mart_production_read`` 经 ``resolve_b_pit_mart_production_read``；
    cutover ON → MART_CUTOVER，窗外/缺影 fail-closed → LEGACY mart（产品信任层
    窗外=EMPTY，窗内缺=UNTRUSTED）。``days`` 数值不改。
    """
    rows = conn.execute(f"""
        SELECT {', '.join(_SENTIMENT_COLS)} FROM (
            SELECT * FROM {mp.MARKET_TABLE} ORDER BY trade_date DESC LIMIT ?)
        ORDER BY trade_date ASC""", [days]).fetchall()
    day_rows = [dict(zip(_SENTIMENT_COLS, r)) for r in rows]
    latest = str(day_rows[-1]["trade_date"]) if day_rows else ""
    cfg = _margin_promote_cfg()
    tier12 = attest_pulse_tier12_production_read(
        latest,
        config=_TIER12_CUTOVER_CONFIG,
        artifact_root=_TIER12_ARTIFACT_ROOT,
        config_path=_TIER12_CONFIG_PATH,
    )
    b_pit = attest_pulse_b_pit_mart_production_read(
        latest,
        config=_B_PIT_CUTOVER_CONFIG,
        artifact_root=_B_PIT_ARTIFACT_ROOT,
        config_path=_B_PIT_CONFIG_PATH,
    )
    if latest:
        shadow = _shadow_reconcile_for_day(conn, latest)
        promote_gate = _promote_gate_for_day(conn, latest, shadow)
        margin_empty = promote_gate.get("status") == "EMPTY_OK"
        breadth_promoted = (
            b_pit.get("status") == "MART_CUTOVER"
            and b_pit.get("source") == "project_universe_pit"
        )
        breadth_empty = False
        breadth_empty_reason = None
        if not breadth_promoted:
            bcfg = _b_pit_cutover_cfg()
            absence = classify_breadth_day_absence(
                latest,
                window_start=bcfg.expected_window_start,
                window_end=bcfg.expected_window_end,
                is_trading_day=True,
            )
            # Resolver may also stamp outside-window; treat as not_expected.
            for reason in b_pit.get("reasons") or ():
                if "trade_date_outside_shadow_window" in str(reason):
                    absence = "not_expected"
                    break
            if absence in NORMAL_BREADTH_ABSENCE_KINDS:
                breadth_empty = True
                breadth_empty_reason = f"typed_empty_{absence}"
        scope = attest_market_pulse_scope(
            latest,
            margin_source_accepted=bool(cfg.pulse_source_accepted),
            margin_promoted=promote_gate.get("status") == "PROMOTED",
            margin_empty=margin_empty,
            margin_empty_reason=(
                f"typed_empty_{promote_gate.get('absence_kind')}"
                if margin_empty
                else None
            ),
            breadth_promoted=breadth_promoted,
            breadth_empty=breadth_empty,
            breadth_empty_reason=breadth_empty_reason,
        ).as_dict()
    else:
        scope = {
            "trade_date": "",
            "overall_status": "NOT_EVALUATED",
            "fields": [],
            "notes": ["no_sentiment_rows"],
        }
        shadow = reconcile_market_pulse_shadow("").as_dict()
        shadow["issues"] = list(shadow.get("issues") or []) + ["no_sentiment_rows"]
        promote_gate = evaluate_margin_pulse_promote_gate(
            "",
            shadow=shadow,
            accepted_margin_rows=(),
            pulse_source_accepted=bool(cfg.pulse_source_accepted),
            promote_allowed=False,
        ).as_dict()
    return {
        "status": "ok",
        "days": day_rows,
        "population_scope": scope,
        "shadow_reconcile": shadow,
        "promote_gate": promote_gate,
        "cutover_allowed": bool(tier12.get("cutover_allowed", False)),
        "tier12_production_read": tier12,
        "b_pit_mart_cutover_allowed": bool(b_pit.get("cutover_allowed", False)),
        "b_pit_mart_production_read": b_pit,
    }


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
            chain: str = mp.CHAIN_DC_INDUSTRY,
            conn=Depends(get_members_conn)):
    """板块成分下钻 (v2 第 6 条): dc = dc_member 该板块最新快照日成分;
    sw = index_member_all 当前成分 (is_new='Y' 快照 — 展示口径, 非 PIT; 特征侧另走
    v_sw_industry_pit, 本端点零入模)。未知板块码 → 200 + 空成分 (不猜)。"""
    _require_chain(chain)
    return pulse_serve.list_sector_members(conn, chain=chain, sector_code=sector_code)


@router.get("/warnings")
def warnings(conn=Depends(get_pulse_conn)):
    """退潮预警 (描述性, 非操作建议):
    1) rank_dropouts: 前一入库日 rs_rank_4w <= top_n_sectors 而最新日 > top_n_sectors 的
       sw L1 板块 (跌出 RS top-N; v3 锁 level='L1' — rank 已按同级分区, 不锁则 L2/L3 混入);
    2) quiet_outflows: 两个 DC namespace 各自最新日 quiet_outflow_days 达阈值的板块
       (价稳连续净流出 = 静默派发嫌疑, 引擎 quiet_* 列语义不变)。"""
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
            WHERE c.chain = '{mp.CHAIN_SW}' AND c.level = 'L1' AND c.trade_date = ?
              AND p.rs_rank_4w <= ? AND c.rs_rank_4w > ?
            ORDER BY p.rs_rank_4w, c.sector_code""",
            [prev_d, latest_d, rank_top, rank_top]).fetchall()]
    out_cols = ["chain", "sector_code", "sector_name", "trade_date", "quiet_outflow_days",
                "net_amount", "pct_change"]
    dc_chains = ",".join(mp._sql_str(chain) for chain in mp.DC_CHAINS)
    outflows = [dict(zip(out_cols, r)) for r in conn.execute(f"""
        SELECT chain, sector_code, sector_name, trade_date, quiet_outflow_days, net_amount, pct_change
        FROM {mp.SECTOR_TABLE} p
        WHERE p.chain IN ({dc_chains})
          AND p.trade_date = (
              SELECT MAX(trade_date) FROM {mp.SECTOR_TABLE} WHERE chain = p.chain)
          AND quiet_outflow_days >= ?
        ORDER BY chain, quiet_outflow_days DESC, net_amount ASC""", [outflow_min]).fetchall()]
    return {"status": "ok",
            "thresholds": {"rank_top": rank_top, "quiet_outflow_days": outflow_min},
            "rank_dropouts": dropouts, "quiet_outflows": outflows}
