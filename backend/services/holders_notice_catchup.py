"""Holders 到期分区规划 + 循环体 (刀 B1 重写; spec_holders_pagination.md §4.5)。

到期集合驱动改为账本 (``services/holders_notice_ledger.py``): 「日历
[exposure_start .. provider_max] 减去已 settled 的日子」, 取代旧的
「MAX(notice_date) 水位 + 前向 / 同日两分支」。旧的按事实表 catchup 与几个
仅供其调用的辅助函数随本刀整体删除, 不留墓碑 (CLAUDE.md 第 17 条)。

``MAX_DUE_DAYS_PER_RUN`` 是本文件的唯一定义处 (V9: 一个事实一个地方) ——
``services/holders_aif10.py`` 从这里 import, 不再各自重复一份字面量 40。

Evidence: ``git log --grep holders_ann_date_axis`` ·
``git log --grep holders_fact_retire`` ·
``sandbox/p1_specs_20260925/spec_holders_pagination.md`` §4.4/§4.5。
"""
from __future__ import annotations

from typing import Any, Callable

MAX_DUE_DAYS_PER_RUN = 40  # eng_gov ≤40d / max notice-day partitions per run


def plan_due_notice_days(
    conn,
    *,
    provider_max: str,
    settle_days: int,
    floor: str,
    max_days: int = MAX_DUE_DAYS_PER_RUN,
) -> list[str]:
    """薄壳: 到期规划的真正机制在 ``services.holders_notice_ledger``
    (账本 settled / floor 守卫, §4.4); 本函数只是日更/回补两条路径共用的入口。
    """
    from services.holders_notice_ledger import plan_due_notice_days as _plan_from_ledger

    return _plan_from_ledger(
        conn,
        provider_max=provider_max,
        settle_days=settle_days,
        floor=floor,
        max_days=max_days,
    )


def run_due_notice_days(
    conn,
    due: list[str],
    *,
    client: Any,
    run_kind: str,
    now_fn: Callable[[], Any],
) -> dict:
    """到期日期逐日执行的循环体 (§4.2 日更第 4 步的循环体; 供日更与 B2
    ``by_day`` 共用)。

    ``AIF10BlockedError`` 直接冒出并停止 (B6: 被封当次不再继续后面的日期,
    已完成的日期不撤销); 其它任何异常都由 ``recheck_notice_day`` 自己捕获、
    记账本 ``failed``、以 ``{"error": ...}`` 形式返回, 这里只是把它收进
    ``errors`` 继续下一天 (重试由到期集合自身保证, R3)。
    """
    from services.holders_aif10 import recheck_notice_day

    landed_partitions: list[str] = []
    empty_partitions: list[str] = []
    failed_partitions: list[str] = []
    errors: list[str] = []
    rows_inserted = 0
    rows_revised_recorded = 0

    for notice_date in due:
        outcome = recheck_notice_day(
            conn, notice_date, client=client, run_kind=run_kind, write=True, now_fn=now_fn
        )
        if "error" in outcome:
            failed_partitions.append(notice_date)
            errors.append(f"{notice_date}:{outcome['error']}")
            continue
        if outcome.get("outcome") == "empty":
            empty_partitions.append(notice_date)
        else:
            landed_partitions.append(notice_date)
        rows_inserted += int(outcome.get("rows_inserted") or 0)
        rows_revised_recorded += int(outcome.get("revised_rows") or 0)

    return {
        "landed_partitions": landed_partitions,
        "empty_partitions": empty_partitions,
        "failed_partitions": failed_partitions,
        "errors": errors,
        "rows_inserted": rows_inserted,
        "rows_revised_recorded": rows_revised_recorded,
    }


__all__ = [
    "MAX_DUE_DAYS_PER_RUN",
    "plan_due_notice_days",
    "run_due_notice_days",
]
