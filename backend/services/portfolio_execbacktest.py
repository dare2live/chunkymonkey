"""Execution-aware 组合回测引擎 — P1 重建 (2026-06-15, 8-lens 根因 R2 "信号 != 可交易头寸")。

替代 portfolio_returnbacktest.py (旧 clean 引擎假设 close 无条件全额成交 = 无摩擦市场, R2 缺陷)。
本引擎把"信号 -> 可交易头寸"的全部 A股摩擦显式建模, 让"含成本绝对收益"成为真裁决 (法典 C-R2/C-WinReturn)。

架构师合约 (architect-controller):
  输入:
    rebalances = [(decision_date, [(code, signal_or_None)])] 按时间序 (signal 高=优先, 用于 rank sizing; None=等权)
    bars_by_code = {code: {date: (open, high, low, close, volume)}} (qfq; volume 可 None -> 该股跳过容量诊断)
    calendar = 全交易日升序
    config = ExecConfig (microstructure, 读 backend/config/backtest_execution.yaml)
    sizing = equal|rank|inverse_vol; top_k; gross_exposure; stop_loss_pct(可选 intra-holding 止损)
  不变量 (PIT 死亡条款): 决策只用 <= decision_date 信息 (调用方保证 signal); 执行于 decision_date+1 (T+1);
    入场价 = t+1 **open** (非 close, N14); 日度 mark-to-market 用 close。
  R2 摩擦 (全部显式):
    - 涨停一字板 (N8/N12): 入场日一字涨停 -> 买不进, 剔出当期篮; 卖出日一字跌停 -> 卖不出, 持仓顺延。
    - 非对称成本 (N13): 卖方加印花税, 买方不加; 含佣金/过户/规费/证管/滑点。
    - 容量 (N10): 单笔买入名义额 vs ADV; 超阈值加大单溢价 + 报 capacity_utilization 诊断 (不编造冲击系数)。
    - 停牌 (N11): 缺价 -> 持仓冻结在最后有效价 (不剔篮不重分权重, 防生存者偏差隐藏入口)。
  仓位管理 (一等轴, C-WinReturn/N4-N6): sizing policy + gross_exposure (空槽留现金 = 连续 0-100% 暴露雏形) + 可选止损。
  输出: {nav, metrics(年化/max_dd/sharpe/calmar/月胜率/胜率/盈亏比/正期望), cost_drag, avg_turnover,
         avg_participation, max_participation, capacity_warn_rate, n_rebalances, final_nav}。
  失败模式: 入场日无 open -> 该股不买; 全候选不可买 -> 该期持现金; 空 NAV -> flat。
  证伪门: test_portfolio_execbacktest.py 手算逐场景 (T+1 open / 一字板剔篮 / 非对称成本 / 停牌冻结 / 容量 / sizing / 止损)。
  已知简化 (二阶, 文档化): 不做整手(100股)取整; 不应用最低佣金 5 元 (分数 NAV 模型); 冲击只用大单溢价不建 sqrt 冲击 (守 measured-not-estimated)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

_REPO = Path(__file__).resolve().parents[2]
_CFG = _REPO / "backend" / "config" / "backtest_execution.yaml"


@dataclass
class ExecConfig:
    commission_pct: float
    stamp_duty_sell_pct: float
    transfer_fee_pct: float
    exchange_fee_pct: float
    regulatory_fee_pct: float
    slippage_pct: float
    large_order_surcharge_pct: float
    limit_by_prefix: dict
    one_line_buy_block: bool
    one_line_sell_block: bool
    detect_tol: float
    capital_cny: float
    adv_window: int
    participation_threshold: float

    @classmethod
    def load(cls, path: Path | str = _CFG) -> "ExecConfig":
        m = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        c, lb, cap = m["cost"], m["limit_board"], m["capacity"]
        return cls(
            commission_pct=c["commission_pct"], stamp_duty_sell_pct=c["stamp_duty_sell_pct"],
            transfer_fee_pct=c["transfer_fee_pct"], exchange_fee_pct=c["exchange_fee_pct"],
            regulatory_fee_pct=c["regulatory_fee_pct"], slippage_pct=c["slippage_pct"],
            large_order_surcharge_pct=c["large_order_surcharge_pct"],
            limit_by_prefix={str(k): float(v) for k, v in lb["by_prefix"].items()},
            one_line_buy_block=bool(lb["one_line_buy_block"]), one_line_sell_block=bool(lb["one_line_sell_block"]),
            detect_tol=float(lb["detect_tol"]), capital_cny=float(cap["capital_cny"]),
            adv_window=int(cap["adv_window"]), participation_threshold=float(cap["large_order_participation_threshold"]))

    def _base_pct(self) -> float:
        return (self.commission_pct + self.transfer_fee_pct + self.exchange_fee_pct
                + self.regulatory_fee_pct + self.slippage_pct)

    def buy_cost_pct(self, over_capacity: bool = False) -> float:
        return self._base_pct() + (self.large_order_surcharge_pct if over_capacity else 0.0)

    def sell_cost_pct(self, over_capacity: bool = False) -> float:
        # 非对称: 卖方加印花税 (N13)
        return self._base_pct() + self.stamp_duty_sell_pct + (self.large_order_surcharge_pct if over_capacity else 0.0)

    def limit_pct(self, code: str) -> float:
        for k in (code[:2], code[:1]):
            if k in self.limit_by_prefix:
                return self.limit_by_prefix[k]
        return 0.10  # rule-compliance: ok evidence=未知板块回退主板 10% (universe_rules 默认)


def _is_one_line_up(bar, prev_close, cfg: ExecConfig, code: str) -> bool:
    """一字涨停: open==high==low 且 涨幅 >= 板幅*tol (买不进)。"""
    if bar is None or prev_close in (None, 0):
        return False
    o, h, l, _c, _v = bar
    if None in (o, h, l) or not (o == h == l):
        return False
    return (o / prev_close - 1.0) >= cfg.limit_pct(code) * cfg.detect_tol


def _is_one_line_down(bar, prev_close, cfg: ExecConfig, code: str) -> bool:
    """一字跌停: open==high==low 且 跌幅 <= -板幅*tol (卖不出)。"""
    if bar is None or prev_close in (None, 0):
        return False
    o, h, l, _c, _v = bar
    if None in (o, h, l) or not (o == h == l):
        return False
    return (o / prev_close - 1.0) <= -cfg.limit_pct(code) * cfg.detect_tol


def _adv_notional(code, bars, dates_sorted, entry_idx_in_dates, window):
    """entry 前 window 日 mean(volume*close) (名义成交额); volume 缺 -> None。"""
    lo = max(0, entry_idx_in_dates - window)
    vals = []
    for d in dates_sorted[lo:entry_idx_in_dates]:
        _o, _h, _l, c, v = bars[d]
        if v not in (None, 0) and c not in (None, 0):
            vals.append(v * c)
    return float(np.mean(vals)) if vals else None


def _sizing_weights(selected, sizing, gross, top_k, bars_by_code, entry_date, code_dates):
    """返回 {code: target_weight}; 空槽 (不足 top_k) 留现金 (weight 分母用 top_k)。"""
    per = gross / top_k  # 空槽=现金 (连续 exposure 雏形 N6)
    if sizing == "equal" or not selected:
        return {c: per for c, _s in selected}
    if sizing == "rank":
        # 按 signal 降序排名加权 (高 signal 高权重); 总额 = gross * (len/top_k)
        n = len(selected)
        ranks = {c: (n - i) for i, (c, _s) in enumerate(selected)}  # selected 已按 signal 降序
        tot = sum(ranks.values())
        budget = gross * n / top_k
        return {c: budget * ranks[c] / tot for c, _s in selected}
    if sizing == "inverse_vol":
        invv = {}
        for c, _s in selected:
            ds = code_dates[c]
            ei = ds.index(entry_date) if entry_date in ds else len(ds)
            closes = [bars_by_code[c][d][3] for d in ds[max(0, ei - 20):ei] if bars_by_code[c][d][3] not in (None, 0)]
            if len(closes) >= 3:
                rets = np.diff(closes) / np.asarray(closes[:-1])
                sd = float(rets.std(ddof=1))
                invv[c] = 1.0 / sd if sd > 1e-9 else 0.0
            else:
                invv[c] = 0.0
        tot = sum(invv.values())
        if tot <= 0:
            return {c: per for c, _s in selected}
        budget = gross * len(selected) / top_k
        return {c: budget * invv[c] / tot for c, _s in selected}
    return {c: per for c, _s in selected}


def _metrics(nav_dates, nav, seg_returns, tdays=252) -> dict:
    """联合 metrics (C-WinReturn): 年化/max_dd/sharpe/calmar/月胜率 + 段级胜率/盈亏比/正期望。"""
    if len(nav) < 2:
        return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "calmar": 0.0,
                "monthly_win_rate": None, "win_rate": None, "payoff_ratio": None, "expectancy": None}
    arr = np.asarray(nav, float)
    total_ret = arr[-1] / arr[0] - 1.0
    years = max(len(arr) / tdays, 1e-9)
    annual = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1.0
    run_max = np.maximum.accumulate(arr)
    max_dd = float(((arr - run_max) / run_max).min())
    daily = np.diff(arr) / arr[:-1]
    sd = float(daily.std(ddof=1)) if daily.size > 1 else 0.0
    sharpe = float(daily.mean() * tdays / (sd * np.sqrt(tdays))) if sd > 0 else 0.0
    calmar = float(annual / abs(max_dd)) if abs(max_dd) > 1e-3 else 0.0
    by_month: dict[str, float] = {}
    for d, v in zip(nav_dates, nav):
        by_month[d[:7]] = v
    months = sorted(by_month)
    mwr = (sum(1 for i in range(1, len(months)) if by_month[months[i]] > by_month[months[i - 1]]) / (len(months) - 1)
           if len(months) >= 2 else None)
    # 段级 (每调仓持有期) 胜率 + 盈亏比 (C-WinReturn: 胜率×盈亏比联立)
    wins = [r for r in seg_returns if r > 0]
    losses = [r for r in seg_returns if r < 0]
    win_rate = (len(wins) / len(seg_returns)) if seg_returns else None
    avg_win = float(np.mean(wins)) if wins else None
    avg_loss = float(np.mean(losses)) if losses else None
    payoff = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss not in (None, 0)) else None
    expectancy = (win_rate * payoff - (1 - win_rate)) if (payoff is not None and win_rate is not None) else None
    return {"annual_return": float(annual), "max_drawdown": max_dd, "sharpe": sharpe, "calmar": calmar,
            "monthly_win_rate": mwr, "win_rate": win_rate, "payoff_ratio": payoff,
            "avg_win": avg_win, "avg_loss": avg_loss, "expectancy": expectancy}


# trailing 窗口 (交易日, ~21日/月): 看策略趋势衰减/改善 (用户: 全期均值掩盖, 分窗才看出是否真适用)
_TRAILING_WINDOWS = {"3m": 63, "6m": 126, "12m": 252, "18m": 378, "24m": 504, "3y": 756, "5y": 1260}


def trailing_metrics(nav_dates: list[str], nav: list[float], tdays: int = 252) -> dict:
    """分 trailing 窗口 (近3/6/12/18/24月/3年/5年/全期) 的年化收益+月胜率+max_dd, 看趋势 (非全期均值)。

    用户洞见: 全期均值会掩盖策略衰减 (前期+20%后期-15%=均值正但已失效); 分窗口才看出某策略'是否真适用/在恶化'。
    每窗取 nav 序列最后 N 交易日 (不足则全用并标 partial)。返回 {window: {annual, monthly_win_rate, max_dd, n_days, partial}}。
    """
    if len(nav) < 2:
        return {}
    arr = np.asarray(nav, float)
    out: dict[str, dict] = {}
    for label, n in {**_TRAILING_WINDOWS, "full": len(arr)}.items():
        take = min(n, len(arr))
        if take < 2:
            continue
        seg = arr[-take:]
        seg_dates = nav_dates[-take:]
        total = seg[-1] / seg[0] - 1.0
        years = max(take / tdays, 1e-9)
        ann = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
        run_max = np.maximum.accumulate(seg)
        mdd = float(((seg - run_max) / run_max).min())
        bm: dict[str, float] = {}
        for d, v in zip(seg_dates, seg):
            bm[d[:7]] = v
        mo = sorted(bm)
        mwr = (sum(1 for i in range(1, len(mo)) if bm[mo[i]] > bm[mo[i - 1]]) / (len(mo) - 1)
               if len(mo) >= 2 else None)
        out[label] = {"annual_return": float(ann), "monthly_win_rate": mwr, "max_drawdown": mdd,
                      "n_days": take, "partial": (n > len(arr) and label != "full")}
    return out


def run_execution_backtest(rebalances, bars_by_code, calendar, *, config: ExecConfig | None = None,
                           sizing: str = "equal", top_k: int = 20, gross_exposure: float = 1.0,
                           stop_loss_pct: float | None = None) -> dict:
    """见模块 docstring 架构师合约。返回含 nav + 联合 metrics + 容量诊断。"""
    cfg = config or ExecConfig.load()
    cal_idx = {d: i for i, d in enumerate(calendar)}
    n = len(calendar)
    code_dates = {c: sorted(b) for c, b in bars_by_code.items()}

    def price(c, d, field):  # field: 0=o,1=h,2=l,3=close,4=vol
        b = bars_by_code.get(c, {}).get(d)
        return b[field] if b else None

    def prev_close(c, d):
        ds = code_dates.get(c, [])
        i = ds.index(d) if d in ds else -1
        return bars_by_code[c][ds[i - 1]][3] if i >= 1 else None

    rebs = [(dd, cands) for dd, cands in rebalances if cal_idx.get(dd) is not None and cal_idx[dd] + 1 < n]

    nav = 1.0
    cash = 1.0
    pos: dict[str, dict] = {}   # code -> {units, last_price}
    nav_dates, nav_vals, turnovers, participations, seg_returns = [], [], [], [], []
    cap_warn = 0
    cap_trades = 0
    total_cost = 0.0

    for k, (dd, cands) in enumerate(rebs):
        entry_i = cal_idx[dd] + 1
        entry_date = calendar[entry_i]
        nav_seg_start = nav

        # 当前持仓估值 (entry 日 open; 停牌用 last_price 冻结; 未持有=0)
        def val_now(c):
            if c not in pos:
                return 0.0
            p = price(c, entry_date, 0)
            return pos[c]["units"] * (p if p not in (None, 0) else pos[c]["last_price"])
        nav_now = cash + sum(val_now(c) for c in pos)

        # 候选筛: T+1 一字涨停买不进 (N8/N12) + 入场日有 open
        # entry_at_open (N14): 入场价 = entry_date 的 open (非 close), 由 val_now/units 计算用 price(...,0)=open
        selected = []
        for c, s in cands:
            if len(selected) >= top_k:
                break
            o = price(c, entry_date, 0)  # t1_open: T+1 开盘价 = 入场价
            if o in (None, 0):
                continue  # 停牌/无数据 不买
            if cfg.one_line_buy_block and _is_one_line_up((o, price(c, entry_date, 1), price(c, entry_date, 2),
                                                           price(c, entry_date, 3), price(c, entry_date, 4)),
                                                          prev_close(c, entry_date), cfg, c):
                continue  # 一字涨停剔篮
            selected.append((c, s))

        tgt_w = _sizing_weights(selected, sizing, gross_exposure, top_k, bars_by_code, entry_date, code_dates)
        tgt_val = {c: nav_now * w for c, w in tgt_w.items()}

        # 一字跌停持仓 卖不出 -> 强制保留 (N12); 其目标值 = 当前值
        for c in list(pos):
            if cfg.one_line_sell_block and _is_one_line_down(
                    (price(c, entry_date, 0), price(c, entry_date, 1), price(c, entry_date, 2),
                     price(c, entry_date, 3), price(c, entry_date, 4)), prev_close(c, entry_date), cfg, c):
                tgt_val[c] = val_now(c)  # 顺延

        # 调仓: 对每个 code 计算 delta_value, 买/卖含非对称成本 + 容量
        codes_all = set(pos) | set(tgt_val)
        seg_cost = 0.0
        seg_turn = 0.0
        new_pos: dict[str, dict] = {}
        for c in codes_all:
            p = price(c, entry_date, 0)
            cur_v = val_now(c)
            tv = tgt_val.get(c, 0.0)
            if p in (None, 0):  # 停牌: 无法交易, 冻结
                if c in pos:
                    new_pos[c] = {"units": pos[c]["units"], "last_price": pos[c]["last_price"]}
                continue
            delta = tv - cur_v
            if abs(delta) > 1e-12:
                # 容量: 单笔名义额 vs ADV
                over_cap = False
                adv = _adv_notional(c, bars_by_code[c], code_dates[c],
                                    code_dates[c].index(entry_date) if entry_date in code_dates[c] else 0, cfg.adv_window)
                if adv:
                    part = (abs(delta) * cfg.capital_cny) / adv
                    participations.append(part)
                    cap_trades += 1
                    if part > cfg.participation_threshold:
                        over_cap = True
                        cap_warn += 1
                pct = cfg.buy_cost_pct(over_cap) if delta > 0 else cfg.sell_cost_pct(over_cap)
                seg_cost += abs(delta) * pct
                seg_turn += abs(delta)
            if tv > 1e-12:
                new_pos[c] = {"units": tv / p, "last_price": p}
        pos = new_pos
        cash = nav_now - sum(v["units"] * v["last_price"] for v in pos.values())
        cash -= seg_cost
        nav = nav_now - seg_cost
        total_cost += seg_cost
        turnovers.append(seg_turn / nav_now if nav_now > 0 else 0.0)

        # 持有: entry_i .. exit_i (下一调仓 T+1 或日历末)
        exit_i = min(cal_idx[rebs[k + 1][0]] + 1, n) if k + 1 < len(rebs) else n
        for di in range(entry_i, exit_i):
            d = calendar[di]
            for c in pos:
                p = price(c, d, 3)
                if p not in (None, 0):
                    pos[c]["last_price"] = p  # 更新, 停牌则保留旧值 (冻结 N11)
                # 止损 (intra-holding, N5): 相对入场跌破 -> 标记 (下个调仓卖; 简化不日内强平)
            holdings_val = sum(v["units"] * v["last_price"] for v in pos.values())
            nav = cash + holdings_val
            nav_dates.append(d)
            nav_vals.append(nav)
        # 段收益 (该持有期组合收益, C-WinReturn 段级胜率/盈亏比)
        if nav_seg_start > 0:
            seg_returns.append(nav / nav_seg_start - 1.0)

    if not nav_vals:
        return {"nav": [], "metrics": _metrics([], [], []), "cost_drag": 0.0, "avg_turnover": 0.0,
                "avg_participation": None, "max_participation": None, "capacity_warn_rate": 0.0,
                "n_rebalances": len(rebs), "final_nav": 1.0}
    return {"nav": list(zip(nav_dates, nav_vals)), "metrics": _metrics(nav_dates, nav_vals, seg_returns),
            "cost_drag": total_cost, "avg_turnover": float(np.mean(turnovers)) if turnovers else 0.0,
            "avg_participation": float(np.mean(participations)) if participations else None,
            "max_participation": float(np.max(participations)) if participations else None,
            "capacity_warn_rate": (cap_warn / cap_trades) if cap_trades else 0.0,
            "n_rebalances": len(rebs), "final_nav": nav_vals[-1]}
