"""Phase η++++ — survey 因子构建 orchestrator (无 I/O).

输入: raw events dict (stock_code → [(date, inst_count), ...])
输出: 完整 mart 行 list (stock_code, as_of_date, count_30d/60d, inst_30d/60d, survey_bin)

ETL 脚本只负责 I/O (DB 读/写), 计算逻辑全在这里 (便于单测).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from services.sentiment.bin_assigner import assign_survey_bin
from services.sentiment.configs import SURVEY_BIN, WINDOWS, SurveyBinThresholds, WindowConfig
from services.sentiment.validators import (
    SentimentValidationError, validate_bin_distribution, validate_survey_window,
)
from services.sentiment.window_calculator import SurveyWindowResult, daily_grid_from_events


@dataclass(frozen=True)
class SurveyFeatureRow:
    """完整的 mart row (含 bin)."""
    stock_code: str
    as_of_date: str
    survey_count_30d: int
    survey_count_60d: int
    survey_inst_30d: int
    survey_inst_60d: int
    survey_bin: str


def build_survey_features(
    events_by_stock: dict[str, list[tuple[str, int]]],
    grid_start: str,
    grid_end: str,
    trading_dates: list[str] | None = None,
    bin_cfg: SurveyBinThresholds = SURVEY_BIN,
    win_cfg: WindowConfig = WINDOWS,
    validate: bool = True,
) -> list[SurveyFeatureRow]:
    """主入口: events → feature rows.

    Args:
        events_by_stock: {stock_code: [(survey_date, inst_count), ...] (升序)}
        grid_start / grid_end: 输出日期范围 (YYYY-MM-DD)
        trading_dates: 仅在交易日输出 (若提供)
        bin_cfg: 桶阈值 (默认全局)
        win_cfg: 窗口长度 (默认全局)
        validate: 是否每行校验 (默认 True, 测试可关)

    Returns:
        全部行 (按 (stock_code, as_of_date) 升序)

    Raises:
        SentimentValidationError: validate=True 且数据不合规
    """
    rows: list[SurveyFeatureRow] = []
    for code in sorted(events_by_stock.keys()):
        evts = events_by_stock[code]
        if not evts:
            continue
        # 1) 窗口聚合
        windows = daily_grid_from_events(
            stock_code=code,
            events=evts,
            grid_start=grid_start,
            grid_end=grid_end,
            short_days=win_cfg.survey_short_days,
            long_days=win_cfg.survey_long_days,
            trading_dates=trading_dates,
        )
        # 2) 单行校验
        if validate:
            for w in windows:
                validate_survey_window(w)
        # 3) 桶分配
        for w in windows:
            rows.append(SurveyFeatureRow(
                stock_code=w.stock_code,
                as_of_date=w.as_of_date,
                survey_count_30d=w.count_30d,
                survey_count_60d=w.count_60d,
                survey_inst_30d=w.inst_30d,
                survey_inst_60d=w.inst_60d,
                survey_bin=assign_survey_bin(w.count_60d, bin_cfg),
            ))
    # 4) 桶分布校验 (整批)
    if validate and rows:
        counter = Counter(r.survey_bin for r in rows)
        try:
            validate_bin_distribution(dict(counter))
        except SentimentValidationError:
            # 桶分布问题不致命, 但要打 WARN (留给 entry script 处理)
            raise
    return rows


def bin_distribution(rows: list[SurveyFeatureRow]) -> dict[str, int]:
    """统计 bin 分布."""
    return dict(Counter(r.survey_bin for r in rows))
