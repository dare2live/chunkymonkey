"""Domain-parametric library core for one-time contract-version restamp
migrations of accepted ``SecurityDayDomain`` tables (land→accept partitions).

**Why this module exists (2026-09-18, ST 契约 v2 抽取)**: ``restamp_nominal_
ohlcv_contract.py`` (daily v1->v2) was written first and hand-rolled every
column name. When ``stock_st`` needed the exact same migration shape
(``ADD COLUMN <enrichment> -> backfill by ``ingest_batch.source_name`` ->
``DROP NOT NULL`` on the newly-nullable column(s) -> restamp pointer/canonical
stamps -> ``SET NOT NULL`` on the new column``), copying the script and
hand-editing every literal (``pre_close_origin``, ``NUMERIC_FIELDS``, the
daily YAML path, ...) would have created a second copy of the same mechanism
that drifts the moment either domain's rollback machinery gets a bugfix
(exactly the "反模式" this repo's rules forbid: 一件事写两遍会漂移)。

This module is that mechanism, parameterized by :class:`RestampTarget`.
``restamp_nominal_ohlcv_contract.py`` and ``restamp_stock_st_contract.py`` are
now thin CLIs that each construct one ``RestampTarget`` and re-export these
functions bound to it — see either script's module docstring for its own
target's specifics.

Design carried over unchanged from the original daily-only script (all still
apply, still true, still enforced the same way):

1. **No hardcoded column names**. Columns to ADD / SET NOT NULL / DROP NOT
   NULL are computed as a diff between "schema_payload declares" and "table
   actually has" — never a hand-written list.
2. **No hardcoded hashes**. Target stamps always come from
   ``target.load_contract()`` (the domain's own current contract factory) —
   a literal hash here would be a second copy of a truth that already lives
   in the schema module, and it would drift from it.
3. **No side ledger**. Old stamps stay legible forever inside ``ingest_batch``
   (append-only evidence); restamping never deletes information, it only
   updates the *pointer*.
4. **One connection, one transaction** wherever DuckDB allows it (see the
   ``SET NOT NULL`` exception below, carried over from the daily script's own
   real-DB experiment).
5. **plan/execute separation, execute never re-queries** — the assertions
   right before writing compare against the plan's own frozen baseline
   reads, not a second live query that could itself have moved.
6. **写后自证五条** (any one failing rolls back): accepted_partition stamps
   == fresh contract; canonical stamps == fresh contract; every partition's
   ``content_hash`` is bit-for-bit unchanged (restamping never touches
   content); ``ingest_batch`` is untouched (grouped stamp-distribution counts
   identical before/after); table shape (column set + NOT NULL set) matches
   the schema's declaration.

``RestampTarget.enrichment_backfill`` generalizes the single hardcoded
``pre_close_origin`` CASE-expression backfill into "for every column in this
mapping, if that column was just newly ADDed this run, backfill it via
``CASE ingest_batch.source_name ... END``" — a column that already existed on
the table before this run (e.g. a later re-run once the new adapter path has
started writing that column directly, per-row, with no need for a historical
backfill) is left alone entirely: that is exactly the ``is_noop`` /
"don't re-backfill already-live rows" property the ST cut's spec requires
(重打脚本第二次跑时若 st_origin 已存在, 不得把 baostock 路径的行错标成名称路径)。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.nominal_ohlcv_contract_versions import load_rollback_target


class RestampMismatchError(RuntimeError):
    """计划与实测不一致, 或写后自证失败 —— 调用方所在事务必须 ROLLBACK。

    不是 ValueError: 这是运行时数据状态问题, 不是入参形状错误。
    """


@dataclass(frozen=True)
class RestampTarget:
    """Everything the generic restamp mechanics need to know about one
    domain. Built once by each thin per-domain CLI script — see
    ``restamp_nominal_ohlcv_contract.py`` / ``restamp_stock_st_contract.py``.
    """

    dataset_id: str
    canonical_table: str
    schema_fields: tuple[Mapping[str, Any], ...]
    # Returns the domain's *current* contract (an object with .contract_version
    # / .contract_hash / .config_hash) — always called fresh, never cached, so
    # a restamp run always targets today's schema/registry state.
    load_contract: Callable[[], Any]
    # column name -> {ingest_batch.source_name -> enrichment value}. Only
    # columns that are *newly added* this run are backfilled this way (see
    # module docstring) — a column already present on the table is left
    # exactly as it is.
    enrichment_backfill: Mapping[str, Mapping[str, str]]
    rollback_targets_path: Path
    rollback_version: str = "1"

    @property
    def enrichment_columns(self) -> frozenset[str]:
        return frozenset(self.enrichment_backfill)


@dataclass(frozen=True)
class RestampPlan:
    """一次重打的全部计划值 —— execute 只照它做, 不再自己查。"""

    target: RestampTarget
    target_contract_version: str
    target_contract_hash: str
    target_config_hash: str
    pointer_rows: int
    canonical_rows: int
    pointer_stale: int
    canonical_stale: int
    add_columns: tuple[tuple[str, str], ...]
    drop_not_null: tuple[str, ...]
    set_not_null: tuple[str, ...]
    # column -> {source_name: label}, restricted to columns actually being
    # newly added this run (see ``RestampTarget`` docstring).
    backfill_by_source: Mapping[str, Mapping[str, str]]
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


def plan(con: Any, target: RestampTarget) -> RestampPlan:
    """只读: 算出要改什么, 以及重打后必须保持不变的那些基线读数。"""

    contract = target.load_contract()
    declared = {str(f["name"]): f for f in target.schema_fields}
    cols, not_null = _table_shape(con, target.canonical_table)

    add_columns = tuple(
        (name, str(f["duckdb_type"]))
        for name, f in declared.items()
        if name not in cols
    )
    newly_added = {name for name, _ in add_columns}

    drop_not_null = tuple(sorted(
        name for name, f in declared.items()
        if name in cols and bool(f["nullable"]) and name in not_null
    ))
    set_not_null = tuple(sorted(
        name for name, f in declared.items()
        if name in cols and not bool(f["nullable"]) and name not in not_null
    ))
    set_not_null += tuple(sorted(
        name for name, _ in add_columns if not bool(declared[name]["nullable"])
    ))

    sources = [
        str(r[0])
        for r in con.execute(
            f"SELECT DISTINCT source_name FROM {INGEST_BATCH_TABLE} WHERE dataset_id = ?",
            [target.dataset_id],
        ).fetchall()
    ]
    backfill_by_source: dict[str, dict[str, str]] = {}
    unmapped_set: set[str] = set()
    for column, backfill_map in target.enrichment_backfill.items():
        if column not in newly_added:
            # 已经在表上 (is_noop 的一部分) —— 不进回填分支, 不判 unmapped。
            continue
        backfill_by_source[column] = {s: backfill_map[s] for s in sources if s in backfill_map}
        unmapped_set.update(s for s in sources if s not in backfill_map)
    unmapped = tuple(sorted(unmapped_set))

    pointer_rows = int(con.execute(
        f"SELECT COUNT(*) FROM {ACCEPTED_TABLE} WHERE dataset_id = ?", [target.dataset_id]
    ).fetchone()[0])
    canonical_rows = int(con.execute(f"SELECT COUNT(*) FROM {target.canonical_table}").fetchone()[0])
    pointer_stale = int(con.execute(
        f"""SELECT COUNT(*) FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND (contract_version <> ? OR contract_hash <> ?
                                       OR config_hash <> ?)""",
        [target.dataset_id, str(contract.contract_version), str(contract.contract_hash),
         str(contract.config_hash)],
    ).fetchone()[0])
    canonical_stale = int(con.execute(
        f"""SELECT COUNT(*) FROM {target.canonical_table}
             WHERE contract_version <> ? OR config_hash <> ?""",
        [str(contract.contract_version), str(contract.config_hash)],
    ).fetchone()[0])
    content_before = {
        str(r[0]): str(r[1])
        for r in con.execute(
            f"SELECT partition_value, content_hash FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
            [target.dataset_id],
        ).fetchall()
    }
    ingest_before = tuple(
        tuple(r)
        for r in con.execute(
            f"""SELECT contract_version, contract_hash, config_hash, source_name,
                       status, COUNT(*)
                  FROM {INGEST_BATCH_TABLE} WHERE dataset_id = ?
                 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5""",
            [target.dataset_id],
        ).fetchall()
    )
    return RestampPlan(
        target=target,
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
        backfill_by_source=backfill_by_source,
        unmapped_sources=unmapped,
        content_hash_before=content_before,
        ingest_batch_before=ingest_before,
    )


def format_plan(p: RestampPlan) -> str:
    target = p.target
    lines = [
        f"目标契约: contract_version={p.target_contract_version}",
        f"          contract_hash={p.target_contract_hash}",
        f"          config_hash={p.target_config_hash}",
        f"{ACCEPTED_TABLE}: {p.pointer_stale} / {p.pointer_rows} 行戳与目标不符",
        f"{target.canonical_table}: {p.canonical_stale:,} / {p.canonical_rows:,} 行戳与目标不符",
        f"{INGEST_BATCH_TABLE}: 0 行 —— 落地证据永不重打 (现有 {len(p.ingest_batch_before)} 种戳组合原样保留)",
        f"加列: {[f'{n} {t}' for n, t in p.add_columns] or '无'}",
        f"解除 NOT NULL: {list(p.drop_not_null) or '无'}",
        f"加上 NOT NULL: {list(p.set_not_null) or '无'}",
        f"回填映射: {({k: dict(v) for k, v in p.backfill_by_source.items()}) or '无'}",
    ]
    if p.is_noop:
        lines.append(
            "==> 无事可做: 戳已等于现算契约, 且表形状与契约一致 (重跑本脚本是 no-op)。"
        )
    if p.unmapped_sources:
        lines.append(
            f"!! 无法执行: ingest_batch 里有未登记的 source_name {list(p.unmapped_sources)} —— "
            "回填映射缺这些源的裁决, 补进对应的 acquire_rules YAML 的 "
            "backfill_origin_by_source 并写明实证依据后再跑 "
            "(不猜: 猜错就是给历史行的一部分编造血缘)"
        )
    return "\n".join(lines)


def execute(con: Any, plan_obj: RestampPlan) -> RestampPlan:
    """一个事务内完成全部改动; 任一断言失败即 ROLLBACK 并抛 RestampMismatchError。"""

    target = plan_obj.target
    if not plan_obj.executable:
        raise RestampMismatchError(
            f"计划不可执行: 未登记的 source_name {list(plan_obj.unmapped_sources)}"
        )

    con.execute("BEGIN TRANSACTION")
    try:
        now_pointer = int(con.execute(
            f"SELECT COUNT(*) FROM {ACCEPTED_TABLE} WHERE dataset_id = ?", [target.dataset_id]
        ).fetchone()[0])
        now_canonical = int(con.execute(f"SELECT COUNT(*) FROM {target.canonical_table}").fetchone()[0])
        if (now_pointer, now_canonical) != (plan_obj.pointer_rows, plan_obj.canonical_rows):
            raise RestampMismatchError(
                f"计划时 pointer={plan_obj.pointer_rows} canonical={plan_obj.canonical_rows}, "
                f"写库时实测 pointer={now_pointer} canonical={now_canonical} —— "
                "数据在计划与执行之间发生了变化"
            )

        for name, duck_type in plan_obj.add_columns:
            con.execute(f"ALTER TABLE {target.canonical_table} ADD COLUMN {name} {duck_type}")

        for name, backfill_map in plan_obj.backfill_by_source.items():
            if not backfill_map:
                continue
            cases = " ".join(
                f"WHEN '{src}' THEN '{label}'" for src, label in sorted(backfill_map.items())
            )
            con.execute(
                f"""
                UPDATE {target.canonical_table}
                   SET {name} = CASE b.source_name {cases} END
                  FROM {INGEST_BATCH_TABLE} b
                 WHERE b.batch_id = {target.canonical_table}.ingest_batch_id
                """
            )
            left = int(con.execute(
                f"SELECT COUNT(*) FROM {target.canonical_table} WHERE {name} IS NULL"
            ).fetchone()[0])
            if left:
                raise RestampMismatchError(
                    f"{name} 回填后仍有 {left} 行为 NULL —— 有 canonical 行 JOIN 不上 "
                    "ingest_batch, 或其 source_name 不在回填映射里"
                )

        for name in plan_obj.drop_not_null:
            con.execute(f"ALTER TABLE {target.canonical_table} ALTER COLUMN {name} DROP NOT NULL")
        # SET NOT NULL **不在本段** —— 实测 DuckDB 1.5.2: 它内部要建索引, 与同一事务里
        # 尚未提交的 UPDATE 互斥, 直接抛
        # TransactionException: Cannot create index with outstanding updates。

        con.execute(
            f"""
            UPDATE {target.canonical_table} SET contract_version = ?, config_hash = ?
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
                target.dataset_id,
                plan_obj.target_contract_version,
                plan_obj.target_contract_hash,
                plan_obj.target_config_hash,
            ],
        )

        _assert_after(con, plan_obj, shape=False)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    # ── 段 2: SET NOT NULL 必须在自己的事务里 (实测理由见上) ────────────────
    if plan_obj.set_not_null:
        con.execute("BEGIN TRANSACTION")
        try:
            for name in plan_obj.set_not_null:
                con.execute(f"ALTER TABLE {target.canonical_table} ALTER COLUMN {name} SET NOT NULL")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    _assert_after(con, plan_obj)
    return plan_obj


