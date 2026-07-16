"""Legacy 手工观察账本；NONCONFORMING，不是 Tier4 paper execution。

当前只用最新 qfq close 做近似记账，缺订单/成交、T+1、停牌、涨跌停和 nominal execution
price，因此任何收益、胜率、NAV 只能作为观察值，不能作为 StrategyRelease 或候选信号证据。

现有实现边界:
  - 取价走 SERVE: DataAccess.get('kline_qfq'/'index_daily')，只适合研究估值
  - 交易日走 services.calendar.latest_closed_or_raise (交易日历真相源, 不 wall-clock)
  - 阈值走 config/paper_portfolio.yaml (init_cash/佣金/印花税 — 判断死红线)
  - 表 = smartmoney display 层 (Type A: mark-to-market 给定持仓+价格结果唯一, 确定性 PIT 重排),
    declare-on-build + data_layers 声明
  - 只允许显式手动 mark；已从数据管线 store 阶段移除，避免数据更新暗中改变观察账本
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from services.db import get_conn

logger = logging.getLogger(__name__)

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "paper_portfolio.yaml"


def _cfg() -> dict[str, Any]:
    return yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))


DDL = """
CREATE TABLE IF NOT EXISTS paper_position (
    position_id   TEXT PRIMARY KEY,
    strategy_tag  TEXT NOT NULL,              -- 观察来源标签，不代表已发布策略
    stock_code    TEXT NOT NULL,
    shares        DOUBLE NOT NULL,            -- 观察名义数量 (按金额换算)
    entry_date    TEXT NOT NULL,              -- 记入日 (最新完成交易日, ISO)
    entry_price   DOUBLE NOT NULL,            -- 记入日 qfq close；非成交价
    entry_fee     DOUBLE NOT NULL DEFAULT 0,  -- 买入佣金
    exit_date     TEXT,
    exit_price    DOUBLE,
    exit_fee      DOUBLE,                     -- 卖出佣金+印花税
    status        TEXT NOT NULL DEFAULT 'open',   -- open | closed
    note          TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_nav_daily (
    nav_date      TEXT NOT NULL,              -- ISO
    nav           DOUBLE NOT NULL,            -- cash + 持仓市值
    cash          DOUBLE NOT NULL,
    position_value DOUBLE NOT NULL,
    n_open        INTEGER NOT NULL,
    bench_close   DOUBLE,                     -- HS300 收盘
    built_at      TEXT NOT NULL,
    PRIMARY KEY (nav_date)
);
"""


def ensure_tables(conn) -> None:
    conn.executescript(DDL)


# ── 取价 (SERVE 单读路: 本模块是 CONSUME 方, 禁内联 FROM raw_) ─────────────────

def _latest_trade_date() -> str:
    from services.calendar import latest_closed_or_raise
    return latest_closed_or_raise()  # ISO YYYY-MM-DD


def _close_price(stock_code: str, as_of: str, conn=None) -> float | None:
    """as_of 当日 (或之前最近交易日) 的 qfq close — 经 SERVE kline_qfq entity (rows=归一 dict 行)。"""
    from services.data_access import DataAccess
    rows = DataAccess().get("kline_qfq", codes=[stock_code], as_of=as_of).rows
    if not rows:
        return None
    last = max(rows, key=lambda r: r["date"])
    return float(last["close"])


def _bench_close(as_of: str) -> float | None:
    from services.data_access import DataAccess
    cfg = _cfg()
    rows = DataAccess().get(cfg["benchmark_entity"], codes=[cfg["benchmark_code"]], as_of=as_of).rows
    if not rows:
        return None
    last = max(rows, key=lambda r: r["trade_date"])
    return float(last["close"])


# ── 观察账本操作面（兼容函数名保留）────────────────────────────────────────

def add_position(stock_code: str, *, amount: float | None = None, shares: float | None = None,
                 strategy_tag: str = "manual", note: str = "", conn=None) -> dict[str, Any]:
    """记入观察：entry_price=最新完成交易日 qfq close，不代表可成交价格。"""
    if (amount is None) == (shares is None):
        raise ValueError("amount 与 shares 二选一")
    cfg = _cfg()
    as_of = _latest_trade_date()
    px = _close_price(stock_code, as_of)
    if px is None or px <= 0:
        raise ValueError(f"{stock_code} 在 {as_of} 无 qfq 价格 (停牌/非活跃股?)")
    if shares is None:
        shares = round(amount / px / 100) * 100  # A股一手=100股
        if shares <= 0:
            raise ValueError(f"金额 {amount} 不足一手 ({stock_code}@{px:.2f})")
    fee = shares * px * cfg["commission_rate"]

    own = conn is None
    c = conn or get_conn()
    try:
        ensure_tables(c)
        # 现金校验 (init_cash − open成本 + closed回笼)
        snap = _cash_and_value(c, as_of)
        need = shares * px + fee
        if need > snap["cash"]:
            raise ValueError(f"现金不足: 需 {need:,.0f} > 可用 {snap['cash']:,.0f}")
        pid = uuid.uuid4().hex[:12]
        c.execute(
            "INSERT INTO paper_position (position_id, strategy_tag, stock_code, shares, "
            "entry_date, entry_price, entry_fee, status, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,'open',?,?)",
            (pid, strategy_tag, stock_code, float(shares), as_of, px, fee, note,
             datetime.now(timezone.utc).isoformat()))
        c.commit()
        return {"position_id": pid, "stock_code": stock_code, "shares": shares,
                "entry_date": as_of, "entry_price": px, "fee": round(fee, 2)}
    finally:
        if own:
            c.close()


def close_position(position_id: str, conn=None) -> dict[str, Any]:
    """出池: exit_price=最新完成交易日 qfq close; 摩擦=佣金+印花税(卖方)。"""
    cfg = _cfg()
    as_of = _latest_trade_date()
    own = conn is None
    c = conn or get_conn()
    try:
        ensure_tables(c)
        row = c.execute("SELECT stock_code, shares, entry_price, entry_fee FROM paper_position "
                        "WHERE position_id=? AND status='open'", (position_id,)).fetchone()
        if not row:
            raise ValueError(f"open position 不存在: {position_id}")
        stock, shares, entry_px, entry_fee = row[0], float(row[1]), float(row[2]), float(row[3])
        px = _close_price(stock, as_of)
        if px is None:
            raise ValueError(f"{stock} 在 {as_of} 无价格")
        fee = shares * px * (cfg["commission_rate"] + cfg["stamp_tax_rate"])
        c.execute("UPDATE paper_position SET status='closed', exit_date=?, exit_price=?, exit_fee=? "
                  "WHERE position_id=?", (as_of, px, fee, position_id))
        c.commit()
        pnl = shares * (px - entry_px) - entry_fee - fee
        return {"position_id": position_id, "stock_code": stock, "exit_date": as_of,
                "exit_price": px, "pnl": round(pnl, 2),
                "ret_pct": round((px - entry_px) / entry_px * 100, 2)}
    finally:
        if own:
            c.close()


# ── mark-to-market + KPI ───────────────────────────────────────────────────

def _cash_and_value(c, as_of: str) -> dict[str, float]:
    cfg = _cfg()
    cash = float(cfg["init_cash"])
    pos_value, n_open = 0.0, 0
    for (pid, stock, shares, entry_px, entry_fee, status, exit_px, exit_fee) in c.execute(
            "SELECT position_id, stock_code, shares, entry_price, entry_fee, status, "
            "exit_price, exit_fee FROM paper_position").fetchall():
        shares, entry_px, entry_fee = float(shares), float(entry_px), float(entry_fee or 0)
        cash -= shares * entry_px + entry_fee
        if status == "closed":
            cash += shares * float(exit_px) - float(exit_fee or 0)
        else:
            n_open += 1
            px = _close_price(stock, as_of)
            pos_value += shares * (px if px is not None else entry_px)
    return {"cash": cash, "position_value": pos_value, "n_open": n_open}


def mark_to_market(as_of: str | None = None, conn=None) -> dict[str, Any]:
    """显式手工写入观察账本快照；不由 daily_update 自动调用。同日重跑覆盖。"""
    as_of = as_of or _latest_trade_date()
    own = conn is None
    c = conn or get_conn()
    try:
        ensure_tables(c)
        snap = _cash_and_value(c, as_of)
        nav = snap["cash"] + snap["position_value"]
        bench = _bench_close(as_of)
        c.execute("DELETE FROM paper_nav_daily WHERE nav_date=?", (as_of,))
        c.execute("INSERT INTO paper_nav_daily VALUES (?,?,?,?,?,?,?)",
                  (as_of, nav, snap["cash"], snap["position_value"], snap["n_open"], bench,
                   datetime.now(timezone.utc).isoformat()))
        c.commit()
        return {"nav_date": as_of, "nav": round(nav, 2), **{k: round(v, 2) if isinstance(v, float) else v for k, v in snap.items()}}
    finally:
        if own:
            c.close()


def portfolio_kpi(conn=None) -> dict[str, Any]:
    """胜率/收益率/超额 (用户三指标)。closed 计已实现; open 浮盈单列不混排。"""
    cfg = _cfg()
    own = conn is None
    c = conn or get_conn()
    try:
        ensure_tables(c)
        closed = c.execute(
            "SELECT COUNT(*), SUM(CASE WHEN shares*(exit_price-entry_price)-entry_fee-exit_fee > 0 "
            "THEN 1 ELSE 0 END) FROM paper_position WHERE status='closed'").fetchone()
        n_closed, n_win = int(closed[0] or 0), int(closed[1] or 0)
        nav_rows = c.execute(
            "SELECT nav_date, nav, bench_close FROM paper_nav_daily ORDER BY nav_date").fetchall()
        out: dict[str, Any] = {
            "init_cash": cfg["init_cash"], "n_closed": n_closed,
            "win_rate": round(n_win / n_closed, 4) if n_closed else None,
        }
        if nav_rows:
            first, last = nav_rows[0], nav_rows[-1]
            out["nav"] = round(float(last[1]), 2)
            out["ret_cum"] = round(float(last[1]) / cfg["init_cash"] - 1, 4)
            if first[2] and last[2]:
                bench_ret = float(last[2]) / float(first[2]) - 1
                out["bench_ret_cum"] = round(bench_ret, 4)
                out["excess_cum"] = round(out["ret_cum"] - bench_ret, 4)
        return out
    finally:
        if own:
            c.close()
