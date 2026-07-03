"""institution_profile.py — 机构画像引擎 (edge"机构档案"数据层, 2026-07-02 探索弧 promote)

产品语义 (用户定调): 机构档案逐机构展示 收益/胜率 + 行业/年份/类型维度表现, **用户自己选择跟随**
(非自动全跟策略 — E2 实测全体机构无 alpha, 分层桶才有信号, verdict=inst_follow_e1e2_20260702)。

方法 (sandbox/inst_follow 探索弧验证, 三成本方案敏感性稳健):
  episode = 同机构(holder_name_norm)×同股票 的 建仓(新进,可增持)→部分了结(减持)→清仓(退出)/持有中。
  成本三方案: C1 窗口VWAP(主) / C2 期末价 / C3 龙虎榜机构席位日按额加权(缺→C1); 增持=加权平均成本。
  收益口径: realized_pnl / (cost×peak_shares) [峰值投入分母, 保守]; alpha = ret − 同窗 HS300。
  alpha_c1 口径 (方法学披露, 2026-07-03 审计修6): 基准 = 期界点到点收盘 (open/close 期各取
    <= 期界日最近 HS300 收盘), 成本 = 整窗 VWAP — 两窗不严格对齐, alpha 含窗口错位噪声;
  avg_hold_days = 披露期界日历天 (open_date→close_date), 非真实持仓天数 (期内实际买卖点不可知)。
  纪律: 被动产品(ETF/指数/联接=申赎驱动非选股观点)标记剔除; n<MIN_EPISODES 标 low_sample 不排名;
        行业维度用 PIT 行业 (v_sw_industry_pit as-of 建仓日, 非当前行业)。

复权口径红线: 成本与卖价全用 qfq (v_price_kline_qfq), 收益=含分红总收益 (禁 raw amount/volume 混算)。
数据: 全走 database_manifest 路由 (smartmoney holder 事件 / market qfq K线 / tushare_raw 龙虎榜+HS300+PIT行业)。
产物: feature_store (L2_feature, declare-on-build, data_layers 已声明) — fact_inst_episode /
      mart_inst_profile / mart_inst_profile_dim。wipeable, 全量重建 (rebuild_all)。
"""
from __future__ import annotations

import logging
from typing import Any

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

logger = logging.getLogger(__name__)

MIN_EPISODES = 10   # 画像排名样本量护栏 (设计定稿 §3: <10 标 low_sample 不进排名)
LOOKBACK_FIRST_WINDOW_DAYS = 92  # 首个披露期无 prev → 回看一季 (成本窗口下界)

# 被动产品判定 (E2 实测: ETF/联接申赎驱动的名册进出非选股观点, 混入会把指数 beta 当机构技能)
PASSIVE_NAME_PATTERNS = ("%ETF%", "%交易型开放%", "%指数%", "%联接%")


def _db(alias: str) -> str:
    return str(get_database_manifest().path_for(alias))


def _attach_sources(con) -> None:
    con.execute(f"ATTACH IF NOT EXISTS '{_db('smartmoney')}' AS sm (READ_ONLY)")
    con.execute(f"ATTACH IF NOT EXISTS '{_db('market')}' AS mk (READ_ONLY)")
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")


