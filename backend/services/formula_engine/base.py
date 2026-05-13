"""FormulaBase 抽象 + 数据契约。

所有选股公式必须实现 FormulaBase 协议:
  - metadata: 公式元信息
  - compute_signals(kline_df, code): 输入单股 K 线 numpy 结构,返回信号列表

历史回测调度由 backend/scripts/build_formula_horizon_evidence.py 统一负责。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator, Protocol, runtime_checkable

import numpy as np


# ============================================================
# 数据契约
# ============================================================


@dataclass(frozen=True)
class FormulaMetadata:
    """公式元数据,展示用 + 注册表唯一标识。

    formula_id: 全局唯一标识,与 fact_technical_trigger.formula_id 对齐
    tag:        UI 简写 (2 字符,如 'MA' 'TT' 'BO')
    default_horizon_days: 默认建议持仓周期 (来自历史回测最优,初始猜测)
    """

    formula_id: str
    name: str
    tag: str
    description: str
    default_horizon_days: int
    has_variant: bool = False  # 海龟 20/55 这种双 variant
    has_state: bool = False    # MACD 5 态


@dataclass(frozen=True)
class FormulaSignal:
    """单条触发信号,直接对应 fact_technical_trigger 一行。"""

    stock_code: str
    date: str               # 信号日 T (YYYY-MM-DD)
    formula_id: str
    formula_variant: str    # 默认 = formula_id;海龟会用 'turtle_20'/'turtle_55'
    strength: float         # 0-1,公式自定义计算
    state: str | None       # macd 五态: just_crossed/holding/imminent/just_dead/waiting; 其他 None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def to_db_row(self) -> dict:
        """转换为 DB 行,reason_codes 序列化为 JSON。"""
        return {
            "stock_code": self.stock_code,
            "date": self.date,
            "formula_id": self.formula_id,
            "formula_variant": self.formula_variant,
            "strength": float(self.strength),
            "state": self.state,
            "reason_codes_json": json.dumps(list(self.reason_codes), ensure_ascii=False),
        }


# ============================================================
# Protocol
# ============================================================


@runtime_checkable
class FormulaBase(Protocol):
    """所有公式实现的统一接口。"""

    metadata: FormulaMetadata

    def compute_signals(
        self,
        code: str,
        dates: np.ndarray,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        amounts: np.ndarray,
    ) -> list[FormulaSignal]:
        """对单只股票一段 K 线计算所有触发信号。

        输入:
          code:    股票代码
          dates:   字符串日期 array (YYYY-MM-DD),按时间升序
          opens/highs/lows/closes:  qfq 价格 (numpy float array)
          volumes: 成交量 (numpy float array,单位:股)
          amounts: 成交额 (numpy float array,单位:元)

        输出:
          所有触发日的 FormulaSignal 列表 (按 date 升序)
        """
        ...


# ============================================================
# 注册表 (全局)
# ============================================================


REGISTRY: dict[str, FormulaBase] = {}


def register_formula(formula: FormulaBase) -> None:
    """注册公式到全局 REGISTRY,formula_id 重复会抛错。"""
    fid = formula.metadata.formula_id
    if fid in REGISTRY:
        raise ValueError(f"formula_id {fid} already registered")
    REGISTRY[fid] = formula


# ============================================================
# 辅助计算工具 (公式实现可共用)
# ============================================================


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均 (与通达信 EMA 一致: alpha = 2/(N+1))。"""
    n = len(values)
    if n == 0:
        return np.empty(0)
    alpha = 2.0 / (period + 1)
    out = np.empty(n)
    out[0] = values[0]
    for i in range(1, n):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def sma(values: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均,前 period-1 行为 nan。"""
    n = len(values)
    if n < period:
        return np.full(n, np.nan)
    out = np.full(n, np.nan)
    cumsum = np.cumsum(values)
    out[period - 1] = cumsum[period - 1] / period
    out[period:] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def cross_up(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """检测 a 上穿 b: a[t-1] < b[t-1] 且 a[t] > b[t]。返回 bool array。"""
    if len(a) < 2:
        return np.zeros(len(a), dtype=bool)
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[:-1] < b[:-1]) & (a[1:] > b[1:])
    return out


def cross_down(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """检测 a 下穿 b: a[t-1] > b[t-1] 且 a[t] < b[t]。"""
    if len(a) < 2:
        return np.zeros(len(a), dtype=bool)
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[:-1] > b[:-1]) & (a[1:] < b[1:])
    return out
