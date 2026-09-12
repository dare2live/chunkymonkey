#!/usr/bin/env python3
"""K 线真相表重复实体史修正 (asof_identity_r1.md §5 / §9 S5)。

背景 (业主 2026-09-12 已裁定「删, 走记账修正」, 详见 scratchpad/asof_identity_r1.md §5/§8
决策 2): ``tushare_raw.canonical_nominal_ohlcv_daily`` (名义 K 线, 项目身份真相源) 里,
``backend/config/security_code_changes.yaml`` 登记的每条换码事件, 其 ``new_code`` 在
``effective_date`` 之前都带着一段本不该存在的历史 (2026-06-11 曾按码全史回写造成, 见
asof_identity_r1.md I2)。同一段日期区间, ``old_code`` 也有完整的行 —— 两份历史逐日并存,
删掉 ``new_code`` 那份不会丢任何一个交易日的数据 (I7: 后继价格核对精确配对, 清理前无
更短的巧合对), 这是本脚本删除操作的依据。

不删会怎样: 2019-2025 的每日股票池因此多出一只当时并不存在的证券 (302132.SZ/001914.SZ
在换码生效日之前就"活着"), 而股票池是项目全部 PIT 判断的地基 (universe_rules.yaml
"t 日有名义 K 线 = 在交易" 规则), 市场宽度/分层/qfq 等全部按日横截面的表都会被污染。

判据来源 (红线 5: 推导物锁规则有效期, 过期 fail 不 warn): 删除对象**只从
``security_code_changes.yaml`` 登记的事件读出**——不 hardcode 代码或日期。对每条事件,
删除 ``ts_code == new_code AND trade_date < effective_date`` 的行。

不能裸 DELETE (asof_identity_r1.md I8): ``accepted_partition`` 按
(dataset_id, partition_value=日) 存 ``row_count``/``content_hash``, 读侧
(``security_day_reader.load_accepted_security_day_partition``) 断言
``len(ts_codes) == row_count`` 且重算 ``content_hash`` 必须相等。所以每个受影响日必须在
同一事务内: 删 canonical 行 → 用现成的 ``security_day_partition.canonical_content_hash``
对该日剩余行重算 content_hash → 更新 accepted_partition 指针 (row_count-1, 新 hash) →
用现成的 ``services.data_deletion.record_data_deletion`` 记账 (不手写第二套记账表)。

单写者纪律 (CLAUDE.md 红线 6): 全程一个连接、一个事务; 不在本脚本内并发开库。

默认 dry-run; 只有显式 ``--execute`` 才写库, 写库前取 ``services.writer_lock.writer_lock``。

用法:
  python backend/scripts/amend_kline_entity_duplicates.py
  python backend/scripts/amend_kline_entity_duplicates.py --execute --run-id r1

本文件只提供脚本本身; ``plan``/``execute`` 都接受注入的 ``conn`` (测试用内存 DuckDB /
tmp_path, 不连生产库 —— 本仓硬规矩: 不打开任何 ``data/*.duckdb``), CLI 层的 ``main`` 才
落到生产路径。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_deletion import record_data_deletion  # noqa: E402
from services.data_sources.nominal_ohlcv_schema import (  # noqa: E402
    CANONICAL_TABLE,
    DATASET_ID,
    PROVIDER_FIELDS,
)
from services.data_sources.security_day_partition import canonical_content_hash  # noqa: E402
from services.security_identity import (  # noqa: E402
    CodeChangeSet,
    load_security_code_changes,
)
from services.writer_lock import writer_lock  # noqa: E402


class AmendMismatchError(RuntimeError):
    """写库前/后的一致性断言失败 (§5): 触发时调用方所在事务必须 ROLLBACK。

    不是 ``ValueError``: 这是运行时数据状态与 dry-run 计划不一致 (或写后自证失败),
    不是入参形状错误, 与 :mod:`services.security_identity` 里 ``IdentityError`` 不是
    ``ValueError`` 子类同一个理由 —— 不同失败家族, 调用方可能想分开捕获。
    """


def _to_date(yyyymmdd: str) -> date:
    return datetime.strptime(yyyymmdd, "%Y%m%d").date()


@dataclass(frozen=True)
class AmendEventPlan:
    """一条换码事件的修正计划 (§9 S5: "每事件: new_code, dates<effective 的日清单,
    各日 row_count 前后")。"""

    old_code: str
    new_code: str
    effective_date: str
    source_ref: str
    # new_code 在 effective_date 之前、按升序排列的交易日 (YYYYMMDD)。
    dates: tuple[str, ...]
    # dates 的子集: old_code 在同一天也有行 (两份历史并存的证据, I7)。
    old_code_shared_dates: tuple[str, ...]

    @property
    def row_count(self) -> int:
        return len(self.dates)


@dataclass(frozen=True)
class AmendPlan:
    events: tuple[AmendEventPlan, ...]

    @property
    def total_rows(self) -> int:
        return sum(event.row_count for event in self.events)


def plan(
    con: Any,
    ccs: CodeChangeSet,
    *,
    canonical_table: str = CANONICAL_TABLE,
) -> AmendPlan:
    """§5/§9 S5: 对 ``ccs`` 里的每条事件, 现查 ``new_code`` 在 ``effective_date`` 之前的行,
    以及同期 ``old_code`` 是否也有行 (两份历史并存的证据)。纯读, 不开事务、不写。

    事件表为空 (``ccs.events`` 为空 tuple) 时返回一个空 ``AmendPlan`` (no-op, 不报错)。
    """

    event_plans: list[AmendEventPlan] = []
    for event in ccs.events:
        effective = _to_date(event.effective_date)
        new_rows = con.execute(
            f"""
            SELECT strftime(trade_date, '%Y%m%d')
              FROM {canonical_table}
             WHERE ts_code = ? AND trade_date < ?
             ORDER BY trade_date
            """,
            [event.new_code, effective],
        ).fetchall()
        dates = tuple(str(row[0]) for row in new_rows)
        shared: tuple[str, ...] = ()
        if dates:
            old_rows = con.execute(
                f"""
                SELECT strftime(trade_date, '%Y%m%d')
                  FROM {canonical_table}
                 WHERE ts_code = ? AND trade_date < ?
                """,
                [event.old_code, effective],
            ).fetchall()
            old_dates = frozenset(str(row[0]) for row in old_rows)
            shared = tuple(d for d in dates if d in old_dates)
        event_plans.append(
            AmendEventPlan(
                old_code=event.old_code,
                new_code=event.new_code,
                effective_date=event.effective_date,
                source_ref=event.source_ref,
                dates=dates,
                old_code_shared_dates=shared,
            )
        )
    return AmendPlan(events=tuple(event_plans))


def format_plan(p: AmendPlan) -> str:
    """dry-run 报告 (§5/交付要求): 每个 new_code 的行数/日期范围/同期旧码是否也有行/合计。"""

    lines: list[str] = []
    if not p.events:
        return "no registered code-change events; nothing to do"
    for ep in p.events:
        if not ep.dates:
            lines.append(
                f"{ep.new_code} (换自 {ep.old_code}, 生效日 {ep.effective_date}): "
                "0 行, 已无需修正"
            )
            continue
        lines.append(
            f"{ep.new_code} (换自 {ep.old_code}, 生效日 {ep.effective_date}): "
            f"{ep.row_count} 行, 日期范围 [{ep.dates[0]}..{ep.dates[-1]}], "
            f"同期旧码 {ep.old_code} 有行: {len(ep.old_code_shared_dates)}/{ep.row_count}"
            + (" (两份历史并存)" if ep.old_code_shared_dates else "")
        )
    lines.append(f"合计: {p.total_rows} 行")
    return "\n".join(lines)


def execute(
    con: Any,
    plan_obj: AmendPlan,
    *,
    run_id: str,
    dataset_id: str = DATASET_ID,
    canonical_table: str = CANONICAL_TABLE,
    provider_fields: Sequence[str] = PROVIDER_FIELDS,
) -> AmendPlan:
    """§5: 按 ``plan_obj`` 逐事件、逐日, 在**同一事务**内 (CLAUDE.md 红线 6: 一个连接、
    一个事务) 完成 删除 → 重算 content_hash → 更新 accepted_partition 指针 → 记账。

    ``plan_obj`` 是调用方 (通常先调 :func:`plan`) 算好的计划, 不在这里重新查——这样
    "写库前" 的行数断言才能真正对照"计划时刻"与"写库时刻"两个独立的读数, 而不是
    读同一次查询骗过自己。

    一致性断言 (§5 交付要求, 任一失败都 ROLLBACK 并抛 :class:`AmendMismatchError`):
      - 写库前: 该 new_code 实际的 pre-effective 行数必须等于 ``plan_obj`` 里的行数;
      - 每日: accepted_partition 旧 row_count - 1 必须等于删除后 canonical 该日剩余
        行数 (防止分区指针与 canonical 实际内容脱节);
      - 写库后: 该 new_code 在 effective_date 之前必须 0 行。
    事件没有任何待删日期 (``ep.dates`` 为空, 例如已清理过) 时该事件整体 no-op, 不
    调用 :func:`record_data_deletion`。
    """

    con.execute("BEGIN TRANSACTION")
    try:
        for ep in plan_obj.events:
            if not ep.dates:
                continue
            effective = _to_date(ep.effective_date)
            actual_before = int(
                con.execute(
                    f"SELECT COUNT(*) FROM {canonical_table} WHERE ts_code = ? AND trade_date < ?",
                    [ep.new_code, effective],
                ).fetchone()[0]
            )
            if actual_before != ep.row_count:
                raise AmendMismatchError(
                    f"{ep.new_code}: dry-run 预告 {ep.row_count} 行, 写库时实测 "
                    f"{actual_before} 行, 不一致 (数据在计划与执行之间发生了变化)"
                )

            per_date_before: dict[str, int] = {}
            per_date_after: dict[str, int] = {}
            for d in ep.dates:
                dd = _to_date(d)
                con.execute(
                    f"DELETE FROM {canonical_table} WHERE ts_code = ? AND trade_date = ?",
                    [ep.new_code, dd],
                )
                field_list = ", ".join(provider_fields)
                remaining = con.execute(
                    f"SELECT {field_list} FROM {canonical_table} WHERE trade_date = ?",
                    [dd],
                ).fetchall()
                remaining_rows = [dict(zip(provider_fields, row)) for row in remaining]
                new_row_count = len(remaining_rows)
                new_content_hash = canonical_content_hash(remaining_rows, provider_fields)

                pointer = con.execute(
                    "SELECT row_count FROM accepted_partition "
                    "WHERE dataset_id = ? AND partition_value = ?",
                    [dataset_id, d],
                ).fetchone()
                if pointer is None:
                    raise AmendMismatchError(
                        f"no accepted_partition pointer for dataset_id={dataset_id} "
                        f"partition_value={d}"
                    )
                old_row_count = int(pointer[0])
                if old_row_count - 1 != new_row_count:
                    raise AmendMismatchError(
                        f"{d}: accepted_partition.row_count={old_row_count} - 1 != "
                        f"canonical 剩余行数={new_row_count} (指针与内容脱节)"
                    )
                con.execute(
                    "UPDATE accepted_partition SET row_count = ?, content_hash = ? "
                    "WHERE dataset_id = ? AND partition_value = ?",
                    [new_row_count, new_content_hash, dataset_id, d],
                )
                per_date_before[d] = old_row_count
                per_date_after[d] = new_row_count

            actual_after = int(
                con.execute(
                    f"SELECT COUNT(*) FROM {canonical_table} WHERE ts_code = ? AND trade_date < ?",
                    [ep.new_code, effective],
                ).fetchone()[0]
            )
            if actual_after != 0:
                raise AmendMismatchError(
                    f"{ep.new_code}: 删除后仍有 {actual_after} 行早于 {ep.effective_date}"
                )

            record_data_deletion(
                con,
                deletion_run_id=run_id,
                table_name=canonical_table,
                delete_scope="rows_removed_future_code_backfill",
                key_column="ts_code",
                key_value=ep.new_code,
                deleted_rows=ep.row_count,
                reason=(
                    f"K 线真相表实体重复史修正: {ep.old_code} -> {ep.new_code} 生效日 "
                    f"{ep.effective_date} 之前的 {ep.row_count} 行是按码全史回写造成的"
                    "未来代码历史 (asof_identity_r1.md I2/I7); 同期旧码历史逐日并存, "
                    "删除不丢任何交易日数据"
                ),
                verification={
                    "old_code": ep.old_code,
                    "new_code": ep.new_code,
                    "effective_date": ep.effective_date,
                    "dates": list(ep.dates),
                    "old_code_shared_dates": len(ep.old_code_shared_dates),
                    "source_ref": ep.source_ref,
                    "per_date_row_count_before": per_date_before,
                    "per_date_row_count_after": per_date_after,
                },
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return plan_obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="实际写库 (默认 dry-run)")
    parser.add_argument("--run-id", default=None, help="record_data_deletion 的 deletion_run_id")
    parser.add_argument(
        "--code-changes", type=Path, default=None,
        help="security_code_changes.yaml 路径 (默认 backend/config/security_code_changes.yaml)",
    )
    args = parser.parse_args(argv)

    from services.data_access.resolver import db_path  # noqa: E402  (延迟: 避免测试导入即触发)
    from services.duck_adapter import connect  # noqa: E402

    ccs = load_security_code_changes(args.code_changes)

    if not args.execute:
        con = connect(str(db_path("tushare_raw")), read_only=True)
        try:
            p = plan(con, ccs)
        finally:
            con.close()
        print(format_plan(p))
        return 0

    run_id = args.run_id or (
        f"amend_kline_entity_duplicates_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    )
    with writer_lock("amend_kline_entity_duplicates"):
        con = connect(str(db_path("tushare_raw")), read_only=False)
        try:
            p = plan(con, ccs)
            print(format_plan(p))
            result = execute(con, p, run_id=run_id)
        finally:
            con.close()
    print(f"executed: {result.total_rows} 行已删除, 涉及 {len(result.events)} 条事件, run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
