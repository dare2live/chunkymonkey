"""Tier3 机构披露研究画像；只提供 evidence，不产生 CandidateSignal 或执行指令。

现有档案展示历史收益/胜率与分层表现；这些是披露数据研究结果，不是建仓信号、
自动跟随策略或 StrategyRelease。

方法 (sandbox/inst_follow 探索弧验证, 三成本方案敏感性稳健):
  episode = 同机构(holder_name_norm)×同股票 的 建仓(新进,可增持)→部分了结(减持)→清仓(退出)/持有中。
  成本三方案: C1 窗口VWAP(主) / C2 期末价 / C3 龙虎榜机构席位日按额加权(缺→C1); 增持=加权平均成本。
  收益口径: realized_pnl / (cost×peak_shares) [峰值投入分母, 保守]; alpha = ret − 同窗 HS300。
  alpha_c1 口径 (方法学披露, 2026-07-03 审计修6): 基准 = 期界点到点收盘 (open/close 期各取
    <= 期界日最近 HS300 收盘), 成本 = 整窗 VWAP — 两窗不严格对齐, alpha 含窗口错位噪声;
  avg_hold_days = 披露期界日历天 (open_date→close_date), 非真实持仓天数 (期内实际买卖点不可知)。
  纪律: 被动产品(ETF/指数/联接=申赎驱动非选股观点)不进技能排名指标; n<MIN_EPISODES 标 low_sample 不排名;
        但有 episode 的 holder 仍写 display 档案行 (deep-link), metrics 未知则 NULL。
        行业维度用 PIT 行业 (v_sw_industry_pit as-of 建仓日, 非当前行业)。

复权口径红线: 成本与卖价全用 qfq (v_price_kline_qfq), 收益=含分红总收益 (禁 raw amount/volume 混算)。
数据: 全走 database_manifest 路由 (smartmoney holder 事件 / market qfq K线 / tushare_raw 龙虎榜+HS300+PIT行业)。
产物: feature_store (L2_feature, declare-on-build, data_layers 已声明) — fact_inst_episode /
      mart_inst_profile / mart_inst_profile_dim。wipeable, 全量重建 (rebuild_all)。

E0 read note: disclosure provider-field consumers prefer accepted canonical via
``disclosure_research_read`` when shadow MATCH.  Episode rebuild uses
``disclosure_enrichment_projection`` (canonical-only after holders fact retire).

K4 consume deferral (2026-08-28): this module does **not** ATTACH the
``org_holding`` alias. Episode spine is ``holders_top10`` 十大流通股东进出
(``holder_name_norm`` × stock). ``org_holding`` is 机构持股明细 at
``holder_code`` × ``fund_derivecode`` — a different grain. A fake ATTACH
without a real SELECT would be theater; folding org_holding into
``run_episode_state_machine`` would invent a product join, not repair a
consume chain. The API router already opens the ``org_holding`` alias for
``compare_disclosure_research_shadow``, and ``disclosure_research_read``
already maps ``org_holding`` → ``canonical_org_holding_detail_period``.
Residual owner = K5 资金方侧 / named query, not this knife.
"""
from __future__ import annotations

import logging
from typing import Any

from services.data_sources.disclosure_enrichment_projection import (
    holders_episode_events_sql,
    holders_period_keys_sql,
)
from services.holder_capital_role import classify_capital_role
from services.research_identity import annotate_holder
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE as HOLDERS_CANONICAL,
)
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect
from services.data_access.spec import load_registry
from services.top_inst_seat_publish import daily_metric_filter_sql

logger = logging.getLogger(__name__)

MIN_EPISODES = 10   # 画像排名样本量护栏 (设计定稿 §3: <10 标 low_sample 不进排名)
LOOKBACK_FIRST_WINDOW_DAYS = 92  # 首个披露期无 prev → 回看一季 (成本窗口下界)
HOLDERS_REBUILD_SOURCE = "canonical_only"

# 被动产品判定 (E2 实测: ETF/联接申赎驱动的名册进出非选股观点, 混入会把指数 beta 当机构技能)
PASSIVE_NAME_PATTERNS = ("%ETF%", "%交易型开放%", "%指数%", "%联接%")

_ACCESS_REG = None