def _assert_after(con: Any, p: RestampPlan, *, shape: bool = True) -> None:
    """写后自证五条。任一不过即抛 —— 调用方负责 ROLLBACK。

    ``shape=False``: 只证"数据与戳", 跳过表形状那两条 —— 段 1 结束时
    ``SET NOT NULL`` 还没做, 此刻形状本就不该吻合。
    """

    target = p.target
    bad_ptr = con.execute(
        f"""SELECT COUNT(*) FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND (contract_version <> ? OR contract_hash <> ?
                                       OR config_hash <> ?)""",
        [target.dataset_id, p.target_contract_version, p.target_contract_hash, p.target_config_hash],
    ).fetchone()[0]
    if bad_ptr:
        raise RestampMismatchError(f"{ACCEPTED_TABLE}: {bad_ptr} 行重打后戳仍不等于现算契约")

    bad_canon = con.execute(
        f"""SELECT COUNT(*) FROM {target.canonical_table}
             WHERE contract_version <> ? OR config_hash <> ?""",
        [p.target_contract_version, p.target_config_hash],
    ).fetchone()[0]
    if bad_canon:
        raise RestampMismatchError(f"{target.canonical_table}: {bad_canon} 行重打后戳仍不等于现算契约")

    after = {
        str(r[0]): str(r[1])
        for r in con.execute(
            f"SELECT partition_value, content_hash FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
            [target.dataset_id],
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
            [target.dataset_id],
        ).fetchall()
    )
    if ingest_after != p.ingest_batch_before:
        raise RestampMismatchError(
            "ingest_batch 的戳组合分布变了 —— 落地证据被动过, 那是封印不是指针"
        )

    if not shape:
        return
    cols, not_null = _table_shape(con, target.canonical_table)
    fields = target.schema_fields
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


