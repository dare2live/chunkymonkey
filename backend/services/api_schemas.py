from typing import Optional
from pydantic import BaseModel, ConfigDict


# ==========================================
# 历史记录: P7 (2026-04-28) 删除了 EastMoneyHoldingsItem /
# EastMoneyHoldingsResponse — 它们只为 RPT_F10_EH_FREEHOLDERS 字段服务,
# 整套 miaoxiang holders 通道已退役, schema 不再有调用方.
# ==========================================


# ==========================================
# AKShare DataFrame Schemas (Row Level Validations)
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


class SWIndustryRow(BaseModel):
    """AKShare SW Industry tree structure row parsing"""
    股票代码: str

    model_config = ConfigDict(extra="allow")


class SWIndustryTreeRow(BaseModel):
    """AKShare SW Industry Tree category row"""
    类目编码: str

    model_config = ConfigDict(extra="allow")