def _access_reg():
    global _ACCESS_REG
    if _ACCESS_REG is None:
        _ACCESS_REG = load_registry()
    return _ACCESS_REG


def _tr_entity(entity: str) -> str:
    """Resolve DataAccess physical table for feature_store SQL.

    ``tushare_raw`` entities live on the READ_ONLY ``tr`` attach;
    ``smartmoney`` publication entities (B2 fact_index_daily /
    fact_top_inst_seat_daily) on ``sm``.
    """
    ent = _access_reg().entity(entity)
    if ent.db == "tushare_raw":
        return f"tr.{ent.table}"
    if ent.db == "smartmoney":
        return f"sm.{ent.table}"
    raise ValueError(
        f"unsupported data_access db for institution SQL: {ent.db!r} (entity={entity})"
    )


def _db(alias: str) -> str:
    return str(get_database_manifest().path_for(alias))


def _attach_sources(con) -> None:
    # K4 deferral: do not ATTACH org_holding. Episode spine is holders_top10
    # (十大流通股东进出). org_holding grain is holder_code × fund_derivecode
    # (机构持股明细). Mixing it into run_episode_state_machine would invent a
    # product join, not repair a consume chain. Router already opens
    # org_holding alias for compare_disclosure_research_shadow;
    # disclosure_research_read already maps org_holding → canonical.
    # Residual: K5 资金方侧 / named query.
    con.execute(f"ATTACH IF NOT EXISTS '{_db('smartmoney')}' AS sm (READ_ONLY)")
    con.execute(f"ATTACH IF NOT EXISTS '{_db('market')}' AS mk (READ_ONLY)")
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")


