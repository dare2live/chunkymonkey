"""北向资金研究页 API —— w3 规格 + b1 核验报告裁定边界的落地 (2026-09-08)。

前端契约:
  GET /api/v3/northbound/stock/{stock_code}   Tab1 单股: 香港中央结算有限公司(陆股通北向
                                                持仓境内代理人, holder_code=10671586)在这只
                                                股票十大流通股东名单里的季度序列 (占流通股比例
                                                / 排名 / 进入退出事件)。?as_of=YYYY-MM-DD 可选,
                                                省略=生产缺省 PIT 上界(最近完整交易日)。
  GET /api/v3/northbound/market-breadth        Tab2 市场广度: 按标准季度末统计"十大流通股东
                                                含香港中央结算"的股票数 / 当期有披露的股票数。
                                                ?as_of=YYYY-MM-DD 可选。
  GET /api/v3/northbound/market-flow           Tab3: 治理阻塞状态说明 (不是数据查询) ——
                                                moneyflow_hsgt 待 owner 改判 retire 决策前,
                                                本页不建消费方, 这个端点只回状态不回数字。

这一页只回答"香港中央结算在多少只股票的十大流通股东名单里出现、持股比例多高、这个覆盖面
按季度如何变化" —— 季度级参考读数, 不是逐日资金流, 不进策略入场信号。做不了什么、为什么,
见 ``services.northbound_research`` 模块 docstring。

措辞纪律 (feedback-frontend-plain-finance-terms): 用"进入/退出十大流通股东"、"持股比例上升
/下降"、"覆盖面扩大/收窄"这类对称金融术语；不用"资金流入/涌入/出逃/加仓/减仓"这类暗示
逐笔买卖或情绪的口语化说法——我们看到的是期末持股比例的期间变化, 不是资金流。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services import northbound_research as nr

router = APIRouter()


@router.get("/stock/{stock_code}")
def get_stock_series(
    stock_code: str,
    as_of: str | None = Query(None, description="ISO 决策日 (YYYY-MM-DD)，省略=最近完整交易日"),
) -> dict:
    try:
        return nr.stock_series(stock_code, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/market-breadth")
def get_market_breadth(
    as_of: str | None = Query(None, description="ISO 决策日 (YYYY-MM-DD)，省略=最近完整交易日"),
) -> dict:
    try:
        return nr.market_breadth(as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/market-flow")
def get_market_flow_status() -> dict:
    return nr.market_flow_status()


__all__ = ["router"]