def build_period_windows(con) -> int:
    """每 (stock, report_date) 披露窗口 (prev_period, period] 的 C1/C2/C3 价格 (SQL 一把出)。"""
    con.execute(f"""
    CREATE OR REPLACE TABLE period_windows AS
    WITH periods AS (
        SELECT DISTINCT stock_code, report_date
        FROM sm.fact_top10_holder_period WHERE length(report_date) = 8
    ), win AS (
        SELECT stock_code, report_date,
               LAG(report_date) OVER (PARTITION BY stock_code ORDER BY report_date) AS prev_period
        FROM periods
    ), win_dated AS (
        SELECT stock_code, report_date, prev_period,
               COALESCE(strftime(strptime(prev_period,'%Y%m%d'), '%Y-%m-%d'),
                        strftime(strptime(report_date,'%Y%m%d') - INTERVAL {LOOKBACK_FIRST_WINDOW_DAYS} DAY, '%Y-%m-%d')) AS w_start,
               strftime(strptime(report_date,'%Y%m%d'), '%Y-%m-%d') AS w_end
        FROM win
    ), vwap AS (
        SELECT w.stock_code, w.report_date,
               SUM(k.close * k.volume) / NULLIF(SUM(k.volume), 0) AS c1_vwap
        FROM win_dated w
        JOIN mk.v_price_kline_qfq k
          ON k.code = w.stock_code AND k.date > w.w_start AND k.date <= w.w_end
        GROUP BY 1, 2
    ), eod AS (
        SELECT w.stock_code, w.report_date, k.close AS c2_eod
        FROM win_dated w
        JOIN mk.v_price_kline_qfq k ON k.code = w.stock_code AND k.date <= w.w_end
        QUALIFY ROW_NUMBER() OVER (PARTITION BY w.stock_code, w.report_date ORDER BY k.date DESC) = 1
    ), lhb AS (
        SELECT w.stock_code, w.report_date,
               SUM(k.close * ABS(t.net_buy)) / NULLIF(SUM(ABS(t.net_buy)), 0) AS c3_lhb
        FROM win_dated w
        JOIN tr.raw_tushare_top_inst t
          ON substr(t.ts_code,1,6) = w.stock_code AND t.exalter LIKE '%机构%'
         AND strftime(strptime(t.trade_date,'%Y%m%d'),'%Y-%m-%d') > w.w_start
         AND strftime(strptime(t.trade_date,'%Y%m%d'),'%Y-%m-%d') <= w.w_end
        JOIN mk.v_price_kline_qfq k
          ON k.code = w.stock_code AND k.date = strftime(strptime(t.trade_date,'%Y%m%d'),'%Y-%m-%d')
        GROUP BY 1, 2
    )
    SELECT w.stock_code, w.report_date, w.prev_period, w.w_start, w.w_end,
           v.c1_vwap, e.c2_eod, l.c3_lhb, COALESCE(l.c3_lhb, v.c1_vwap) AS c3_eff
    FROM win_dated w
    LEFT JOIN vwap v USING (stock_code, report_date)
    LEFT JOIN eod  e USING (stock_code, report_date)
    LEFT JOIN lhb  l USING (stock_code, report_date)
    """)
    return con.execute("SELECT COUNT(*) FROM period_windows").fetchone()[0]