def build_period_windows(con) -> int:
    """每 (stock, report_date) 披露窗口 (prev_period, period] 的 C1/C2/C3 价格 (SQL 一把出)。"""
    period_keys = holders_period_keys_sql(
        canonical_qual=f"sm.{HOLDERS_CANONICAL}",
    )
    con.execute(f"""
    CREATE OR REPLACE TABLE period_windows AS
    WITH periods AS (
        {period_keys}
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
        -- C3 龙虎榜机构席位日按额加权成本 —— 业主口径 (2026-09-11/09-12 批准): D1 同股同日
        -- 同席位买卖金额完全相同的多榜记录已在发布面 (fact_top_inst_seat_daily) 按一笔折叠
        -- (匿名/机构专用同样处理, 可能少算); D2 投资者类别行不计入; D3 只计单日榜; D4 只算
        -- 项目股票池内证券 (排除可转债/北交所/B股)。四条口径合一为 daily_metric_filter_sql()
        -- (发布模块 top_inst_seat_publish 拥有, 此处 import 不复制字面量) —— 注意
        -- exalter LIKE '%机构%' 单独会把「机构投资者」这个投资者类别行也吃进来, 所以谓词
        -- 必须在这里的 JOIN 条件里也生效, 不能只信 LIKE。
        SELECT w.stock_code, w.report_date,
               SUM(k.close * ABS(t.net_buy)) / NULLIF(SUM(ABS(t.net_buy)), 0) AS c3_lhb
        FROM win_dated w
        JOIN {_tr_entity("top_inst")} t
          ON substr(t.ts_code,1,6) = w.stock_code AND t.exalter LIKE '%机构%'
         AND {daily_metric_filter_sql()}
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


def _load_person_allowlist(con) -> frozenset[str]:
    """自然人白名单 = dim_holder_name_tag 里在册的名字 (业主拍板的 9 人牛散名录)。

    从**已发布的表**读而不是 import publish 脚本的内嵌名录: 那张表是这条链的 accepted 面,
    脚本内嵌的是它的生产者。消费生产者 = 绕过 accepted 层(红线 4 依赖只向下)。

    表缺失/为空一律返回空集合让调用方 fail closed —— "还没建名录"与"名录里没有这个人"
    是两件事, 不许静默退化成"收全部自然人"。
    """
    try:
        rows = con.execute(
            "SELECT DISTINCT holder_name FROM sm.dim_holder_name_tag WHERE tag = 'niusan'"
        ).fetchall()
    except Exception:  # noqa: BLE001 — 表不存在时返回空, 由调用方拒绝继续
        return frozenset()
    return frozenset(str(r[0]) for r in rows if r[0])


def build_episodes(con) -> dict:
    """事件流 → fact_inst_episode (含 alpha/被动标记/PIT 行业)。"""
    # share_class='A' (2026-07-03 审计修2a): B/H 股行混入 A 股 qfq 价计价 = 价格错配, 硬滤;
    # QUALIFY 去重 (修2b): 源 (holder,stock,period,is_exit_row) 存在双行 (实测 60 组) → 状态机
    # 会双计开/平仓, 稳定序取 1 行 (rank/row_seq 主行优先, notice 新者优先, raw_hash 决胜)。
    # E0: canonical-only spine (holders fact retired 2026-07-26).
    events = holders_episode_events_sql(
        canonical_qual=f"sm.{HOLDERS_CANONICAL}",
    )

    # ── 资金角色切分 (2026-09-08, 业主点名) ──────────────────────────────────────
    # 同一个 holder_code 下的「-自有资金」与「-客户资金」是**经济上不同的行为主体**:
    # 前者是机构自己的判断, 后者是代客。只按码合并会把它们并成一个"机构", 毁掉信号。
    # 实测: 10088552(华泰金融控股香港) 一个码下同时挂着自有资金 360 行 / 客户资金 93 行 /
    # 无标注 11 行。全库这样的码只有 1 个 —— 少, 但性质是真的, 且随新数据会再出现。
    #
    # 反过来, 排版差异必须合: 10137791(J.P.Morgan) 一个码下 12 种写法, 其中 11 种是自有资金,
    # 差别是 "Secur ities" / "Securitie s" / "JP.Morgan" 这类错字, 还有 en-dash 与连字符之别。
    # 所以判据不能是"名字不同就分开", 只能按**资金角色**这一个语义轴分。
    #
    # 角色分类走 holder_capital_role.py(读 holder_capital_role.yaml 的 typed 词表 + 政策依据
    # URL), 不在 SQL 里重写一遍正则 —— 那样 YAML 就不再是单一真相源。
    role_rows = con.execute(
        f"SELECT DISTINCT COALESCE(holder_name_norm, holder_name) FROM sm.{HOLDERS_CANONICAL}"
        " WHERE NOT is_exit_row"
    ).fetchall()
    role_pairs = []
    for (nm,) in role_rows:
        if not nm:
            continue
        tags = classify_capital_role(str(nm)).tags
        role = next((t for t in ("own_funds_account", "client_funds_account") if t in tags), None)
        if role:
            role_pairs.append((str(nm), role))
    con.execute("CREATE OR REPLACE TABLE _ep_capital_role (holder_name VARCHAR, capital_role VARCHAR)")
    if role_pairs:
        con.executemany("INSERT INTO _ep_capital_role VALUES (?, ?)", role_pairs)

    # 有角色的名字, identity_key 追加 '#<role>'; 无角色的原样(不引入 '#none' 这种假区分)。
    events = f"""
        SELECT h.* EXCLUDE (identity_key),
               h.identity_key || COALESCE('#' || r.capital_role, '') AS identity_key
          FROM ({events}) AS h
          LEFT JOIN _ep_capital_role r ON r.holder_name = h.holder_name_norm
    """
    # ── 身份键 (2026-09-08 Step 4) ────────────────────────────────────────────────
    # 原来用 holder_name_norm 当键。名字不是稳定身份: 同实体多写法、不同实体同名。
    # 实测: episode 里 124,874 个名字只有 33% 能对上 dim_holder_identity。
    # 现在用 identity_key(单一计算点在 holders_episode_events_sql), 并按 kind 分流:
    #
    #   institution -> 全收。有码 829,249 行的 42,927 个身份**恰好等于**
    #                  dim_holder_identity 的行数, 覆盖完整。
    #   person      -> 只收 dim_holder_name_tag 里在册的名字(业主拍板的 9 人牛散名录)。
    #                  其余 77,000+ 个自然人是一次性出现、无跟随价值, 收进来只会让
    #                  "机构档案"名不副实。这会砍掉约 47% 的 episode 行 —— 是有意的。
    #
    # 白名单从**已发布的 dim_holder_name_tag 读**, 不 import publish 脚本的内嵌名录:
    # 那张表是这条链的 accepted 面, 且带 known_from —— PIT 约束(标签只在公开知名之后可用)
    # 要靠它, 见下面 known_from 的处理。表不存在时 fail closed(不静默退化成"收全部人")。
    person_allow = _load_person_allowlist(con)
    if not person_allow:
        raise ValueError(
            "dim_holder_name_tag 为空或不存在 —— 自然人白名单是 Step 4 的硬前置, "
            "缺它就分不清'这个人该进档案'和'我们还没建名录', 拒绝静默收全部自然人"
        )
    allow_sql = ", ".join(f"'{n}'" for n in sorted(person_allow))

    rows = con.execute(f"""
        SELECT h.identity_key, h.stock_code, h.report_date, h.change_status, h.is_exit_row,
               h.shares_approx, h.hold_change_num, h.holder_type, h.notice_date,
               w.c1_vwap, w.c2_eod, w.c3_eff
        FROM ({events}) AS h
        JOIN period_windows w ON w.stock_code = h.stock_code AND w.report_date = h.report_date
        WHERE h.identity_key IS NOT NULL AND length(h.report_date) = 8
          AND h.share_class = 'A'
          AND (h.identity_kind = 'institution'
               OR h.holder_name_norm IN ({allow_sql}))
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY h.identity_key, h.stock_code, h.report_date, h.is_exit_row
            ORDER BY h.holder_rank NULLS LAST, h.row_seq,
                     COALESCE(h.notice_date, '') DESC, COALESCE(h.raw_hash, '')) = 1
        ORDER BY h.identity_key, h.stock_code, h.report_date, h.is_exit_row
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

    # 身份维 (2026-09-08 Step 4): identity_key -> (kind, grade, 显示名)。
    # holder 列现在存的是 identity_key(code:xxx / name:xxx), 不是人可读的名字, 所以
    # 显示名必须单独带出来 —— 否则前端与被动判定都拿不到名字。
    # 同一个 identity_key 可能对应多种名字写法(这正是换键要解决的问题)。
    #
    # 显示名选择必须确定性 (2026-09-12 业主裁定, 根因见下): 按该名字在源表里的
    # 出现行数降序, 行数相同则按名字字典序升序, 取第一个。这两个信息 (出现行数/
    # 字典序) 在这段 SQL 的上下文里都拿得到 (h.holder_name_norm 本身就是源表列),
    # 不需要退化成"只按字典序最小"。
    #
    # 根因 (2026-09-12 实测): 原来用 any_value(holder_name_norm) 任选一个变体当
    # holder_display —— DuckDB 的 any_value 不保证跨次运行稳定。对 institution_profile
    # 做一次全量重建 rebuild_all() (源数据未变), mart_inst_profile 42,149 行数与
    # identity_key 体系完全不变, 但 holder 列取值集合变了: 320 个名字消失、315 个
    # 新出现 (distinct 42,020 -> 42,015); 这 635 个名字在源表
    # smartmoney.canonical_top10_float_holders_period 的 holder_name / holder_name_norm
    # 里全部仍然存在, 证实不是源数据变化, 是 any_value 换了挑法。
    # 展示名在两次重建之间漂移会让前端/按名字查询的下游看到"同一家机构改名了"。
    # 牛散的 PIT 边界与身份置信度 (2026-09-08 Step 4): 做成**数据属性**不做隐藏过滤。
    # known_from = 该人公开知名之日。这 9 个人是 2026 年从网络调研挑出来的, 挑他们的理由
    # 恰恰是他们后来出名了 —— 拿他们知名之前的持仓做跟随回测就是"跟随事后被证明做对的人",
    # 是选择偏差(同 feedback-param-selection-peek)。所以给每段标 niusan_usable_at_open,
    # 消费方自己决定收不收, 而不是在这里悄悄 WHERE 掉: 悄悄过滤会让"先例不足"看起来像
    # "没有先例", 两者要能区分。
    # identity_confidence 同理带出来: 实测徐开东既是唯一 suspected_multiple, 又是身份标志
    # 未记录(holder_type_proxy)的 —— 两个独立信号都说"不知道这是谁", 默认不该进跟随池。
    con.execute("""
    CREATE OR REPLACE TABLE _ep_niusan AS
    SELECT holder_name, ANY_VALUE(known_from) AS known_from,
           ANY_VALUE(identity_confidence) AS identity_confidence,
           ANY_VALUE(identity_grade) AS niusan_identity_grade
      FROM sm.dim_holder_name_tag WHERE tag = 'niusan' GROUP BY holder_name
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE _ep_identity AS
    WITH h AS (
        {events}
    ), name_counts AS (
        -- "该名字在源表里的出现行数" = 这个 identity_key 下, 该 holder_name_norm
        -- 写法在 h (未去重的原始披露事件) 里出现了多少行。
        SELECT identity_key, holder_name_norm, COUNT(*) AS n
          FROM h
         WHERE identity_key IS NOT NULL AND holder_name_norm IS NOT NULL
         GROUP BY identity_key, holder_name_norm
    ), best_name AS (
        -- 出现行数降序; 行数相同按名字字典序升序 —— ORDER BY 的 key 里已经含
        -- holder_name_norm 本身, 同一 identity_key 分区内不可能有并列名次。
        SELECT identity_key, holder_name_norm AS holder_display
          FROM name_counts
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY identity_key ORDER BY n DESC, holder_name_norm ASC
        ) = 1
    )
    SELECT h.identity_key,
           any_value(h.identity_kind)  AS identity_kind,
           any_value(h.identity_grade) AS identity_grade,
           any_value(b.holder_display) AS holder_display,
           COUNT(DISTINCT h.holder_name_norm) AS n_name_variants
      FROM h
      LEFT JOIN best_name b ON b.identity_key = h.identity_key
     WHERE h.identity_key IS NOT NULL
     GROUP BY h.identity_key
    """)

    # 富化: ret/alpha (closed) + 被动标记 + PIT 行业 (as-of 建仓日)
    # 被动判定改打在 holder_display 上: PASSIVE_NAME_PATTERNS 是**名字**模式(指数基金/ETF 之类),
    # 换键后 holder 是 identity_key, 拿它去 LIKE 名字模式会恒为假 —— 静默把所有被动持仓
    # 判成主动。这一处是换键最容易漏的连带点。
    passive_pred = " OR ".join(f"i.holder_display LIKE '{p}'" for p in PASSIVE_NAME_PATTERNS)
    con.execute(f"""
    CREATE OR REPLACE TABLE fact_inst_episode AS
    WITH bench AS (
        SELECT trade_date, close FROM {_tr_entity("index_daily")} WHERE ts_code = '000300.SH'
    ), base AS (
        -- identity_key 只做**内部分组**, 对外 holder 仍是显示名。
        -- 换键的目的是"同一实体的多种写法要聚成一个 episode 序列", 不是"把名字换成码"。
        -- 实测下游把 holder 当名字用得很深: stock_dossier 路由 / mart_inst_profile /
        -- 三处 annotate_holder(row['holder'])。把 holder 换成 'code:10671586' 会让整个
        -- 档案服务面产出垃圾, 而那不是本次要解决的问题。
        -- 所以: 分组按 identity_key(已在状态机里生效), 输出 holder = 该身份的显示名,
        -- identity_key/kind/grade 作为新列并存, 想按真身份查的消费方有得可用。
        SELECT e.* EXCLUDE (holder),
               i.holder_display AS holder,
               e.holder         AS identity_key,
               i.identity_kind, i.identity_grade, i.n_name_variants,
               n.known_from            AS niusan_known_from,
               n.identity_confidence   AS niusan_identity_confidence,
               -- NULL = 不是牛散(机构); TRUE/FALSE = 是牛散且这一段在/不在其知名之后。
               -- known_from 为空(徐开东)时判 FALSE: 没有起点就没有 PIT 干净的区间。
               CASE WHEN n.holder_name IS NULL THEN NULL
                    WHEN n.known_from IS NULL THEN FALSE
                    ELSE e.open_date >= n.known_from END AS niusan_usable_at_open,
               CASE WHEN e.status='closed' AND e.cost_c1 > 0 AND e.peak_shares > 0
                    THEN e.realized_c1 / (e.cost_c1 * e.peak_shares) END AS ret_c1,
               ({passive_pred}) AS is_passive
        FROM _ep_raw e
        JOIN _ep_identity i ON i.identity_key = e.holder
        LEFT JOIN _ep_niusan n ON n.holder_name = i.holder_display
    ), bo AS (
        SELECT b.*, x.close AS bench_open
        FROM base b LEFT JOIN bench x ON x.trade_date <= b.open_date
        QUALIFY ROW_NUMBER() OVER (PARTITION BY b.identity_key, b.stock, b.open_date, b.status
                                   ORDER BY x.trade_date DESC) = 1
    ), ba AS (
        SELECT bo.*,
               CASE WHEN bo.status='closed' AND bo.bench_open > 0 AND bo.ret_c1 IS NOT NULL
                    THEN bo.ret_c1 - (x.close / bo.bench_open - 1) END AS alpha_c1
        FROM bo LEFT JOIN bench x ON bo.close_date IS NOT NULL AND x.trade_date <= bo.close_date
        QUALIFY ROW_NUMBER() OVER (PARTITION BY bo.identity_key, bo.stock, bo.open_date, bo.status
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


# Rankable closed episodes only — alpha/returns never invented from holding/seeded/passive.
_RANKABLE_EP = (
    "status = 'closed' AND NOT seeded AND NOT is_passive AND alpha_c1 IS NOT NULL"
)


def build_profiles(con) -> dict[str, int]:
    """机构画像: 总体 + 维度 (industry_pit / year / holder_type)。

    Display contract (2026-07-23 coverage lift): every non-empty holder with ≥1
    episode gets a ``mart_inst_profile`` row so dossier can deep-link 机构档案.
    Rankable metrics (median_alpha / win_rate / n_closed) still only aggregate
    closed + non-seeded + non-passive + measurable alpha; otherwise NULL /
    ``low_sample`` / typed ``metrics_status`` — fail-closed, no fake returns.

    Passive products: kept as display rows (``metrics_status=passive_product``)
    for deep-link honesty; excluded from skill ranking because ``n_closed`` stays 0
    (list_profiles still gates on ``n_closed >= MIN_EPISODES``). Empty names dropped.
    """
    rankable = _RANKABLE_EP
    con.execute(f"""
    CREATE OR REPLACE TABLE mart_inst_profile AS
    WITH base AS (
        SELECT *
        FROM fact_inst_episode
        WHERE holder IS NOT NULL AND length(trim(CAST(holder AS VARCHAR))) > 0
    )
    -- 2026-09-08 Step 4: 按 identity_key 聚, 不按显示名。
    -- 原来 GROUP BY holder(显示名) 会把两个同名不同身份的持有人合成一个档案 —— 而
    -- "同实体多写法 / 不同实体同名"正是换键要解决的那件事, 在这里按名字聚等于把它放回去。
    -- holder 仍出显示名(下游 get_profile/路由/annotate_holder 全按名字取), identity_key
    -- 与 kind/grade 并存, 想按真身份查的消费方有得可用。
    SELECT ANY_VALUE(holder) AS holder,
           identity_key,
           ANY_VALUE(identity_kind)  AS identity_kind,
           ANY_VALUE(identity_grade) AS identity_grade,
           ANY_VALUE(holder_type) AS holder_type,
           COUNT(*) FILTER (WHERE {rankable}) AS n_closed,
           median(alpha_c1) FILTER (WHERE {rankable}) AS median_alpha,
           AVG(alpha_c1) FILTER (WHERE {rankable}) AS avg_alpha,
           (SUM(CASE WHEN ({rankable}) AND alpha_c1 > 0 THEN 1 ELSE 0 END) * 1.0
            / NULLIF(COUNT(*) FILTER (WHERE {rankable}), 0)) AS win_rate_alpha,
           median(ret_c1) FILTER (WHERE {rankable}) AS median_ret,
           AVG(date_diff('day',
                         strptime(open_date, '%Y%m%d'),
                         strptime(close_date, '%Y%m%d')))
               FILTER (WHERE {rankable}) AS avg_hold_days,
           (COUNT(*) FILTER (WHERE {rankable}) < {MIN_EPISODES}) AS low_sample,
           COUNT(*) AS n_episodes,
           COUNT(*) FILTER (WHERE status = 'holding') AS n_holding,
           BOOL_AND(COALESCE(is_passive, FALSE)) AS is_passive_holder,
           CASE
               WHEN BOOL_AND(COALESCE(is_passive, FALSE)) THEN 'passive_product'
               WHEN COUNT(*) FILTER (WHERE {rankable}) >= {MIN_EPISODES} THEN 'ranked'
               WHEN COUNT(*) FILTER (WHERE {rankable}) > 0 THEN 'low_sample'
               WHEN COUNT(*) FILTER (WHERE status = 'holding') > 0 THEN 'holding_only'
               ELSE 'no_closed_alpha'
           END AS metrics_status
    FROM base
    GROUP BY identity_key
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE mart_inst_profile_dim AS
    WITH dims AS (
        SELECT identity_key, holder, 'industry_pit' AS dim_type,
               COALESCE(sw_l1_at_open,'未知') AS dim_value, alpha_c1
        FROM fact_inst_episode WHERE {rankable}
        UNION ALL
        SELECT identity_key, holder, 'year', substr(open_date,1,4), alpha_c1
        FROM fact_inst_episode WHERE {rankable}
        UNION ALL
        SELECT identity_key, holder, 'holder_type', COALESCE(holder_type,'未知'), alpha_c1
        FROM fact_inst_episode WHERE {rankable}
    )
    SELECT ANY_VALUE(holder) AS holder, identity_key, dim_type, dim_value,
           COUNT(*) AS n_closed,
           median(alpha_c1) AS median_alpha,
           SUM(CASE WHEN alpha_c1 > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate_alpha,
           COUNT(*) < {MIN_EPISODES} AS low_sample
    -- 显式列名而非位置: 上面加了 identity_key 一列, 位置 GROUP BY 1,2,3 会当场错位
    -- (与本轮早先那次位置 INSERT 同形 —— 物理顺序不是契约)。
    FROM dims GROUP BY identity_key, dim_type, dim_value
    """)
    return {
        "profiles": con.execute("SELECT COUNT(*) FROM mart_inst_profile").fetchone()[0],
        "profile_dims": con.execute("SELECT COUNT(*) FROM mart_inst_profile_dim").fetchone()[0],
    }


def rebuild_all() -> dict[str, Any]:
    """全量重建 (L2 wipeable, declare-on-build)。

    Closed-loop 2026-07-23: daily ``process`` delta-gates this when holders
    notice frontier advances (see ``pipeline.closed_loop``). Manual still OK.
    """
    con = duck_connect(_db("feature_store"), read_only=False)
    try:
        _attach_sources(con)
        n_win = build_period_windows(con)
        ep_stats = build_episodes(con)
        prof = build_profiles(con)
        con.execute("CHECKPOINT")
        out = {"period_windows": n_win, **ep_stats, **prof}
        logger.info("[institution_profile] rebuild_all: %s", out)
    finally:
        con.close()
    from services.duckdb_compact import maybe_compact_alias

    maybe_compact_alias("feature_store", always=True)
    return out


# ── 读侧 API (档案 serving, router 经此访问 — 本模块是数据模块成员 owns 这些表;
#    PIT 注意: 档案展示"截至今天的全部战绩"给用户手选=合法 (今日决策用今日可得信息);
#    D 阶段回测选机构必须用 expanding PIT 评级, 禁用本读侧 (设计文档 §4 红线)) ──────

def _ro_conn():
    return duck_connect(_db("feature_store"), read_only=True)


def list_profiles(*, holder_type: str | None = None, min_episodes: int = MIN_EPISODES,
                  order_by: str = "median_alpha", limit: int = 50) -> list[dict[str, Any]]:
    """机构排名列表 (默认剔 low_sample / 无 rankable metrics; order_by 白名单防注入)。

    Thin display rows (holding_only / passive_product / n_closed=0) stay out of
    the ranked list unless the caller lowers ``min_episodes`` and the row has
    measurable ``median_alpha`` — never sort NULLs as zero skill.
    """
    order_whitelist = {"median_alpha", "win_rate_alpha", "n_closed", "avg_alpha"}
    if order_by not in order_whitelist:
        raise ValueError(f"order_by 只允许 {sorted(order_whitelist)}")
    con = _ro_conn()
    try:
        where, params = ["n_closed >= ?", "median_alpha IS NOT NULL"], [int(min_episodes)]
        if holder_type:
            where.append("holder_type = ?")
            params.append(holder_type)
        rows = con.execute(f"""
            SELECT holder, holder_type, n_closed, median_alpha, avg_alpha, win_rate_alpha,
                   median_ret, avg_hold_days, low_sample, n_episodes, n_holding,
                   is_passive_holder, metrics_status
            FROM mart_inst_profile WHERE {' AND '.join(where)}
            ORDER BY {order_by} DESC LIMIT ?""", [*params, int(limit)]).fetchall()
        cols = ["holder", "holder_type", "n_closed", "median_alpha", "avg_alpha",
                "win_rate_alpha", "median_ret", "avg_hold_days", "low_sample",
                "n_episodes", "n_holding", "is_passive_holder", "metrics_status"]
        out = [dict(zip(cols, r)) for r in rows]
        for row in out:
            row["research_identity"] = annotate_holder(str(row["holder"]))
        return out
    finally:
        con.close()


def get_profile(holder: str) -> dict[str, Any] | None:
    """单机构档案: 总体 + 维度表现 + episode 时间线 (前端档案页数据契约)。"""
    con = _ro_conn()
    try:
        head = con.execute(
            "SELECT holder, holder_type, n_closed, median_alpha, avg_alpha, win_rate_alpha, "
            "median_ret, avg_hold_days, low_sample, n_episodes, n_holding, "
            "is_passive_holder, metrics_status "
            "FROM mart_inst_profile WHERE holder = ?",
            [holder]).fetchone()
        if head is None:
            return None
        cols = ["holder", "holder_type", "n_closed", "median_alpha", "avg_alpha",
                "win_rate_alpha", "median_ret", "avg_hold_days", "low_sample",
                "n_episodes", "n_holding", "is_passive_holder", "metrics_status"]
        out: dict[str, Any] = dict(zip(cols, head))
        out["research_identity"] = annotate_holder(str(out["holder"]))
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
        # 名称投影 (2026-08-25, 加工层下移: 前端只展示不 lookup): dim_active_a_stock 是
        # 身份真相源 (同 stock_dossier 读路); fail-open — dim 未覆盖时 name=None,
        # 前端照实空态, 绝不虚构名称。
        try:
            from services.security_master import active_stock_name_map
            codes = list({ep["stock"] for ep in out["episodes"]})
            names = active_stock_name_map(codes) if codes else {}
        except Exception:  # noqa: BLE001 — identity dim optional; fail-open unknown name
            names = {}
        for ep in out["episodes"]:
            ep["name"] = names.get(ep["stock"])
        return out
    finally:
        con.close()


def recent_signals(*, days: int = 30, min_holder_episodes: int = MIN_EPISODES,
                   limit: int = 100) -> list[dict[str, Any]]:
    """近 N 天新开 episode 展示流。

    PIT 锚 (2026-07-08 修, 实测中位滞后31天): 过滤用 open_notice(真实披露日 notice_date),
    非 open_date(报告期末) — report_date 那一刻市场还看不到该持仓变动, 用它做"近N天"过滤
    会把季末就该有、两个月后才公开的变动当"最新信号"展示。

    **非跟随回测口径声明**: 本函数只是"最近新开episode"展示流, 返回的 holder_median_alpha/
    holder_win_rate 是该机构自身历史战绩(自身整窗VWAP成本口径, 见 build_profiles), 不是
    "跟随该signal的预期收益"。真正 execution-aware 的跟随策略是 (退役范式, 见 git log --grep institution_follow_v1)
    的 `institution_follow_v1` StrategySpec
    （画像 ≠ 跟随 spec ≠ E B0/B4 隔夜动量消融）。本函数属于画像展示层, 消费方/前端
    展示结果时不应暗示"跟随可获得同等收益"。
    """
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
              AND e.open_notice IS NOT NULL
              AND strptime(e.open_notice, '%Y%m%d') >= now() - to_days(CAST(? AS INTEGER))
            ORDER BY p.median_alpha DESC, e.open_notice DESC LIMIT ?""",
            [int(min_holder_episodes), int(days), int(limit)]).fetchall()
        cols = ["holder", "stock", "open_date", "open_notice", "holder_type", "sw_l1_at_open",
                "n_adds", "holder_n_closed", "holder_median_alpha", "holder_win_rate"]
        out = [dict(zip(cols, r)) for r in rows]
        for row in out:
            row["research_identity"] = annotate_holder(str(row["holder"]))
        return out
    finally:
        con.close()
