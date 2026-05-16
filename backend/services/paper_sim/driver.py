"""Paper Sim v2 — 单日主循环 driver.

每个交易日按顺序:
  1. 加载当前 open positions
  2. 加载当日 K 线 (open + close + high + low + amount + amount_ma20) + 当日 stage
  3. 退出评估: 每只持仓走 exit_rules → 触发的写 SELL trade + close position
  4. swap 评估: 剩下 open holdings + 当日 candidates → swap_rules.rank_swap_candidates
     → SWAP_OUT (close 旧) + SWAP_IN (open 新)
  5. 入新: 剩余 slot + 剩余现金, 按 score 挑前 N 个 candidate BUY
  6. NAV 计算: cash + Σ(shares × close) + HS300 基准 → 写 mart_paper_sim_nav

走单一 BEGIN/COMMIT 事务 (Phase β crash 防御模式).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

from services.paper_sim.config import PaperSimConfig
from services.paper_sim.exit_rules import ExitInputs, evaluate_exit
from services.paper_sim.selector import (
    CandidateRow, load_today_candidates_dispatch, filter_by_liquidity, to_swap_candidate,
)
from services.paper_sim.sizer import allocate_positions
from services.paper_sim.swap_rules import (
    HoldingState, rank_swap_candidates,
)
from services.paper_sim.tx_cost import compute_buy_cost, compute_sell_revenue
from services.paper_sim.tradability import can_buy, can_sell, is_suspended
from services.paper_sim.risk_control import (
    compute_portfolio_dd, should_hard_stop, is_buy_frozen, compute_freeze_until,
)
from services.portfolio_walk_forward.liquidity import round_to_lots


logger = logging.getLogger("paper_sim.driver")


# ===================== open position 数据结构 =====================

class _OpenPosition:
    """driver 内部用; 跟 fact_paper_sim_position schema 一致.

    包含: 买入元数据 + 当前价 + trailing 状态 (armed + high_since_arm 跨日).
    """
    __slots__ = (
        "position_id", "stock_code", "formula_id", "formula_variant",
        "stage_at_buy", "optimal_hp", "optimal_stop_pct", "optimal_target_pct",
        "optimal_trailing_pct", "open_date", "open_price", "shares",
        "buy_cost", "expected_target_pct", "trailing_armed", "high_since_arm",
        # Phase 1a Option C (Codex round 4 MAJOR): exit params 来源 'pit' or 'fallback'
        "exit_source",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _load_open_positions(conn, sim_run_id: str) -> list[_OpenPosition]:
    rows = conn.execute(
        """
        SELECT position_id, stock_code, formula_id, formula_variant,
               stage_at_buy, optimal_hp, optimal_stop_pct, optimal_target_pct,
               optimal_trailing_pct, open_date, open_price, shares,
               buy_cost, expected_target_pct, trailing_armed, high_since_arm,
               COALESCE(exit_source, 'pit') AS exit_source
          FROM fact_paper_sim_position
         WHERE sim_run_id = ? AND is_open = TRUE
        """,
        [sim_run_id],
    ).fetchall()
    return [_OpenPosition(
        position_id=r[0], stock_code=r[1], formula_id=r[2], formula_variant=r[3],
        stage_at_buy=r[4], optimal_hp=r[5], optimal_stop_pct=r[6],
        optimal_target_pct=r[7], optimal_trailing_pct=r[8], open_date=r[9],
        open_price=r[10], shares=r[11], buy_cost=r[12], expected_target_pct=r[13],
        trailing_armed=bool(r[14]), high_since_arm=float(r[15]) if r[15] is not None else None,
        exit_source=str(r[16]) if r[16] is not None else "pit",
    ) for r in rows]


def _load_kline_today(mkt_conn, codes: list[str], today: str) -> dict[str, dict]:
    """当日 K 线 + 20 日均量 (PIT-safe prior-day only) + 前一交易日 close.

    Returns {code: {close, open, high, low, volume, amount, amount_ma20, pre_close}}.

    PIT-safe (Codex aa2d79d2 CRITICAL fix 2026-05-15):
    - amount_ma20 用 prior 20 days only (date < today, 不含今日), 避免 leak 今日 amount.
      实盘 T+1 09:25 决策时今日 amount 还未知, paper_sim 必须 mirror 这点.
    - pre_close: 前一个交易日 close (跨 20 trading days 防长假期).
    """
    if not codes:
        return {}
    qs = ",".join("?" * len(codes))
    rows = mkt_conn.execute(
        f"""
        WITH today AS (
          SELECT code, close, open, high, low, volume, amount
            FROM v_price_kline_qfq
           WHERE adjust='qfq' AND freq='daily' AND date = ? AND code IN ({qs})
        ),
        ma20 AS (
          -- PIT: date < today (strict), 仅 prior 20 trading days. Codex aa2d79d2 CRITICAL fix.
          SELECT code, AVG(amount) AS amount_ma20
            FROM v_price_kline_qfq
           WHERE adjust='qfq' AND freq='daily' AND date < ? AND code IN ({qs})
             AND date > strftime(strptime(?, '%Y-%m-%d') - INTERVAL '40 days', '%Y-%m-%d')
           GROUP BY code
        ),
        prev AS (
          SELECT code,
                 LAST(close ORDER BY date) AS pre_close
            FROM v_price_kline_qfq
           WHERE adjust='qfq' AND freq='daily' AND code IN ({qs})
             AND date < ?
             AND date > strftime(strptime(?, '%Y-%m-%d') - INTERVAL '20 days', '%Y-%m-%d')
           GROUP BY code
        )
        SELECT t.code, t.close, t.open, t.high, t.low, t.volume, t.amount,
               m.amount_ma20, p.pre_close
          FROM today t
          LEFT JOIN ma20 m ON t.code = m.code
          LEFT JOIN prev p ON t.code = p.code
        """,
        [today, *codes, today, *codes, today, *codes, today, today],
    ).fetchall()
    return {
        r[0]: {
            "close": float(r[1] or 0), "open": float(r[2] or 0),
            "high": float(r[3] or 0), "low": float(r[4] or 0),
            "volume": float(r[5] or 0), "amount": float(r[6] or 0),
            "amount_ma20": float(r[7] or 0) if r[7] else None,
            "pre_close": float(r[8] or 0) if r[8] else None,
        }
        for r in rows
    }


def _vwap(k: dict) -> float:
    """当日 VWAP (元/股), 含 sanity check 防数据源单位不一致.

    A 股 v_price_kline_qfq 表里**应该**是:
      - amount 单位 = 元
      - volume 单位 = 手 (1 手 = 100 股)
      - VWAP = amount / (volume × 100)

    但实测 tdxhub 数据源 volume = 手, akshare_sina 数据源 volume = 股 (单位差 100×).
    这导致 paper_sim 在 source 切换日 VWAP 算错 100× — 触发 stop_hit 卖出价 0.11 元
    (实际 11 元), pnl_pct -99% 误判, NAV 暴跌.

    Sanity check (Rule 9.4 数据失败先承认):
    1. 算 vwap_raw = amount / volume  (假设 volume = 股)
    2. 算 vwap_lot = amount / (volume × 100)  (假设 volume = 手)
    3. 看哪个落在 [low, high] 区间 — 用那个
    4. 都不落区间 → fallback close (停牌 / 数据脏)

    volume=0 (停牌) 或 amount=0 → fallback close.
    """
    vol = k.get("volume") or 0
    amt = k.get("amount") or 0
    close = k.get("close") or 0
    low = k.get("low") or 0
    high = k.get("high") or float("inf")

    if vol <= 0 or amt <= 0:
        return close

    vwap_lot = amt / (vol * 100.0)   # tdxhub 假设
    vwap_raw = amt / vol             # akshare 假设

    # 哪个落在 [low, high] (容忍 5% buffer)
    if low > 0 and high > 0:
        lo_b = low * 0.95
        hi_b = high * 1.05
        lot_ok = lo_b <= vwap_lot <= hi_b
        raw_ok = lo_b <= vwap_raw <= hi_b
        if lot_ok and not raw_ok:
            return vwap_lot
        if raw_ok and not lot_ok:
            return vwap_raw
        if lot_ok and raw_ok:
            # 两个都"合理", 用 close 安全 (理论上不可能, 除非 close = vwap × 100)
            return close
        # 都不合理 → fallback close
        return close
    # low/high 缺失 — 选离 close 近的
    if close > 0:
        if abs(vwap_lot - close) < abs(vwap_raw - close):
            return vwap_lot
        return vwap_raw
    return vwap_lot   # 全失败兜底


def _today_stage(conn, codes: list[str], today: str) -> dict[str, str]:
    """当日 technical_stage. 用于 exit_rules.stage_deterioration_sell."""
    if not codes:
        return {}
    qs = ",".join("?" * len(codes))
    rows = conn.execute(
        f"""SELECT stock_code, technical_stage FROM fact_signal_context
             WHERE date = ? AND stock_code IN ({qs})""",
        [today, *codes],
    ).fetchall()
    return {r[0]: (r[1] or "?") for r in rows}


# ===================== 单日主循环 =====================

def run_paper_sim_day(
    conn,
    mkt_conn,
    *,
    sim_run_id: str,
    today: str,
    cfg: PaperSimConfig,
    starting_cash: Optional[float] = None,
) -> dict[str, Any]:
    """跑一个交易日.

    Args:
        sim_run_id: 唯一 ID (一次完整 walk-forward 共用一个 ID)
        today: 'YYYY-MM-DD'
        cfg: 全 PaperSimConfig
        starting_cash: 第一天可传 initial_cash; 后续天 driver 从最近 NAV 推导.

    Returns:
        summary dict {n_exits, n_swaps, n_buys, total_value, cash, n_positions, regime}
    """
    summary: dict[str, Any] = {
        "today": today, "sim_run_id": sim_run_id,
        "n_exits": 0, "n_swaps": 0, "n_buys": 0,
    }

    # 1. 加载持仓 + 现金
    open_positions = _load_open_positions(conn, sim_run_id)
    if starting_cash is None:
        # 取最近一日 NAV 推下
        r = conn.execute(
            """SELECT cash FROM mart_paper_sim_nav
                WHERE sim_run_id = ? AND date < ?
                ORDER BY date DESC LIMIT 1""",
            [sim_run_id, today],
        ).fetchone()
        cash = float(r[0]) if r else cfg.portfolio.initial_cash
    else:
        cash = starting_cash

    # 2. K 线 + stage
    held_codes = [p.stock_code for p in open_positions]
    candidates_raw = load_today_candidates_dispatch(conn, today, cfg.selection)
    candidate_codes = list({c.stock_code for c in candidates_raw})
    all_codes = list(set(held_codes) | set(candidate_codes))
    kline = _load_kline_today(mkt_conn, all_codes, today)
    stage_today = _today_stage(conn, held_codes, today)

    # 2.5. 风控 — portfolio_dd hard stop (v3 实验 2026-05-15)
    # 跟踪 sim 起 peak NAV, 若当前 dd <= max_dd_hard_stop_pct → 全清 + 冻结 N 天
    peak_nav_row = conn.execute(
        "SELECT MAX(total_value) FROM mart_paper_sim_nav WHERE sim_run_id=?",
        [sim_run_id],
    ).fetchone()
    last_nav_row = conn.execute(
        "SELECT total_value FROM mart_paper_sim_nav WHERE sim_run_id=? "
        "ORDER BY date DESC LIMIT 1",
        [sim_run_id],
    ).fetchone()
    peak_nav = float(peak_nav_row[0]) if peak_nav_row and peak_nav_row[0] else cfg.portfolio.initial_cash
    prev_nav = float(last_nav_row[0]) if last_nav_row and last_nav_row[0] else cfg.portfolio.initial_cash
    current_dd_yesterday = compute_portfolio_dd(prev_nav, peak_nav)
    hard_stop_triggered = should_hard_stop(current_dd_yesterday, cfg.risk.max_dd_hard_stop_pct)

    # 3. 退出评估
    closed_position_ids: set[str] = set()
    if hard_stop_triggered:
        # 全清: 用当日 VWAP 卖所有 open positions (按 tradability mask 顺延)
        summary["n_hard_stop"] = 0
        for p in open_positions:
            k = kline.get(p.stock_code)
            if not k or k.get("close", 0) <= 0:
                continue
            if is_suspended(k):
                continue   # 停牌的下次再清
            if not can_sell(k, k.get("pre_close"), p.stock_code):
                continue   # 跌停板也卖不动 (Codex 严)
            sell_price = _vwap(k)
            adv20 = k.get("amount_ma20")
            _close_position(conn, p, today, sell_price, "hard_stop_portfolio_dd",
                            sim_run_id, cfg, adv20=adv20)
            cash += compute_sell_revenue(
                cfg.tx_cost, sell_price, p.shares, adv20=adv20,
            ).effective_amount
            closed_position_ids.add(p.position_id)
            summary["n_hard_stop"] += 1
        logger.warning(
            f"  HARD STOP triggered: dd={current_dd_yesterday:.1%} <= "
            f"{cfg.risk.max_dd_hard_stop_pct:.0%}, 全清 {summary['n_hard_stop']} 仓"
        )
    for p in open_positions:
        k = kline.get(p.stock_code)
        if not k or k.get("close", 0) <= 0:
            continue
        # C-C mask: 停牌 → 持仓 hold (无法 sell), 不算 exit
        if is_suspended(k):
            summary.setdefault("n_blocked_suspended_sell", 0)
            summary["n_blocked_suspended_sell"] += 1
            continue
        days_held = (datetime.strptime(today, "%Y-%m-%d").date()
                     - datetime.strptime(p.open_date, "%Y-%m-%d").date()).days
        d = evaluate_exit(ExitInputs(
            stock_code=p.stock_code,
            entry_price=p.open_price, entry_stage=p.stage_at_buy,
            optimal_hp=p.optimal_hp,
            optimal_stop_pct=p.optimal_stop_pct,
            optimal_target_pct=p.optimal_target_pct,
            optimal_trailing_pct=p.optimal_trailing_pct,
            days_held=days_held, current_close=_vwap(k),
            current_high=k.get("high", k["close"]),    # target arm 用 high (盘中触摸到 target 就 arm)
            peak_since_entry=p.high_since_arm or p.open_price,
            trailing_armed=p.trailing_armed,
            today_stage=stage_today.get(p.stock_code),
            min_forced_hp=cfg.selection.min_forced_hp,   # Path A anti-churn
        ))
        if d.should_exit:
            # C-C mask: 跌停板 → sell 排不到队, 保守 hold 一天
            if not can_sell(k, k.get("pre_close"), p.stock_code):
                summary.setdefault("n_blocked_limit_down_sell", 0)
                summary["n_blocked_limit_down_sell"] += 1
                continue
            adv20 = k.get("amount_ma20")
            _close_position(conn, p, today, d.exit_price, d.reason, sim_run_id, cfg,
                            adv20=adv20)
            cash += compute_sell_revenue(
                cfg.tx_cost, d.exit_price, p.shares, adv20=adv20,
            ).effective_amount
            closed_position_ids.add(p.position_id)
            summary["n_exits"] += 1
        elif d.new_trailing_armed != p.trailing_armed or d.new_peak != p.high_since_arm:
            # 跨日更新 trailing 状态
            conn.execute(
                "UPDATE fact_paper_sim_position SET trailing_armed=?, high_since_arm=? WHERE position_id=?",
                [d.new_trailing_armed, d.new_peak, p.position_id],
            )

    remaining_holdings = [p for p in open_positions if p.position_id not in closed_position_ids]

    # 4. swap 评估
    if cfg.swap.enabled and remaining_holdings and candidates_raw:
        candidates_passed, _rej = filter_by_liquidity(candidates_raw, kline, cfg.selection)
        held_set = {p.stock_code for p in remaining_holdings}
        swap_candidates = [
            to_swap_candidate(c) for c in candidates_passed
            if c.stock_code not in held_set
        ]
        # 转 holdings 成 HoldingState
        hs_list: list[HoldingState] = []
        for p in remaining_holdings:
            k = kline.get(p.stock_code)
            if not k:
                continue
            days_held = (datetime.strptime(today, "%Y-%m-%d").date()
                         - datetime.strptime(p.open_date, "%Y-%m-%d").date()).days
            hs_list.append(HoldingState(
                stock_code=p.stock_code, entry_price=p.open_price,
                sell_target=p.open_price * (1 + (p.expected_target_pct or 0)),
                optimal_hp=p.optimal_hp, days_held=days_held,
                current_price=_vwap(k), score_at_entry=0,
            ))
        swap_decisions = rank_swap_candidates(hs_list, swap_candidates, cfg.swap)
        for d in swap_decisions:
            # SWAP_OUT — 用当日 VWAP 卖
            p_out = next(p for p in remaining_holdings if p.stock_code == d.holding.stock_code)
            k_out = kline[p_out.stock_code]
            # C-C mask: swap-out 停牌/跌停 → skip 这条 swap
            if not can_sell(k_out, k_out.get("pre_close"), p_out.stock_code):
                summary.setdefault("n_blocked_swap_out", 0)
                summary["n_blocked_swap_out"] += 1
                continue
            # swap-in 也得 can_buy
            k_in = kline[d.candidate.stock_code]
            if not can_buy(k_in, k_in.get("pre_close"), d.candidate.stock_code):
                summary.setdefault("n_blocked_swap_in", 0)
                summary["n_blocked_swap_in"] += 1
                continue
            sell_vwap = _vwap(k_out)
            adv20_out = k_out.get("amount_ma20")
            _close_position(conn, p_out, today, sell_vwap, f"swap:{d.reason}",
                             sim_run_id, cfg, swap_uplift=d.swap_uplift_estimate,
                             adv20=adv20_out)
            cash += compute_sell_revenue(
                cfg.tx_cost, sell_vwap, p_out.shares, adv20=adv20_out,
            ).effective_amount
            closed_position_ids.add(p_out.position_id)
            summary["n_swaps"] += 1
            # SWAP_IN — 用当日 VWAP 买
            cand = next(c for c in candidates_passed if c.stock_code == d.candidate.stock_code)
            cash = _open_position(conn, cand, today, _vwap(kline[cand.stock_code]),
                                   cash, sim_run_id, cfg, source="swap",
                                   adv20=kline[cand.stock_code].get("amount_ma20"))

    # 5. 入新 (填满 max_positions) — hard_stop 冻结期内 skip
    # 检 hard_stop 历史 (Codex Rule 5 PIT-safe: 历史 trade reason)
    hard_stop_last_row = conn.execute(
        "SELECT MAX(date) FROM fact_paper_sim_trade "
        "WHERE sim_run_id=? AND reason LIKE 'hard_stop%'",
        [sim_run_id],
    ).fetchone()
    buy_frozen = False
    if hard_stop_last_row and hard_stop_last_row[0]:
        freeze_until = compute_freeze_until(hard_stop_last_row[0], cfg.risk.hard_stop_freeze_days)
        buy_frozen = is_buy_frozen(today, freeze_until)
    if buy_frozen:
        summary["n_blocked_buy_hard_stop_frozen"] = 1
    current_holding_count = len([p for p in open_positions if p.position_id not in closed_position_ids]) \
                              + summary["n_swaps"]
    slots_left = cfg.portfolio.max_positions - current_holding_count
    if slots_left > 0 and not buy_frozen:
        candidates_passed, _ = filter_by_liquidity(candidates_raw, kline, cfg.selection)
        held_set = {p.stock_code for p in open_positions if p.position_id not in closed_position_ids}
        # also exclude already swapped-in stocks
        # 简化: held_set 包括了 swap_in 后的新持仓 (因为 _open_position 写入 fact_paper_sim_position)
        # 但 in-memory 这里没刷新; 重新查
        new_held = {r[0] for r in conn.execute(
            "SELECT stock_code FROM fact_paper_sim_position WHERE sim_run_id=? AND is_open=TRUE",
            [sim_run_id]
        ).fetchall()}
        candidates_for_new = [c for c in candidates_passed if c.stock_code not in new_held][:slots_left]
        # 用 sizer 算每个候选的 cny
        nav_for_sector = cash + _positions_value(conn, sim_run_id, mkt_conn, today)
        sizing = allocate_positions(candidates_for_new, cfg.portfolio, cash,
                                     total_capital=nav_for_sector)

        # Phase 2 sector budget (Codex round 5): 加载 PIT industry + 算 current exposure
        sector_map: dict = {}
        sector_exposure: dict = {}
        if getattr(cfg.selection, "sector_budget_enabled", False):
            from services.paper_sim.sector_budget import (
                load_industry_pit, compute_current_sector_exposure,
                check_sector_quota, log_sector_breach,
            )
            level = getattr(cfg.selection, "sector_budget_level", "tdx_l1")
            conf = getattr(cfg.selection, "sector_budget_confidence_filter", "observed_snapshot")
            sector_map = load_industry_pit(conn, today, level=level, confidence_filter=conf)
            current_prices = {sc: float(k.get("close", 0)) for sc, k in kline.items()}
            current_positions = _load_open_positions(conn, sim_run_id)
            sector_exposure = compute_current_sector_exposure(
                current_positions, current_prices, sector_map, nav_for_sector,
            )

        for s, c in zip(sizing, candidates_for_new):
            k_c = kline[c.stock_code]
            # C-C mask: 停牌 + 涨停板 不能 buy
            if not can_buy(k_c, k_c.get("pre_close"), c.stock_code):
                summary.setdefault("n_blocked_limit_up_buy", 0)
                summary["n_blocked_limit_up_buy"] += 1
                continue
            # Phase 2 sector budget check (Codex round 5+13 hard cap 40% NAV)
            _sector_budget_on = getattr(cfg.selection, "sector_budget_enabled", False)
            if _sector_budget_on:
                hard_cap = getattr(cfg.selection, "sector_budget_hard_cap_pct", 0.40)
                allowed, reason = check_sector_quota(
                    c.stock_code, s.target_cny, sector_map,
                    sector_exposure, nav_for_sector, hard_cap,
                )
                if not allowed:
                    summary.setdefault("n_blocked_sector_budget", 0)
                    summary["n_blocked_sector_budget"] += 1
                    log_sector_breach(sim_run_id, today, c.stock_code,
                                       sector_map.get(c.stock_code) or "UNKNOWN", reason or "")
                    continue
            buy_price = _vwap(k_c)    # 用户指定: 当日 VWAP 入场
            if buy_price <= 0 or s.target_cny <= 0:
                continue
            shares = round_to_lots(s.target_cny, buy_price)
            if shares <= 0:
                continue
            buy = compute_buy_cost(
                cfg.tx_cost, buy_price, shares,
                adv20=k_c.get("amount_ma20"),
            )
            if buy.effective_amount > cash:
                # 不够买就跳过
                continue
            cash = _open_position_directly(conn, c, today, buy_price, shares, buy,
                                            sim_run_id, cfg, source="new", cash=cash)
            summary["n_buys"] += 1
            # Phase 2 sector budget (Codex round 13 MAJOR fix): exposure 累加移到成交后,
            # 用实际成交额 buy.effective_amount (不是 sizer.target_cny), 避免跳单误挡.
            if _sector_budget_on:
                _sect = sector_map.get(c.stock_code)
                if _sect:
                    sector_exposure[_sect] = sector_exposure.get(_sect, 0.0) + \
                                              (buy.effective_amount / nav_for_sector if nav_for_sector > 0 else 0)

    # 6. NAV 计算 + 写 mart_paper_sim_nav
    positions_value = _positions_value(conn, sim_run_id, mkt_conn, today)
    total_value = cash + positions_value
    n_pos = conn.execute(
        "SELECT COUNT(*) FROM fact_paper_sim_position WHERE sim_run_id=? AND is_open=TRUE",
        [sim_run_id]
    ).fetchone()[0]

    # HS300 基准
    hs300 = _hs300_normalized(mkt_conn, conn, today, sim_run_id, cfg)

    conn.execute(
        """INSERT INTO mart_paper_sim_nav
           (sim_run_id, date, total_value, cash, positions_value, n_positions, hs300_nav)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [sim_run_id, today, total_value, cash, positions_value, n_pos, hs300],
    )
    conn.commit()

    summary.update({
        "total_value": total_value, "cash": cash,
        "positions_value": positions_value, "n_positions": n_pos,
        "hs300_nav": hs300,
    })
    return summary