# ── --to-v1: 回退到某个历史契约版本 ──────────────────────────────────────
#
# 目标戳来自 target.rollback_targets_path 指向的 YAML (一次性历史事实), 不来自
# target.load_contract() (那是**当前**契约工厂, 只会算出当前版本自己的戳) 也不来自
# ingest_batch (里面躺着的可能是更早一代的戳, 不是要回退到的那个版本 —— 见
# nominal_ohlcv_contract_versions.py 模块 docstring 的实测更正)。
#
# 要删/要恢复 NOT NULL 的列不硬编码字面量: drop_columns = 当前表上存在的增补列;
# restore_not_null = 当前 schema 声明可空、且不是增补列本身的那些列, 在表上仍是
# nullable 的那些 —— 这是 v1->v2 迁移里"哪些列被从 NOT NULL 改成可空"的通用反推,
# 不依赖 NUMERIC_FIELDS 这类 daily 专属结构。


@dataclass(frozen=True)
class RollbackPlan:
    """回退到某个历史 contract_version 的计划。"""

    target: RestampTarget = field(repr=False)
    target_contract_version: str
    target_schema_hash: str
    target_config_hash: str
    target_contract_hash: str
    derived_from: str
    drop_columns: tuple[str, ...]
    restore_not_null: tuple[str, ...]
    null_row_count: int

    @property
    def executable(self) -> bool:
        return self.null_row_count == 0


