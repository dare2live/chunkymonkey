from typing import Optional
from pydantic import BaseModel, ConfigDict


# ==========================================
# 历史记录: P7 (2026-04-28) 删除了 EastMoneyHoldingsItem /
# EastMoneyHoldingsResponse — 它们只为 RPT_F10_EH_FREEHOLDERS 字段服务,
# 整套 miaoxiang holders 通道已退役, schema 不再有调用方.
# ==========================================


# ==========================================
# AKShare records schemas (row-level validations)
# ==========================================

class KLineDailyRow(BaseModel):
    """Daily K-Line structure from various sources"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    amount: Optional[float] = None


# SW industry parsing classes removed (Phase η++ 2026-05-12): 申万已在 Phase 2/3 退役,
# 行业现切东财 dim_stock_dc_industry (2026-06-23). 这两个 SW 解析类无活引用, 已删除.