def _positions_value(conn, sim_run_id: str, mkt_conn, today: str) -> float:
    """当前所有 open position 按今日 close 估值."""
    open_rows = conn.execute(
        "SELECT stock_code, shares FROM fact_paper_sim_position WHERE sim_run_id=? AND is_open=TRUE",
        [sim_run_id]
    ).fetchall()
    if not open_rows:
        return 0.0
    codes = [r[0] for r in open_rows]
    qs = ",".join("?" * len(codes))
    price_rows = mkt_conn.execute(
        f"""SELECT code, close FROM v_price_kline_qfq
             WHERE adjust='qfq' AND freq='daily' AND date = ? AND code IN ({qs})""",
        [today, *codes],
    ).fetchall()
    price = {r[0]: float(r[1] or 0) for r in price_rows}
    return sum(shares * price.get(code, 0) for code, shares in open_rows)


def _close_position(conn, p, today: str, sell_price: float, reason: str,
                     sim_run_id: str, cfg: PaperSimConfig,
                     swap_uplift: Optional[float] = None,
                     adv20: Optional[float] = None) -> None:
    sell = compute_sell_revenue(cfg.tx_cost, sell_price, p.shares, adv20=adv20)
    pnl = sell.effective_amount - p.buy_cost
    pnl_pct = pnl / p.buy_cost if p.buy_cost > 0 else 0
    days_held = (datetime.strptime(today, "%Y-%m-%d").date()
                 - datetime.strptime(p.open_date, "%Y-%m-%d").date()).days
    conn.execute(
        """UPDATE fact_paper_sim_position SET
              close_date=?, close_price=?, sell_revenue=?, close_reason=?,
              pnl=?, pnl_pct=?, days_held=?, is_open=FALSE
            WHERE position_id=?""",
        [today, sell_price, sell.effective_amount, reason,
         pnl, pnl_pct, days_held, p.position_id],
    )
    # trade log (Phase 1a Option C: 继承 position.exit_source)
    conn.execute(
        """INSERT INTO fact_paper_sim_trade
           (trade_id, sim_run_id, position_id, date, type, reason,
            price, shares, gross_amount, tx_cost, net_amount, swap_uplift_estimate, exit_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [str(uuid.uuid4()), sim_run_id, p.position_id, today,
         "SWAP_OUT" if reason.startswith("swap:") else "SELL",
         reason, sell_price, p.shares, sell.base_amount, sell.total_cost,
         sell.effective_amount, swap_uplift,
         getattr(p, "exit_source", "pit") or "pit"],
    )


def _open_position_directly(conn, c: CandidateRow, today: str, buy_price: float,
                              shares: int, buy_result, sim_run_id: str,
                              cfg: PaperSimConfig, source: str, cash: float) -> float:
    """已经算好 shares + buy_cost, 直接 INSERT. 返回新的 cash."""
    position_id = f"{c.stock_code}_{today}_{uuid.uuid4().hex[:8]}"
    _exit_src = getattr(c, "exit_source", "pit") or "pit"  # Phase 1a Option C
    conn.execute(
        """INSERT INTO fact_paper_sim_position
           (position_id, sim_run_id, stock_code, formula_id, formula_variant,
            stage_at_buy, optimal_hp, optimal_stop_pct, optimal_target_pct,
            optimal_trailing_pct, open_date, open_price, shares, buy_cost,
            expected_target_pct, is_open, exit_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?)""",
        [position_id, sim_run_id, c.stock_code, c.formula_id, c.formula_variant,
         c.stage, c.optimal_hp, c.optimal_stop_pct, c.optimal_target_pct,
         c.optimal_trailing_pct, today, buy_price, shares, buy_result.effective_amount,
         c.optimal_target_pct or 0, _exit_src],
    )
    conn.execute(
        """INSERT INTO fact_paper_sim_trade
           (trade_id, sim_run_id, position_id, date, type, reason,
            price, shares, gross_amount, tx_cost, net_amount, exit_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [str(uuid.uuid4()), sim_run_id, position_id, today,
         "SWAP_IN" if source == "swap" else "BUY",
         f"{source}:tier={c.tier},score={c.score:.1f}", buy_price, shares,
         buy_result.base_amount, buy_result.total_cost, buy_result.effective_amount,
         _exit_src],
    )
    return cash - buy_result.effective_amount


def _open_position(conn, c: CandidateRow, today: str, buy_price: float,
                    cash: float, sim_run_id: str, cfg: PaperSimConfig,
                    source: str, adv20: Optional[float] = None) -> float:
    """从候选 + 当前 cash 算 shares 并下单 (用于 swap_in 简化路径)."""
    # swap-in 简化: 用 cash 的 1/max_positions 比例买入
    target_cny = cash / max(cfg.portfolio.max_positions, 1)
    shares = round_to_lots(target_cny, buy_price)
    if shares <= 0:
        return cash
    buy = compute_buy_cost(cfg.tx_cost, buy_price, shares, adv20=adv20)
    if buy.effective_amount > cash:
        return cash
    return _open_position_directly(conn, c, today, buy_price, shares, buy,
                                    sim_run_id, cfg, source, cash)


def _hs300_normalized(mkt_conn, conn, today: str, sim_run_id: str,
                       cfg: PaperSimConfig) -> Optional[float]:
    """HS300 基准 NAV (归一化到 sim 起点)."""
    r = mkt_conn.execute(
        "SELECT close FROM v_price_kline_qfq WHERE code='000300' AND adjust='qfq' AND freq='daily' AND date = ? LIMIT 1",
        [today],
    ).fetchone()
    if not r or not r[0]:
        return None
    today_close = float(r[0])
    # 起点 close
    first = conn.execute(
        "SELECT date FROM mart_paper_sim_nav WHERE sim_run_id=? ORDER BY date LIMIT 1",
        [sim_run_id],
    ).fetchone()
    if not first:
        # 第一天, hs300_nav = 1
        return 1.0
    start_close = mkt_conn.execute(
        "SELECT close FROM v_price_kline_qfq WHERE code='000300' AND adjust='qfq' AND freq='daily' AND date <= ? ORDER BY date LIMIT 1",
        [first[0]],
    ).fetchone()
    if not start_close or not start_close[0]:
        return None
    return today_close / float(start_close[0])


# ===================== 多 portfolio (实盘 forward 用) =====================

def run_paper_sim_day_multi(
    conn,
    mkt_conn,
    *,
    today: str,
    portfolios: dict[str, "tuple[str, PaperSimConfig]"],
    starting_cash_map: Optional[dict[str, float]] = None,
) -> dict[str, dict[str, Any]]:
    """跑多个 portfolio 同日 (用户 2026-05-16: 3 组 paper_sim 对比).

    Args:
        portfolios: {portfolio_id: (sim_run_id, cfg)}.
            e.g. {'A': ('live_A_2026-05-16', cfg_v4), 'B': ('live_B_2026-05-16', cfg_v8)}
        starting_cash_map: optional {portfolio_id: cash} 仅 sim 第一天用; 后续天 driver 从最近 NAV 推.

    Returns:
        {portfolio_id: summary} — 每 portfolio 独立 summary (n_exits/n_swaps/n_buys/total_value/...).

    各 portfolio 独立 sim_run_id, 独立 NAV / position / trade / KPI 表写入.
    适用: live forward simulation 每日盘后 cron 触发, 3 组并行 dashboard 对比.
    """
    results: dict[str, dict[str, Any]] = {}
    for pid, (sim_run_id, cfg) in portfolios.items():
        starting_cash = (starting_cash_map or {}).get(pid)
        res = run_paper_sim_day(
            conn, mkt_conn,
            sim_run_id=sim_run_id, today=today, cfg=cfg,
            starting_cash=starting_cash,
        )
        results[pid] = res
    return results
