"""组合回测引擎 — P2.7 (2026-04-28).

跟现有 backtest_engine.py 区别:
- backtest_engine.py: 跑机构行业表现等"研究表生成器" (4 张 research_*)
- portfolio_backtest.py (本): 严格事件驱动组合回测 + 滑点 + 持仓约束

核心组件:
1. SlippageModel: 4 种 (fixed_bps / linear / sqrt / impact)
2. PositionConstraint: max_pos / max_n / max_industry / cash_reserve
3. run_portfolio_backtest(signals, ...) → equity curve + sharpe + dd + turnover

设计:
- signals: records with columns=[date, stock_code, action, weight]
  action ∈ {'buy','sell','hold'}, weight 是目标权重 [0,1]
- 简化: 日频再平衡, 使用执行日 VWAP 作为成交基准
- 滑点应用在每笔成交的成交价上
- 约束在每次再平衡时强制 (违反则截断或拒绝)
- 输出 equity curve + 交易明细

不依赖外部库. statsmodels / vectorbt 不引入.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from collections import defaultdict
from collections.abc import Mapping
from typing import Callable, Literal

from services.pricing_policy import load_pricing_label_policy

logger = logging.getLogger("cm-api.portfolio_backtest")
PRICING_POLICY = load_pricing_label_policy()


# ===========================================================================
# 滑点模型
# ===========================================================================

@dataclass
class SlippageModel:
    """滑点模型配置. 实际成交价 = 名义价 × (1 + slippage_bps / 10000) (买入).

    type:
      - fixed_bps: 固定 bps (默认 5 bps = 万分之五)
      - linear: 线性成交量冲击 ~ size / adv * impact_coef
      - sqrt: 平方根 ~ sqrt(size / adv) * impact_coef (Almgren-Chriss 简化)
      - impact: 综合 (含买卖价差 + 流动性)
    """
    type: Literal["fixed_bps", "linear", "sqrt", "impact"] = "fixed_bps"
    fixed_bps: float = PRICING_POLICY.transaction_cost_bps
    impact_coef: float = 10.0    # bps per unit (linear/sqrt/impact)
    bid_ask_spread_bps: float = 5.0  # impact 模式: bid-ask 半价差

    def calc(self, side: Literal["buy", "sell"], notional: float, adv: float | None = None) -> float:
        """返回 slippage in bps. notional = 成交金额, adv = 日均成交额 (impact 模式必传)."""
        if self.type == "fixed_bps":
            bps = self.fixed_bps
        elif self.type == "linear" and adv:
            bps = self.fixed_bps + self.impact_coef * (notional / adv)
        elif self.type == "sqrt" and adv:
            bps = self.fixed_bps + self.impact_coef * ((notional / adv) ** 0.5)
        elif self.type == "impact" and adv:
            spread = self.bid_ask_spread_bps
            impact = self.impact_coef * ((notional / adv) ** 0.5)
            bps = spread + impact
        else:
            bps = self.fixed_bps
        # 卖出滑点反向 (实际成交价更低)
        return bps if side == "buy" else -bps


# ===========================================================================
# 持仓约束
# ===========================================================================

@dataclass
class PositionConstraint:
    """持仓约束. 违反时截断 (capping)."""
    max_position_pct: float = 0.10        # 单股上限 10%
    max_n_holdings: int = 30              # 持仓最多 N 只
    max_industry_pct: float = 0.30        # 单行业上限 30%
    cash_reserve_pct: float = 0.05        # 强制 5% 现金
    min_position_pct: float = 0.01        # 单股下限 1% (低于则不开仓)

    def apply(self, target_weights: dict[str, float], industry_map: dict[str, str] | None = None) -> dict[str, float]:
        """对目标权重应用约束. 返回截断后的权重."""
        if not target_weights:
            return {}
        out = dict(target_weights)

        # 1. 截断单股 max_position_pct + 过滤 min_position_pct
        out = {k: min(v, self.max_position_pct) for k, v in out.items() if v >= self.min_position_pct}

        # 2. 限制持仓数 (按权重降序保留 top N)
        if len(out) > self.max_n_holdings:
            top = sorted(out.items(), key=lambda x: -x[1])[: self.max_n_holdings]
            out = dict(top)

        # 3. 行业约束 (按行业总权重)
        if industry_map and self.max_industry_pct < 1.0:
            from collections import defaultdict
            industry_total: dict[str, float] = defaultdict(float)
            for sc, w in out.items():
                ind = industry_map.get(sc, "unknown")
                industry_total[ind] += w
            # 超限时按比例缩
            for ind, total in industry_total.items():
                if total > self.max_industry_pct:
                    scale = self.max_industry_pct / total
                    for sc in list(out.keys()):
                        if industry_map.get(sc) == ind:
                            out[sc] *= scale

        # 4. 现金保留 (剩余 1 - cash_reserve_pct 给股票)
        max_total = 1.0 - self.cash_reserve_pct
        total = sum(out.values())
        if total > max_total:
            scale = max_total / total
            out = {k: v * scale for k, v in out.items()}

        return out


# ===========================================================================
# 回测主函数
# ===========================================================================

@dataclass
class BacktestResult:
    equity_curve: list[dict] = field(default_factory=list)  # [{date, total, cash, position_count}]
    trades: list[dict] = field(default_factory=list)         # [{date, stock_code, side, qty, price, slip_bps, cost}]
    metrics: dict = field(default_factory=dict)              # {total_return, sharpe, max_dd, turnover, n_trades}


def _records_from_signals(signals) -> list[dict]:
    if signals is None:
        return []
    to_dict = getattr(signals, "to_dict", None)
    if callable(to_dict):
        try:
            return [dict(row) for row in to_dict("records")]
        except TypeError:
            pass
    if isinstance(signals, Mapping):
        return [dict(signals)]
    rows = []
    try:
        iterator = iter(signals)
    except TypeError:
        return []
    for row in iterator:
        if isinstance(row, Mapping):
            rows.append(dict(row))
            continue
        try:
            rows.append(dict(row))
        except Exception:
            continue
    return rows


def _normalize_signal_rows(signals) -> list[dict]:
    rows = []
    for row in _records_from_signals(signals):
        date = str(row.get("date") or "").strip()[:10]
        stock_code = str(row.get("stock_code") or row.get("code") or "").strip()
        if not date or not stock_code:
            continue
        try:
            target_weight = float(row.get("target_weight", row.get("weight", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        rows.append({
            "date": date,
            "stock_code": stock_code,
            "target_weight": target_weight,
        })
    return rows


def run_portfolio_backtest(
    signals,
    *,
    price_fn: Callable[[str, str], float | None],  # (stock_code, date) → execution reference price
    initial_capital: float = 1_000_000,
    slippage: SlippageModel | None = None,
    constraint: PositionConstraint | None = None,
    industry_fn: Callable[[str], str] | None = None,
    adv_fn: Callable[[str, str], float | None] | None = None,
    rebalance_freq: Literal["daily", "weekly", "monthly"] = "daily",
) -> BacktestResult:
    """跑一次组合回测.

    signals: 必须含列 [date (str YYYY-MM-DD), stock_code, target_weight (0-1)].
             同一天同一只股最多一行. 没出现的日子视作空仓.
    price_fn: (sc, date) → execution reference price, 缺失返回 None (跳过该日成交).
    industry_fn: 可选, 用于行业约束.
    adv_fn: (sc, date) → adv (近 20 日均额), linear/sqrt/impact 滑点模式必传.
    """
    if slippage is None:
        slippage = SlippageModel()
    if constraint is None:
        constraint = PositionConstraint()

    signal_rows = _normalize_signal_rows(signals)
    if not signal_rows:
        return BacktestResult(metrics={"error": "empty signals"})

    # 状态: 持仓 + 现金
    cash = initial_capital
    positions: dict[str, dict] = {}  # stock_code → {qty, avg_cost}
    equity_curve = []
    trades = []

    signals_by_date: dict[str, list[dict]] = defaultdict(list)
    for row in signal_rows:
        signals_by_date[row["date"]].append(row)
    dates = sorted(signals_by_date)
    last_total = initial_capital

    for date in dates:
        # 当日信号
        day_signals = signals_by_date[date]
        target_weights = {
            row["stock_code"]: row["target_weight"]
            for row in day_signals
        }

        # 应用约束
        industry_map = {sc: industry_fn(sc) for sc in target_weights} if industry_fn else None
        target_weights = constraint.apply(target_weights, industry_map)

        # 当前估值
        current_total = cash
        for sc, pos in positions.items():
            p = price_fn(sc, date)
            if p:
                current_total += pos["qty"] * p

        if current_total <= 0:
            equity_curve.append({"date": date, "total": current_total, "cash": cash, "position_count": len(positions)})
            continue

        # 计算每股目标 notional
        target_notional = {sc: w * current_total for sc, w in target_weights.items()}

        # 现有持仓 → 当前 notional
        current_notional = {}
        for sc, pos in positions.items():
            p = price_fn(sc, date)
            if p:
                current_notional[sc] = pos["qty"] * p

        # 卖出: 老仓里 target=0 或减仓
        for sc in list(positions.keys()):
            if sc not in target_notional:
                # 全平
                p = price_fn(sc, date)
                if not p:
                    continue
                qty = positions[sc]["qty"]
                adv = adv_fn(sc, date) if adv_fn else None
                slip_bps = slippage.calc("sell", qty * p, adv)
                exec_price = p * (1 + slip_bps / 10000)
                proceeds = qty * exec_price
                cost = qty * p - proceeds  # 滑点损耗 (>0 = 损失)
                cash += proceeds
                trades.append({
                    "date": date, "stock_code": sc, "side": "sell",
                    "qty": qty, "price": p, "exec_price": round(exec_price, 4),
                    "slip_bps": round(slip_bps, 2), "cost": round(cost, 2),
                })
                del positions[sc]
            else:
                # 减仓?
                target_n = target_notional[sc]
                cur_n = current_notional.get(sc, 0)
                if cur_n > target_n * 1.05:  # 5% 容差
                    delta = cur_n - target_n
                    p = price_fn(sc, date)
                    if not p:
                        continue
                    qty_sell = delta / p
                    adv = adv_fn(sc, date) if adv_fn else None
                    slip_bps = slippage.calc("sell", qty_sell * p, adv)
                    exec_price = p * (1 + slip_bps / 10000)
                    proceeds = qty_sell * exec_price
                    cash += proceeds
                    positions[sc]["qty"] -= qty_sell
                    if positions[sc]["qty"] <= 1e-9:
                        del positions[sc]
                    trades.append({
                        "date": date, "stock_code": sc, "side": "sell",
                        "qty": round(qty_sell, 0), "price": p, "exec_price": round(exec_price, 4),
                        "slip_bps": round(slip_bps, 2), "cost": round(qty_sell * p - proceeds, 2),
                    })

        # 买入: 新仓或加仓
        for sc, target_n in target_notional.items():
            cur_n = 0
            if sc in positions:
                p = price_fn(sc, date)
                if p:
                    cur_n = positions[sc]["qty"] * p
            if target_n > cur_n * 1.05:  # 5% 容差
                delta = target_n - cur_n
                if delta > cash:
                    delta = cash * 0.95  # 不够现金, 部分买
                if delta < 0:
                    continue
                p = price_fn(sc, date)
                if not p:
                    continue
                adv = adv_fn(sc, date) if adv_fn else None
                slip_bps = slippage.calc("buy", delta, adv)
                exec_price = p * (1 + slip_bps / 10000)
                qty_buy = delta / exec_price
                cash -= delta
                if sc in positions:
                    positions[sc]["qty"] += qty_buy
                else:
                    positions[sc] = {"qty": qty_buy, "avg_cost": exec_price}
                trades.append({
                    "date": date, "stock_code": sc, "side": "buy",
                    "qty": round(qty_buy, 0), "price": p, "exec_price": round(exec_price, 4),
                    "slip_bps": round(slip_bps, 2), "cost": round(qty_buy * (exec_price - p), 2),
                })

        # 当日收盘估值
        end_total = cash
        for sc, pos in positions.items():
            p = price_fn(sc, date)
            if p:
                end_total += pos["qty"] * p
        equity_curve.append({
            "date": date,
            "total": round(end_total, 2),
            "cash": round(cash, 2),
            "position_count": len(positions),
        })
        last_total = end_total

    # 性能指标
    if not equity_curve:
        return BacktestResult(metrics={"error": "no equity"})
    import math
    total_return = (last_total / initial_capital) - 1
    rets = []
    prev = initial_capital
    for e in equity_curve:
        rets.append(e["total"] / prev - 1)
        prev = e["total"]
    n = len(rets)
    if n > 1:
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        sd = math.sqrt(var)
        sharpe = (mean * 252) / (sd * math.sqrt(252)) if sd > 0 else 0
    else:
        sharpe = 0
    peak = initial_capital
    max_dd = 0.0
    for e in equity_curve:
        if e["total"] > peak:
            peak = e["total"]
        if peak > 0:
            dd = (peak - e["total"]) / peak
            if dd > max_dd:
                max_dd = dd

    n_trades = len(trades)
    total_volume = sum(t["qty"] * t["price"] for t in trades)
    turnover = total_volume / initial_capital if initial_capital > 0 else 0

    return BacktestResult(
        equity_curve=equity_curve,
        trades=trades,
        metrics={
            "initial_capital": initial_capital,
            "final_capital": round(last_total, 2),
            "total_return": round(total_return, 4),
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "n_days": len(equity_curve),
            "n_trades": n_trades,
            "turnover": round(turnover, 2),
            "slippage_type": slippage.type,
            "max_position_pct": constraint.max_position_pct,
            "max_n_holdings": constraint.max_n_holdings,
        },
    )


# ===========================================================================
# 默认 helpers (用 market.duckdb 拿价格)
# ===========================================================================

_PRICE_CACHE: dict[tuple, float | None] = {}


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _resolve_execution_price(row) -> float | None:
    """Execution basis: daily VWAP first, then open fallback."""
    amount = _safe_float(row["amount"] if isinstance(row, dict) else row[2])
    volume = _safe_float(row["volume"] if isinstance(row, dict) else row[3])
    close = _safe_float(row["close"] if isinstance(row, dict) else row[1])
    factor = _safe_float(row.get("factor") if isinstance(row, dict) else (row[4] if len(row) > 4 else None)) or 1.0
    if amount and volume:
        vwap = amount / volume
        if close is None or 0.5 <= vwap / close <= 1.5:
            return vwap
        hand_adjusted = vwap / 100.0
        factor_adjusted = hand_adjusted * factor
        if abs(factor - 1.0) > 1e-9 and 0.5 <= factor_adjusted / close <= 1.5:
            return factor_adjusted
        if 0.5 <= hand_adjusted / close <= 1.5:
            return hand_adjusted
    return _safe_float(row["open"] if isinstance(row, dict) else row[0])


def make_default_price_fn():
    """从 market.duckdb canonical K-line relation 拿执行日 VWAP. 进程内 cache."""
    from services.market_db import get_canonical_kline_qfq_relation, get_market_conn
    mc = get_market_conn()
    relation = get_canonical_kline_qfq_relation()

    def _fn(stock_code: str, date: str) -> float | None:
        key = (stock_code, date)
        if key in _PRICE_CACHE:
            return _PRICE_CACHE[key]
        try:
            r = mc.execute(
                f"""
                SELECT open, close, amount, volume, COALESCE(factor, 1.0) AS factor FROM {relation}
                 WHERE code = ? AND date = ?
                   AND freq = 'daily' AND adjust = 'qfq'
                 LIMIT 1
                """,
                [stock_code, date],
            ).fetchone()
            v = _resolve_execution_price(r) if r else None
        except Exception:
            v = None
        _PRICE_CACHE[key] = v
        return v
    return _fn
