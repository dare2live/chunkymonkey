#!/usr/bin/env python3
"""nominal_ohlcv 契约升版后的库内重打戳 (一次性迁移工具, dry-run 默认)。

为什么需要它 (2026-09-13):
  ``security_day_reader.load_accepted_security_day_partition`` 对指针戳做**严格相等**
  校验 (``accepted_partition_contract_drift``), 而 ``nominal_ohlcv_reader`` 每次读都
  现算契约。所以契约指纹一变, 1,859 个 accepted 分区当场全部读不出来 —— 指针与
  canonical 必须跟着重打。``ingest_batch`` 相反: 它是落地那一刻的证据封印
  (payload_hash 从它派生), **永不重打** (git log --grep landing_seal_vs_contract_restamp)。

  2026-09-02 的 ``2c4af4a0`` 那次改契约**没有**这一步, 因为它只把 source/api 移出
  config_payload —— contract_hash 变了而 config_hash 没变, 而 canonical 表只存
  config_hash 不存 contract_hash。本次改的是 schema_hash (它进 config_payload),
  config_hash 必变, 所以这是第一次真正需要库内重打, 没有现成脚本可抄。

设计取舍 (逐条都有实测依据):

1. **不硬编码列名**。要 ADD / SET NOT NULL / DROP NOT NULL 的列, 全部由
   "schema_payload 声明" 与 "表实际约束" 做差集算出。手写清单迟早漏一个, 而差集
   是机器算的且可断言。

2. **不硬编码哈希**。目标戳一律从 ``load_nominal_ohlcv_contract()`` 现算 —— 脚本里
   写死哈希等于再造一份真相源, 而它必然与契约工厂漂移。副作用: 本脚本必须在
   schema 与 sync_registry 都改完之后才能运行 (否则契约工厂自己就抛 drift)。

3. **不记账**。``amend_kline_entity_duplicates`` 必须 ``record_data_deletion`` 是因为
   它**删行** (信息永久消失); 重打只改戳, 旧值原封不动留在 ``ingest_batch`` 里可查
   (实测: daily 的 ingest_batch 至今并存 7d122f28…×2,020 与 21d86185…×2 两个
   config_hash, 正是 09-02 那次重打留下的证据, 当时也没有任何记账表)。证据天然在
   数据里, 再建一张表是多一个会漂移的副本。

   **2026-09-16 更正 (这里曾经写过一句错话)**: 早先的表述隐含"回退不用单独处理,
   因为 ingest_batch 保留着旧戳, 从那里读回去就行"—— 这个假设是错的, 差点造成灾难。
   实测 1,858/1,859 个分区的 ingest_batch 戳根本不是 v1 的原始值, 而是**更早一代**
   的 ``contract_hash=5ec8471c…`` / ``config_hash=7d122f28…`` (来自比 v1 还早的一次
   重打, 同一张表上摞了两代历史证据)。若真按"读 ingest_batch 的旧戳往回写"去做回退,
   会把全库打成这个两代之前、早已作废的戳组合 —— 而 v1 契约工厂现算出来的真实值是
   ``contract_hash=a25c126e…`` / ``config_hash=21d86185…`` / ``schema_hash=fd84a583…``
   (见 ``backend/config/nominal_ohlcv_contract_versions.yaml``, 该文件的值是对
   ``git 44b772c2^`` 那棵树现跑一次 v1 版契约工厂得到的, 不是猜的也不是从
   ingest_batch 反推的)。两者不等, ``security_day_reader`` 的严格 hash 相等校验会
   拒绝读回**每一个**分区。正确回退法: 目标戳只能来自一次独立算出的 v1 契约, 绝不能
   从 ingest_batch 的历史记录反推 —— ingest_batch 是走过的路留下的脚印, 不是"上一站"
   的地图。``--to-v1`` (见下方用法) 就是这条正确路径的实现: 目标戳从
   ``nominal_ohlcv_contract_versions.yaml`` 读, 从不读 ingest_batch。

   **回退窗口**: 只在**第一行 NULL 进 canonical 之前**安全。v1 要求
   ``pre_close``/``change``/``pct_chg`` 三列 ``NOT NULL``, ``ALTER COLUMN … SET NOT
   NULL`` 遇到哪怕一行 NULL 就直接失败 (不会跳过、不会部分生效)。一旦已经落了 NULL
   行, ``--to-v1`` 会拒绝执行并报"必须先删除含 NULL 的分区"——删分区是不可逆动作
   (信息永久消失), 所以刀3 (补 09-01 起的缺口, 其中北交所行按设计就是 NULL) 一开跑,
   即视为放弃"纯改戳"回退到 v1 契约这条路。

4. **一个连接、一个事务** (CLAUDE.md 红线 6)。实测 DuckDB 1.5.2 的 DDL 是事务性的:
   事务内 ADD COLUMN / DROP NOT NULL 后 ROLLBACK 能完全还原, 中途 ConstraintException
   之后 ROLLBACK 同样还原且数据完好 —— 所以 DDL 与 UPDATE 可以同事务, 失败不留半改态。

5. **plan 与 execute 分离, execute 不重新查**。照 ``amend_kline_entity_duplicates``
   的纪律: 写库前的断言要对照"计划时刻"与"写库时刻"两个独立读数, 而不是读同一次
   查询骗过自己。

6. **写后自证五条** (任一不过即 ROLLBACK):
   - accepted_partition 全部行的戳 == 现算契约
   - canonical 全部行的戳 == 现算契约
   - **content_hash 逐分区一个都没变** (重打不碰内容; 实测 canonical_content_hash 只按
     provider_fields 算, 与增列、与字段顺序都无关)
   - ingest_batch **一行未动** (戳组合分布逐项相等)
   - 表形状与 schema 声明一致 (列集合 + NOT NULL 集合)

用法::

    # 1) 先改 nominal_ohlcv_schema.py 与 sync_registry.yaml (schema_hash/contract_version)
    # 2) 在生产库副本上验证全流程
    PYTHONPATH=backend python backend/scripts/restamp_nominal_ohlcv_contract.py \\
        --db-override /tmp/copy.duckdb --execute
    # 3) 确认无误后对生产库执行
    PYTHONPATH=backend python backend/scripts/restamp_nominal_ohlcv_contract.py --execute

    # 回退到 v1 契约 (仅在"回退窗口"关闭之前安全, 见上文"3. 不记账"的更正)。
    # --to-v1 同 --execute 一样: 省略 --execute 即 dry-run (只读, 打印计划)。
    PYTHONPATH=backend python backend/scripts/restamp_nominal_ohlcv_contract.py \\
        --to-v1 --db-override /tmp/copy.duckdb
    PYTHONPATH=backend python backend/scripts/restamp_nominal_ohlcv_contract.py \\
        --to-v1 --db-override /tmp/copy.duckdb --execute

退出码: 0 = 成功 (dry-run 或 execute); 非 0 = 计划不可执行 / 断言失败 (已 ROLLBACK)。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_sources.accepted_schema import (  # noqa: E402
    ACCEPTED_TABLE,
    INGEST_BATCH_TABLE,
)
from services.data_sources.nominal_ohlcv_acquire_rules import (  # noqa: E402
    load_nominal_ohlcv_acquire_rules,
)
from services.data_sources.nominal_ohlcv_contract import (  # noqa: E402
    load_nominal_ohlcv_contract,
)
from services.data_sources.nominal_ohlcv_contract_versions import (  # noqa: E402
    load_rollback_target,
)
from services.data_sources.nominal_ohlcv_schema import (  # noqa: E402
    CANONICAL_TABLE,
    DATASET_ID,
    DOMAIN,
    ENRICHMENT_FIELDS,
    NON_NULL_NUMERIC_FIELDS,
    NUMERIC_FIELDS,
)
from services.writer_lock import writer_lock  # noqa: E402


class RestampMismatchError(RuntimeError):
    """计划与实测不一致, 或写后自证失败 —— 调用方所在事务必须 ROLLBACK。

    不是 ValueError: 这是运行时数据状态问题, 不是入参形状错误 (同
    ``amend_kline_entity_duplicates.AmendMismatchError`` 的理由)。
    """


# 历史行的 pre_close_origin 回填映射 —— 2026-09-16 起从
# backend/config/nominal_ohlcv_acquire.yaml 的 backfill_origin_by_source 键读取
# (业主 09-16 明令: 会随供货商增减而变的事实是参数, 参数进配置, 脚本里不留字面量)。
# 该 yaml 文件里逐条记着每个映射的实证依据 (tushare/tdxhub 的取证过程), 不在这里
# 重复一份会漂移的注释。


@dataclass(frozen=True)
class RestampPlan:
    """一次重打的全部计划值 —— execute 只照它做, 不再自己查。"""

    target_contract_version: str
    target_contract_hash: str
    target_config_hash: str
    # 总行数: 给"写库前数据没在计划与执行之间变过"这条断言用。
    pointer_rows: int
    canonical_rows: int
    # **戳与目标不符**的行数 —— 这才是"还有多少要改"。
    # 2026-09-13 修: 初版把总行数当待改行数报, 于是空载时也说"1,859 + 859 万行待重打",
    # 既误导人、又让 execute 做一次无谓的全表 UPDATE, 更要命的是「重打做完没有」这个
    # 问题变得不可判 (跑一百次都答"待重打")。判据必须问被判对象本身。
    pointer_stale: int
    canonical_stale: int
    add_columns: tuple[tuple[str, str], ...]
    drop_not_null: tuple[str, ...]
    set_not_null: tuple[str, ...]
    backfill_by_source: Mapping[str, str]
    unmapped_sources: tuple[str, ...]
    content_hash_before: Mapping[str, str]
    ingest_batch_before: tuple[tuple[Any, ...], ...]

    @property
    def executable(self) -> bool:
        return not self.unmapped_sources

    @property
    def is_noop(self) -> bool:
        """已是目标状态 —— 重跑本脚本什么都不会改。

        「重打做完没有」必须可判, 否则脚本永远只会说"待重打"。
        """
        return not (
            self.pointer_stale
            or self.canonical_stale
            or self.add_columns
            or self.drop_not_null
            or self.set_not_null
        )


def _table_shape(con: Any, table: str) -> tuple[dict[str, str], set[str]]:
    cols = {str(r[0]): str(r[1]).upper() for r in con.execute(f"DESCRIBE {table}").fetchall()}
    rows = con.execute(
        "SELECT constraint_type, constraint_column_names FROM duckdb_constraints() "
        "WHERE table_name = ?",
        [table],
    ).fetchall()
    not_null = {
        str(r[1][0])
        for r in rows
        if str(r[0]).upper() == "NOT NULL" and r[1] and len(r[1]) == 1
    }
    return cols, not_null


def plan(con: Any) -> RestampPlan:
    """只读: 算出要改什么, 以及重打后必须保持不变的那些基线读数。"""

    contract = load_nominal_ohlcv_contract()
    backfill_map = load_nominal_ohlcv_acquire_rules().backfill_origin_by_source
    fields = tuple(DOMAIN.schema_payload["fields"])
    declared = {str(f["name"]): f for f in fields}
    cols, not_null = _table_shape(con, CANONICAL_TABLE)

    add_columns = tuple(
        (name, str(f["duckdb_type"]))
        for name, f in declared.items()
        if name not in cols
    )
    # 已存在的列里, 契约与表不一致的两个方向
    drop_not_null = tuple(sorted(
        name for name, f in declared.items()
        if name in cols and bool(f["nullable"]) and name in not_null
    ))
    set_not_null = tuple(sorted(
        name for name, f in declared.items()
        if name in cols and not bool(f["nullable"]) and name not in not_null
    ))
    # 新增列若声明非空, 必须先可空加列 → 回填 → 再 SET NOT NULL (加列时既有行没有值)
    set_not_null += tuple(sorted(
        name for name, _ in add_columns if not bool(declared[name]["nullable"])
    ))

    sources = [
        str(r[0])
        for r in con.execute(
            f"SELECT DISTINCT source_name FROM {INGEST_BATCH_TABLE} WHERE dataset_id = ?",
            [DATASET_ID],
        ).fetchall()
    ]
    unmapped = tuple(sorted(s for s in sources if s not in backfill_map))

    pointer_rows = int(con.execute(
        f"SELECT COUNT(*) FROM {ACCEPTED_TABLE} WHERE dataset_id = ?", [DATASET_ID]
    ).fetchone()[0])
    canonical_rows = int(con.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0])
    pointer_stale = int(con.execute(
        f"""SELECT COUNT(*) FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND (contract_version <> ? OR contract_hash <> ?
                                       OR config_hash <> ?)""",
        [DATASET_ID, str(contract.contract_version), str(contract.contract_hash),
         str(contract.config_hash)],
    ).fetchone()[0])
    canonical_stale = int(con.execute(
        f"""SELECT COUNT(*) FROM {CANONICAL_TABLE}
             WHERE contract_version <> ? OR config_hash <> ?""",
        [str(contract.contract_version), str(contract.config_hash)],
    ).fetchone()[0])
    content_before = {
        str(r[0]): str(r[1])
        for r in con.execute(
            f"SELECT partition_value, content_hash FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
            [DATASET_ID],
        ).fetchall()
    }
    ingest_before = tuple(
        tuple(r)
        for r in con.execute(
            f"""SELECT contract_version, contract_hash, config_hash, source_name,
                       status, COUNT(*)
                  FROM {INGEST_BATCH_TABLE} WHERE dataset_id = ?
                 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5""",
            [DATASET_ID],
        ).fetchall()
    )
    return RestampPlan(
        target_contract_version=str(contract.contract_version),
        target_contract_hash=str(contract.contract_hash),
        target_config_hash=str(contract.config_hash),
        pointer_rows=pointer_rows,
        canonical_rows=canonical_rows,
        pointer_stale=pointer_stale,
        canonical_stale=canonical_stale,
        add_columns=add_columns,
        drop_not_null=drop_not_null,
        set_not_null=set_not_null,
        backfill_by_source={s: backfill_map[s] for s in sources if s in backfill_map},
        unmapped_sources=unmapped,
        content_hash_before=content_before,
        ingest_batch_before=ingest_before,
    )


def format_plan(p: RestampPlan) -> str:
    lines = [
        f"目标契约: contract_version={p.target_contract_version}",
        f"          contract_hash={p.target_contract_hash}",
        f"          config_hash={p.target_config_hash}",
        f"{ACCEPTED_TABLE}: {p.pointer_stale} / {p.pointer_rows} 行戳与目标不符",
        f"{CANONICAL_TABLE}: {p.canonical_stale:,} / {p.canonical_rows:,} 行戳与目标不符",
        f"{INGEST_BATCH_TABLE}: 0 行 —— 落地证据永不重打 (现有 {len(p.ingest_batch_before)} 种戳组合原样保留)",
        f"加列: {[f'{n} {t}' for n, t in p.add_columns] or '无'}",
        f"解除 NOT NULL: {list(p.drop_not_null) or '无'}",
        f"加上 NOT NULL: {list(p.set_not_null) or '无'}",
        f"回填映射: {dict(p.backfill_by_source) or '无'}",
    ]
    if p.is_noop:
        lines.append(
            "==> 无事可做: 戳已等于现算契约, 且表形状与契约一致 (重跑本脚本是 no-op)。"
        )
    if p.unmapped_sources:
        lines.append(
            f"!! 无法执行: ingest_batch 里有未登记的 source_name {list(p.unmapped_sources)} —— "
            "回填映射缺这些源的裁决, 补进 nominal_ohlcv_acquire.yaml 的 "
            "backfill_origin_by_source 并写明实证依据后再跑 "
            "(不猜: 猜错就是给 859 万行里的一部分编造血缘)"
        )
    return "\n".join(lines)


def execute(con: Any, plan_obj: RestampPlan) -> RestampPlan:
    """一个事务内完成全部改动; 任一断言失败即 ROLLBACK 并抛 RestampMismatchError。"""

    if not plan_obj.executable:
        raise RestampMismatchError(
            f"计划不可执行: 未登记的 source_name {list(plan_obj.unmapped_sources)}"
        )

    con.execute("BEGIN TRANSACTION")
    try:
        # 写库前: 计划时刻的读数必须仍然成立 (独立于 plan 的第二次读)
        now_pointer = int(con.execute(
            f"SELECT COUNT(*) FROM {ACCEPTED_TABLE} WHERE dataset_id = ?", [DATASET_ID]
        ).fetchone()[0])
        now_canonical = int(con.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0])
        if (now_pointer, now_canonical) != (plan_obj.pointer_rows, plan_obj.canonical_rows):
            raise RestampMismatchError(
                f"计划时 pointer={plan_obj.pointer_rows} canonical={plan_obj.canonical_rows}, "
                f"写库时实测 pointer={now_pointer} canonical={now_canonical} —— "
                "数据在计划与执行之间发生了变化"
            )

        for name, duck_type in plan_obj.add_columns:
            con.execute(f"ALTER TABLE {CANONICAL_TABLE} ADD COLUMN {name} {duck_type}")

        if plan_obj.add_columns:
            cases = " ".join(
                f"WHEN '{src}' THEN '{label}'"
                for src, label in sorted(plan_obj.backfill_by_source.items())
            )
            for name, _ in plan_obj.add_columns:
                if name != "pre_close_origin":
                    continue
                con.execute(
                    f"""
                    UPDATE {CANONICAL_TABLE}
                       SET pre_close_origin = CASE b.source_name {cases} END
                      FROM {INGEST_BATCH_TABLE} b
                     WHERE b.batch_id = {CANONICAL_TABLE}.ingest_batch_id
                    """
                )
                left = int(con.execute(
                    f"SELECT COUNT(*) FROM {CANONICAL_TABLE} WHERE {name} IS NULL"
                ).fetchone()[0])
                if left:
                    raise RestampMismatchError(
                        f"{name} 回填后仍有 {left} 行为 NULL —— 有 canonical 行 JOIN 不上 "
                        "ingest_batch, 或其 source_name 不在回填映射里"
                    )

        for name in plan_obj.drop_not_null:
            con.execute(f"ALTER TABLE {CANONICAL_TABLE} ALTER COLUMN {name} DROP NOT NULL")
        # SET NOT NULL **不在本段** —— 见下方"段 2"。实测 DuckDB 1.5.2: 它内部要建索引,
        # 与同一事务里尚未提交的 UPDATE 互斥, 直接抛
        # TransactionException: Cannot create index with outstanding updates。
        # 组合矩阵 (穷举实测): 全部同事务 ✗ / 全部无事务 ✓ / UPDATE 已提交后再 SET NOT NULL ✓ /
        # 事务内只含 ADD COLUMN + DROP NOT NULL + UPDATE ✓。

        # 幂等做进 SQL 而不是外面包一层 if: WHERE 只命中戳确实不符的行, 所以重跑第二次
        # 天然是 0 行。把"还要不要改"的判断交给数据本身, 不交给调用方记得先查一次。
        con.execute(
            f"""
            UPDATE {CANONICAL_TABLE} SET contract_version = ?, config_hash = ?
             WHERE contract_version <> ? OR config_hash <> ?
            """,
            [
                plan_obj.target_contract_version,
                plan_obj.target_config_hash,
                plan_obj.target_contract_version,
                plan_obj.target_config_hash,
            ],
        )
        con.execute(
            f"""
            UPDATE {ACCEPTED_TABLE}
               SET contract_version = ?, contract_hash = ?, config_hash = ?
             WHERE dataset_id = ?
               AND (contract_version <> ? OR contract_hash <> ? OR config_hash <> ?)
            """,
            [
                plan_obj.target_contract_version,
                plan_obj.target_contract_hash,
                plan_obj.target_config_hash,
                DATASET_ID,
                plan_obj.target_contract_version,
                plan_obj.target_contract_hash,
                plan_obj.target_config_hash,
            ],
        )

        # 段 1 只自证"数据与戳"; 形状要等段 2 之后才成立。
        _assert_after(con, plan_obj, shape=False)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    # ── 段 2: SET NOT NULL 必须在自己的事务里 (上面注释的实测理由) ──────────────
    #
    # 这是对"全程一个事务"(红线 6 纪律) 的实质松动, 所以中间态必须是安全的, 而它是:
    #   段 1 失败 -> 全回滚, 零改动;
    #   段 1 成功、段 2 失败 -> 数据完整、戳已正确、只差 pre_close_origin 的非空约束。
    #     该中间态不丢数据也不产生错值; 且重跑本脚本时 plan() 只会算出 set_not_null 一项
    #     (其余皆已是目标态), 第二次就把它补上 —— 幂等天然覆盖, 不需要人工修。
    if plan_obj.set_not_null:
        con.execute("BEGIN TRANSACTION")
        try:
            for name in plan_obj.set_not_null:
                con.execute(f"ALTER TABLE {CANONICAL_TABLE} ALTER COLUMN {name} SET NOT NULL")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    # 两段都落定后, 完整自证五条 (含形状)。
    _assert_after(con, plan_obj)
    return plan_obj


def _assert_after(con: Any, p: RestampPlan, *, shape: bool = True) -> None:
    """写后自证五条。任一不过即抛 —— 调用方负责 ROLLBACK。

    ``shape=False``: 只证"数据与戳", 跳过表形状那两条 —— 段 1 结束时
    ``SET NOT NULL`` 还没做 (它必须在段 2 独立事务里), 此刻形状本就不该吻合。
    """

    bad_ptr = con.execute(
        f"""SELECT COUNT(*) FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND (contract_version <> ? OR contract_hash <> ?
                                       OR config_hash <> ?)""",
        [DATASET_ID, p.target_contract_version, p.target_contract_hash, p.target_config_hash],
    ).fetchone()[0]
    if bad_ptr:
        raise RestampMismatchError(f"{ACCEPTED_TABLE}: {bad_ptr} 行重打后戳仍不等于现算契约")

    bad_canon = con.execute(
        f"""SELECT COUNT(*) FROM {CANONICAL_TABLE}
             WHERE contract_version <> ? OR config_hash <> ?""",
        [p.target_contract_version, p.target_config_hash],
    ).fetchone()[0]
    if bad_canon:
        raise RestampMismatchError(f"{CANONICAL_TABLE}: {bad_canon} 行重打后戳仍不等于现算契约")

    after = {
        str(r[0]): str(r[1])
        for r in con.execute(
            f"SELECT partition_value, content_hash FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
            [DATASET_ID],
        ).fetchall()
    }
    if after != dict(p.content_hash_before):
        changed = [k for k, v in after.items() if p.content_hash_before.get(k) != v]
        raise RestampMismatchError(
            f"content_hash 变了 {len(changed)} 个分区 (重打只该改戳不该碰内容): {changed[:5]}"
        )

    ingest_after = tuple(
        tuple(r)
        for r in con.execute(
            f"""SELECT contract_version, contract_hash, config_hash, source_name,
                       status, COUNT(*)
                  FROM {INGEST_BATCH_TABLE} WHERE dataset_id = ?
                 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5""",
            [DATASET_ID],
        ).fetchall()
    )
    if ingest_after != p.ingest_batch_before:
        raise RestampMismatchError(
            "ingest_batch 的戳组合分布变了 —— 落地证据被动过, 那是封印不是指针"
        )

    if not shape:
        return
    cols, not_null = _table_shape(con, CANONICAL_TABLE)
    fields = tuple(DOMAIN.schema_payload["fields"])
    expect_cols = {str(f["name"]) for f in fields}
    expect_nn = {str(f["name"]) for f in fields if not bool(f["nullable"])}
    if set(cols) != expect_cols:
        raise RestampMismatchError(
            f"列集合与契约不符: 缺={sorted(expect_cols - set(cols))} "
            f"多={sorted(set(cols) - expect_cols)}"
        )
    if not_null != expect_nn:
        raise RestampMismatchError(
            f"NOT NULL 集合与契约不符: 表上多={sorted(not_null - expect_nn)} "
            f"表上缺={sorted(expect_nn - not_null)}"
        )


# ── --to-v1: 回退到 v1 契约 ──────────────────────────────────────────────────
#
# 目标戳来自 nominal_ohlcv_contract_versions.yaml (一次性历史事实, 现算自
# git 44b772c2^ 的契约工厂), 不来自 load_nominal_ohlcv_contract() (那是**当前** v2
# 契约工厂, 只会算出 v2 自己的戳, 答不出 v1 的值) 也不来自 ingest_batch (见上方
# "3. 不记账" 的 2026-09-16 更正 —— ingest_batch 里躺着的是更早一代的戳, 不是 v1)。
#
# 要删/要恢复 NOT NULL 的列不硬编码字面量, 照 plan() 的纪律现算:
#   drop_columns    = ENRICHMENT_FIELDS               (v1 没有这一列)
#   restore_not_null = NUMERIC_FIELDS 里被 v2 改成可空的那些
#                     = set(NUMERIC_FIELDS) - set(NON_NULL_NUMERIC_FIELDS)
# 两者都是从当前 v2 schema 声明反推 v1 与 v2 的差集, 不是拍脑袋写的列名清单。


@dataclass(frozen=True)
class RollbackPlan:
    """回退到某个历史 contract_version 的计划。"""

    target_contract_version: str
    target_schema_hash: str
    target_config_hash: str
    target_contract_hash: str
    derived_from: str
    drop_columns: tuple[str, ...]
    restore_not_null: tuple[str, ...]
    # 待恢复 NOT NULL 的列里, 现在已经是 NULL 的行数 —— 这才是"能不能回退"的判据
    # (SET NOT NULL 遇到哪怕一行 NULL 就直接失败, 不会跳过)。
    null_row_count: int

    @property
    def executable(self) -> bool:
        return self.null_row_count == 0


def plan_to_v1(con: Any) -> RollbackPlan:
    """只读: 算出回退到 v1 契约要做什么, 以及是否已经被 NULL 行挡住 (回退窗口已关)。"""

    target = load_rollback_target("1")
    cols, not_null = _table_shape(con, CANONICAL_TABLE)
    drop_columns = tuple(name for name in ENRICHMENT_FIELDS if name in cols)
    candidate_not_null = tuple(sorted(set(NUMERIC_FIELDS) - set(NON_NULL_NUMERIC_FIELDS)))
    restore_not_null = tuple(
        name for name in candidate_not_null if name in cols and name not in not_null
    )
    blocking_columns = tuple(name for name in candidate_not_null if name in cols)
    if blocking_columns:
        where = " OR ".join(f"{name} IS NULL" for name in blocking_columns)
        null_row_count = int(
            con.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE} WHERE {where}").fetchone()[0]
        )
    else:
        null_row_count = 0
    return RollbackPlan(
        target_contract_version=target.contract_version,
        target_schema_hash=target.schema_hash,
        target_config_hash=target.config_hash,
        target_contract_hash=target.contract_hash,
        derived_from=target.derived_from,
        drop_columns=drop_columns,
        restore_not_null=restore_not_null,
        null_row_count=null_row_count,
    )


def format_plan_to_v1(p: RollbackPlan) -> str:
    lines = [
        f"回退目标: contract_version={p.target_contract_version} ({p.derived_from})",
        f"          schema_hash={p.target_schema_hash}",
        f"          config_hash={p.target_config_hash}",
        f"          contract_hash={p.target_contract_hash}",
        f"删列: {list(p.drop_columns) or '无'}",
        f"恢复 NOT NULL: {list(p.restore_not_null) or '无'}",
    ]
    if p.null_row_count:
        lines.append(
            f"!! 无法执行: {CANONICAL_TABLE} 里有 {p.null_row_count} 行待恢复 NOT NULL "
            "的列为 NULL —— 必须先删除含 NULL 的分区 (accept 的 DELETE+INSERT 原子替换) "
            "才能回退到 v1 契约; SET NOT NULL 遇 NULL 行会直接失败, 不会半途而止"
        )
    elif not (p.drop_columns or p.restore_not_null):
        lines.append("==> 无事可做: 表形状已是 v1 契约形状 (重跑本命令是 no-op)。")
    return "\n".join(lines)


def execute_to_v1(con: Any, p: RollbackPlan) -> RollbackPlan:
    """一个事务删列+改戳, 再一个独立事务恢复 NOT NULL (理由同 execute() 段2的实测:
    SET NOT NULL 与同事务里刚做完的写操作互斥)。"""

    if not p.executable:
        raise RestampMismatchError(
            f"计划不可执行: {CANONICAL_TABLE} 里有 {p.null_row_count} 行含 NULL, "
            "必须先删除含 NULL 的分区 (回退窗口已关闭)"
        )

    con.execute("BEGIN TRANSACTION")
    try:
        for name in p.drop_columns:
            con.execute(f"ALTER TABLE {CANONICAL_TABLE} DROP COLUMN {name}")
        con.execute(
            f"UPDATE {CANONICAL_TABLE} SET contract_version = ?, config_hash = ?",
            [p.target_contract_version, p.target_config_hash],
        )
        con.execute(
            f"""
            UPDATE {ACCEPTED_TABLE}
               SET contract_version = ?, contract_hash = ?, config_hash = ?
             WHERE dataset_id = ?
            """,
            [p.target_contract_version, p.target_contract_hash, p.target_config_hash, DATASET_ID],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    if p.restore_not_null:
        con.execute("BEGIN TRANSACTION")
        try:
            for name in p.restore_not_null:
                con.execute(f"ALTER TABLE {CANONICAL_TABLE} ALTER COLUMN {name} SET NOT NULL")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    _assert_after_to_v1(con, p)
    return p


def _assert_after_to_v1(con: Any, p: RollbackPlan) -> None:
    bad_ptr = con.execute(
        f"""SELECT COUNT(*) FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND (contract_version <> ? OR contract_hash <> ?
                                       OR config_hash <> ?)""",
        [DATASET_ID, p.target_contract_version, p.target_contract_hash, p.target_config_hash],
    ).fetchone()[0]
    if bad_ptr:
        raise RestampMismatchError(f"{ACCEPTED_TABLE}: {bad_ptr} 行回退后戳仍不等于 v1 目标")

    bad_canon = con.execute(
        f"""SELECT COUNT(*) FROM {CANONICAL_TABLE}
             WHERE contract_version <> ? OR config_hash <> ?""",
        [p.target_contract_version, p.target_config_hash],
    ).fetchone()[0]
    if bad_canon:
        raise RestampMismatchError(f"{CANONICAL_TABLE}: {bad_canon} 行回退后戳仍不等于 v1 目标")

    cols, not_null = _table_shape(con, CANONICAL_TABLE)
    still_present = [name for name in p.drop_columns if name in cols]
    if still_present:
        raise RestampMismatchError(f"回退后仍残留应删的列: {still_present}")
    not_restored = [name for name in p.restore_not_null if name not in not_null]
    if not_restored:
        raise RestampMismatchError(f"回退后仍未恢复 NOT NULL 的列: {not_restored}")


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
        help="回退到 v1 契约 (目标戳来自 nominal_ohlcv_contract_versions.yaml, 不现算; "
             "回退窗口: 第一行 NULL 进 canonical 之前, 见模块 docstring)",
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

        with writer_lock("restamp_nominal_ohlcv_contract"):
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

    with writer_lock("restamp_nominal_ohlcv_contract"):
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
            f"\nexecuted: {ACCEPTED_TABLE} {p.pointer_stale} 行 + "
            f"{CANONICAL_TABLE} {p.canonical_stale:,} 行已重打 "
            f"(报的是**实际改了多少**, 不是表里有多少行); "
            f"加列={[n for n, _ in p.add_columns] or '无'} "
            f"解NOT NULL={list(p.drop_not_null) or '无'} "
            f"加NOT NULL={list(p.set_not_null) or '无'}; "
            f"{INGEST_BATCH_TABLE} 未动; 写后自证五条全过"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