def run_episode_state_machine(rows: list[tuple]) -> tuple[list[dict], dict]:
    """纯函数状态机: 有序事件行 → episodes (单测证伪门在此)。

    rows: (holder, stock, period, status, is_exit, shares, chg, htype, notice, c1, c2, c3)
          须按 (holder, stock, period, is_exit) 排序。
    """
    episodes: list[dict] = []
    open_eps: dict[tuple, dict] = {}
    stats = {"opened": 0, "closed": 0, "seeded": 0, "no_price_skip": 0,
             "unpriced_close": 0, "superseded": 0}

    def _close(ep: dict, prices: tuple, close_date: str, notice: str | None,
               status: str = "closed") -> None:
        for k, sell in zip(("c1", "c2", "c3"), prices):
            if sell and ep[f"cost_{k}"]:
                ep[f"realized_{k}"] += ep["shares"] * (sell - ep[f"cost_{k}"])
        ep.update(status=status, close_date=close_date, close_notice=notice)
        episodes.append(ep)
        stats["closed" if status == "closed" else status] += 1

    for (holder, stock, period, status, is_exit, shares, chg, htype, notice, c1, c2, c3) in rows:
        key = (holder, stock)
        ep = open_eps.get(key)

        # 退出分支先于无价跳过 (2026-07-03 审计修2c): 退出行即使窗口无价也必须关闭 episode,
        # 否则退出被吞 → 幽灵 holding。无价 → status='unpriced_close': 最终腿 PnL 不可测
        # (不知道≠0, 不拿旧窗价估), 已实现部分保留; 富化/画像只认 'closed', 该类不进评级。
        if is_exit or status == "退出":
            if ep:
                if c1 is None:
                    ep.update(status="unpriced_close", close_date=period, close_notice=notice)
                    episodes.append(ep)
                    stats["unpriced_close"] += 1
                else:
                    _close(ep, (c1, c2 or c1, c3 or c1), period, notice)
                del open_eps[key]
            continue

        if c1 is None:
            stats["no_price_skip"] += 1
            continue
        c2, c3 = c2 or c1, c3 or c1

        if status == "新进" or (ep is None and status in ("增持", "减持", "不变")):
            # '新进'遇已开 episode = 中间退出披露缺失 (2026-07-03 审计修2c): 先按当期窗口价
            # 关闭旧 episode (status='superseded', 退出时点不可知 → 不进 'closed' 评级),
            # 再开新 — 禁 dict 直接覆盖静默丢 episode。
            if ep is not None:
                _close(ep, (c1, c2, c3), period, notice, status="superseded")
            seeded = status != "新进"
            open_eps[key] = {
                "holder": holder, "stock": stock, "holder_type": htype,
                "open_date": period, "open_notice": notice, "seeded": seeded,
                "shares": float(shares or 0),
                "cost_c1": c1, "cost_c2": c2, "cost_c3": c3,
                "realized_c1": 0.0, "realized_c2": 0.0, "realized_c3": 0.0,
                "n_adds": 0, "n_trims": 0, "peak_shares": float(shares or 0),
            }
            stats["opened"] += 1
            stats["seeded"] += int(seeded)
            continue

        if ep is None:
            continue
        if status == "增持":
            delta = float(chg or 0)
            new_shares = float(shares if shares is not None else ep["shares"] + delta)
            if new_shares > 0 and delta > 0:
                for k, px in (("c1", c1), ("c2", c2), ("c3", c3)):
                    ep[f"cost_{k}"] = (ep[f"cost_{k}"] * ep["shares"] + delta * px) / new_shares
            ep["shares"] = new_shares
            ep["peak_shares"] = max(ep["peak_shares"], new_shares)
            ep["n_adds"] += 1
        elif status == "减持":
            sold = min(abs(float(chg or 0)), ep["shares"])
            for k, px in (("c1", c1), ("c2", c2), ("c3", c3)):
                ep[f"realized_{k}"] += sold * (px - ep[f"cost_{k}"])
            ep["shares"] = float(shares if shares is not None else ep["shares"] - sold)
            ep["n_trims"] += 1
        # 不变 → 无操作

    for ep in open_eps.values():
        ep.update(status="holding", close_date=None, close_notice=None)
        episodes.append(ep)
    return episodes, stats


_EPISODE_COLS = [
    "holder", "stock", "holder_type", "open_date", "open_notice", "seeded", "shares",
    "cost_c1", "cost_c2", "cost_c3", "realized_c1", "realized_c2", "realized_c3",
    "n_adds", "n_trims", "peak_shares", "status", "close_date", "close_notice",
]


