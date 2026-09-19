#!/usr/bin/env python3
"""stock_st 契约 v2 升版后的库内重打戳 (一次性迁移工具, dry-run 默认)。

为什么需要它, 与 daily 那次 (2026-09-13, ``restamp_nominal_ohlcv_contract.py``)
同一个理由: ``security_day_reader.load_accepted_security_day_partition`` 对指针戳
做严格相等校验, 契约指纹一变, 既有 accepted 分区 (1,128+) 当场全部读不出来 ——
指针与 canonical 必须跟着重打。``ingest_batch`` 相反, 永不重打 (落地证据封印)。

本文件是薄 CLI: 构造 stock_st 自己的 ``RestampTarget`` 并把
``services/data_sources/security_day_restamp.py`` 的核心函数原名重新导出。完整
设计取舍 (为什么不硬编码列名/哈希, 为什么不记账, 为什么 SET NOT NULL 要独立
事务, 写后自证五条的理由, --to-v1 回退窗口的语义) 见该模块 docstring, 不在这里
复述第二份。

回填映射的有效期声明 (见 ``stock_st_acquire.yaml`` 与 spec §4.6): ``stock_st_derive
-> derived_name_prefix`` 只对**重打时刻**已落地的批次成立——今天全部
``stock_st_derive`` 批次都是名称路径; 双水库上线后, 新落地的 ``stock_st_derive``
批次逐行给出真实 ``st_origin`` (可能是 ``provider_baostock_isst``), 不经这份
回填映射 (v2 起 land 阶段就要求逐行给出 ``st_origin``)。表上已有 ``st_origin`` 列
时, ``plan()`` 不会把该列再放进 ``add_columns``, 因此也不会进回填分支
(``is_noop`` 覆盖这一路径, 不会把水库来源的行错标成名称路径)。

用法::

    # 1) 先改 stock_st_schema.py 与 sync_registry.yaml (schema_hash/contract_version)
    # 2) 在生产库副本上验证全流程
    PYTHONPATH=backend python backend/scripts/restamp_stock_st_contract.py \\
        --db-override /tmp/copy.duckdb --execute
    # 3) 确认无误后对生产库执行
    PYTHONPATH=backend python backend/scripts/restamp_stock_st_contract.py --execute

    # 回退到 v1 契约 (仅在"回退窗口"关闭之前安全 —— 第一行 name=NULL 进 canonical
    # 之前; baostock 来源分区一旦落地, --to-v1 会因 null_row_count > 0 拒绝执行,
    # 必须先删那些分区)。
    PYTHONPATH=backend python backend/scripts/restamp_stock_st_contract.py \\
        --to-v1 --db-override /tmp/copy.duckdb --execute

退出码: 0 = 成功 (dry-run 或 execute); 非 0 = 计划不可执行 / 断言失败 (已 ROLLBACK)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_sources.security_day_restamp import (  # noqa: E402
    RestampMismatchError,
    RestampPlan,
    RestampTarget,
    RollbackPlan,
)
from services.data_sources.security_day_restamp import execute as _core_execute  # noqa: E402
from services.data_sources.security_day_restamp import (  # noqa: E402
    execute_to_v1 as _core_execute_to_v1,
)
from services.data_sources.security_day_restamp import format_plan as _core_format_plan  # noqa: E402
from services.data_sources.security_day_restamp import (  # noqa: E402
    format_plan_to_v1 as _core_format_plan_to_v1,
)
from services.data_sources.security_day_restamp import plan as _core_plan  # noqa: E402
from services.data_sources.security_day_restamp import plan_to_v1 as _core_plan_to_v1  # noqa: E402
from services.data_sources.stock_st_acquire_rules import (  # noqa: E402
    load_stock_st_acquire_rules,
)
from services.data_sources.stock_st_contract import load_stock_st_contract  # noqa: E402
from services.data_sources.stock_st_schema import (  # noqa: E402
    CANONICAL_TABLE,
    DATASET_ID,
    DOMAIN,
    ENRICHMENT_FIELDS,
)
from services.writer_lock import writer_lock  # noqa: E402

_ROLLBACK_TARGETS_PATH = ROOT / "backend" / "config" / "stock_st_contract_versions.yaml"

assert ENRICHMENT_FIELDS == ("st_origin",)  # 见 RestampTarget 构造的假设前提


def _target() -> RestampTarget:
    return RestampTarget(
        dataset_id=DATASET_ID,
        canonical_table=CANONICAL_TABLE,
        schema_fields=tuple(DOMAIN.schema_payload["fields"]),
        load_contract=load_stock_st_contract,
        enrichment_backfill={
            "st_origin": dict(load_stock_st_acquire_rules().backfill_origin_by_source)
        },
        rollback_targets_path=_ROLLBACK_TARGETS_PATH,
        rollback_version="1",
    )


def plan(con) -> RestampPlan:
    return _core_plan(con, _target())


def format_plan(p: RestampPlan) -> str:
    return _core_format_plan(p)


def execute(con, plan_obj: RestampPlan) -> RestampPlan:
    return _core_execute(con, plan_obj)


def plan_to_v1(con) -> RollbackPlan:
    return _core_plan_to_v1(con, _target())


def format_plan_to_v1(p: RollbackPlan) -> str:
    return _core_format_plan_to_v1(p)


def execute_to_v1(con, p: RollbackPlan) -> RollbackPlan:
    return _core_execute_to_v1(con, p)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--execute", action="store_true", help="实际写库 (默认 dry-run)")
    parser.add_argument(
        "--db-override", default=None,
        help="改写目标库路径 (先在生产库副本上验证全流程, 再对生产库跑)",
    )
    parser.add_argument(
        "--to-v1", action="store_true",
        help="回退到 v1 契约 (目标戳来自 stock_st_contract_versions.yaml, 不现算; "
             "回退窗口: 第一行 name=NULL 进 canonical 之前)",
    )
    args = parser.parse_args(argv)

    from services.data_access.resolver import db_path  # noqa: PLC0415
    from services.duck_adapter import connect  # noqa: PLC0415

    target = args.db_override or str(db_path("tushare_raw"))

    if args.to_v1:
        if not args.execute:
            con = connect(target, read_only=True)
            try:
                rp = plan_to_v1(con)
            finally:
                con.close()
            print(format_plan_to_v1(rp))
            print("\n(dry-run; 加 --execute 才写库)")
            return 0 if rp.executable else 2

        with writer_lock("restamp_stock_st_contract"):
            con = connect(target, read_only=False)
            try:
                rp = plan_to_v1(con)
                print(format_plan_to_v1(rp))
                if not rp.executable:
                    return 2
                execute_to_v1(con, rp)
            finally:
                con.close()
        print(
            f"\nexecuted: 已回退到 contract_version={rp.target_contract_version} "
            f"({rp.derived_from}); 删列={list(rp.drop_columns) or '无'} "
            f"恢复NOT NULL={list(rp.restore_not_null) or '无'}; 写后自证通过"
        )
        return 0

    if not args.execute:
        con = connect(target, read_only=True)
        try:
            p = plan(con)
        finally:
            con.close()
        print(format_plan(p))
        print("\n(dry-run; 加 --execute 才写库)")
        return 0 if p.executable else 2

    with writer_lock("restamp_stock_st_contract"):
        con = connect(target, read_only=False)
        try:
            p = plan(con)
            print(format_plan(p))
            if not p.executable:
                return 2
            execute(con, p)
        finally:
            con.close()
    if p.is_noop:
        print("\nexecuted: 无事可做 (戳已等于现算契约, 表形状已一致); 写后自证五条全过")
    else:
        print(
            f"\nexecuted: {p.pointer_stale} 指针行 + {p.canonical_stale:,} canonical 行已重打 "
            f"(报的是**实际改了多少**, 不是表里有多少行); "
            f"加列={[n for n, _ in p.add_columns] or '无'} "
            f"解NOT NULL={list(p.drop_not_null) or '无'} "
            f"加NOT NULL={list(p.set_not_null) or '无'}; "
            "ingest_batch 未动; 写后自证五条全过"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