def plan_to_v1(con: Any, target: RestampTarget) -> RollbackPlan:
    """只读: 算出回退要做什么, 以及是否已经被 NULL 行挡住 (回退窗口已关)。"""

    rollback_target = load_rollback_target(
        target.rollback_version, path=target.rollback_targets_path
    )
    cols, not_null = _table_shape(con, target.canonical_table)
    declared = {str(f["name"]): f for f in target.schema_fields}
    enrichment_cols = target.enrichment_columns

    drop_columns = tuple(name for name in enrichment_cols if name in cols)
    candidate_not_null = tuple(sorted(
        name for name, f in declared.items()
        if name not in enrichment_cols and bool(f["nullable"])
    ))
    restore_not_null = tuple(
        name for name in candidate_not_null if name in cols and name not in not_null
    )
    blocking_columns = tuple(name for name in candidate_not_null if name in cols)
    if blocking_columns:
        where = " OR ".join(f"{name} IS NULL" for name in blocking_columns)
        null_row_count = int(
            con.execute(f"SELECT COUNT(*) FROM {target.canonical_table} WHERE {where}").fetchone()[0]
        )
    else:
        null_row_count = 0
    return RollbackPlan(
        target=target,
        target_contract_version=rollback_target.contract_version,
        target_schema_hash=rollback_target.schema_hash,
        target_config_hash=rollback_target.config_hash,
        target_contract_hash=rollback_target.contract_hash,
        derived_from=rollback_target.derived_from,
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
            f"!! 无法执行: {p.target.canonical_table} 里有 {p.null_row_count} 行待恢复 NOT NULL "
            "的列为 NULL —— 必须先删除含 NULL 的分区 (accept 的 DELETE+INSERT 原子替换) "
            "才能回退; SET NOT NULL 遇 NULL 行会直接失败, 不会半途而止"
        )
    elif not (p.drop_columns or p.restore_not_null):
        lines.append("==> 无事可做: 表形状已是目标版本形状 (重跑本命令是 no-op)。")
    return "\n".join(lines)


