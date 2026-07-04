"""market_pulse.py — B4 市场感知引擎 (Follow the Money, 2026-07-02 master plan B4)

设计契约: analysis/market_pulse_design_20260702.md (唯一契约)。
用户定调: "看钱在哪里从哪里流出流向哪里 … 哪里资金悄悄的在流入、哪里悄悄在流出"。

两链并列 (vendor 自洽红线, 任何计算禁跨链 JOIN):
  - dc_concept  链: 东财板块 (行业+概念) 资金流 (moneyflow_ind_dc) × 东财板块广度 (dc_index)。
    专属列: net_amount / elg_amount / rank_flow / quiet_*_days
    + v2: leading/leading_code/leading_pct (dc_index 涨幅龙头) / flow_leader_stock
    (moneyflow_ind_dc.buy_sm_amount_stock 资金龙头) / inflow_breadth (dc_member ×
    moneyflow_dc 链内 JOIN, 当日有个股资金流数据的成分股中 net_amount>0 占比 0-1);
    rs_* / limit_*_n 恒 NULL。
  - sw_industry 链: 申万 L1 行情 (sw_daily) × HS300 基准 (index_daily) 算 RS 双窗;
    广度 (raw_tushare_daily) / 涨跌停 (limit_list_d) 经 B1 (dim_stock_segment_daily.sw_l1) 聚合。
    专属列: rs_4w / rs_12w / rs_rank_4w / limit_*_n / turnover_amt_share; net_amount / quiet_* 恒 NULL。
  - content_type (v2 缺口①): dc 链透出源 content_type (行业|概念; 地域被 dc_content_types
    白名单挡在引擎外), sw 链恒 '申万L1' (CONTENT_SW)。

v2 全市场行新列 (2026-07-02 第一批, 契约=设计文档 "v2 增强设计" 1-7):
  - limit_list_d 情绪周期族 (口径契约: limit_list_d 官方不含 ST):
    max_limit_times 当日最高连板 / limit_times_dist_json n板家数 {"1":x,"2":y} /
    promotion_rate 晋级率 (今日>=2板家数 ÷ 前一源日>=1板家数, 昨日 0 板 → NULL 不除零) /
    sec_board_n 秒板数 (first_time <= sec_board_cutoff 且 U; 源 first_time 无前导零, lpad 归一) /
    avg_fd_amount U 行封单均额 / open_times_total 炸板总次数 (U+Z 行 open_times 直和)。
  - rzrqye / rzrqye_chg: raw_tushare_margin 跨交易所直和 + 相邻 qualifying 日差 (t+1 披露,
    行日期=余额日, 直接按 trade_date 对齐; 覆盖门 2026-07-03 审计修1: 当日 >= 2 家交易所
    在场才出值, SSE-only 日 NULL 不知道≠0, chg 跨过缺失日在 qualifying 序列上算)。
  - mkt_pe / mkt_turnover: raw_tushare_index_dailybasic 取 mkt_valuation_code 行
    (pe_ttm / turnover_rate_f — TTM 口径抗财报季跳变, 自由流通换手贴情绪水位)。
  - lhb_count / lhb_inst_net: top_list 当日上榜家数 (DISTINCT ts_code, 同股多理由算 1 家) /
    top_inst 席位净买直和 (源含游资营业部席位非纯机构, 列名沿设计契约; 同席位双向重复行去重)。
  - strongest_sectors_json: limit_cpt_list 当日最强板块榜整日 JSON (rank 升序;
    885xxx.TI 同花顺码, 禁与 dc/sw 任何链 JOIN — 独立展示卡专用)。

v3 (2026-07-03, 契约=设计文档 "v3 设计" v3.1-v3.5):
  - mart_sector_pulse_daily 加列 level / flow_z / flow_streak / cum_ratio_20d / flow_regime:
    · level: sw 链 'L1'|'L2'|'L3' (index_member_all 三级码表判); dc 链透出源 dc_index.level
      (东财行业层级, 数值 1/2/3 归一为同词汇 'L1'/'L2'/'L3'; 概念/源缺 → NULL)。
    · flow_regime (资金流形态分类学, 判定序第一命中, 阈值全 yaml): surge_in/surge_out (脉冲流入/
      流出, flow_z 超阈且 net 同号) → accum_in_silent/accum_out_silent (横盘累积流入/流出,
      streak 达阈且价稳) → accum_in_driving/accum_out_driving (上行/下行累积流入/流出) → neutral;
      net NULL → NULL (无流数据 ≠ 无形态)。个股级 regime 不落表 (下钻 API 实时算, 共用
      _flow_annotate_sql 防双实现漂移)。
  - sw 链扩 L2/L3 行: sw_daily 588 码含全级指数 — RS 双窗/rs_rank_4w/turnover_amt_share 全部
    按 (trade_date, level) 分区同级内算 (L1 行数值与 v2 逐 bit 一致); 广度/涨跌停聚合仍仅 L1
    (B1 桥只有 sw_l1), L2/L3 行该族 NULL (不知道≠0, 不 COALESCE 造 0)。
  - **net_amount 两链口径差异 (并列不可比 — 禁跨链比较/排名/JOIN)**:
    · dc 链 = 东财板块**主力**净流入 (moneyflow_ind_dc.net_amount, 源单位元, 大单+超大单口径);
    · sw 链 = 申万成分个股**全单**净流入聚合 (raw_tushare_moneyflow.net_mf_amount 万元 ×1e4→元,
      tushare 个股全部单型净额, × v_sw_industry_pit as-of 归属 GROUP BY l1/l2/l3, 同股同日
      多区间取 in_date 最新一行防双计) — sw 链 net_amount 自 v3 不再恒 NULL;
    · 同名列只为 schema 复用, 语义按 chain 读; 展示端两链数字不排同一榜。
  - cum_ratio_20d 分母 (板块市值, 同为跨链不可比): dc = dc_index.total_mv (万元→元, **总市值**
    口径, 源无流通市值字段); sw = 成分个股 daily_basic.circ_mv 聚合 (万元→元, **流通市值**口径)。

Type A (确定性 PIT 重排): t 日行只用 <= t 数据 (rolling window 尾对齐, streak 逐日递推),
无策略阈值判断; 全部阈值读 config/market_pulse.yaml (代码零 hardcode)。
感知层只描述现状, 不给买卖暗示 (RS top3 过滤器等信号层候选进 D2 消融另验;
flow_regime 是 D2 特征候选, 有效性由消融裁决, 本层零 claim)。

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
   raw_tushare_moneyflow_mkt_dc / raw_tushare_index_member_all (L1 码表 + sw 成分下钻) /
   dim_stock_segment_daily (B1, sw 链聚合桥)
   + v2 新增: raw_tushare_dc_member / raw_tushare_moneyflow_dc (inflow_breadth) /
   raw_tushare_margin / raw_tushare_index_dailybasic / raw_tushare_top_list /
   raw_tushare_top_inst / raw_tushare_limit_cpt_list
   + v3 新增: raw_tushare_moneyflow + v_sw_industry_pit (sw 链板块净流聚合) /
   raw_tushare_daily_basic (sw 板块流通市值) / fact_stock_form_daily (B2, 下钻叶子层 form);
   consumer = C4 /api/v3/pulse/* (v3 增 /drill /flow_stripe /flow_board, 退 /quiet)。
3. pipeline process 步挂钩: 建议挂在 segments.build_latest (B1) 之后同一 process 段,
   调用 market_pulse.build_latest() (无参; 幂等, 无新日期时 no-op)。
4. 真实数据 smoke (回填锁释放后主会话跑): rebuild_all() → 抽查
   (a) dc 链行数 ≈ 板块数×交易日 (2024+, 早期仅行业 ~86/日);
   (b) sw 链 v3 行数: 31 L1 + ~134 L2 + ~346 L3 ≈ 511 码/日 (v2 仅 31; 实际以 sw_daily 588 码
       与 index_member_all 三级码交集为准), rs_4w 前 20 行 NULL (尾对齐);
   (c) 任一日 SUM(turnover_amt_share)≈1 **按 level 分别成立** (sw 链 3 个分区各自归一);
   (d) quiet_inflow_days 抽 1 板块人工核对连续性;
   (e) v2: inflow_breadth 只在 dc_member 覆盖窗 (2025+) 非 NULL 且全落 [0,1]; 抽 1 板块 1 日
       手核 (成分股数 vs moneyflow_dc net>0 数); (f) limit_times_dist_json 抽 1 日与
       limit_list_d 按 limit_times GROUP BY 对账; promotion_rate 抽 1 日手算;
   (g) rzrqye 抽 1 日 = margin 当日 SUM (仅 SSE 1 行的日必须 NULL, 覆盖门); (h) strongest_sectors_json
       抽 1 日 rank 序与 limit_cpt_list 一致; (i) 增量: build_latest 补 1 日后新列非全 NULL;
   (j) v3: 抽 1 个 L1 1 日, sw net_amount = 该行业成分股 moneyflow.net_mf_amount 和 ×1e4 手核
       (as-of 归属); L1 net ≈ Σ 其下 L2 net (成分覆盖差留容差); (k) flow_regime 分布抽查:
       六标签 + neutral 皆出现, sw 2019 (moneyflow 覆盖前) 行 flow_regime 全 NULL;
   (l) flow_z 窗口: 每链前 zscore_window 个源日 flow_z 全 NULL (尾对齐)。
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
CONTENT_SW = "申万L1"  # sw 链 content_type 词汇 (dc 链透出源 content_type: 行业|概念)
SECTOR_TABLE = "mart_sector_pulse_daily"
MARKET_TABLE = "mart_market_pulse_daily"


def _cfg() -> dict[str, Any]:
    return yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))


def _db(alias: str) -> str:
    return str(get_database_manifest().path_for(alias))


def _sql_str(v: Any) -> str:
    """SQL 单引号字面量 (防注入转义)。"""
    return "'" + str(v).replace("'", "''") + "'"


def _clean_num(col: str) -> str:
    """NaN→NULL 数值清洗片段: tushare pandas ingest 可能落 NaN 而非 NULL (实测 limit_list_d
    样本 limit_amount=NaN), NaN 会毒化 SUM/AVG/MAX; TRY_CAST 兼容源列 int/double/varchar。"""
    return f"(CASE WHEN NOT isnan(TRY_CAST({col} AS DOUBLE)) THEN TRY_CAST({col} AS DOUBLE) END)"


def _flow_annotate_sql(cfg: dict[str, Any], base_sql: str, key: str) -> str:
    """资金流形态窗口标注 (v3.1 分类学, 判定序第一命中) — 板块面板与下钻个股实时计算共用
    同一段生成 SQL, 双实现漂移根子上掐死。

    base_sql 须产出至少 ({key}, trade_date, net_amount, pct_change) 列 (可带任意透传列;
    net_amount 单位=元, pct_change=百分数); key 可复合 ("chain, sector_code")。
    追加列:
      flow_z       当日 net 对自身近 zscore_window 日 (不含当日) z-score; 窗口不满/std=0 → NULL。
      flow_streak  连续同号净流向天数, 带符号 (+N 连续净流入 / -N 连续净流出; net=0/NULL → 0 断段)。
      _px_cum      当前 streak 段内价格累计涨跌 (%, 复利连乘; pct 缺日不计入 — SUM 跳 NULL)。
      _cum_net     近 cum_window 行累计净流入 (元, 含当日; 行窗口口径 — 停牌缺行日无流不占位)。
      _cum_cnt     该窗口内非空净流个数 (=cum_window 才算窗口满, 消费侧据此判 cum_ratio NULL)。
      flow_regime  判定序: 脉冲 (surge_in/surge_out, z 超阈且 net 同号 — 深负均值序列 z 可为正,
                   同号 guard 防把流出日误标脉冲流入) → 累积 (streak 达阈: |px|<band 横盘累积 /
                   px>=+band 上行累积 / px<=-band 下行累积) → neutral; net NULL → NULL。
    纯嵌套子查询 (无 WITH), 可安全内嵌进任意外层 CTE。"""
    zw = int(cfg["zscore_window"])
    cw = int(cfg["cum_window"])
    zs = float(cfg["flow_z_surge"])
    ms = int(cfg["accum_min_streak"])
    band = float(cfg["silent_px_band"])
    return f"""
    SELECT *,
           CASE WHEN net_amount IS NULL THEN NULL
                WHEN flow_z >= {zs} AND net_amount > 0 THEN 'surge_in'
                WHEN flow_z <= -{zs} AND net_amount < 0 THEN 'surge_out'
                WHEN flow_streak >= {ms} AND ABS(_px_cum) < {band} THEN 'accum_in_silent'
                WHEN flow_streak >= {ms} AND _px_cum >= {band} THEN 'accum_in_driving'
                WHEN flow_streak <= -{ms} AND ABS(_px_cum) < {band} THEN 'accum_out_silent'
                WHEN flow_streak <= -{ms} AND _px_cum <= -{band} THEN 'accum_out_driving'
                ELSE 'neutral' END AS flow_regime
    FROM (
        SELECT *,
               CASE WHEN _sign != 0 THEN _sign * ROW_NUMBER() OVER (
                    PARTITION BY {key}, _sign, _sgrp ORDER BY trade_date) ELSE 0 END AS flow_streak,
               CASE WHEN net_amount IS NOT NULL AND _zcnt = {zw} AND _zstd > 0
                    THEN (net_amount - _zmean) / _zstd END AS flow_z,
               CASE WHEN _sign != 0 THEN (EXP(SUM(
                    CASE WHEN pct_change > -100 THEN LN(1 + pct_change / 100.0) END)
                    OVER (PARTITION BY {key}, _sign, _sgrp ORDER BY trade_date)) - 1) * 100
                    END AS _px_cum
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY trade_date)
                     - ROW_NUMBER() OVER (PARTITION BY {key}, _sign ORDER BY trade_date) AS _sgrp,
                   AVG(net_amount) OVER _wz AS _zmean,
                   STDDEV_SAMP(net_amount) OVER _wz AS _zstd,
                   COUNT(net_amount) OVER _wz AS _zcnt,
                   SUM(net_amount) OVER _wc AS _cum_net,
                   COUNT(net_amount) OVER _wc AS _cum_cnt
            FROM (
                SELECT *, CASE WHEN net_amount > 0 THEN 1 WHEN net_amount < 0 THEN -1 ELSE 0 END AS _sign
                FROM ({base_sql})
            )
            WINDOW _wz AS (PARTITION BY {key} ORDER BY trade_date
                           ROWS BETWEEN {zw} PRECEDING AND 1 PRECEDING),
                   _wc AS (PARTITION BY {key} ORDER BY trade_date
                           ROWS BETWEEN {cw - 1} PRECEDING AND CURRENT ROW)
        )
    )
    """


def _sector_sql(cfg: dict[str, Any], dc_where: str = "1=1", sw_where: str = "1=1",
                dc_day_where: str = "1=1") -> str:
    """板块×日面板 SELECT (两链 UNION, 列序=契约序)。

    窗口/streak (quiet + v3 flow_z/flow_streak/cum) 永远在**全量源历史**上算 (增量插入也一样),
    dc_where/sw_where 只在最外层过滤输出日期 — 保证 rolling RS / streak 跨增量边界正确
    (PIT 尾对齐)。v3 起两个 where 均为**无别名**谓词 (如 "trade_date IN (...)"), 在 flow 标注后的
    最终 SELECT 按链分别套用 ((chain=dc AND dc_where) OR (chain=sw AND sw_where))。
    dc_day_where (m. 限定) = inflow_breadth 的增量日期下推: 该聚合按日独立无窗口依赖,
    增量时把 dc_member(千万行级)×moneyflow_dc JOIN 裁剪到缺日, 不动确定性。
    """
    w4 = int(cfg["rs_window_4w"])
    w12 = int(cfg["rs_window_12w"])
    band = float(cfg["quiet_px_band_pct"])
    min_amt = float(cfg["quiet_min_net_amount"])
    cw = int(cfg["cum_window"])
    bench = _sql_str(cfg["benchmark_code"])
    ctypes = ",".join(_sql_str(c) for c in cfg["dc_content_types"])
    dc_start = _sql_str(cfg["data_start_dc"])
    sw_start = _sql_str(cfg["data_start_sw"])
    lvl = _clean_num("i.level")
    panel_sql = f"""
    WITH dc_flow AS (
        -- dc 链底座: 东财板块资金流。rank 原始字段=分页伪 rank, 弃用; rank_flow 当日截面重算。
        SELECT trade_date, ts_code AS sector_code, name AS sector_name, content_type,
               pct_change, net_amount, buy_elg_amount AS elg_amount,
               buy_sm_amount_stock AS flow_leader_stock,
               RANK() OVER (PARTITION BY trade_date ORDER BY net_amount DESC NULLS LAST) AS rank_flow,
               CASE WHEN ABS(pct_change) < {band} AND net_amount > {min_amt} THEN 1 ELSE 0 END AS in_flag,
               CASE WHEN ABS(pct_change) < {band} AND net_amount < -{min_amt} THEN 1 ELSE 0 END AS out_flag
        FROM tr.raw_tushare_moneyflow_ind_dc
        WHERE trade_date >= {dc_start} AND content_type IN ({ctypes})
    ),
    dc_breadth AS (
        -- 板块内个股流入宽度 (抗龙头绑架): 当日成分股中 net_amount>0 占比。vendor 自洽:
        -- dc_member × moneyflow_dc 全东财链。INNER JOIN → 分母=当日有个股资金流数据的成分股
        -- (停牌/缺流数据成分不进分母); 板块当日无成分快照 (dc_member 2025+ 起) → NULL 不知道≠0。
        SELECT m.trade_date, m.ts_code AS sector_code,
               COUNT(*) FILTER (WHERE f.net_amount > 0) * 1.0 / COUNT(*) AS inflow_breadth
        FROM tr.raw_tushare_dc_member m
        JOIN tr.raw_tushare_moneyflow_dc f
          ON f.ts_code = m.con_code AND f.trade_date = m.trade_date
        WHERE m.trade_date >= {dc_start} AND {dc_day_where}
        GROUP BY 1, 2
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
    sw_dim AS (
        -- 申万三级码表 (v3: L1+L2+L3, 含历史码 PIT 正确); 同码同级, MAX 防同码多名重复行
        SELECT code, MAX(name) AS name, MAX(level) AS level FROM (
            SELECT l1_code AS code, l1_name AS name, 'L1' AS level
            FROM tr.raw_tushare_index_member_all WHERE l1_code IS NOT NULL
            UNION ALL
            SELECT l2_code, l2_name, 'L2'
            FROM tr.raw_tushare_index_member_all WHERE l2_code IS NOT NULL
            UNION ALL
            SELECT l3_code, l3_name, 'L3'
            FROM tr.raw_tushare_index_member_all WHERE l3_code IS NOT NULL
        ) GROUP BY code
    ),
    bench AS (
        SELECT trade_date,
               (close / NULLIF(LAG(close, {w4})  OVER (ORDER BY trade_date), 0) - 1) * 100 AS bench_ret_4w,
               (close / NULLIF(LAG(close, {w12}) OVER (ORDER BY trade_date), 0) - 1) * 100 AS bench_ret_12w
        FROM tr.raw_tushare_index_daily WHERE ts_code = {bench}
    ),
    sw_px AS (
        -- 滚窗累计收益 = close_t/close_(t-w) - 1, 尾对齐 (历史不足 → NULL, 不外推)。
        -- v3: L1/L2/L3 全级同算; turnover_amt_share 按 (日, level) 分区 — 同级内成交额占比
        -- (跨级混分母会被 L2/L3 重复计入稀释, L1 行数值与 v2 逐 bit 一致)。
        SELECT s.ts_code AS sector_code, l.name AS sector_name, l.level, s.trade_date, s.pct_change,
               (s.close / NULLIF(LAG(s.close, {w4})  OVER (PARTITION BY s.ts_code ORDER BY s.trade_date), 0) - 1) * 100 AS ret_4w,
               (s.close / NULLIF(LAG(s.close, {w12}) OVER (PARTITION BY s.ts_code ORDER BY s.trade_date), 0) - 1) * 100 AS ret_12w,
               s.amount / NULLIF(SUM(s.amount) OVER (PARTITION BY s.trade_date, l.level), 0) AS turnover_amt_share
        FROM tr.raw_tushare_sw_daily s
        JOIN sw_dim l ON l.code = s.ts_code
        WHERE s.trade_date >= {sw_start}
    ),
    sw_rs AS (
        SELECT p.*, p.ret_4w - b.bench_ret_4w AS rs_4w, p.ret_12w - b.bench_ret_12w AS rs_12w
        FROM sw_px p LEFT JOIN bench b ON b.trade_date = p.trade_date
    ),
    sw_ranked AS (
        -- v3: 排名按 (日, level) 同级分区 — L1 行 rank 与 v2 逐 bit 一致, L2/L3 各自成榜
        SELECT *, CASE WHEN rs_4w IS NULL THEN NULL ELSE
               RANK() OVER (PARTITION BY trade_date, level ORDER BY rs_4w DESC NULLS LAST) END AS rs_rank_4w
        FROM sw_rs
    ),
    sw_stock_flow AS (
        -- v3 sw 链资金流底座: tushare 个股全单净流 (net_mf_amount 万元) × 申万 as-of 归属。
        -- 口径与 dc 链 net_amount (东财主力口径, 元) 并列不可比 — 见模块头注; 全链 tushare+申万,
        -- vendor 自洽 (无跨链 JOIN)。QUALIFY in_date DESC 首行 = 当时活跃归属 (build_sw_industry_view
        -- 契约的 as-of 语义), 防区间重叠把同一笔净流双计进两套行业。
        SELECT f.trade_date, p.l1_code, p.l2_code, p.l3_code,
               TRY_CAST(f.net_mf_amount AS DOUBLE) AS net_mf_amount
        FROM tr.raw_tushare_moneyflow f
        JOIN tr.v_sw_industry_pit p
          ON p.ts_code = f.ts_code AND p.in_date <= f.trade_date
         AND (p.out_date IS NULL OR p.out_date > f.trade_date)
        WHERE f.trade_date >= {sw_start}
        QUALIFY ROW_NUMBER() OVER (PARTITION BY f.ts_code, f.trade_date ORDER BY p.in_date DESC) = 1
    ),
    sw_flow AS (
        -- 三级同源聚合 (万元 → 元 单位归一, ×1e4 为确定性单位换算非估算)
        SELECT trade_date, code, SUM(net_mf_amount) * 10000.0 AS net_amount
        FROM (
            SELECT trade_date, l1_code AS code, net_mf_amount FROM sw_stock_flow WHERE l1_code IS NOT NULL
            UNION ALL
            SELECT trade_date, l2_code, net_mf_amount FROM sw_stock_flow WHERE l2_code IS NOT NULL
            UNION ALL
            SELECT trade_date, l3_code, net_mf_amount FROM sw_stock_flow WHERE l3_code IS NOT NULL
        ) GROUP BY 1, 2
    ),
    sw_stock_mv AS (
        -- 板块流通市值底座: 成分个股 daily_basic.circ_mv (万元) as-of 归属聚合 (同上防双计)
        SELECT b.trade_date, p.l1_code, p.l2_code, p.l3_code,
               TRY_CAST(b.circ_mv AS DOUBLE) AS circ_mv
        FROM tr.raw_tushare_daily_basic b
        JOIN tr.v_sw_industry_pit p
          ON p.ts_code = b.ts_code AND p.in_date <= b.trade_date
         AND (p.out_date IS NULL OR p.out_date > b.trade_date)
        WHERE b.trade_date >= {sw_start}
        QUALIFY ROW_NUMBER() OVER (PARTITION BY b.ts_code, b.trade_date ORDER BY p.in_date DESC) = 1
    ),
    sw_mv AS (
        SELECT trade_date, code, SUM(circ_mv) * 10000.0 AS sector_mv
        FROM (
            SELECT trade_date, l1_code AS code, circ_mv FROM sw_stock_mv WHERE l1_code IS NOT NULL
            UNION ALL
            SELECT trade_date, l2_code, circ_mv FROM sw_stock_mv WHERE l2_code IS NOT NULL
            UNION ALL
            SELECT trade_date, l3_code, circ_mv FROM sw_stock_mv WHERE l3_code IS NOT NULL
        ) GROUP BY 1, 2
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
           CAST(q.content_type AS VARCHAR) AS content_type,
           CAST(i."leading" AS VARCHAR) AS "leading",
           CAST(i.leading_code AS VARCHAR) AS leading_code,
           CAST(i.leading_pct AS DOUBLE) AS leading_pct,
           CAST(q.flow_leader_stock AS VARCHAR) AS flow_leader_stock,
           CAST(br.inflow_breadth AS DOUBLE) AS inflow_breadth,
           -- v3 level: 透出 dc_index.level (东财行业层级, 数值 1/2/3 → 'L1/2/3' 词汇归一与 sw 同;
           -- 概念板块/2025 前无 dc_index 行/源 NaN → NULL, 不猜)
           CAST(CASE WHEN TRY_CAST({lvl} AS INTEGER) IN (1, 2, 3)
                THEN 'L' || CAST(TRY_CAST({lvl} AS INTEGER) AS VARCHAR) END AS VARCHAR) AS level,
           -- cum_ratio 分母: dc_index.total_mv 万元→元 (总市值口径 — 源无流通市值字段, 见头注)
           {_clean_num('i.total_mv')} * 10000.0 AS sector_mv
    FROM dc_quiet q
    LEFT JOIN tr.raw_tushare_dc_index i
      ON i.ts_code = q.sector_code AND i.trade_date = q.trade_date
    LEFT JOIN dc_breadth br
      ON br.sector_code = q.sector_code AND br.trade_date = q.trade_date
    UNION ALL
    SELECT '{CHAIN_SW}', r.sector_code, r.sector_name, r.trade_date,
           CAST(r.pct_change AS DOUBLE),
           CAST(f.net_amount AS DOUBLE),
           CAST(NULL AS DOUBLE),
           CAST(NULL AS BIGINT),
           CAST(r.rs_4w AS DOUBLE),
           CAST(r.rs_12w AS DOUBLE),
           CAST(r.rs_rank_4w AS BIGINT),
           CAST(b.up_num AS BIGINT),
           CAST(b.down_num AS BIGINT),
           -- 广度/涨跌停族仅 L1 (B1 桥只有 sw_l1); L2/L3 恒 NULL — level guard 防 COALESCE 造假 0
           CAST(CASE WHEN r.level = 'L1' AND ld.trade_date IS NOT NULL THEN COALESCE(lm.limit_up_n, 0) END AS BIGINT),
           CAST(CASE WHEN r.level = 'L1' AND ld.trade_date IS NOT NULL THEN COALESCE(lm.limit_down_n, 0) END AS BIGINT),
           CAST(CASE WHEN r.level = 'L1' AND ld.trade_date IS NOT NULL THEN COALESCE(lm.zha_ban_n, 0) END AS BIGINT),
           CAST(r.turnover_amt_share AS DOUBLE),
           CAST(NULL AS BIGINT),
           CAST(NULL AS BIGINT),
           CAST('{CONTENT_SW}' AS VARCHAR),
           CAST(NULL AS VARCHAR),
           CAST(NULL AS VARCHAR),
           CAST(NULL AS DOUBLE),
           CAST(NULL AS VARCHAR),
           CAST(NULL AS DOUBLE),
           CAST(r.level AS VARCHAR),
           CAST(m.sector_mv AS DOUBLE)
    FROM sw_ranked r
    LEFT JOIN sw_breadth b ON b.sector_name = r.sector_name AND b.trade_date = r.trade_date AND r.level = 'L1'
    LEFT JOIN sw_limits lm ON lm.sector_name = r.sector_name AND lm.trade_date = r.trade_date AND r.level = 'L1'
    LEFT JOIN limit_days ld ON ld.trade_date = r.trade_date
    LEFT JOIN sw_flow f ON f.code = r.sector_code AND f.trade_date = r.trade_date
    LEFT JOIN sw_mv m ON m.code = r.sector_code AND m.trade_date = r.trade_date
    """
    ann = _flow_annotate_sql(cfg, panel_sql, "chain, sector_code")
    return f"""
    SELECT chain, sector_code, sector_name, trade_date, pct_change, net_amount, elg_amount,
           rank_flow, rs_4w, rs_12w, rs_rank_4w, up_num, down_num, limit_up_n, limit_down_n,
           zha_ban_n, turnover_amt_share, quiet_inflow_days, quiet_outflow_days, content_type,
           "leading", leading_code, leading_pct, flow_leader_stock, inflow_breadth,
           level,
           CAST(flow_z AS DOUBLE) AS flow_z,
           CAST(flow_streak AS BIGINT) AS flow_streak,
           -- 近 cum_window 日累计净流 / 板块市值 (%); 窗口不满或市值缺 → NULL (不知道≠0)
           CAST(CASE WHEN net_amount IS NOT NULL AND _cum_cnt = {cw} AND sector_mv > 0
                THEN _cum_net / sector_mv * 100 END AS DOUBLE) AS cum_ratio_20d,
           CAST(flow_regime AS VARCHAR) AS flow_regime,
           CURRENT_TIMESTAMP AS built_at
    FROM ({ann})
    WHERE (chain = '{CHAIN_DC}' AND ({dc_where})) OR (chain = '{CHAIN_SW}' AND ({sw_where}))
    """


def _market_sql(cfg: dict[str, Any], where: str = "1=1") -> str:
    """全市场×日 SELECT。依赖 mart_sector_pulse_daily 已先建/先补 (top_sectors_json 来源)。

    where 用 d. 限定。快照口径: sw 链按 rs_rank_4w (RS 排名), dc 链按 rank_flow (资金流排名)
    — dc 链 rs 恒 NULL (vendor 红线), 快照用其链内原生排序。
    v2 新列 CTE 全部在**全量源历史**上算 (promotion_rate / rzrqye_chg 有 LAG 跨日依赖),
    where 只裁最外层输出日 — 与板块表窗口纪律同一条。
    """
    n = int(cfg["top_n_sectors"])
    mkt_start = _sql_str(cfg["data_start_market"])
    sec_cut = _sql_str(cfg["sec_board_cutoff"])
    val_code = _sql_str(cfg["mkt_valuation_code"])
    lt = _clean_num("limit_times")
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
    limits0 AS (
        -- 情绪周期族 (源口径契约: limit_list_d 官方不含 ST)。源在场缺组 = 真 0 (COALESCE);
        -- 源整日缺失由外层 LEFT JOIN 落 NULL (不知道≠0)。first_time 无前导零 → lpad 6 归一。
        SELECT trade_date,
               COUNT(*) FILTER (WHERE "limit" = 'U') AS u_n,
               COUNT(*) FILTER (WHERE "limit" = 'D') AS d_n,
               COUNT(*) FILTER (WHERE "limit" = 'Z') AS z_n,
               COALESCE(MAX(TRY_CAST({lt} AS INTEGER)) FILTER (WHERE "limit" = 'U'), 0) AS max_limit_times,
               COUNT(*) FILTER (WHERE "limit" = 'U' AND {lt} >= 2) AS n_ge2,
               COUNT(*) FILTER (WHERE "limit" = 'U'
                   AND lpad(CAST(first_time AS VARCHAR), 6, '0') <= {sec_cut}) AS sec_board_n,
               AVG({_clean_num('fd_amount')}) FILTER (WHERE "limit" = 'U') AS avg_fd_amount,
               COALESCE(SUM({_clean_num('open_times')}) FILTER (WHERE "limit" IN ('U', 'Z')), 0)
                   AS open_times_total
        FROM tr.raw_tushare_limit_list_d GROUP BY 1
    ),
    limits AS (
        -- 晋级率 = 今日>=2板家数 ÷ 前一源日>=1板家数 (U 全体, limit_times>=1 恒真);
        -- 昨日 0 板 / 无昨日 → NULL (不除零, 不知道≠0)。LAG 在全量源日上算, 跨增量边界正确。
        SELECT *, CAST(n_ge2 AS DOUBLE) / NULLIF(LAG(u_n) OVER (ORDER BY trade_date), 0)
                   AS promotion_rate
        FROM limits0
    ),
    ladder AS (
        -- n板家数分布 {{"1":x,"2":y}} (U 行, limit_times 缺失行不进桶); 当日无 U 行 → 无行,
        -- 由外层 CASE 区分 '{{}}' (源在场真·无涨停) vs NULL (源整日缺失)。
        SELECT trade_date, CAST(json_group_object(lt_key, n) AS VARCHAR) AS limit_times_dist_json
        FROM (SELECT trade_date, CAST(TRY_CAST({lt} AS INTEGER) AS VARCHAR) AS lt_key, COUNT(*) AS n
              FROM tr.raw_tushare_limit_list_d
              WHERE "limit" = 'U' AND TRY_CAST({lt} AS INTEGER) IS NOT NULL
              GROUP BY 1, 2)
        GROUP BY 1
    ),
    margin_day AS (
        -- 两融余额: 跨交易所直和 (SSE+SZSE[+BSE]); PIT 锚 t+1 披露, 行日期=余额日按 trade_date 对齐。
        -- 覆盖门 (2026-07-03 审计修1): 当日 >= 2 家交易所在场才出值, 否则 NULL (不知道≠0) —
        -- SSE-only 日直和腰斩 (ratio≈0.51), 其 chg 假摆动达史上真实极值 12-14 倍。
        -- 门槛 = 2 非 3: BSE 融资融券 2020 前不存在, 要求 3 会把早期真实 SSE+SZSE 日全判缺。
        SELECT trade_date,
               CASE WHEN COUNT(DISTINCT exchange_id) >= 2 THEN SUM(rzrqye) END AS rzrqye
        FROM tr.raw_tushare_margin GROUP BY 1
    ),
    margin2 AS (
        -- chg 只跨 qualifying (覆盖门通过) 日算: 先滤 NULL 再 LAG (过滤后序列),
        -- 非原序列上 LAG 撞 NULL — SSE-only 日两侧的 qualifying 日 chg 必须跨过它相减。
        SELECT trade_date, rzrqye,
               rzrqye - LAG(rzrqye) OVER (ORDER BY trade_date) AS rzrqye_chg
        FROM margin_day WHERE rzrqye IS NOT NULL
    ),
    idxb AS (
        -- 大盘估值/换手水位 (mkt_valuation_code 行): pe_ttm (TTM 口径) + turnover_rate_f (自由流通)。
        SELECT trade_date, pe_ttm AS mkt_pe, turnover_rate_f AS mkt_turnover
        FROM tr.raw_tushare_index_dailybasic WHERE ts_code = {val_code}
    ),
    lhb AS (
        -- 龙虎榜家数: 同股同日多上榜理由多行 → DISTINCT ts_code 算 1 家。
        SELECT trade_date, COUNT(DISTINCT ts_code) AS lhb_count
        FROM tr.raw_tushare_top_list GROUP BY 1
    ),
    lhb_inst AS (
        -- 席位净买直和 (源=top_inst 全部披露席位含游资营业部, 非纯机构 — 列名沿设计契约);
        -- 同席位同股买/卖双榜重复披露 (side 0/1 同额) → DISTINCT 去重后再和。
        SELECT trade_date, SUM(net_buy) AS lhb_inst_net
        FROM (SELECT DISTINCT trade_date, ts_code, exalter, net_buy FROM tr.raw_tushare_top_inst)
        GROUP BY 1
    ),
    strongest AS (
        -- 最强板块榜整日快照 (rank 升序)。885xxx.TI 同花顺码: 只做独立展示卡, 禁跨链 JOIN。
        SELECT trade_date, CAST(to_json(list(struct_pack(
                   ts_code := ts_code, name := name, days := days, up_stat := up_stat,
                   cons_nums := cons_nums, up_nums := up_nums, pct_chg := pct_chg, rank := "rank")
               ORDER BY "rank" ASC, ts_code)) AS VARCHAR) AS strongest_sectors_json
        FROM tr.raw_tushare_limit_cpt_list GROUP BY 1
    ),
    tops AS (
        SELECT trade_date,
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, net_amount := net_amount)
                     ORDER BY rank_flow ASC, sector_code)
                FILTER (WHERE chain = '{CHAIN_DC}' AND rank_flow IS NOT NULL))[1:{n}] AS dc_top,
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, net_amount := net_amount)
                     ORDER BY rank_flow DESC, sector_code)
                FILTER (WHERE chain = '{CHAIN_DC}' AND rank_flow IS NOT NULL))[1:{n}] AS dc_bottom,
               -- v3: sw 快照锁 L1 (rs_rank_4w 已按 level 分区, 不滤则 L2/L3 的 rank<=N 行混入)
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, rs_4w := rs_4w)
                     ORDER BY rs_rank_4w ASC, sector_code)
                FILTER (WHERE chain = '{CHAIN_SW}' AND level = 'L1' AND rs_rank_4w IS NOT NULL))[1:{n}] AS sw_top,
               (list(struct_pack(sector_code := sector_code, sector_name := sector_name, rs_4w := rs_4w)
                     ORDER BY rs_rank_4w DESC, sector_code)
                FILTER (WHERE chain = '{CHAIN_SW}' AND level = 'L1' AND rs_rank_4w IS NOT NULL))[1:{n}] AS sw_bottom
        FROM {SECTOR_TABLE}
        GROUP BY 1
    )
    SELECT d.trade_date,
           CAST(f.net_amount AS DOUBLE) AS mkt_net_amount,
           CAST(l.u_n AS BIGINT) AS limit_up_total,
           CAST(l.d_n AS BIGINT) AS limit_down_total,
           CAST(l.z_n AS DOUBLE) / NULLIF(l.u_n + l.z_n, 0) AS zha_ban_rate,
           CAST(b.adv_n AS DOUBLE) / NULLIF(b.dec_n, 0) AS adv_dec_ratio,
           CAST(l.max_limit_times AS BIGINT) AS max_limit_times,
           CASE WHEN l.trade_date IS NOT NULL
                THEN COALESCE(lad.limit_times_dist_json, '{{}}') END AS limit_times_dist_json,
           CAST(l.promotion_rate AS DOUBLE) AS promotion_rate,
           CAST(l.sec_board_n AS BIGINT) AS sec_board_n,
           CAST(l.avg_fd_amount AS DOUBLE) AS avg_fd_amount,
           CAST(l.open_times_total AS BIGINT) AS open_times_total,
           CAST(mg.rzrqye AS DOUBLE) AS rzrqye,
           CAST(mg.rzrqye_chg AS DOUBLE) AS rzrqye_chg,
           CAST(ib.mkt_pe AS DOUBLE) AS mkt_pe,
           CAST(ib.mkt_turnover AS DOUBLE) AS mkt_turnover,
           CAST(lb.lhb_count AS BIGINT) AS lhb_count,
           CAST(li.lhb_inst_net AS DOUBLE) AS lhb_inst_net,
           CAST(to_json(struct_pack(dc_top := t.dc_top, dc_bottom := t.dc_bottom,
                                    sw_top := t.sw_top, sw_bottom := t.sw_bottom)) AS VARCHAR) AS top_sectors_json,
           st.strongest_sectors_json AS strongest_sectors_json,
           CURRENT_TIMESTAMP AS built_at
    FROM days d
    LEFT JOIN tr.raw_tushare_moneyflow_mkt_dc f ON f.trade_date = d.trade_date
    LEFT JOIN limits l ON l.trade_date = d.trade_date
    LEFT JOIN ladder lad ON lad.trade_date = d.trade_date
    LEFT JOIN margin2 mg ON mg.trade_date = d.trade_date
    LEFT JOIN idxb ib ON ib.trade_date = d.trade_date
    LEFT JOIN lhb lb ON lb.trade_date = d.trade_date
    LEFT JOIN lhb_inst li ON li.trade_date = d.trade_date
    LEFT JOIN strongest st ON st.trade_date = d.trade_date
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
        # 索引含 content_type (2026-07-04 真库首跑实锤): 12 个 dc 板块代码同日兼具"行业"+"概念"
        # 双重归类是 tushare 源真实现象 (如 BK0733.DC 包装材料), 真实唯一键=四列非三列;
        # check_grain_uniqueness.py MART_GRAINS 同步声明。
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_pulse_sector "
                    f"ON {SECTOR_TABLE}(chain, sector_code, trade_date, content_type)")
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


def _late_dates(con, table: str, n: int, chain: str | None = None) -> list[str]:
    """表内最近 n 个已存在日期 (迟到列回补窗口; chain 限定板块表分链日期域)。"""
    if n <= 0:
        return []
    where = f"WHERE chain = {_sql_str(chain)}" if chain else ""
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT trade_date FROM {table} {where} ORDER BY trade_date DESC LIMIT {int(n)}"
    ).fetchall()]


def build_latest(conn=None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """增量: 补源已有而 pulse 表缺的日期 (分链检测) + 近 N 日迟到列回补 (DELETE+重插)。

    窗口/streak 在全量源历史上重算后只插缺日 → 增量行与全量重建逐 bit 一致 (确定性)。
    顺序: 板块表先补, 全市场表后补 (top_sectors_json 读板块表)。

    迟到列回补 (R1 根因5, 2026-07-03, owner=analysis/data_foundation_root_causes_20260703.md):
    行一旦插入即定格, 而 t+1 披露域 (margin/龙虎榜/limit) 在早跑日尚未到 raw → 该日行以 NULL
    (或部分行半值) 入库后永不回补。修法 = 每次增量对最近 lookback_late_days 个**已存在**源日
    DELETE+重插 — 复用全史窗口/streak 机制, 重插行与全量重建逐 bit 一致, 幂等 (行数不变仅列值治愈)。
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
        # schema 升级守卫: 表在但缺 v2/v3 哨兵列 (旧版残留) → INSERT 列数必炸, 直接全量重建
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN (?, ?)",
            [SECTOR_TABLE, MARKET_TABLE]).fetchall()}
        if ("content_type" not in cols or "strongest_sectors_json" not in cols
                or "flow_regime" not in cols):
            return {"mode": "rebuild", **rebuild_all(conn=con, cfg=cfg)}

        # 迟到列回补窗口: 先取"已存在"的最近 N 日 (缺日检测/插入之前取, 不与新插日重叠)
        late_n = int(cfg["lookback_late_days"])
        dc_late = _late_dates(con, SECTOR_TABLE, late_n, chain=CHAIN_DC)
        sw_late = _late_dates(con, SECTOR_TABLE, late_n, chain=CHAIN_SW)
        mkt_late = _late_dates(con, MARKET_TABLE, late_n)

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
        dc_dates = sorted(set(dc_missing) | set(dc_late))
        sw_dates = sorted(set(sw_missing) | set(sw_late))
        if dc_dates or sw_dates:
            # 迟到回补半边: 先删已存在的近 N 日行, 与缺日合并一次 INSERT 重插 (幂等 DELETE+重插)
            if dc_late:
                dc_late_in = ",".join(_sql_str(d) for d in dc_late)
                con.execute(f"DELETE FROM {SECTOR_TABLE} "
                            f"WHERE chain = '{CHAIN_DC}' AND trade_date IN ({dc_late_in})")
            if sw_late:
                sw_late_in = ",".join(_sql_str(d) for d in sw_late)
                con.execute(f"DELETE FROM {SECTOR_TABLE} "
                            f"WHERE chain = '{CHAIN_SW}' AND trade_date IN ({sw_late_in})")
            # v3: 无别名谓词 (最终 SELECT 层按链套用, 见 _sector_sql docstring)
            dc_where = ("trade_date IN (%s)" % ",".join(_sql_str(d) for d in dc_dates)
                        ) if dc_dates else "1=0"
            sw_where = ("trade_date IN (%s)" % ",".join(_sql_str(d) for d in sw_dates)
                        ) if sw_dates else "1=0"
            # inflow_breadth 按日独立 → 日期下推裁剪 dc_member 千万行级 JOIN (确定性不变)
            dc_day_where = ("m.trade_date IN (%s)" % ",".join(_sql_str(d) for d in dc_dates)
                            ) if dc_dates else "1=0"
            r = con.execute(
                f"INSERT INTO {SECTOR_TABLE} "
                f"{_sector_sql(cfg, dc_where, sw_where, dc_day_where)}").fetchone()
            sector_rows = int(r[0]) if r else 0

        mkt_missing = _missing_dates(con, f"""
            SELECT DISTINCT trade_date FROM tr.raw_tushare_daily
            WHERE trade_date >= ? AND trade_date NOT IN (
                SELECT DISTINCT trade_date FROM {MARKET_TABLE})
            ORDER BY 1""", [str(cfg["data_start_market"])])
        market_rows = 0
        mkt_dates = sorted(set(mkt_missing) | set(mkt_late))
        if mkt_dates:
            if mkt_late:
                mkt_late_in = ",".join(_sql_str(d) for d in mkt_late)
                con.execute(f"DELETE FROM {MARKET_TABLE} WHERE trade_date IN ({mkt_late_in})")
            where = "d.trade_date IN (%s)" % ",".join(_sql_str(d) for d in mkt_dates)
            r = con.execute(f"INSERT INTO {MARKET_TABLE} {_market_sql(cfg, where)}").fetchone()
            market_rows = int(r[0]) if r else 0
        con.commit()
        out = {"dc_added_days": len(dc_missing), "sw_added_days": len(sw_missing),
               "sector_rows": sector_rows, "market_added_days": len(mkt_missing),
               "market_rows": market_rows,
               "late_refreshed_days": {"dc": len(dc_late), "sw": len(sw_late), "market": len(mkt_late)}}
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