def build_episodes(con) -> dict:
    """事件流 → fact_inst_episode (含 alpha/被动标记/PIT 行业)。"""
    # share_class='A' (2026-07-03 审计修2a): B/H 股行混入 A 股 qfq 价计价 = 价格错配, 硬滤;
    # QUALIFY 去重 (修2b): 源 (holder,stock,period,is_exit_row) 存在双行 (实测 60 组) → 状态机
    # 会双计开/平仓, 稳定序取 1 行 (rank/row_seq 主行优先, notice 新者优先, raw_hash 决胜)。
    rows = con.execute("""
        SELECT h.holder_name_norm, h.stock_code, h.report_date, h.change_status, h.is_exit_row,
               h.shares_approx, h.hold_change_num, h.holder_type, h.notice_date,
               w.c1_vwap, w.c2_eod, w.c3_eff
        FROM sm.fact_top10_holder_period h
        JOIN period_windows w ON w.stock_code = h.stock_code AND w.report_date = h.report_date
        WHERE h.holder_name_norm IS NOT NULL AND length(h.report_date) = 8
          AND h.share_class = 'A'
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY h.holder_name_norm, h.stock_code, h.report_date, h.is_exit_row
            ORDER BY h.holder_rank NULLS LAST, h.row_seq,
                     COALESCE(h.notice_date, '') DESC, COALESCE(h.raw_hash, '')) = 1
        ORDER BY h.holder_name_norm, h.stock_code, h.report_date, h.is_exit_row
    """).fetchall()
    episodes, stats = run_episode_state_machine(rows)

    con.execute(f"""CREATE OR REPLACE TABLE _ep_raw (
        holder VARCHAR, stock VARCHAR, holder_type VARCHAR,
        open_date VARCHAR, open_notice VARCHAR, seeded BOOLEAN, shares DOUBLE,
        cost_c1 DOUBLE, cost_c2 DOUBLE, cost_c3 DOUBLE,
        realized_c1 DOUBLE, realized_c2 DOUBLE, realized_c3 DOUBLE,
        n_adds INTEGER, n_trims INTEGER, peak_shares DOUBLE,
        status VARCHAR, close_date VARCHAR, close_notice VARCHAR)""")
    con.executemany(
        f"INSERT INTO _ep_raw VALUES ({','.join('?' * len(_EPISODE_COLS))})",
        [[ep.get(c) for c in _EPISODE_COLS] for ep in episodes])

    # 富化: ret/alpha (closed) + 被动标记 + PIT 行业 (as-of 建仓日)
    passive_pred = " OR ".join(f"holder LIKE '{p}'" for p in PASSIVE_NAME_PATTERNS)
    con.execute(f"""
    CREATE OR REPLACE TABLE fact_inst_episode AS
    WITH bench AS (
        SELECT trade_date, close FROM tr.raw_tushare_index_daily WHERE ts_code = '000300.SH'
    ), base AS (
        SELECT *,
               CASE WHEN status='closed' AND cost_c1 > 0 AND peak_shares > 0
                    THEN realized_c1 / (cost_c1 * peak_shares) END AS ret_c1,
               ({passive_pred}) AS is_passive
        FROM _ep_raw
    ), bo AS (
        SELECT b.*, x.close AS bench_open
        FROM base b LEFT JOIN bench x ON x.trade_date <= b.open_date
        QUALIFY ROW_NUMBER() OVER (PARTITION BY b.holder, b.stock, b.open_date, b.status
                                   ORDER BY x.trade_date DESC) = 1
    ), ba AS (
        SELECT bo.*,
               CASE WHEN bo.status='closed' AND bo.bench_open > 0 AND bo.ret_c1 IS NOT NULL
                    THEN bo.ret_c1 - (x.close / bo.bench_open - 1) END AS alpha_c1
        FROM bo LEFT JOIN bench x ON bo.close_date IS NOT NULL AND x.trade_date <= bo.close_date
        QUALIFY ROW_NUMBER() OVER (PARTITION BY bo.holder, bo.stock, bo.open_date, bo.status
                                   ORDER BY x.trade_date DESC) = 1
    )
    SELECT * FROM ba
    """)
    # PIT 行业标 (v_sw_industry_pit: in_date<=open<out_date)
    con.execute("""
    CREATE OR REPLACE TABLE fact_inst_episode AS
    SELECT e.*, p.l1_name AS sw_l1_at_open
    FROM fact_inst_episode e
    LEFT JOIN tr.v_sw_industry_pit p
      ON p.stock_code = e.stock AND p.in_date <= e.open_date
     AND (p.out_date IS NULL OR p.out_date > e.open_date)
    """)
    con.execute("DROP TABLE _ep_raw")
    stats["episodes"] = con.execute("SELECT COUNT(*) FROM fact_inst_episode").fetchone()[0]
    return stats


