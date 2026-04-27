"""东财行情快照接口 (push2his / push2delay).

push2his.eastmoney.com  → 历史 K 线 / 资金流 (~250 天)
push2delay.eastmoney.com → 仅最新交易日 (1 行)

注: 用户家宽出口 IP 可能在 push2his 反爬黑名单 (§7.8). 该模块提供两种
endpoint 但调用方需自己处理 RemoteDisconnected fallback.

字段编号 (akshare/eastmoney 通用):
    f51: 日期            f52: 主力净流入-净额
    f53: 小单净流入-净额  f54: 中单净流入-净额
    f55: 大单净流入-净额  f56: 超大单净流入-净额
    f57-f61: 净占比 (主/小/中/大/超大)
    f62: 收盘价          f63: 涨跌幅
"""
from __future__ import annotations

import time
from typing import Literal

from .client import EastMoneyClient, default_client


PUSH2_HIS_FFLOW = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
PUSH2_DELAY_FFLOW = "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get"

_MARKET_MAP = {"sh": 1, "sz": 0, "bj": 0}

_FFLOW_FIELDS = {
    "fields1": "f1,f2,f3,f7",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
}

_FFLOW_COLS = [
    "trade_date",
    "main_net_amount",
    "small_net_amount",
    "medium_net_amount",
    "large_net_amount",
    "super_large_net_amount",
    "main_net_pct",
    "small_net_pct",
    "medium_net_pct",
    "large_net_pct",
    "super_large_net_pct",
    "close_price",
    "pct_change",
    "_ignore1",
    "_ignore2",
]


def _fetch_fflow(
    stock_code: str,
    market: str,
    *,
    base_url: str,
    client: EastMoneyClient | None = None,
    timeout: float | None = None,
) -> list[dict]:
    """通用 fflow 拉取. 返回 list[dict] (英文键 + 字符串值)."""
    cli = client or default_client
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": f"{_MARKET_MAP.get(market.lower(), 0)}.{stock_code}",
        **_FFLOW_FIELDS,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        # 东财部分接口要求带 millis cache buster, 否则可能返回 rc=100/data=null
        "_": int(time.time() * 1000),
    }
    headers = {"Referer": "https://data.eastmoney.com/zjlx/detail.html"}
    data = cli.get_json(base_url, params=params, headers=headers, timeout=timeout)
    klines = ((data or {}).get("data") or {}).get("klines") or []
    if not klines:
        return []
    rows = []
    for line in klines:
        parts = line.split(",")
        # 容错: 字段数不一定恰好 15
        row = {}
        for i, col in enumerate(_FFLOW_COLS):
            if col.startswith("_"):
                continue
            row[col] = parts[i] if i < len(parts) else None
        rows.append(row)
    return rows


def fetch_fund_flow_history(
    stock_code: str,
    market: Literal["sh", "sz", "bj"] = "sh",
    *,
    client: EastMoneyClient | None = None,
    timeout: float | None = None,
) -> list[dict]:
    """push2his 历史资金流, ~250 个交易日.

    用户机器若在 eastmoney push2his 反爬黑名单, 会 RemoteDisconnected.
    调用方应捕获 EastMoneyError + RequestException 并 fallback.
    """
    return _fetch_fflow(
        stock_code, market,
        base_url=PUSH2_HIS_FFLOW, client=client, timeout=timeout,
    )


def fetch_fund_flow_latest(
    stock_code: str,
    market: Literal["sh", "sz", "bj"] = "sh",
    *,
    client: EastMoneyClient | None = None,
    timeout: float | None = None,
) -> list[dict]:
    """push2delay 当日资金流, 仅 1 行/票. 接口设计上限."""
    return _fetch_fflow(
        stock_code, market,
        base_url=PUSH2_DELAY_FFLOW, client=client, timeout=timeout,
    )
