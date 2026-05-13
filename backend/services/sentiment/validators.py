"""Phase η++++ — 数据契约 + 范围验证.

raise 不静默. 校验失败立刻挂. 配合 ETL 入库前的最后一道关.

单一职责:
  validate_*  → 检查单一 row / 整批
  collect_errors → 不 raise 而是返回错误列表 (用于 dry-run)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from services.sentiment.configs import SURVEY_BIN
from services.sentiment.window_calculator import SurveyWindowResult


# ─────────────────────────────────────────────────────────────────────
# 错误类型
# ─────────────────────────────────────────────────────────────────────

class SentimentValidationError(ValueError):
    """sentiment 数据校验失败."""


@dataclass(frozen=True)
class ValidationIssue:
    """非致命的校验问题 (供 dry-run 累积)."""
    stock_code: str
    as_of_date: str
    field: str
    issue: str
    value: object


# ─────────────────────────────────────────────────────────────────────
# survey window 验证
# ─────────────────────────────────────────────────────────────────────

def validate_survey_window(r: SurveyWindowResult) -> None:
    """单行严格校验. 失败 raise."""
    if not r.stock_code or not isinstance(r.stock_code, str):
        raise SentimentValidationError(f"stock_code 非法: {r.stock_code!r}")
    if not r.as_of_date or len(r.as_of_date) != 10 or r.as_of_date[4] != '-':
        raise SentimentValidationError(f"as_of_date 非法 (期望 YYYY-MM-DD): {r.as_of_date!r}")
    for fname, v in [
        ("count_30d", r.count_30d), ("count_60d", r.count_60d),
        ("inst_30d", r.inst_30d), ("inst_60d", r.inst_60d),
    ]:
        if not isinstance(v, int):
            raise SentimentValidationError(f"{fname} 必须是 int, 实 {type(v).__name__}: {v}")
        if v < 0:
            raise SentimentValidationError(f"{fname} 不可负: {v}")
    # 单调性: 30d ≤ 60d
    if r.count_30d > r.count_60d:
        raise SentimentValidationError(
            f"count_30d ({r.count_30d}) > count_60d ({r.count_60d}) for {r.stock_code} @ {r.as_of_date}"
        )
    if r.inst_30d > r.inst_60d:
        raise SentimentValidationError(
            f"inst_30d ({r.inst_30d}) > inst_60d ({r.inst_60d}) for {r.stock_code} @ {r.as_of_date}"
        )


def validate_survey_batch(rows: Iterable[SurveyWindowResult]) -> int:
    """整批严格校验. 返回校验通过的行数, 失败 raise."""
    n = 0
    for r in rows:
        validate_survey_window(r)
        n += 1
    return n


def collect_survey_issues(rows: Iterable[SurveyWindowResult]) -> list[ValidationIssue]:
    """不 raise, 返回 issue 列表 (供 dry-run / 报告)."""
    issues: list[ValidationIssue] = []
    for r in rows:
        try:
            validate_survey_window(r)
        except SentimentValidationError as e:
            issues.append(ValidationIssue(
                stock_code=r.stock_code, as_of_date=r.as_of_date,
                field="batch", issue=str(e), value=r,
            ))
    return issues


# ─────────────────────────────────────────────────────────────────────
# 桶分布校验
# ─────────────────────────────────────────────────────────────────────

def validate_bin_distribution(bin_counter: dict[str, int]) -> None:
    """检查桶分布合理性 (狂 < 总 50%, 且至少 2 个桶有数据)."""
    total = sum(bin_counter.values())
    if total == 0:
        raise SentimentValidationError("空桶分布")
    populated = sum(1 for n in bin_counter.values() if n > 0)
    if populated < 2:
        raise SentimentValidationError(
            f"桶分布过窄, 仅 {populated}/{len(bin_counter)} 个桶有数据: {bin_counter}"
        )
    extreme_label = SURVEY_BIN.LABELS[-1]  # 狂
    extreme_pct = bin_counter.get(extreme_label, 0) / total
    if extreme_pct > 0.5:
        raise SentimentValidationError(
            f"{extreme_label}桶占比异常 ({extreme_pct:.1%}), 阈值需调整"
        )