def execute_to_v1(con: Any, p: RollbackPlan) -> RollbackPlan:
    """一个事务删列+改戳, 再一个独立事务恢复 NOT NULL (理由同 execute() 段2)。"""

    target = p.target
    if not p.executable:
        raise RestampMismatchError(
            f"计划不可执行: {target.canonical_table} 里有 {p.null_row_count} 行含 NULL, "
            "必须先删除含 NULL 的分区 (回退窗口已关闭)"
        )

    con.execute("BEGIN TRANSACTION")
    try:
        for name in p.drop_columns:
            con.execute(f"ALTER TABLE {target.canonical_table} DROP COLUMN {name}")
        con.execute(
            f"UPDATE {target.canonical_table} SET contract_version = ?, config_hash = ?",
            [p.target_contract_version, p.target_config_hash],
        )
        con.execute(
            f"""
            UPDATE {ACCEPTED_TABLE}
               SET contract_version = ?, contract_hash = ?, config_hash = ?
             WHERE dataset_id = ?
            """,
            [p.target_contract_version, p.target_contract_hash, p.target_config_hash, target.dataset_id],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    if p.restore_not_null:
        con.execute("BEGIN TRANSACTION")
        try:
            for name in p.restore_not_null:
                con.execute(f"ALTER TABLE {target.canonical_table} ALTER COLUMN {name} SET NOT NULL")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    _assert_after_to_v1(con, p)
    return p


def _assert_after_to_v1(con: Any, p: RollbackPlan) -> None:
    target = p.target
    bad_ptr = con.execute(
        f"""SELECT COUNT(*) FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND (contract_version <> ? OR contract_hash <> ?
                                       OR config_hash <> ?)""",
        [target.dataset_id, p.target_contract_version, p.target_contract_hash, p.target_config_hash],
    ).fetchone()[0]
    if bad_ptr:
        raise RestampMismatchError(f"{ACCEPTED_TABLE}: {bad_ptr} 行回退后戳仍不等于目标")

    bad_canon = con.execute(
        f"""SELECT COUNT(*) FROM {target.canonical_table}
             WHERE contract_version <> ? OR config_hash <> ?""",
        [p.target_contract_version, p.target_config_hash],
    ).fetchone()[0]
    if bad_canon:
        raise RestampMismatchError(f"{target.canonical_table}: {bad_canon} 行回退后戳仍不等于目标")

    cols, not_null = _table_shape(con, target.canonical_table)
    still_present = [name for name in p.drop_columns if name in cols]
    if still_present:
        raise RestampMismatchError(f"回退后仍残留应删的列: {still_present}")
    not_restored = [name for name in p.restore_not_null if name not in not_null]
    if not_restored:
        raise RestampMismatchError(f"回退后仍未恢复 NOT NULL 的列: {not_restored}")


__all__ = [
    "RestampMismatchError",
    "RestampPlan",
    "RestampTarget",
    "RollbackPlan",
    "execute",
    "execute_to_v1",
    "format_plan",
    "format_plan_to_v1",
    "plan",
    "plan_to_v1",
]