def build_profiles(con) -> dict[str, int]:
    """机构画像: 总体 + 维度 (industry_pit / year / holder_type)。closed+非seeded+非passive 进评级。"""
    base_where = "status='closed' AND NOT seeded AND NOT is_passive AND alpha_c1 IS NOT NULL"
    con.execute(f"""
    CREATE OR REPLACE TABLE mart_inst_profile AS
    SELECT holder, ANY_VALUE(holder_type) AS holder_type,
           COUNT(*) AS n_closed,
           median(alpha_c1) AS median_alpha,
           AVG(alpha_c1) AS avg_alpha,
           SUM(CASE WHEN alpha_c1 > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate_alpha,
           median(ret_c1) AS median_ret,
           AVG(date_diff('day', strptime(open_date,'%Y%m%d'), strptime(close_date,'%Y%m%d'))) AS avg_hold_days,
           COUNT(*) < {MIN_EPISODES} AS low_sample
    FROM fact_inst_episode WHERE {base_where}
    GROUP BY holder
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE mart_inst_profile_dim AS
    WITH dims AS (
        SELECT holder, 'industry_pit' AS dim_type, COALESCE(sw_l1_at_open,'未知') AS dim_value, alpha_c1
        FROM fact_inst_episode WHERE {base_where}
        UNION ALL
        SELECT holder, 'year', substr(open_date,1,4), alpha_c1
        FROM fact_inst_episode WHERE {base_where}
        UNION ALL
        SELECT holder, 'holder_type', COALESCE(holder_type,'未知'), alpha_c1
        FROM fact_inst_episode WHERE {base_where}
    )
    SELECT holder, dim_type, dim_value,
           COUNT(*) AS n_closed,
           median(alpha_c1) AS median_alpha,
           SUM(CASE WHEN alpha_c1 > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate_alpha,
           COUNT(*) < {MIN_EPISODES} AS low_sample
    FROM dims GROUP BY 1, 2, 3
    """)
    return {
        "profiles": con.execute("SELECT COUNT(*) FROM mart_inst_profile").fetchone()[0],
        "profile_dims": con.execute("SELECT COUNT(*) FROM mart_inst_profile_dim").fetchone()[0],
    }


def rebuild_all() -> dict[str, Any]:
    """全量重建 (L2 wipeable, declare-on-build)。daily 增量非必需 — holder 季频+临时低频, 手动/随管线全量重建即可。"""
    con = duck_connect(_db("feature_store"), read_only=False)
    try:
        _attach_sources(con)
        n_win = build_period_windows(con)
        ep_stats = build_episodes(con)
        prof = build_profiles(con)
        con.execute("CHECKPOINT")
        out = {"period_windows": n_win, **ep_stats, **prof}
        logger.info("[institution_profile] rebuild_all: %s", out)
        return out
    finally:
        con.close()


# ── 读侧 API (档案 serving, router 经此访问 — 本模块是数据模块成员 owns 这些表;
#    PIT 注意: 档案展示"截至今天的全部战绩"给用户手选=合法 (今日决策用今日可得信息);
#    D 阶段回测选机构必须用 expanding PIT 评级, 禁用本读侧 (设计文档 §4 红线)) ──────

def _ro_conn():
    return duck_connect(_db("feature_store"), read_only=True)


