"""segments.py — 股票分层模块 (B1 基础前置件, 2026-07-02 master plan §4)

用户定调: 分层前置在所有策略之前 = 数据变量加工基础工作, 每天数据获取之后跑。
**单一计算点**: 策略 cell 定义 / 画像维度 / edge 筛选器全部从 dim_stock_segment_daily 取,
禁止各策略自己算市值段 (第二真相源增殖 = 不变量#4 违规)。

Type A (确定性 PIT 重排): t 日标签只用 t 日 daily_basic (circ_mv/turnover_rate 当日分位)
+ 行业 as-of t (v_sw_industry_pit)。给定输入结果唯一, 无前瞻无策略阈值。
分段规则 = config/segments.yaml (分位法, 自适应跨年市值漂移); 表 = smartmoney L1_foundation。
入口: rebuild_all (全量历史) / build_latest (增量, pipeline process 步每日调)。
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


def _case_from_quantile_bands(bands: dict[str, list[float]], rank_col: str) -> str:
    """config 分位段 → SQL CASE (右开区间, 末段闭合)。"""
    items = sorted(bands.items(), key=lambda kv: kv[1][0])
    parts = []
    for i, (name, (lo, hi)) in enumerate(items):
        cond = f"{rank_col} >= {lo} AND {rank_col} " + ("<= " if i == len(items) - 1 else "< ") + str(hi)
        parts.append(f"WHEN {cond} THEN '{name}'")
    return "CASE " + " ".join(parts) + " END"


def _build_sql(where_date: str) -> str:
    cfg = _cfg()
    mkt_case = _case_from_quantile_bands(cfg["mktcap_segments"], "mktcap_rank")
    to_case = _case_from_quantile_bands(cfg["turnover_segments"], "turnover_rank")
    return f"""
    WITH base AS (
        SELECT substr(d.ts_code, 1, 6) AS stock_code,
               d.trade_date,
               PERCENT_RANK() OVER (PARTITION BY d.trade_date ORDER BY d.circ_mv) AS mktcap_rank,
               PERCENT_RANK() OVER (PARTITION BY d.trade_date ORDER BY d.turnover_rate) AS turnover_rank,
               d.circ_mv, d.turnover_rate
        FROM tr.raw_tushare_daily_basic d
        WHERE {where_date} AND d.circ_mv IS NOT NULL
    )
    SELECT b.stock_code, b.trade_date,
           {mkt_case} AS mktcap_seg,
           {to_case} AS turnover_seg,
           p.l1_name AS sw_l1,
           b.circ_mv, b.turnover_rate
    FROM base b
    LEFT JOIN tr.v_sw_industry_pit p
      ON p.stock_code = b.stock_code AND p.in_date <= b.trade_date
     AND (p.out_date IS NULL OR p.out_date > b.trade_date)
    """


def rebuild_all() -> dict[str, Any]:
    """全量历史重建 (data_start 起)。"""
    cfg = _cfg()
    con = duck_connect(_db("smartmoney"), read_only=False)
    try:
        con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")
        con.execute("DROP TABLE IF EXISTS dim_stock_segment_daily")
        where = "d.trade_date >= '" + str(cfg["data_start"]) + "'"
        con.execute(f"CREATE TABLE dim_stock_segment_daily AS {_build_sql(where)}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_seg_code_date ON dim_stock_segment_daily(stock_code, trade_date)")
        n, days = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date) FROM dim_stock_segment_daily").fetchone()
        con.execute("CHECKPOINT")
        out = {"rows": n, "days": days}
        logger.info("[segments] rebuild_all: %s", out)
        return out
    finally:
        con.close()


def build_latest(conn=None) -> dict[str, Any]:
    """增量: 补 daily_basic 已有而 segment 缺的日期 (幂等; pipeline process 步每日调)。"""
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=False)
    try:
        con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")
        missing = [r[0] for r in con.execute("""
            SELECT DISTINCT d.trade_date FROM tr.raw_tushare_daily_basic d
            WHERE d.trade_date >= ? AND d.trade_date NOT IN (
                SELECT DISTINCT trade_date FROM dim_stock_segment_daily)
            ORDER BY 1""", [_cfg()["data_start"]]).fetchall()]
        if not missing:
            return {"added_days": 0, "rows": 0}
        date_list = ",".join(f"'{d}'" for d in missing)
        con.execute(f"INSERT INTO dim_stock_segment_daily {_build_sql(f'd.trade_date IN ({date_list})')}")
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
