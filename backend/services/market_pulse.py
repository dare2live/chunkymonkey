"""market_pulse.py — B4 市场感知引擎 (Follow the Money, 2026-07-02 master plan B4)

设计契约: analysis/market_pulse_design_20260702.md (唯一契约)。
用户定调: "看钱在哪里从哪里流出流向哪里 … 哪里资金悄悄的在流入、哪里悄悄在流出"。

两链并列 (vendor 自洽红线, 任何计算禁跨链 JOIN):
  - dc_concept  链: 东财板块 (行业+概念) 资金流 (moneyflow_ind_dc) × 东财板块广度 (dc_index)。
    专属列: net_amount / elg_amount / rank_flow / quiet_*_days; rs_* / limit_*_n 恒 NULL。
  - sw_industry 链: 申万 L1 行情 (sw_daily) × HS300 基准 (index_daily) 算 RS 双窗;
    广度 (raw_tushare_daily) / 涨跌停 (limit_list_d) 经 B1 (dim_stock_segment_daily.sw_l1) 聚合。
    专属列: rs_4w / rs_12w / rs_rank_4w / limit_*_n / turnover_amt_share; net_amount / quiet_* 恒 NULL。

Type A (确定性 PIT 重排): t 日行只用 <= t 数据 (rolling window 尾对齐, streak 逐日递推),
无策略阈值判断; 全部阈值读 config/market_pulse.yaml (代码零 hardcode)。
感知层只描述现状, 不给买卖暗示 (RS top3 过滤器等信号层候选进 D2 消融另验)。

产出表 (smartmoney, display 层):
  - mart_sector_pulse_daily  板块×日 (chain 字段隔离两链)
  - mart_market_pulse_daily  全市场×日 1 行

入口: rebuild_all (全量) / build_latest (幂等增量; pipeline process 步在 segments B1 之后调 —
B1 表是 sw 链广度/涨跌停聚合的输入, 顺序不可反)。

注 (源字段陷阱): moneyflow_ind_dc 原始 rank 字段 = 分页伪 rank (sync_registry pit_anchor +
INDEX §8 陷阱), 本模块弃用之 — rank_flow 由当日截面 net_amount DESC 重算 (1=流入最强)。

──────────────────────────────────────────────────────────────────────
收编清单 (主会话收编时做, side-agent 不碰控制面文件):
1. backend/config/data_layers.yaml: mart_sector_pulse_daily / mart_market_pulse_daily
   → db=smartmoney, layer=display, 加工类型 Type A (确定性 PIT 重排)。
2. roster/lineage 登记 (data_module_members.yaml): producer=services/market_pulse.py;
   upstream = raw_tushare_moneyflow_ind_dc / raw_tushare_dc_index / raw_tushare_sw_daily /
   raw_tushare_index_daily / raw_tushare_limit_list_d / raw_tushare_daily /
   raw_tushare_moneyflow_mkt_dc / raw_tushare_index_member_all (L1 码表) /
   dim_stock_segment_daily (B1, sw 链聚合桥); consumer = C4 /api/v3/pulse/* (未建)。
3. pipeline process 步挂钩: 建议挂在 segments.build_latest (B1) 之后同一 process 段,
   调用 market_pulse.build_latest() (无参; 幂等, 无新日期时 no-op)。
4. 真实数据 smoke (回填锁释放后主会话跑): rebuild_all() → 抽查
   (a) dc 链行数 ≈ 板块数×交易日 (2024+, 早期仅行业 ~86/日);
   (b) sw 链 31 L1 × 2019+ 交易日, rs_4w 前 20 行 NULL (尾对齐);
   (c) 任一日 SUM(turnover_amt_share)≈1 (sw 链); (d) quiet_inflow_days 抽 1 板块人工核对连续性。
──────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

logger = logging.getLogger(__name__)

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "market_pulse.yaml"

# chain 标识 = 表内数据词汇 (同表名级常量, 非阈值)
CHAIN_DC = "dc_concept"
CHAIN_SW = "sw_industry"
SECTOR_TABLE = "mart_sector_pulse_daily"
MARKET_TABLE = "mart_market_pulse_daily"


def _cfg() -> dict[str, Any]:
    return yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))


def _db(alias: str) -> str:
    return str(get_database_manifest().path_for(alias))


def _sql_str(v: Any) -> str:
    """SQL 单引号字面量 (防注入转义)。"""
    return "'" + str(v).replace("'", "''") + "'"


def _sector_sql(cfg: dict[str, Any], dc_where: str = "1=1", sw_where: str = "1=1") -> str:
    """板块×日面板 SELECT (两链 UNION, 列序=契约序)。

    窗口/streak 永远在**全量源历史**上算 (增量插入也一样), dc_where/sw_where 只在最外层
    过滤输出日期 — 保证 rolling RS / quiet streak 跨增量边界正确 (PIT 尾对齐)。
    dc_where 用 q./i. 限定, sw_where 用 r. 限定。
    """
    w4 = int(cfg["rs_window_4w"])
    w12 = int(cfg["rs_window_12w"])
    band = float(cfg["quiet_px_band_pct"])
    min_amt = float(cfg["quiet_min_net_amount"])
    bench = _sql_str(cfg["benchmark_code"])
    ctypes = ",".join(_sql_str(c) for c in cfg["dc_content_types"])
    dc_start = _sql_str(cfg["data_start_dc"])
    sw_start = _sql_str(cfg["data_start_sw"])
    return f"""
    WITH dc_flow AS (
        -- dc 链底座: 东财板块资金流。rank 原始字段=分页伪 rank, 弃用; rank_flow 当日截面重算。
        SELECT trade_date, ts_code AS sector_code, name AS sector_name,
               pct_change, net_amount, buy_elg_amount AS elg_amount,
               RANK() OVER (PARTITION BY trade_date ORDER BY net_amount DESC NULLS LAST) AS rank_flow,
               CASE WHEN ABS(pct_change) < {band} AND net_amount > {min_amt} THEN 1 ELSE 0 END AS in_flag,
               CASE WHEN ABS(pct_change) < {band} AND net_amount < -{min_amt} THEN 1 ELSE 0 END AS out_flag
        FROM tr.raw_tushare_moneyflow_ind_dc
        WHERE trade_date >= {dc_start} AND content_type IN ({ctypes})
    ),
    dc_grp AS (
        -- gaps-and-islands: rn 差在连续 flag 段内恒定 → 段 id (断一天 flag=0 即换段)
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY sector_code ORDER BY trade_date)
                 - ROW_NUMBER() OVER (PARTITION BY sector_code, in_flag ORDER BY trade_date) AS g_in,
               ROW_NUMBER() OVER (PARTITION BY sector_code ORDER BY trade_date)
                 - ROW_NUMBER() OVER (PARTITION BY sector_code, out_flag ORDER BY trade_date) AS g_out
        FROM dc_flow
    ),
    dc_quiet AS (
        SELECT *,
               CASE WHEN in_flag = 1 THEN ROW_NUMBER() OVER (
                    PARTITION BY sector_code, in_flag, g_in ORDER BY trade_date) ELSE 0 END AS quiet_inflow_days,
               CASE WHEN out_flag = 1 THEN ROW_NUMBER() OVER (
                    PARTITION BY sector_code, out_flag, g_out ORDER BY trade_date) ELSE 0 END AS quiet_outflow_days
        FROM dc_grp
    ),
    sw_l1 AS (
        -- 申万 L1 码表 (含历史 L1, PIT 正确); MAX 防同码多名重复行
        SELECT l1_code, MAX(l1_name) AS l1_name
        FROM tr.raw_tushare_index_member_all WHERE l1_code IS NOT NULL GROUP BY 1
    ),
    bench AS (
        SELECT trade_date,
               (close / NULLIF(LAG(close, {w4})  OVER (ORDER BY trade_date), 0) - 1) * 100 AS bench_ret_4w,
               (close / NULLIF(LAG(close, {w12}) OVER (ORDER BY trade_date), 0) - 1) * 100 AS bench_ret_12w
        FROM tr.raw_tushare_index_daily WHERE ts_code = {bench}
    ),
    sw_px AS (
        -- 滚窗累计收益 = close_t/close_(t-w) - 1, 尾对齐 (历史不足 → NULL, 不外推)
        SELECT s.ts_code AS sector_code, l.l1_name AS sector_name, s.trade_date, s.pct_change,
               (s.close / NULLIF(LAG(s.close, {w4})  OVER (PARTITION BY s.ts_code ORDER BY s.trade_date), 0) - 1) * 100 AS ret_4w,
               (s.close / NULLIF(LAG(s.close, {w12}) OVER (PARTITION BY s.ts_code ORDER BY s.trade_date), 0) - 1) * 100 AS ret_12w,
               s.amount / NULLIF(SUM(s.amount) OVER (PARTITION BY s.trade_date), 0) AS turnover_amt_share
        FROM tr.raw_tushare_sw_daily s
        JOIN sw_l1 l ON l.l1_code = s.ts_code
        WHERE s.trade_date >= {sw_start}
    ),
    sw_rs AS (
        SELECT p.*, p.ret_4w - b.bench_ret_4w AS rs_4w, p.ret_12w - b.bench_ret_12w AS rs_12w
        FROM sw_px p LEFT JOIN bench b ON b.trade_date = p.trade_date
    ),
    sw_ranked AS (
        SELECT *, CASE WHEN rs_4w IS NULL THEN NULL ELSE
               RANK() OVER (PARTITION BY trade_date ORDER BY rs_4w DESC NULLS LAST) END AS rs_rank_4w
        FROM sw_rs
    ),
    sw_breadth AS (
        -- 申万行业广度: 个股 pct_chg × B1 分层表 (dim_stock_segment_daily.sw_l1)
        SELECT seg.sw_l1 AS sector_name, d.trade_date,
               COUNT(*) FILTER (WHERE d.pct_chg > 0) AS up_num,
               COUNT(*) FILTER (WHERE d.pct_chg < 0) AS down_num
        FROM tr.raw_tushare_daily d
        JOIN dim_stock_segment_daily seg
          ON seg.stock_code = substr(d.ts_code, 1, 6) AND seg.trade_date = d.trade_date
        WHERE d.trade_date >= {sw_start} AND seg.sw_l1 IS NOT NULL
        GROUP BY 1, 2
    ),
    sw_limits AS (
        SELECT seg.sw_l1 AS sector_name, ll.trade_date,
               COUNT(*) FILTER (WHERE ll."limit" = 'U') AS limit_up_n,
               COUNT(*) FILTER (WHERE ll."limit" = 'D') AS limit_down_n,
               COUNT(*) FILTER (WHERE ll."limit" = 'Z') AS zha_ban_n
        FROM tr.raw_tushare_limit_list_d ll
        JOIN dim_stock_segment_daily seg
          ON seg.stock_code = substr(ll.ts_code, 1, 6) AND seg.trade_date = ll.trade_date
        WHERE seg.sw_l1 IS NOT NULL
        GROUP BY 1, 2
    ),
    limit_days AS (
        -- 源当日有数据才把缺组记 0 (真·零涨停); 源整日缺失 (2023 前/断供) 记 NULL (不知道≠0)
        SELECT DISTINCT trade_date FROM tr.raw_tushare_limit_list_d
    )
    SELECT '{CHAIN_DC}' AS chain, q.sector_code, q.sector_name, q.trade_date,
           CAST(q.pct_change AS DOUBLE) AS pct_change,
           CAST(q.net_amount AS DOUBLE) AS net_amount,
           CAST(q.elg_amount AS DOUBLE) AS elg_amount,
           CAST(q.rank_flow AS BIGINT) AS rank_flow,
           CAST(NULL AS DOUBLE) AS rs_4w,
           CAST(NULL AS DOUBLE) AS rs_12w,
           CAST(NULL AS BIGINT) AS rs_rank_4w,
           TRY_CAST(i.up_num AS BIGINT) AS up_num,
           TRY_CAST(i.down_num AS BIGINT) AS down_num,
           CAST(NULL AS BIGINT) AS limit_up_n,
           CAST(NULL AS BIGINT) AS limit_down_n,
           CAST(NULL AS BIGINT) AS zha_ban_n,
           CAST(NULL AS DOUBLE) AS turnover_amt_share,
           CAST(q.quiet_inflow_days AS BIGINT) AS quiet_inflow_days,
           CAST(q.quiet_outflow_days AS BIGINT) AS quiet_outflow_days,
           CURRENT_TIMESTAMP AS built_at
    FROM dc_quiet q
    LEFT JOIN tr.raw_tushare_dc_index i
      ON i.ts_code = q.sector_code AND i.trade_date = q.trade_date
    WHERE {dc_where}
    UNION ALL
    SELECT '{CHAIN_SW}', r.sector_code, r.sector_name, r.trade_date,
           CAST(r.pct_change AS DOUBLE),
           CAST(NULL AS DOUBLE),
           CAST(NULL AS DOUBLE),
           CAST(NULL AS BIGINT),
           CAST(r.rs_4w AS DOUBLE),
           CAST(r.rs_12w AS DOUBLE),
           CAST(r.rs_rank_4w AS BIGINT),
           CAST(b.up_num AS BIGINT),
           CAST(b.down_num AS BIGINT),
           CAST(CASE WHEN ld.trade_date IS NOT NULL THEN COALESCE(lm.limit_up_n, 0) END AS BIGINT),
           CAST(CASE WHEN ld.trade_date IS NOT NULL THEN COALESCE(lm.limit_down_n, 0) END AS BIGINT),
           CAST(CASE WHEN ld.trade_date IS NOT NULL THEN COALESCE(lm.zha_ban_n, 0) END AS BIGINT),
           CAST(r.turnover_amt_share AS DOUBLE),
           CAST(NULL AS BIGINT),
           CAST(NULL AS BIGINT),
           CURRENT_TIMESTAMP
    FROM sw_ranked r
    LEFT JOIN sw_breadth b ON b.sector_name = r.sector_name AND b.trade_date = r.trade_date
    LEFT JOIN sw_limits lm ON lm.sector_name = r.sector_name AND lm.trade_date = r.trade_date
    LEFT JOIN limit_days ld ON ld.trade_date = r.trade_date
    WHERE {sw_where}
    """


def _market_sql(cfg: dict[str, Any], where: str = "1=1") -> str:
    """全市场×日 SELECT。依赖 mart_sector_pulse_daily 已先建/先补 (top_sectors_json 来源)。

    where 用 d. 限定。快照口径: sw 链按 rs_rank_4w (RS 排名), dc 链按 rank_flow (资金流排名)
    — dc 链 rs 恒 NULL (vendor 红线), 快照用其链内原生排序。
    """
    n = int(cfg["top_n_sectors"])
    mkt_start = _sql_str(cfg["data_start_market"])
    return f"""
    WITH days AS (
        SELECT DISTINCT trade_date FROM tr.raw_tushare_daily WHERE trade_date >= {mkt_start}
    ),
    breadth AS (
        SELECT trade_date,
               COUNT(*) FILTER (WHERE pct_chg > 0) AS adv_n,
               COUNT(*) FILTER (WHERE pct_chg < 0) AS dec_n
        FROM tr.raw_tushare_daily GROUP BY 1
    ),
    limits AS (
        SELECT trade_date,
               COUNT(*) FILTER (WHERE "limit" = 'U') AS u_n,
               COUNT(*) FILTER (WHERE "limit" = 'D') AS d_n,
               COUNT(*) FILTER (WHERE "limit" = 'Z') AS z_n
        FROM tr.raw_tushare_limit_list_d GROUP BY 1
    ),
    tops AS (
        SELECT trade_date,
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, net_amount := net_amount)
                     ORDER BY rank_flow ASC, sector_code)
                FILTER (WHERE chain = '{CHAIN_DC}' AND rank_flow IS NOT NULL))[1:{n}] AS dc_top,
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, net_amount := net_amount)
                     ORDER BY rank_flow DESC, sector_code)
                FILTER (WHERE chain = '{CHAIN_DC}' AND rank_flow IS NOT NULL))[1:{n}] AS dc_bottom,
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, rs_4w := rs_4w)
                     ORDER BY rs_rank_4w ASC, sector_code)
                FILTER (WHERE chain = '{CHAIN_SW}' AND rs_rank_4w IS NOT NULL))[1:{n}] AS sw_top,
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, rs_4w := rs_4w)
                     ORDER BY rs_rank_4w DESC, sector_code)
                FILTER (WHERE chain = '{CHAIN_SW}' AND rs_rank_4w IS NOT NULL))[1:{n}] AS sw_bottom
        FROM {SECTOR_TABLE}
        GROUP BY 1
    )
    SELECT d.trade_date,
           CAST(f.net_amount AS DOUBLE) AS mkt_net_amount,
           CAST(l.u_n AS BIGINT) AS limit_up_total,
           CAST(l.d_n AS BIGINT) AS limit_down_total,
           CAST(l.z_n AS DOUBLE) / NULLIF(l.u_n + l.z_n, 0) AS zha_ban_rate,
           CAST(b.adv_n AS DOUBLE) / NULLIF(b.dec_n, 0) AS adv_dec_ratio,
           CAST(to_json(struct_pack(dc_top := t.dc_top, dc_bottom := t.dc_bottom,
                                    sw_top := t.sw_top, sw_bottom := t.sw_bottom)) AS VARCHAR) AS top_sectors_json,
           CURRENT_TIMESTAMP AS built_at
    FROM days d
    LEFT JOIN tr.raw_tushare_moneyflow_mkt_dc f ON f.trade_date = d.trade_date
    LEFT JOIN limits l ON l.trade_date = d.trade_date
    LEFT JOIN breadth b ON b.trade_date = d.trade_date
    LEFT JOIN tops t ON t.trade_date = d.trade_date
    WHERE {where}
    """


def _attach_sources(con) -> None:
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")


def rebuild_all(conn=None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """全量重建两表 (data_start 起)。conn=None 时自管连接并 ATTACH tushare_raw;
    注入 conn (测试/复用) 时调用方负责 tr.* 可解析 (schema 或 ATTACH)。"""
    cfg = cfg or _cfg()
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=False)
    try:
        if own:
            _attach_sources(con)
        con.execute(f"DROP TABLE IF EXISTS {SECTOR_TABLE}")
        con.execute(f"CREATE TABLE {SECTOR_TABLE} AS {_sector_sql(cfg)}")
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_pulse_sector "
                    f"ON {SECTOR_TABLE}(chain, sector_code, trade_date)")
        con.execute(f"DROP TABLE IF EXISTS {MARKET_TABLE}")
        con.execute(f"CREATE TABLE {MARKET_TABLE} AS {_market_sql(cfg)}")
        s_rows, s_days = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT trade_date) FROM {SECTOR_TABLE}").fetchone()
        m_rows = con.execute(f"SELECT COUNT(*) FROM {MARKET_TABLE}").fetchone()[0]
        if own:
            con.execute("CHECKPOINT")
        out = {"sector_rows": s_rows, "sector_days": s_days, "market_rows": m_rows}
        logger.info("[market_pulse] rebuild_all: %s", out)
        return out
    finally:
        if own:
            con.close()


def _missing_dates(con, src_sql: str, params: list[Any]) -> list[str]:
    return [r[0] for r in con.execute(src_sql, params).fetchall()]


def build_latest(conn=None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """增量: 补源已有而 pulse 表缺的日期 (分链检测; 幂等, 无缺日 = no-op)。

    窗口/streak 在全量源历史上重算后只插缺日 → 增量行与全量重建逐 bit 一致 (确定性)。
    顺序: 板块表先补, 全市场表后补 (top_sectors_json 读板块表)。
    """
    cfg = cfg or _cfg()
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=False)
    try:
        if own:
            _attach_sources(con)
        have = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN (?, ?)",
            [SECTOR_TABLE, MARKET_TABLE]).fetchall()}
        if SECTOR_TABLE not in have or MARKET_TABLE not in have:
            return {"mode": "rebuild", **rebuild_all(conn=con, cfg=cfg)}

        dc_missing = _missing_dates(con, f"""
            SELECT DISTINCT trade_date FROM tr.raw_tushare_moneyflow_ind_dc
            WHERE trade_date >= ? AND trade_date NOT IN (
                SELECT DISTINCT trade_date FROM {SECTOR_TABLE} WHERE chain = '{CHAIN_DC}')
            ORDER BY 1""", [str(cfg["data_start_dc"])])
        sw_missing = _missing_dates(con, f"""
            SELECT DISTINCT trade_date FROM tr.raw_tushare_sw_daily
            WHERE trade_date >= ? AND trade_date NOT IN (
                SELECT DISTINCT trade_date FROM {SECTOR_TABLE} WHERE chain = '{CHAIN_SW}')
            ORDER BY 1""", [str(cfg["data_start_sw"])])
        sector_rows = 0
        if dc_missing or sw_missing:
            dc_where = ("q.trade_date IN (%s)" % ",".join(_sql_str(d) for d in dc_missing)
                        ) if dc_missing else "1=0"
            sw_where = ("r.trade_date IN (%s)" % ",".join(_sql_str(d) for d in sw_missing)
                        ) if sw_missing else "1=0"
            r = con.execute(
                f"INSERT INTO {SECTOR_TABLE} {_sector_sql(cfg, dc_where, sw_where)}").fetchone()
            sector_rows = int(r[0]) if r else 0

        mkt_missing = _missing_dates(con, f"""
            SELECT DISTINCT trade_date FROM tr.raw_tushare_daily
            WHERE trade_date >= ? AND trade_date NOT IN (
                SELECT DISTINCT trade_date FROM {MARKET_TABLE})
            ORDER BY 1""", [str(cfg["data_start_market"])])
        market_rows = 0
        if mkt_missing:
            where = "d.trade_date IN (%s)" % ",".join(_sql_str(d) for d in mkt_missing)
            r = con.execute(f"INSERT INTO {MARKET_TABLE} {_market_sql(cfg, where)}").fetchone()
            market_rows = int(r[0]) if r else 0
        con.commit()
        out = {"dc_added_days": len(dc_missing), "sw_added_days": len(sw_missing),
               "sector_rows": sector_rows, "market_added_days": len(mkt_missing),
               "market_rows": market_rows}
        logger.info("[market_pulse] build_latest: %s", out)
        return out
    finally:
        if own:
            con.close()


def get_sector_pulse(as_of: str, chain: str | None = None, conn=None) -> list[dict[str, Any]]:
    """as-of 查询: 取 <= as_of 最近一个入库日的板块面板 (chain 可选过滤; as-of 日按链内独立回退,
    dc/sw 源新鲜度不同步时各取各的最近日)。as_of=YYYYMMDD。"""
    if chain is not None and chain not in (CHAIN_DC, CHAIN_SW):
        raise ValueError(f"unknown chain: {chain!r} (expect {CHAIN_DC!r}/{CHAIN_SW!r})")
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=True)
    try:
        chain_filter = f"AND chain = {_sql_str(chain)}" if chain else ""
        rows = con.execute(f"""
            SELECT * FROM {SECTOR_TABLE} p
            WHERE p.trade_date = (
                SELECT MAX(trade_date) FROM {SECTOR_TABLE}
                WHERE trade_date <= ? AND chain = p.chain)
            {chain_filter}
            ORDER BY chain, sector_code""", [as_of]).fetchall()
        return [dict(zip(r.keys(), list(r))) for r in rows]
    finally:
        if own:
            con.close()


def get_market_pulse(as_of: str, conn=None) -> dict[str, Any] | None:
    """as-of 查询: <= as_of 最近一个入库日的全市场脉搏行 (无数据返回 None)。as_of=YYYYMMDD。"""
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=True)
    try:
        r = con.execute(f"""
            SELECT * FROM {MARKET_TABLE}
            WHERE trade_date = (SELECT MAX(trade_date) FROM {MARKET_TABLE} WHERE trade_date <= ?)
            """, [as_of]).fetchone()
        return dict(zip(r.keys(), list(r))) if r else None
    finally:
        if own:
            con.close()