def list_profiles(*, holder_type: str | None = None, min_episodes: int = MIN_EPISODES,
                  order_by: str = "median_alpha", limit: int = 50) -> list[dict[str, Any]]:
    """机构排名列表 (默认剔 low_sample; order_by 白名单防注入)。"""
    order_whitelist = {"median_alpha", "win_rate_alpha", "n_closed", "avg_alpha"}
    if order_by not in order_whitelist:
        raise ValueError(f"order_by 只允许 {sorted(order_whitelist)}")
    con = _ro_conn()
    try:
        where, params = ["n_closed >= ?"], [int(min_episodes)]
        if holder_type:
            where.append("holder_type = ?")
            params.append(holder_type)
        rows = con.execute(f"""
            SELECT holder, holder_type, n_closed, median_alpha, avg_alpha, win_rate_alpha,
                   median_ret, avg_hold_days, low_sample
            FROM mart_inst_profile WHERE {' AND '.join(where)}
            ORDER BY {order_by} DESC LIMIT ?""", [*params, int(limit)]).fetchall()
        cols = ["holder", "holder_type", "n_closed", "median_alpha", "avg_alpha",
                "win_rate_alpha", "median_ret", "avg_hold_days", "low_sample"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def get_profile(holder: str) -> dict[str, Any] | None:
    """单机构档案: 总体 + 维度表现 + episode 时间线 (前端档案页数据契约)。"""
    con = _ro_conn()
    try:
        head = con.execute(
            "SELECT holder, holder_type, n_closed, median_alpha, avg_alpha, win_rate_alpha, "
            "median_ret, avg_hold_days, low_sample FROM mart_inst_profile WHERE holder = ?",
            [holder]).fetchone()
        if head is None:
            return None
        cols = ["holder", "holder_type", "n_closed", "median_alpha", "avg_alpha",
                "win_rate_alpha", "median_ret", "avg_hold_days", "low_sample"]
        out: dict[str, Any] = dict(zip(cols, head))
        out["dims"] = [dict(zip(["dim_type", "dim_value", "n_closed", "median_alpha",
                                 "win_rate_alpha", "low_sample"], r)) for r in con.execute(
            "SELECT dim_type, dim_value, n_closed, median_alpha, win_rate_alpha, low_sample "
            "FROM mart_inst_profile_dim WHERE holder = ? ORDER BY dim_type, median_alpha DESC",
            [holder]).fetchall()]
        out["episodes"] = [dict(zip(["stock", "open_date", "close_date", "status", "ret_c1",
                                     "alpha_c1", "n_adds", "n_trims", "sw_l1_at_open", "seeded"], r))
                           for r in con.execute(
            "SELECT stock, open_date, close_date, status, ret_c1, alpha_c1, n_adds, n_trims, "
            "sw_l1_at_open, seeded FROM fact_inst_episode WHERE holder = ? "
            "ORDER BY open_date DESC LIMIT 200", [holder]).fetchall()]
        return out
    finally:
        con.close()


def recent_signals(*, days: int = 30, min_holder_episodes: int = MIN_EPISODES,
                   limit: int = 100) -> list[dict[str, Any]]:
    """最新建仓信号流 (跟随入口): 近 N 天新开 episode × 该机构历史战绩 (今日视角合法)。"""
    con = _ro_conn()
    try:
        rows = con.execute("""
            SELECT e.holder, e.stock, e.open_date, e.open_notice, e.holder_type,
                   e.sw_l1_at_open, e.n_adds,
                   p.n_closed, p.median_alpha, p.win_rate_alpha
            FROM fact_inst_episode e
            JOIN mart_inst_profile p ON p.holder = e.holder
            WHERE e.status = 'holding' AND NOT e.seeded AND NOT e.is_passive
              AND p.n_closed >= ?
              AND strptime(e.open_date, '%Y%m%d') >= now() - to_days(CAST(? AS INTEGER))
            ORDER BY p.median_alpha DESC, e.open_date DESC LIMIT ?""",
            [int(min_holder_episodes), int(days), int(limit)]).fetchall()
        cols = ["holder", "stock", "open_date", "open_notice", "holder_type", "sw_l1_at_open",
                "n_adds", "holder_n_closed", "holder_median_alpha", "holder_win_rate"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()
