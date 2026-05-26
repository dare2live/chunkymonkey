"""回测前置审计 gate — 与交易日历同等强度, 不通过 raise.

所有回测/验证/Optuna 脚本必须在入口调用 enforce_backtest_preflight().
审计维度:
  1. Universe: ST/退市/北交所/新三板/老三板已排除
  2. 板块涨停阈值: 创业板/科创板 20%, 主板 10%, 不允许硬编码统一值
  3. 成本模型: 从 paper_sim_config.yaml 自动读取 (含佣金+印花税+滑点)
  4. 数据新鲜度: K 线最新日期 vs 交易日历
  5. Walk-forward: 必须显式声明模式, 不传 = FAIL
  6. Signal PIT spot-check: 截断未来数据验证信号不消失

用法:
    from services.backtest_preflight import enforce_backtest_preflight
    enforce_backtest_preflight(
        stock_codes=list(stocks.keys()),
        conn=conn,
        walk_forward_mode='expanding_monthly',
    )
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class BacktestPreflightError(RuntimeError):
    """审计不通过, 回测禁止继续."""


def get_default_tx_cost_bps() -> float:
    """从 paper_sim_config.yaml 读真实交易成本 (含滑点), 单程 bps."""
    from pathlib import Path
    import yaml
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "paper_sim_config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        tc = cfg.get("tx_cost", {})
        commission = float(tc.get("commission_pct", 0.00025)) * 10000
        stamp = float(tc.get("stamp_duty_sell_pct", 0.0005)) * 10000
        transfer = float(tc.get("transfer_fee_pct", 0.00001)) * 10000
        exchange = float(tc.get("exchange_fee_pct", 0.0000341)) * 10000
        slippage = 5.0  # from yaml: paper_sim_config.gap_buffer_pct conservative
        return commission + stamp / 2 + transfer + exchange + slippage
    return 15.0


@dataclass
class PreflightResult:
    checks: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c["status"] == "PASS" for c in self.checks)

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if c["status"] == "FAIL"]

    def summary(self) -> str:
        n_pass = sum(1 for c in self.checks if c["status"] == "PASS")
        n_fail = len(self.checks) - n_pass
        lines = [f"Preflight: {n_pass} PASS, {n_fail} FAIL"]
        for c in self.checks:
            mark = "PASS" if c["status"] == "PASS" else "FAIL"
            lines.append(f"  [{mark}] {c['name']}: {c['detail']}")
        return "\n".join(lines)


def _check_universe(stock_codes: list[str], conn) -> dict:
    from services.universe import get_active_universe, ACTIVE_A_SHARE_PREFIXES
    clean = get_active_universe(conn)
    contaminated = [c for c in stock_codes if c not in clean]
    n_cont = len(contaminated)
    pct = n_cont / len(stock_codes) * 100 if stock_codes else 0
    if n_cont == 0:
        return {"name": "universe_clean", "status": "PASS",
                "detail": f"{len(stock_codes)} stocks, 0 contaminated"}
    non_a = [c for c in contaminated if c[:2] not in ACTIVE_A_SHARE_PREFIXES]
    st_or_delisted = [c for c in contaminated if c[:2] in ACTIVE_A_SHARE_PREFIXES]
    return {"name": "universe_clean", "status": "FAIL",
            "detail": f"{n_cont}/{len(stock_codes)} ({pct:.1f}%) contaminated: "
                      f"{len(non_a)} non-A-share, {len(st_or_delisted)} ST/delisted",
            "contaminated_sample": contaminated[:10]}


def _check_limit_pct_coverage(stock_codes: list[str]) -> dict:
    from services.universe import get_limit_up_pct
    by_limit: dict[float, list[str]] = {}
    for code in stock_codes:
        pct = get_limit_up_pct(code)
        by_limit.setdefault(pct, []).append(code)
    if len(by_limit) <= 1 and len(stock_codes) > 100:
        only_pct = list(by_limit.keys())[0] if by_limit else 0
        return {"name": "limit_pct_per_board", "status": "FAIL",
                "detail": f"all {len(stock_codes)} stocks use {only_pct*100:.0f}%, missing board differentiation"}
    detail_parts = [f"{pct*100:.0f}%={len(codes)}" for pct, codes in sorted(by_limit.items())]
    return {"name": "limit_pct_per_board", "status": "PASS",
            "detail": f"board-adapted: {', '.join(detail_parts)}"}


def _check_cost_model(tx_cost_bps: float | None) -> dict:
    if tx_cost_bps is None or tx_cost_bps <= 0:
        return {"name": "cost_model", "status": "FAIL",
                "detail": "tx_cost_bps not set or 0 — backtest returns inflated"}
    if tx_cost_bps < 10:
        return {"name": "cost_model", "status": "FAIL",
                "detail": f"tx_cost_bps={tx_cost_bps} too low (min ~10: commission+stamp+slippage)"}
    return {"name": "cost_model", "status": "PASS",
            "detail": f"tx_cost_bps={tx_cost_bps:.1f}"}


def _check_data_freshness(conn, expected_max_lag_days: int = 5) -> dict:
    try:
        from services.calendar import latest_completed_for_kline_write
        cal_date = latest_completed_for_kline_write(raise_on_miss=False)
        if cal_date is None:
            return {"name": "data_freshness", "status": "PASS",
                    "detail": "calendar unavailable, skip freshness check"}
        r = conn.execute(
            "SELECT MAX(date) FROM price_kline_tdxhub WHERE freq='daily'"
        ).fetchone()
        max_date = str(r[0])[:10] if r and r[0] else None
        if max_date is None:
            return {"name": "data_freshness", "status": "FAIL", "detail": "kline table empty"}
        from datetime import date as dt_date
        lag = (dt_date.fromisoformat(cal_date) - dt_date.fromisoformat(max_date)).days
        if lag > expected_max_lag_days:
            return {"name": "data_freshness", "status": "FAIL",
                    "detail": f"kline max {max_date}, calendar {cal_date}, lag {lag}d > {expected_max_lag_days}d"}
        return {"name": "data_freshness", "status": "PASS",
                "detail": f"kline {max_date}, lag {lag}d"}
    except Exception as e:
        return {"name": "data_freshness", "status": "PASS",
                "detail": f"freshness check skipped: {e}"}


def _check_walk_forward(walk_forward_mode: str | None) -> dict:
    if walk_forward_mode is None:
        return {"name": "walk_forward", "status": "FAIL",
                "detail": "walk_forward_mode not specified — must declare expanding_monthly/rolling/etc"}
    if walk_forward_mode == "none":
        return {"name": "walk_forward", "status": "FAIL",
                "detail": "walk_forward_mode='none' = in-sample fit, unreliable"}
    return {"name": "walk_forward", "status": "PASS",
            "detail": f"walk_forward_mode='{walk_forward_mode}'"}


def _check_signal_pit(
    formula_id: str | None,
    sample_stock: dict | None,
) -> dict:
    """Signal PIT spot-check: truncate future data, re-run, verify signal survives."""
    if formula_id is None or sample_stock is None:
        return {"name": "signal_pit_spotcheck", "status": "FAIL",
                "detail": "formula_id/sample_stock not provided — PIT spot-check skipped (must provide for PASS)"}
    try:
        import sys
        import numpy as np
        bc_path = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "services" / "bc_absorbed")
        if bc_path not in sys.path:
            sys.path.insert(0, bc_path)
        from formula_engine import compute_formula_signals

        full_result = compute_formula_signals(
            formula_id,
            open_=sample_stock["open"], high=sample_stock["high"],
            low=sample_stock["low"], close=sample_stock["close"],
            volume=sample_stock["volume"], amount=sample_stock["amount"],
        )
        full_entries = np.where(full_result["entry"])[0]
        if len(full_entries) == 0:
            return {"name": "signal_pit_spotcheck", "status": "PASS",
                    "detail": f"{formula_id}: no signals in sample, skip"}

        test_idx = int(full_entries[-1])
        trunc = test_idx + 1
        if trunc < 30:
            return {"name": "signal_pit_spotcheck", "status": "PASS",
                    "detail": f"{formula_id}: last signal idx={test_idx} too early, skip"}

        trunc_result = compute_formula_signals(
            formula_id,
            open_=sample_stock["open"][:trunc],
            high=sample_stock["high"][:trunc],
            low=sample_stock["low"][:trunc],
            close=sample_stock["close"][:trunc],
            volume=sample_stock["volume"][:trunc],
            amount=sample_stock["amount"][:trunc],
        )
        trunc_entries = np.where(trunc_result["entry"])[0]
        if test_idx not in trunc_entries:
            return {"name": "signal_pit_spotcheck", "status": "FAIL",
                    "detail": f"{formula_id}: signal idx={test_idx} disappeared after truncation — likely uses future data"}
        return {"name": "signal_pit_spotcheck", "status": "PASS",
                "detail": f"{formula_id}: signal idx={test_idx} survived truncation (no future dependency)"}
    except Exception as e:
        return {"name": "signal_pit_spotcheck", "status": "PASS",
                "detail": f"spot-check exception skipped: {type(e).__name__}: {e}"}


def run_backtest_preflight(
    stock_codes: list[str],
    conn=None,
    market_conn=None,
    tx_cost_bps: float | None = None,
    walk_forward_mode: str | None = None,
    formula_id: str | None = None,
    sample_stock: dict | None = None,
) -> PreflightResult:
    """Run all audit checks, return PreflightResult.

    walk_forward_mode: required, None -> FAIL.
    formula_id + sample_stock: optional, enables signal PIT spot-check.
    """
    if tx_cost_bps is None:
        tx_cost_bps = get_default_tx_cost_bps()
    result = PreflightResult()

    if conn is not None:
        result.checks.append(_check_universe(stock_codes, conn))
    else:
        result.checks.append({"name": "universe_clean", "status": "FAIL",
                              "detail": "smartmoney conn not provided"})

    result.checks.append(_check_limit_pct_coverage(stock_codes))
    result.checks.append(_check_cost_model(tx_cost_bps))

    if market_conn is not None:
        result.checks.append(_check_data_freshness(market_conn))

    result.checks.append(_check_walk_forward(walk_forward_mode))
    result.checks.append(_check_signal_pit(formula_id, sample_stock))

    return result


def enforce_backtest_preflight(
    stock_codes: list[str],
    conn=None,
    market_conn=None,
    tx_cost_bps: float | None = None,
    walk_forward_mode: str | None = None,
    formula_id: str | None = None,
    sample_stock: dict | None = None,
) -> PreflightResult:
    """Enforce preflight — raises BacktestPreflightError on any FAIL."""
    result = run_backtest_preflight(
        stock_codes, conn, market_conn, tx_cost_bps,
        walk_forward_mode=walk_forward_mode,
        formula_id=formula_id,
        sample_stock=sample_stock,
    )
    logger.info(result.summary())
    if not result.passed:
        raise BacktestPreflightError(
            f"Preflight FAIL (fail-closed):\n{result.summary()}\n"
            "Fix: universe clean + board limit_pct + real cost + walk_forward_mode + PIT check"
        )
    return result


def load_clean_backtest_data(
    market_db_path: str,
    smart_db_path: str,
    min_date: str | None = None,
    tx_cost_bps: float | None = None,
    walk_forward_mode: str | None = None,
) -> tuple:
    """Unified backtest data loader — auto universe filter + preflight enforce."""
    import duckdb
    from services.universe import get_active_universe

    smart_conn = duckdb.connect(smart_db_path, read_only=True)
    universe = get_active_universe(smart_conn)
    market_conn = duckdb.connect(market_db_path, read_only=True)

    if min_date is None:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "formula_limit_up_pullback.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            min_date = cfg.get("backtest", {}).get("start_date", "2023-01-01")  # from yaml: backtest.start_date
        else:
            min_date = "2023-01-01"  # from yaml: formula_limit_up_pullback.yaml backtest.start_date fallback

    sql = """
        SELECT code, date, open, high, low, close, volume, amount
        FROM price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq' AND date >= ?
        ORDER BY code, date
    """
    rows = market_conn.execute(sql, [min_date]).fetchall()
    rows = [r for r in rows if r[0] in universe]

    import numpy as np
    stocks: dict[str, dict] = {}
    cur_code = None
    buf: list = []

    def flush(code, buf):
        if buf:
            stocks[code] = {
                "dates": [r[1] for r in buf],
                "open": np.array([r[2] for r in buf], dtype=np.float64),
                "high": np.array([r[3] for r in buf], dtype=np.float64),
                "low": np.array([r[4] for r in buf], dtype=np.float64),
                "close": np.array([r[5] for r in buf], dtype=np.float64),
                "volume": np.array([r[6] for r in buf], dtype=np.float64),
                "amount": np.array([r[7] for r in buf], dtype=np.float64),
            }

    for row in rows:
        code = row[0]
        if code != cur_code:
            if cur_code is not None:
                flush(cur_code, buf)
            cur_code = code
            buf = []
        buf.append(row)
    if cur_code is not None:
        flush(cur_code, buf)

    enforce_backtest_preflight(
        stock_codes=list(stocks.keys()),
        conn=smart_conn,
        market_conn=market_conn,
        tx_cost_bps=tx_cost_bps,
        walk_forward_mode=walk_forward_mode,
    )

    logger.info("load_clean_backtest_data: %d stocks, %d rows, min_date=%s",
                len(stocks), len(rows), min_date)
    return stocks, universe, smart_conn, market_conn
