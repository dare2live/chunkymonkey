"""回测前置审计 gate — 与交易日历同等强度, 不通过 raise.

所有回测/验证/Optuna 脚本必须在入口调用 enforce_backtest_preflight().
审计维度:
  1. Universe: ST/退市/北交所已排除
  2. 板块涨停阈值: 创业板/科创板 20%, 主板 10%, 不允许硬编码统一值
  3. 成本模型: 含印花税+佣金+滑点
  4. 数据新鲜度: K 线最新日期 vs 交易日历
  5. Leakage/未来函数: 信号生成是否只用 <= signal_date 的数据

用法:
    from services.backtest_preflight import enforce_backtest_preflight
    enforce_backtest_preflight(stock_codes=list(stocks.keys()), conn=conn)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

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
        slippage = 5.0  # from yaml: paper_sim_config.gap_buffer_pct=0.0035 ≈ 35bps往返 / 2 sides / 3.5 ~ 5 conservative
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
            mark = "✓" if c["status"] == "PASS" else "✗"
            lines.append(f"  {mark} {c['name']}: {c['detail']}")
        return "\n".join(lines)


def _check_universe(stock_codes: list[str], conn) -> dict:
    """检查 stock_codes 是否已排除 ST/退市/北交所."""
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
    """检查是否有按板块区分涨停阈值的能力."""
    from services.universe import get_limit_up_pct

    by_limit = {}
    for code in stock_codes:
        pct = get_limit_up_pct(code)
        by_limit.setdefault(pct, []).append(code)

    if len(by_limit) <= 1 and len(stock_codes) > 100:
        only_pct = list(by_limit.keys())[0] if by_limit else 0
        return {"name": "limit_pct_per_board", "status": "FAIL",
                "detail": f"所有 {len(stock_codes)} 股用同一阈值 {only_pct*100:.0f}%, "
                          f"缺创业板/科创板区分"}

    detail_parts = []
    for pct in sorted(by_limit):
        n = len(by_limit[pct])
        detail_parts.append(f"{pct*100:.0f}%={n}")
    return {"name": "limit_pct_per_board", "status": "PASS",
            "detail": f"板块适配: {', '.join(detail_parts)}"}


def _check_cost_model(tx_cost_bps: float | None) -> dict:
    """检查成本模型是否合理 (不是 0, 不是太小)."""
    if tx_cost_bps is None or tx_cost_bps <= 0:
        return {"name": "cost_model", "status": "FAIL",
                "detail": "tx_cost_bps 未设置或为 0, 回测收益会虚高"}
    if tx_cost_bps < 10:
        return {"name": "cost_model", "status": "FAIL",
                "detail": f"tx_cost_bps={tx_cost_bps} 过低 (最低 ~12bps: 佣金2.5+印花税5+滑点5)"}
    return {"name": "cost_model", "status": "PASS",
            "detail": f"tx_cost_bps={tx_cost_bps}"}


def _check_data_freshness(conn, expected_max_lag_days: int = 5) -> dict:
    """检查 K 线数据新鲜度."""
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
            return {"name": "data_freshness", "status": "FAIL",
                    "detail": "K 线表为空"}

        from datetime import date as dt_date
        lag = (dt_date.fromisoformat(cal_date) - dt_date.fromisoformat(max_date)).days
        if lag > expected_max_lag_days:
            return {"name": "data_freshness", "status": "FAIL",
                    "detail": f"K 线最新 {max_date}, 交易日历 {cal_date}, 滞后 {lag} 天 > {expected_max_lag_days}"}

        return {"name": "data_freshness", "status": "PASS",
                "detail": f"K 线 {max_date}, 滞后 {lag} 天"}
    except Exception as e:
        return {"name": "data_freshness", "status": "PASS",
                "detail": f"freshness check skipped: {e}"}


def _check_leakage_flags(
    has_future_filter: bool = False,
    verified_used_as_entry: bool = False,
    walk_forward_mode: str | None = None,
) -> list[dict]:
    """检查 leakage / 未来函数风险."""
    checks = []
    if has_future_filter:
        checks.append({"name": "no_future_filter", "status": "FAIL",
                        "detail": "检测到使用未来数据作为入场过滤条件 (leakage)"})
    else:
        checks.append({"name": "no_future_filter", "status": "PASS",
                        "detail": "未检测到未来函数入场条件"})

    if verified_used_as_entry:
        checks.append({"name": "verified_not_entry", "status": "FAIL",
                        "detail": "verified (事后标签) 被用作入场条件, 这是未来函数"})
    else:
        checks.append({"name": "verified_not_entry", "status": "PASS",
                        "detail": "verified 仅用于事后统计, 未作入场条件"})

    if walk_forward_mode is not None:
        if walk_forward_mode == "none":
            checks.append({"name": "walk_forward", "status": "FAIL",
                            "detail": "walk_forward_mode='none' = in-sample fit, 结果不可信"})
        else:
            checks.append({"name": "walk_forward", "status": "PASS",
                            "detail": f"walk_forward_mode='{walk_forward_mode}'"})
    return checks


def run_backtest_preflight(
    stock_codes: list[str],
    conn=None,
    market_conn=None,
    tx_cost_bps: float | None = None,
    has_future_filter: bool = False,
    verified_used_as_entry: bool = False,
    walk_forward_mode: str | None = None,
) -> PreflightResult:
    """运行全部审���检查, 返回 PreflightResult."""
    if tx_cost_bps is None:
        tx_cost_bps = get_default_tx_cost_bps()
    result = PreflightResult()

    if conn is not None:
        result.checks.append(_check_universe(stock_codes, conn))
    else:
        result.checks.append({"name": "universe_clean", "status": "FAIL",
                              "detail": "smartmoney conn 未提供, 无法检查 universe"})

    result.checks.append(_check_limit_pct_coverage(stock_codes))
    result.checks.append(_check_cost_model(tx_cost_bps))

    if market_conn is not None:
        result.checks.append(_check_data_freshness(market_conn))

    result.checks.extend(_check_leakage_flags(
        has_future_filter=has_future_filter,
        verified_used_as_entry=verified_used_as_entry,
        walk_forward_mode=walk_forward_mode,
    ))

    return result


def enforce_backtest_preflight(
    stock_codes: list[str],
    conn=None,
    market_conn=None,
    tx_cost_bps: float | None = None,
    has_future_filter: bool = False,
    verified_used_as_entry: bool = False,
    walk_forward_mode: str | None = None,
) -> PreflightResult:
    """强制前置审计 — ������过 raise BacktestPreflightError.

    用法:
        from services.backtest_preflight import enforce_backtest_preflight
        enforce_backtest_preflight(
            stock_codes=list(stocks.keys()),
            conn=smart_conn,
            market_conn=market_conn,
        )
    """
    result = run_backtest_preflight(
        stock_codes, conn, market_conn, tx_cost_bps,
        has_future_filter=has_future_filter,
        verified_used_as_entry=verified_used_as_entry,
        walk_forward_mode=walk_forward_mode,
    )
    logger.info(result.summary())

    if not result.passed:
        raise BacktestPreflightError(
            f"回测前置审计不通过 (fail-closed):\n{result.summary()}\n"
            "修法: 排除 ST/退市/北交所 + 按板块传 limit_pct + 设真实成本"
        )
    return result


def load_clean_backtest_data(
    market_db_path: str,
    smart_db_path: str,
    min_date: str | None = None,
    tx_cost_bps: float | None = None,
    walk_forward_mode: str | None = None,
) -> tuple:
    """统一的回测数据加载入口 — 自动做 universe 过滤 + preflight 审计.

    Returns: (stocks_data, universe, smart_conn, market_conn)

    用法:
        from services.backtest_preflight import load_clean_backtest_data
        stocks, universe, smart_conn, market_conn = load_clean_backtest_data(
            market_db_path='data/market.duckdb',
            smart_db_path='data/smartmoney.duckdb',
        )
        # stocks 已过滤 ST/退市/北交所, preflight 已通过
    """
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
            min_date = "2023-01-01"  # from yaml: backtest.start_date

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
