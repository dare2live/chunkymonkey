"""segments.py — 股票分层模块 (B1 基础前置件, 2026-07-02 master plan §4)

用户定调: 分层前置在所有策略之前 = 数据变量加工基础工作, 每天数据获取之后跑。
**单一计算点**: 策略 cell 定义 / 画像维度 / edge 筛选器全部从 dim_stock_segment_daily 取,
禁止各策略自己算市值段 (第二真相源增殖 = 不变量#4 违规)。

Type A (确定性 PIT 重排): t 日标签只用 <= t 日数据 — daily_basic (circ_mv/turnover_rate 当日
分位) + 行业 as-of t (v_sw_industry_pit) + K线 <= t (rv_pctile 滚动窗, 尾对齐不外推)。
给定输入结果唯一, 无前瞻无策略阈值。分段规则 = config/segments.yaml。

2026-07-02 B2 设计 H8 裁决加列 (波动 regime 轴单一计算点落 B1, 形态模块只消费):
  rv_pctile  = 滚动 rv_return_window 日收益 std 的 rv_pctile_window 日**严格**分位 (tie 不虚高);
  vol_regime = rv_pctile < threshold → low_vol, 否则 high_vol (NULL = 窗口未满, 不知道 != 低波)。
表 = smartmoney L1_foundation。入口: rebuild_all (全量) / build_latest (增量, pipeline process 步每日调)。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

logger = logging.getLogger(__name__)

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "segments.yaml"


def _cfg() -> dict[str, Any]:
    return yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))


def _db(alias: str) -> str:
    return str(get_database_manifest().path_for(alias))


def vol_regime_threshold(cfg: dict[str, Any] | None = None) -> float:
    """vol_regime 二分阈值唯一 owner (B2 E 轴分数派生只读此值, 不自带第二阈值)。"""
    return float((cfg or _cfg())["vol_regime"]["threshold"])


def _attach_sources(con) -> None:
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")
    con.execute(f"ATTACH IF NOT EXISTS '{_db('market')}' AS mkt (READ_ONLY)")


def _case_from_quantile_bands(bands: dict[str, list[float]], rank_col: str) -> str:
    """config 分位段 → SQL CASE (右开区间, 末段闭合)。"""
    items = sorted(bands.items(), key=lambda kv: kv[1][0])
    parts = []
    for i, (name, (lo, hi)) in enumerate(items):
        cond = f"{rank_col} >= {lo} AND {rank_col} " + ("<= " if i == len(items) - 1 else "< ") + str(hi)
        parts.append(f"WHEN {cond} THEN '{name}'")
    return "CASE " + " ".join(parts) + " END"


def _build_sql(where_date: str, cfg: dict[str, Any] | None = None) -> str:
    """分层面板 SELECT。rv 滚动窗永远在**全量 K线历史**上算 (增量插入也一样), where_date 只
    过滤输出日期 — 保证 rv_pctile 跨增量边界与全量重建逐 bit 一致 (同 market_pulse 模式)。"""
    cfg = cfg or _cfg()
    mkt_case = _case_from_quantile_bands(cfg["mktcap_segments"], "mktcap_rank")
    to_case = _case_from_quantile_bands(cfg["turnover_segments"], "turnover_rank")
    vr = cfg["vol_regime"]
    rv_w = int(vr["rv_return_window"])
    pct_w = int(vr["rv_pctile_window"])
    thr = float(vr["threshold"])
    kline = f"mkt.{vr['kline_table']}"
    return f"""
    WITH base AS (
        SELECT substr(d.ts_code, 1, 6) AS stock_code,
               d.trade_date,
               PERCENT_RANK() OVER (PARTITION BY d.trade_date ORDER BY d.circ_mv) AS mktcap_rank,
               PERCENT_RANK() OVER (PARTITION BY d.trade_date ORDER BY d.turnover_rate) AS turnover_rank,
               d.circ_mv, d.turnover_rate
        FROM tr.raw_tushare_daily_basic d
        WHERE {where_date} AND d.circ_mv IS NOT NULL
    ),
    k_ret AS (
        SELECT code, replace(date, '-', '') AS trade_date,
               close / NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date), 0) - 1 AS ret
        FROM {kline}
    ),
    k_rv AS (
        -- 已实现波动率: 滚动 {rv_w} 日收益样本 std; 窗口未满 → NULL (尾对齐不外推)
        SELECT code, trade_date,
               CASE WHEN COUNT(ret) OVER w = {rv_w} THEN STDDEV_SAMP(ret) OVER w END AS rv
        FROM k_ret
        WINDOW w AS (PARTITION BY code ORDER BY trade_date ROWS BETWEEN {rv_w - 1} PRECEDING AND CURRENT ROW)
    ),
    k_rvl AS (
        SELECT code, trade_date, rv,
               list(rv) OVER (PARTITION BY code ORDER BY trade_date
                              ROWS BETWEEN {pct_w - 1} PRECEDING AND CURRENT ROW) AS rvl
        FROM k_rv
    ),
    k_rvp AS (
        -- 严格分位 (B2 medium 裁决: tie 不虚高): 窗口内严格小于当前 rv 的占比
        SELECT code, trade_date,
               CASE WHEN rv IS NOT NULL AND len(list_filter(rvl, x -> x IS NOT NULL)) >= {pct_w}
                    THEN len(list_filter(rvl, x -> x IS NOT NULL AND x < rv))::DOUBLE
                         / len(list_filter(rvl, x -> x IS NOT NULL)) END AS rv_pctile
        FROM k_rvl
    )
    SELECT b.stock_code, b.trade_date,
           {mkt_case} AS mktcap_seg,
           {to_case} AS turnover_seg,
           p.l1_name AS sw_l1,
           b.circ_mv, b.turnover_rate,
           r.rv_pctile,
           CASE WHEN r.rv_pctile IS NULL THEN NULL
                WHEN r.rv_pctile < {thr} THEN 'low_vol' ELSE 'high_vol' END AS vol_regime
    FROM base b
    LEFT JOIN tr.v_sw_industry_pit p
      ON p.stock_code = b.stock_code AND p.in_date <= b.trade_date
     AND (p.out_date IS NULL OR p.out_date > b.trade_date)
    LEFT JOIN k_rvp r
      ON r.code = b.stock_code AND r.trade_date = b.trade_date
    """


def rebuild_all(conn=None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """全量历史重建 (data_start 起)。conn=None 自管连接并 ATTACH tr/mkt;
    注入 conn (测试) 时调用方负责 tr.*/mkt.* 可解析 (schema 或 ATTACH)。"""
    cfg = cfg or _cfg()
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=False)
    try:
        if own:
            _attach_sources(con)
        con.execute("DROP TABLE IF EXISTS dim_stock_segment_daily")
        where = "d.trade_date >= '" + str(cfg["data_start"]) + "'"
        con.execute(f"CREATE TABLE dim_stock_segment_daily AS {_build_sql(where, cfg)}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_seg_code_date ON dim_stock_segment_daily(stock_code, trade_date)")
        n, days = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date) FROM dim_stock_segment_daily").fetchone()
        if own:
            con.execute("CHECKPOINT")
        out = {"rows": n, "days": days}
        logger.info("[segments] rebuild_all: %s", out)
        return out
    finally:
        if own:
            con.close()


def _table_has_regime_cols(con) -> bool:
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'dim_stock_segment_daily'"
    ).fetchall()}
    return bool(cols) and {"rv_pctile", "vol_regime"} <= cols


def build_latest(conn=None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """增量: 补 daily_basic 已有而 segment 缺的日期 (幂等; pipeline process 步每日调)。
    表缺失或缺 rv_pctile/vol_regime 列 (2026-07-02 加列前的旧 schema) → 自动走全量重建。"""
    cfg = cfg or _cfg()
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=False)
    try:
        if own:
            _attach_sources(con)
        if not _table_has_regime_cols(con):
            return {"mode": "rebuild", **rebuild_all(conn=con, cfg=cfg)}
        missing = [r[0] for r in con.execute("""
            SELECT DISTINCT d.trade_date FROM tr.raw_tushare_daily_basic d
            WHERE d.trade_date >= ? AND d.trade_date NOT IN (
                SELECT DISTINCT trade_date FROM dim_stock_segment_daily)
            ORDER BY 1""", [cfg["data_start"]]).fetchall()]
        if not missing:
            return {"added_days": 0, "rows": 0}
        date_list = ",".join(f"'{d}'" for d in missing)
        con.execute(
            f"INSERT INTO dim_stock_segment_daily {_build_sql(f'd.trade_date IN ({date_list})', cfg)}")
        n = con.execute("SELECT COUNT(*) FROM dim_stock_segment_daily WHERE trade_date IN "
                        f"({date_list})").fetchone()[0]
        con.commit()
        logger.info("[segments] build_latest: +%d days, %d rows", len(missing), n)
        return {"added_days": len(missing), "rows": n}
    finally:
        if own:
            con.close()


def get_segments(codes: list[str], as_of: str, conn=None) -> dict[str, dict[str, Any]]:
    """as-of 查询 (消费方 helper): code → {mktcap_seg, turnover_seg, sw_l1}。as_of=YYYYMMDD。"""
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=True)
    try:
        ph = ",".join("?" for _ in codes)
        rows = con.execute(f"""
            SELECT stock_code, mktcap_seg, turnover_seg, sw_l1 FROM dim_stock_segment_daily
            WHERE stock_code IN ({ph}) AND trade_date = (
                SELECT MAX(trade_date) FROM dim_stock_segment_daily WHERE trade_date <= ?)""",
            [*codes, as_of]).fetchall()
        return {r[0]: {"mktcap_seg": r[1], "turnover_seg": r[2], "sw_l1": r[3]} for r in rows}
    finally:
        if own:
            con.close()
