#!/usr/bin/env python3
"""cleanup_out_of_scope_rows.py — 范围外证券类别行删除工具 (cut_bshare_purge, 2026-09-18)。

背景: 业主裁定某些证券类别 (例如 B 股) 永不在本项目范围内, 已获取的历史必须删除
清理干净, 处置是留痕不归档 (不导出 parquet/不 archive, 只在 mart_data_deletion_record
记一条账)。check_out_of_scope_rows.py 只负责发现 (只读运行时不变量, PASS/FAIL/
UNVERIFIED); 本脚本负责删除, 两者共用同一套判定正则 (regexp_matches + 由
scripts.check_out_of_scope_rows.load_scan_config() 现算的正则), 删除谓词与判 0
的谓词是同一个对象——"删完审计必 0" 因此按构造成立, 不需要第二套正则再去证明等价。

登记表 backend/config/out_of_scope_cleanup.yaml 决定"哪些表允许按哪个类别删行、
怎么删": 未登记的表即便审计 FAIL 也不删 (只报进 plan.unregistered), 逼出裁决而不
是静默扩大删除范围。两种 kind:
  - plain_delete: 无戳直写表, 按 code_column 匹配正则直接 DELETE。
  - disclosure_event_canonical: 带 accepted_partition 指针的标准表, 删行后必须
    用该 domain 自己的 partition_accepted_pointer_stats() 对受影响的每个分区重算
    row_count/content_hash; 分区删空则删指针 (accepted_partition.row_count 有
    CHECK > 0, 不能留 0)。绝不重新走 land→accept (那是一次新的"再观测", 会覆盖
    batch_id/observed_at, 给一次没发生过的供应商观测造血缘)。

不可逆点是每个库各自的 COMMIT。回退不靠反推——记账 verification 里的全部被删键
可以按键从供应商重取, 那是独立算出的目标, 不是从 ingest_batch 反推的历史脚印。

plan()/execute() 只做纯函数式的读/写, 全部数据库连接由调用方经 conn_for 注入 (测试
传 tmp_path 下的 DuckDB 文件, --db-override 供命令行验收/演练用); 只有 main() 落到
生产路径 (services.data_access.resolver.db_path + services.writer_lock)。

用法:
  PYTHONPATH=backend python backend/scripts/cleanup_out_of_scope_rows.py --class b_share
  PYTHONPATH=backend python backend/scripts/cleanup_out_of_scope_rows.py --class b_share \
      --execute --run-id cleanup_out_of_scope_rows_run1
  ... --db-override org_holding=/tmp/x/org_holding.duckdb   # 可重复
  ... --snapshot /path/to/disclosure_dataset_snapshot.json  # 测试/演练用
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.check_out_of_scope_rows import (  # noqa: E402
    CONFIG_PATH as SCAN_CONFIG_PATH,
    ScanConfig,
    load_scan_config,
    scan_database,
)
from services.data_access import resolver  # noqa: E402
from services.data_deletion import record_data_deletion  # noqa: E402
from services.data_sources.accepted_schema import (  # noqa: E402
    ACCEPTED_TABLE,
    INGEST_BATCH_TABLE,
)
from services.data_sources.disclosure_event_partition import (  # noqa: E402
    partition_accepted_pointer_stats,
)
from services.data_sources import holders_top10_schema  # noqa: E402
from services.data_sources.holders_top10_acceptance import (  # noqa: E402
    partition_pointer_stats as holders_top10_partition_pointer_stats,
)
from services.writer_lock import WriterLockBusyError, writer_lock  # noqa: E402

CONFIG_PATH = REPO / "backend" / "config" / "out_of_scope_cleanup.yaml"

_TOP_KEYS = {"version", "dispositions"}
_KIND_PLAIN = "plain_delete"
_KIND_CANONICAL = "disclosure_event_canonical"
_KIND_HOLDERS_CANONICAL = "holders_top10_canonical"
_KNOWN_KINDS = {_KIND_PLAIN, _KIND_CANONICAL, _KIND_HOLDERS_CANONICAL}
_PLAIN_DELETE_KEYS = {"db", "table", "code_column", "kind", "key_columns", "why"}
_CANONICAL_KEYS = {"db", "table", "code_column", "kind", "domain", "why"}
_HOLDERS_CANONICAL_KEYS = {"db", "table", "code_column", "kind", "why"}
_EXPECTED_KEYS_BY_KIND = {
    _KIND_PLAIN: _PLAIN_DELETE_KEYS,
    _KIND_CANONICAL: _CANONICAL_KEYS,
    _KIND_HOLDERS_CANONICAL: _HOLDERS_CANONICAL_KEYS,
}
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DB_ORDER_HINT = ("org_holding", "smartmoney", "tushare_raw")


class OutOfScopeCleanupConfigError(RuntimeError):
    """out_of_scope_cleanup.yaml 加载期错误, 或 CLI 前置条件不满足 (均 fail-closed)。"""


class CleanupMismatchError(RuntimeError):
    """plan 与写库时刻状态不一致, 或写后自证失败: 调用方所在事务必须 ROLLBACK。"""


# ── 配置对象 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _CanonicalShape:
    """两种「带 accepted_partition 指针」kind (disclosure_event_canonical /
    holders_top10_canonical) 共用的五样东西: plan()/execute() 只认这个形状,
    不关心它是从 DisclosureEventDomain 解析出来的还是从 holders_top10_schema
    常量拼出来的——两种 kind 的差别只剩「形状从哪来」, plan/execute 的逻辑一份不复制。
    """

    dataset_id: str
    partition_field: str
    grain: tuple[str, ...]
    canonical_table: str
    pointer_stats: Callable[[Any, str], tuple[int, str]]


@dataclass(frozen=True)
class Disposition:
    db: str
    table: str
    code_column: str
    kind: str
    why: str
    key_columns: tuple[str, ...] = ()
    domain: Any = None  # DisclosureEventDomain, 只对 disclosure_event_canonical 有效
    shape: _CanonicalShape | None = None  # 两种 canonical kind 共用, plain_delete 为 None


@dataclass(frozen=True)
class CleanupConfig:
    version: int
    dispositions: tuple[Disposition, ...]


def _resolve_domain(value: str, *, index: int) -> Any:
    module_path, sep, attr_path = str(value).partition(":")
    if not sep or not module_path or not attr_path:
        raise OutOfScopeCleanupConfigError(
            f"dispositions[{index}].domain must be 'module:attr' form: {value!r}"
        )
    try:
        obj: Any = importlib.import_module(module_path)
    except ImportError as exc:
        raise OutOfScopeCleanupConfigError(
            f"dispositions[{index}].domain module {module_path!r} not importable"
        ) from exc
    for part in attr_path.split("."):
        if not part or not hasattr(obj, part):
            raise OutOfScopeCleanupConfigError(
                f"dispositions[{index}].domain {value!r}: {obj!r} has no attribute {part!r}"
            )
        obj = getattr(obj, part)
    return obj


def load_cleanup_config(
    path: Path | None = None, *, scan_config: ScanConfig | None = None
) -> CleanupConfig:
    """加载并校验 out_of_scope_cleanup.yaml；任何结构问题 fail-closed 抛
    :class:`OutOfScopeCleanupConfigError`。``scan_config`` 可注入 (测试用假
    ScanConfig 校验 code_column), 缺省现读真实 out_of_scope_scan.yaml。"""

    p = path or CONFIG_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise OutOfScopeCleanupConfigError(
            f"missing out_of_scope_cleanup registry: {p}: {exc}"
        ) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OutOfScopeCleanupConfigError(
            f"unreadable out_of_scope_cleanup registry: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise OutOfScopeCleanupConfigError("out_of_scope_cleanup root must be a mapping")
    top_keys = set(raw)
    if top_keys != _TOP_KEYS:
        raise OutOfScopeCleanupConfigError(
            f"out_of_scope_cleanup top-level keys must be exactly {sorted(_TOP_KEYS)}: "
            f"{sorted(top_keys)}"
        )
    if raw.get("version") != 1:
        raise OutOfScopeCleanupConfigError("out_of_scope_cleanup version must be 1")

    dispositions_raw = raw.get("dispositions")
    if not isinstance(dispositions_raw, list) or not dispositions_raw:
        raise OutOfScopeCleanupConfigError("dispositions must be a non-empty list")

    scan_config = scan_config or load_scan_config()
    valid_columns = set(scan_config.security_code_columns)

    from services.database_manifest import get_database_manifest

    manifest = get_database_manifest()

    dispositions: list[Disposition] = []
    seen_pairs: set[tuple[str, str]] = set()
    for i, item in enumerate(dispositions_raw):
        if not isinstance(item, dict):
            raise OutOfScopeCleanupConfigError(f"dispositions[{i}] must be a mapping")

        kind = item.get("kind")
        if kind not in _KNOWN_KINDS:
            raise OutOfScopeCleanupConfigError(
                f"dispositions[{i}].kind must be one of {sorted(_KNOWN_KINDS)}: {kind!r}"
            )
        expected_keys = _EXPECTED_KEYS_BY_KIND[kind]
        actual_keys = set(item)
        if actual_keys != expected_keys:
            raise OutOfScopeCleanupConfigError(
                f"dispositions[{i}] (kind={kind!r}) keys must be exactly "
                f"{sorted(expected_keys)}: {sorted(actual_keys)}"
            )

        db = item["db"]
        if not isinstance(db, str) or not db.strip():
            raise OutOfScopeCleanupConfigError(f"dispositions[{i}].db must be a non-empty string")
        try:
            manifest.require(db)
        except KeyError as exc:
            raise OutOfScopeCleanupConfigError(
                f"dispositions[{i}].db unknown database alias: {db!r}"
            ) from exc

        table = item["table"]
        if not isinstance(table, str) or not _TABLE_NAME_RE.match(table):
            raise OutOfScopeCleanupConfigError(
                f"dispositions[{i}].table must match ^[A-Za-z_][A-Za-z0-9_]*$: {table!r}"
            )

        code_column = item["code_column"]
        if not isinstance(code_column, str) or code_column not in valid_columns:
            raise OutOfScopeCleanupConfigError(
                f"dispositions[{i}].code_column not in scan security_code_columns "
                f"{sorted(valid_columns)}: {code_column!r}"
            )

        why = item["why"]
        if not isinstance(why, str) or not why.strip():
            raise OutOfScopeCleanupConfigError(f"dispositions[{i}].why must be a non-empty string")

        pair = (db, table)
        if pair in seen_pairs:
            raise OutOfScopeCleanupConfigError(f"dispositions[{i}]: duplicate (db, table): {pair}")
        seen_pairs.add(pair)

        if kind == _KIND_CANONICAL:
            domain_ref = item["domain"]
            if not isinstance(domain_ref, str) or not domain_ref.strip():
                raise OutOfScopeCleanupConfigError(
                    f"dispositions[{i}].domain must be a non-empty string"
                )
            domain_obj = _resolve_domain(domain_ref, index=i)
            required_attrs = (
                "canonical_table", "partition_field", "grain", "content_hash_fields", "dataset_id",
            )
            missing_attrs = [a for a in required_attrs if not hasattr(domain_obj, a)]
            if missing_attrs:
                raise OutOfScopeCleanupConfigError(
                    f"dispositions[{i}].domain {domain_ref!r} missing attributes {missing_attrs}"
                )
            if str(getattr(domain_obj, "canonical_table")) != table:
                raise OutOfScopeCleanupConfigError(
                    f"dispositions[{i}].domain {domain_ref!r} canonical_table="
                    f"{getattr(domain_obj, 'canonical_table')!r} != table={table!r}"
                )
            shape = _CanonicalShape(
                dataset_id=domain_obj.dataset_id,
                partition_field=domain_obj.partition_field,
                grain=tuple(domain_obj.grain),
                canonical_table=domain_obj.canonical_table,
                pointer_stats=(
                    lambda c, pv, _domain=domain_obj: partition_accepted_pointer_stats(c, _domain, pv)
                ),
            )
            dispositions.append(
                Disposition(
                    db=db, table=table, code_column=code_column, kind=kind, why=why.strip(),
                    key_columns=tuple(domain_obj.grain), domain=domain_obj, shape=shape,
                )
            )
        elif kind == _KIND_HOLDERS_CANONICAL:
            if str(holders_top10_schema.CANONICAL_TABLE) != table:
                raise OutOfScopeCleanupConfigError(
                    f"dispositions[{i}].table must equal holders_top10_schema.CANONICAL_TABLE "
                    f"({holders_top10_schema.CANONICAL_TABLE!r}): {table!r}"
                )
            shape = _CanonicalShape(
                dataset_id=holders_top10_schema.DATASET_ID,
                partition_field=holders_top10_schema.PARTITION_FIELD,
                grain=tuple(holders_top10_schema.GRAIN),
                canonical_table=holders_top10_schema.CANONICAL_TABLE,
                pointer_stats=holders_top10_partition_pointer_stats,
            )
            dispositions.append(
                Disposition(
                    db=db, table=table, code_column=code_column, kind=kind, why=why.strip(),
                    key_columns=tuple(holders_top10_schema.GRAIN), domain=None, shape=shape,
                )
            )
        else:
            key_columns_raw = item["key_columns"]
            if not isinstance(key_columns_raw, list) or not key_columns_raw:
                raise OutOfScopeCleanupConfigError(
                    f"dispositions[{i}].key_columns must be a non-empty list"
                )
            key_columns: list[str] = []
            for kc in key_columns_raw:
                if not isinstance(kc, str) or not _COLUMN_NAME_RE.match(kc):
                    raise OutOfScopeCleanupConfigError(
                        f"dispositions[{i}].key_columns entries must match "
                        f"^[A-Za-z_][A-Za-z0-9_]*$: {kc!r}"
                    )
                key_columns.append(kc)
            dispositions.append(
                Disposition(
                    db=db, table=table, code_column=code_column, kind=kind, why=why.strip(),
                    key_columns=tuple(key_columns), domain=None, shape=None,
                )
            )

    return CleanupConfig(version=1, dispositions=tuple(dispositions))


# ── plan/execute 数据结构 ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class PartitionPlan:
    partition_value: str
    before_count: int
    hit_in_partition: int
    expected_after: int
    will_empty: bool
    pointer_before: dict[str, Any] | None
    format_consistent: bool


@dataclass(frozen=True)
class TablePlan:
    db: str
    table: str
    code_column: str
    kind: str
    key_columns: tuple[str, ...]
    checked_before: int
    hit: int
    non_hit: int
    distinct_codes: tuple[str, ...]
    keys: tuple[tuple[Any, ...], ...]
    shape: _CanonicalShape | None = None
    partitions: tuple[PartitionPlan, ...] = ()
    pointer_baseline: Mapping[str, tuple[int, str, str]] = field(default_factory=dict)
    ingest_batch_baseline: tuple[tuple[Any, ...], ...] = ()


@dataclass(frozen=True)
class CleanupPlan:
    cls: str
    regex: str
    ruling: str
    tables: tuple[TablePlan, ...]
    unregistered: tuple[dict[str, Any], ...]
    snapshot_intersection: Mapping[str, tuple[str, ...]]
    snapshot_checked: Mapping[str, tuple[str, ...]]
    base_table_names: Mapping[str, frozenset[str]]
    reasons: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return not self.reasons

    @property
    def is_noop(self) -> bool:
        return all(t.hit == 0 for t in self.tables)


def _base_table_names(conn) -> frozenset[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' AND table_type='BASE TABLE'"
    ).fetchall()
    return frozenset(str(r[0]) for r in rows)


def _count(conn, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _count_hit(conn, table: str, code_column: str, regex: str) -> int:
    return int(
        conn.execute(
            f'SELECT COUNT(*) FILTER (WHERE regexp_matches(CAST("{code_column}" AS VARCHAR), ?)) '
            f'FROM "{table}"',
            [regex],
        ).fetchone()[0]
    )


def _ingest_batch_group_counts(conn, dataset_id: str) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        tuple(r)
        for r in conn.execute(
            "SELECT contract_version, contract_hash, config_hash, source_name, status, COUNT(*) "
            "FROM ingest_batch WHERE dataset_id = ? "
            "GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5",
            [dataset_id],
        ).fetchall()
    )


def _pointer_row(conn, dataset_id: str, partition_value: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT row_count, content_hash, batch_id FROM {ACCEPTED_TABLE} "
        "WHERE dataset_id = ? AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?",
        [dataset_id, partition_value],
    ).fetchone()
    if row is None:
        return None
    return {"row_count": int(row[0]), "content_hash": str(row[1]), "batch_id": str(row[2])}


def plan(
    conn_for: Callable[[str], Any],
    config: CleanupConfig,
    cls_name: str,
    *,
    snapshot_partitions: Mapping[str, Any] | None = None,
) -> CleanupPlan:
    """只读计划: 对每个登记的 db 别名开一个连接 (调用方注入, 可重复利用同一连接给
    execute)。任何结构性前置条件不满足 (未知 class) fail-closed 抛
    :class:`OutOfScopeCleanupConfigError`；数据层面的不可执行条件 (格式不一致 /
    指针不唯一 / 撞冻结快照) 记进 ``plan.reasons``, 通过 ``plan.executable`` 暴露,
    不在这里抛异常 (dry-run 需要能把原因打印出来)。"""

    scan_config = load_scan_config()
    classes_by_name = {c.name: c for c in scan_config.classes}
    if cls_name not in classes_by_name:
        raise OutOfScopeCleanupConfigError(
            f"--class {cls_name!r} not in scan classes: {sorted(classes_by_name)}"
        )
    cls_obj = classes_by_name[cls_name]
    regex = cls_obj.regex
    ruling = cls_obj.ruling
    snapshot_partitions = snapshot_partitions or {}

    conns: dict[str, Any] = {}
    table_plans: list[TablePlan] = []
    reasons: list[str] = []
    snapshot_hits: dict[str, tuple[str, ...]] = {}
    snapshot_checked: dict[str, tuple[str, ...]] = {}
    registered_pairs = {(d.db, d.table) for d in config.dispositions}

    for disp in config.dispositions:
        conn = conns.get(disp.db)
        if conn is None:
            conn = conn_for(disp.db)
            conns[disp.db] = conn

        checked_before = _count(conn, disp.table)
        hit = _count_hit(conn, disp.table, disp.code_column, regex)
        non_hit = checked_before - hit
        distinct_codes = tuple(
            str(r[0])
            for r in conn.execute(
                f'SELECT DISTINCT CAST("{disp.code_column}" AS VARCHAR) FROM "{disp.table}" '
                f'WHERE regexp_matches(CAST("{disp.code_column}" AS VARCHAR), ?) ORDER BY 1',
                [regex],
            ).fetchall()
        )
        key_cols_sql = ", ".join(f'CAST("{c}" AS VARCHAR)' for c in disp.key_columns)
        keys = tuple(
            tuple(r)
            for r in conn.execute(
                f'SELECT {key_cols_sql} FROM "{disp.table}" '
                f'WHERE regexp_matches(CAST("{disp.code_column}" AS VARCHAR), ?) '
                f'ORDER BY {key_cols_sql}',
                [regex],
            ).fetchall()
        )

        partitions: tuple[PartitionPlan, ...] = ()
        pointer_baseline: dict[str, tuple[int, str, str]] = {}
        ingest_baseline: tuple[tuple[Any, ...], ...] = ()

        if disp.kind in (_KIND_CANONICAL, _KIND_HOLDERS_CANONICAL):
            shape = disp.shape
            partition_col = shape.partition_field

            pointer_baseline = {
                str(r[0]): (int(r[1]), str(r[2]), str(r[3]))
                for r in conn.execute(
                    "SELECT replace(CAST(partition_value AS VARCHAR), '-', '') AS pv, "
                    f"row_count, content_hash, batch_id FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
                    [shape.dataset_id],
                ).fetchall()
            }
            ingest_baseline = _ingest_batch_group_counts(conn, shape.dataset_id)
            snap_set = frozenset(snapshot_partitions.get(shape.dataset_id, ()))
            snapshot_checked[shape.dataset_id] = tuple(sorted(snap_set))

            if hit > 0:
                affected_raw = conn.execute(
                    f'SELECT DISTINCT CAST("{partition_col}" AS VARCHAR) FROM "{disp.table}" '
                    f'WHERE regexp_matches(CAST("{disp.code_column}" AS VARCHAR), ?) ORDER BY 1',
                    [regex],
                ).fetchall()
                part_plans: list[PartitionPlan] = []
                affected_compact: list[str] = []
                for row in affected_raw:
                    pv = "".join(ch for ch in str(row[0]) if ch.isdigit())[:8]
                    affected_compact.append(pv)

                    compact_count = int(
                        conn.execute(
                            f'SELECT COUNT(*) FROM "{disp.table}" WHERE {partition_col} = ?',
                            [pv],
                        ).fetchone()[0]
                    )
                    normalized_count = int(
                        conn.execute(
                            f'SELECT COUNT(*) FROM "{disp.table}" '
                            f"WHERE replace(CAST({partition_col} AS VARCHAR), '-', '') = ?",
                            [pv],
                        ).fetchone()[0]
                    )
                    format_consistent = compact_count == normalized_count
                    if not format_consistent:
                        reasons.append(
                            f"{disp.db}.{disp.table} partition {pv}: compact-equality count "
                            f"{compact_count} != normalized-equality count {normalized_count} "
                            "(mixed available_date formats; hash recompute silently skips rows)"
                        )
                    before_count = normalized_count

                    hit_in_partition = int(
                        conn.execute(
                            f'SELECT COUNT(*) FROM "{disp.table}" '
                            f"WHERE replace(CAST({partition_col} AS VARCHAR), '-', '') = ? "
                            f'AND regexp_matches(CAST("{disp.code_column}" AS VARCHAR), ?)',
                            [pv, regex],
                        ).fetchone()[0]
                    )
                    expected_after = before_count - hit_in_partition

                    pointer_rows = conn.execute(
                        f"SELECT row_count, content_hash, batch_id FROM {ACCEPTED_TABLE} "
                        "WHERE dataset_id = ? AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?",
                        [shape.dataset_id, pv],
                    ).fetchall()
                    if len(pointer_rows) != 1:
                        reasons.append(
                            f"{disp.db}.{disp.table} partition {pv}: expected exactly 1 "
                            f"accepted_partition pointer row, found {len(pointer_rows)}"
                        )
                        pointer_before = None
                    else:
                        pointer_before = {
                            "row_count": int(pointer_rows[0][0]),
                            "content_hash": str(pointer_rows[0][1]),
                            "batch_id": str(pointer_rows[0][2]),
                        }

                    part_plans.append(
                        PartitionPlan(
                            partition_value=pv, before_count=before_count,
                            hit_in_partition=hit_in_partition, expected_after=expected_after,
                            will_empty=(expected_after == 0), pointer_before=pointer_before,
                            format_consistent=format_consistent,
                        )
                    )
                partitions = tuple(part_plans)

                inter = tuple(sorted(set(affected_compact) & snap_set))
                if inter:
                    snapshot_hits[shape.dataset_id] = inter
                    reasons.append(
                        f"{disp.db}.{disp.table}: affected partitions intersect frozen snapshot "
                        f"for dataset {shape.dataset_id}: {inter}"
                    )

        table_plans.append(
            TablePlan(
                db=disp.db, table=disp.table, code_column=disp.code_column, kind=disp.kind,
                key_columns=disp.key_columns, checked_before=checked_before, hit=hit,
                non_hit=non_hit, distinct_codes=distinct_codes, keys=keys, shape=disp.shape,
                partitions=partitions, pointer_baseline=pointer_baseline,
                ingest_batch_baseline=ingest_baseline,
            )
        )

    unregistered: list[dict[str, Any]] = []
    base_table_names: dict[str, frozenset[str]] = {}
    for db, conn in conns.items():
        base_table_names[db] = _base_table_names(conn)
        for row in scan_database(conn, db, scan_config):
            if row.get("status") != "FAIL" or row.get("class") != cls_name:
                continue
            if (db, row.get("table")) in registered_pairs:
                continue
            unregistered.append(row)

    return CleanupPlan(
        cls=cls_name, regex=regex, ruling=ruling, tables=tuple(table_plans),
        unregistered=tuple(unregistered), snapshot_intersection=snapshot_hits,
        snapshot_checked=snapshot_checked, base_table_names=base_table_names,
        reasons=tuple(reasons),
    )


def format_plan(p: CleanupPlan) -> str:
    lines = [f"class={p.cls} regex={p.regex}"]
    for t in p.tables:
        lines.append(
            f"  {t.db}.{t.table} ({t.kind}): checked={t.checked_before} hit={t.hit} "
            f"non_hit={t.non_hit} distinct_codes={list(t.distinct_codes)}"
        )
        for part in t.partitions:
            lines.append(
                f"    partition {part.partition_value}: before={part.before_count} "
                f"expected_after={part.expected_after} will_empty={part.will_empty} "
                f"pointer_before={part.pointer_before}"
            )
    if p.unregistered:
        lines.append(f"  unregistered FAIL rows (not in {CONFIG_PATH.name}):")
        for row in p.unregistered:
            lines.append(f"    {row}")
    if p.snapshot_intersection:
        lines.append(f"  snapshot intersection (blocks execute): {dict(p.snapshot_intersection)}")
    if not p.executable:
        lines.append("  PLAN NOT EXECUTABLE:")
        for reason in p.reasons:
            lines.append(f"    - {reason}")
    if p.is_noop:
        lines.append("  ==> no registered table has any hit: nothing to do")
    return "\n".join(lines)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(
    conn_for: Callable[[str], Any],
    plan_obj: CleanupPlan,
    *,
    run_id: str,
) -> CleanupPlan:
    """按 ``plan_obj`` (通常来自先调用的 :func:`plan`) 逐库、逐表, 在**每库一个
    连接一个事务**内完成 删除 → (canonical) 重打指针 → 记账。任一一致性断言失败
    都 ROLLBACK 并抛 :class:`CleanupMismatchError`，之后的库不再处理。"""

    if not plan_obj.executable:
        raise CleanupMismatchError(
            "plan is not executable: " + "; ".join(plan_obj.reasons)
        )

    dbs_present = {t.db for t in plan_obj.tables}
    ordered_dbs = [d for d in _DB_ORDER_HINT if d in dbs_present]
    ordered_dbs += sorted(dbs_present - set(_DB_ORDER_HINT))

    scan_yaml_sha256 = _sha256_file(SCAN_CONFIG_PATH)
    cleanup_yaml_sha256 = _sha256_file(CONFIG_PATH)

    for db in ordered_dbs:
        conn = conn_for(db)
        db_tables = [t for t in plan_obj.tables if t.db == db]
        conn.execute("BEGIN TRANSACTION")
        try:
            for t in db_tables:
                checked_now = _count(conn, t.table)
                hit_now = _count_hit(conn, t.table, t.code_column, plan_obj.regex)
                if checked_now != t.checked_before or hit_now != t.hit:
                    raise CleanupMismatchError(
                        f"{db}.{t.table}: plan checked_before={t.checked_before} hit={t.hit}, "
                        f"write-time checked={checked_now} hit={hit_now} "
                        "(state changed between plan and execute)"
                    )

                if t.kind in (_KIND_CANONICAL, _KIND_HOLDERS_CANONICAL):
                    for part in t.partitions:
                        pv = part.partition_value
                        current_before = int(
                            conn.execute(
                                f'SELECT COUNT(*) FROM "{t.table}" '
                                f"WHERE replace(CAST(\"{t.shape.partition_field}\" AS VARCHAR), '-', '') = ?",
                                [pv],
                            ).fetchone()[0]
                        )
                        if current_before != part.before_count:
                            raise CleanupMismatchError(
                                f"{db}.{t.table} partition {pv}: plan before_count="
                                f"{part.before_count}, write-time count={current_before}"
                            )
                        current_pointer = _pointer_row(conn, t.shape.dataset_id, pv)
                        if current_pointer != part.pointer_before:
                            raise CleanupMismatchError(
                                f"{db}.{t.table} partition {pv}: pointer changed between plan "
                                f"and execute (plan={part.pointer_before}, now={current_pointer})"
                            )

                conn.execute(
                    f'DELETE FROM "{t.table}" WHERE regexp_matches(CAST("{t.code_column}" AS VARCHAR), ?)',
                    [plan_obj.regex],
                )

                partitions_after: dict[str, Any] = {}
                if t.kind in (_KIND_CANONICAL, _KIND_HOLDERS_CANONICAL):
                    for part in t.partitions:
                        pv = part.partition_value
                        n, h = t.shape.pointer_stats(conn, pv)
                        if n != part.expected_after:
                            raise CleanupMismatchError(
                                f"{db}.{t.table} partition {pv}: expected_after="
                                f"{part.expected_after}, recomputed n={n}"
                            )
                        if n > 0:
                            conn.execute(
                                f"UPDATE {ACCEPTED_TABLE} SET row_count = ?, content_hash = ? "
                                "WHERE dataset_id = ? AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?",
                                [n, h, t.shape.dataset_id, pv],
                            )
                            readback = conn.execute(
                                f"SELECT row_count, content_hash FROM {ACCEPTED_TABLE} "
                                "WHERE dataset_id = ? AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?",
                                [t.shape.dataset_id, pv],
                            ).fetchone()
                            if readback is None or (int(readback[0]), str(readback[1])) != (n, h):
                                raise CleanupMismatchError(
                                    f"{db}.{t.table} partition {pv}: pointer readback after UPDATE "
                                    f"!= (n={n}, h={h}): {readback}"
                                )
                            partitions_after[pv] = {
                                "before": part.before_count, "after": n,
                                "pointer_before": part.pointer_before,
                                "pointer_after": {"row_count": n, "content_hash": h},
                                "action": "updated",
                            }
                            if t.kind == _KIND_HOLDERS_CANONICAL and part.pointer_before is not None:
                                # 已知残留 (spec_bshare_b2.md §4.3): ingest_batch 该批的
                                # canonical_row_count 是本批口径 (accept 时写入, 31 = 该次
                                # 落地批次总行数), 清理刀只重打指针 (分区口径, 变成 21),
                                # 不回写 ingest_batch —— 那是一次没发生过的"重新观测",
                                # 与 org 域同款已知残留 (spec_bshare_purge.md §1.2/§4.5),
                                # 这里显式记进账便于审计核对而不是悄悄留着。
                                residual_row = conn.execute(
                                    f"SELECT canonical_row_count FROM {INGEST_BATCH_TABLE} "
                                    "WHERE batch_id = ?",
                                    [part.pointer_before["batch_id"]],
                                ).fetchone()
                                if residual_row is not None and residual_row[0] is not None:
                                    partitions_after[pv][
                                        "ingest_batch_canonical_row_count_unchanged"
                                    ] = int(residual_row[0])
                        else:
                            conn.execute(
                                f"DELETE FROM {ACCEPTED_TABLE} WHERE dataset_id = ? "
                                "AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?",
                                [t.shape.dataset_id, pv],
                            )
                            still_there = conn.execute(
                                f"SELECT 1 FROM {ACCEPTED_TABLE} WHERE dataset_id = ? "
                                "AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?",
                                [t.shape.dataset_id, pv],
                            ).fetchone()
                            if still_there is not None:
                                raise CleanupMismatchError(
                                    f"{db}.{t.table} partition {pv}: pointer row still present "
                                    "after DELETE"
                                )
                            partitions_after[pv] = {
                                "before": part.before_count, "after": 0,
                                "pointer_before": part.pointer_before, "pointer_after": None,
                                "action": "deleted_empty",
                            }

                checked_after = _count(conn, t.table)
                hit_after = _count_hit(conn, t.table, t.code_column, plan_obj.regex)
                non_hit_after = checked_after - hit_after
                if hit_after != 0 or non_hit_after != t.non_hit:
                    raise CleanupMismatchError(
                        f"{db}.{t.table}: post-delete hit_after={hit_after} (want 0), "
                        f"non_hit_after={non_hit_after} (want {t.non_hit})"
                    )

                if t.kind in (_KIND_CANONICAL, _KIND_HOLDERS_CANONICAL):
                    affected_pvs = {p.partition_value for p in t.partitions}
                    for pv, baseline in t.pointer_baseline.items():
                        if pv in affected_pvs:
                            continue
                        current = _pointer_row(conn, t.shape.dataset_id, pv)
                        current_tuple = (
                            None if current is None
                            else (current["row_count"], current["content_hash"], current["batch_id"])
                        )
                        if current_tuple != baseline:
                            raise CleanupMismatchError(
                                f"{db}.{t.table}: unaffected partition {pv} pointer changed: "
                                f"baseline={baseline} now={current_tuple}"
                            )
                    current_ingest = _ingest_batch_group_counts(conn, t.shape.dataset_id)
                    if current_ingest != t.ingest_batch_baseline:
                        raise CleanupMismatchError(
                            f"{db}.{t.table}: ingest_batch GROUP BY distribution changed: "
                            f"baseline={t.ingest_batch_baseline} now={current_ingest}"
                        )
                    mismatch_n = int(
                        conn.execute(
                            f"""
                            WITH ptr AS (
                                SELECT replace(CAST(partition_value AS VARCHAR), '-', '') pv, row_count
                                  FROM {ACCEPTED_TABLE} WHERE dataset_id = ?
                            ), can AS (
                                SELECT replace(CAST({t.shape.partition_field} AS VARCHAR), '-', '') pv,
                                       count(*) n
                                  FROM "{t.table}" GROUP BY 1
                            )
                            SELECT count(*) FILTER (WHERE p.pv IS NULL OR c.pv IS NULL OR p.row_count <> c.n)
                              FROM ptr p FULL OUTER JOIN can c USING (pv)
                            """,
                            [t.shape.dataset_id],
                        ).fetchone()[0]
                    )
                    if mismatch_n != 0:
                        raise CleanupMismatchError(
                            f"{db}.{t.table}: {mismatch_n} partitions have pointer row_count "
                            "!= canonical row_count after cleanup"
                        )

                if t.hit > 0:
                    verification: dict[str, Any] = {
                        "class": plan_obj.cls,
                        "regex": plan_obj.regex,
                        "scan_yaml_sha256": scan_yaml_sha256,
                        "cleanup_yaml_sha256": cleanup_yaml_sha256,
                        "checked_before": t.checked_before,
                        "hit": t.hit,
                        "non_hit_before": t.non_hit,
                        "non_hit_after": non_hit_after,
                        "hit_after": hit_after,
                        "distinct_codes": list(t.distinct_codes),
                        "key_columns": list(t.key_columns),
                        "deleted_keys": [list(k) for k in t.keys],
                    }
                    if t.kind in (_KIND_CANONICAL, _KIND_HOLDERS_CANONICAL):
                        verification["partitions"] = partitions_after
                        verification["snapshot_partitions_checked"] = list(
                            plan_obj.snapshot_checked.get(t.shape.dataset_id, ())
                        )
                        verification["snapshot_intersection"] = list(
                            plan_obj.snapshot_intersection.get(t.shape.dataset_id, ())
                        )

                    record_data_deletion(
                        conn,
                        deletion_run_id=run_id,
                        table_name=t.table,
                        delete_scope="rows_removed_out_of_scope_class",
                        key_column=t.code_column,
                        key_value=plan_obj.cls,
                        deleted_rows=t.hit,
                        reason=(
                            f"out-of-scope class {plan_obj.cls} cleanup: {plan_obj.ruling}; "
                            "retained-not-archived per owner ruling; evidence in "
                            "data/audit/out_of_scope_rows_<execution date>.json"
                        ),
                        verification=verification,
                    )

            current_base_tables = _base_table_names(conn)
            baseline_base_tables = plan_obj.base_table_names[db]
            if current_base_tables != baseline_base_tables:
                raise CleanupMismatchError(
                    f"{db}: BASE TABLE set changed during cleanup: "
                    f"before={sorted(baseline_base_tables)} after={sorted(current_base_tables)}"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return plan_obj


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_db_overrides(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise OutOfScopeCleanupConfigError(f"--db-override malformed: {item!r} (need alias=path)")
        alias, db_path = item.split("=", 1)
        alias = alias.strip()
        if not alias or not db_path.strip():
            raise OutOfScopeCleanupConfigError(f"--db-override malformed: {item!r}")
        out[alias] = db_path.strip()
    return out


def _load_snapshot_partitions(path: Path) -> dict[str, frozenset[str]]:
    if not path.is_file():
        raise OutOfScopeCleanupConfigError(f"snapshot file missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutOfScopeCleanupConfigError(f"unreadable snapshot: {path}: {exc}") from exc
    domains = raw.get("domains") if isinstance(raw, dict) else None
    if not isinstance(domains, dict):
        raise OutOfScopeCleanupConfigError(f"snapshot missing domains mapping: {path}")
    out: dict[str, set[str]] = {}
    for body in domains.values():
        if not isinstance(body, dict):
            continue
        accepted = body.get("accepted")
        if not isinstance(accepted, list):
            continue
        for item in accepted:
            if not isinstance(item, dict):
                continue
            dsid = item.get("dataset_id")
            part = item.get("partition")
            if not dsid or not part:
                continue
            compact = "".join(ch for ch in str(part) if ch.isdigit())[:8]
            out.setdefault(str(dsid), set()).add(compact)
    return {k: frozenset(v) for k, v in out.items()}


def _resolve_snapshot_path(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value)
    from services.data_sources.disclosure_dataset_snapshot import default_snapshot_path

    return default_snapshot_path()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class", dest="cls", required=True, help="out_of_scope_scan.yaml class name")
    ap.add_argument("--execute", action="store_true", help="actually write (default dry-run)")
    ap.add_argument("--run-id", default=None, help="record_data_deletion deletion_run_id")
    ap.add_argument(
        "--db-override", action="append", default=[],
        help="alias=path override for a database_manifest alias (repeatable)",
    )
    ap.add_argument(
        "--snapshot", default=None,
        help="disclosure_dataset_snapshot.json path override (test/rehearsal)",
    )
    args = ap.parse_args(argv)

    try:
        config = load_cleanup_config()
        overrides = _parse_db_overrides(args.db_override)
        snapshot_path = _resolve_snapshot_path(args.snapshot)
        snapshot_partitions = _load_snapshot_partitions(snapshot_path)
    except OutOfScopeCleanupConfigError as exc:
        print(f"[cleanup_out_of_scope_rows] CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2

    resolved_paths: dict[str, Path] = {}
    for disp in config.dispositions:
        if disp.db in resolved_paths:
            continue
        db_path = Path(overrides[disp.db]) if disp.db in overrides else Path(resolver.db_path(disp.db))
        if not db_path.is_file():
            print(
                f"[cleanup_out_of_scope_rows] CONFIG_ERROR: database file missing for "
                f"alias {disp.db!r}: {db_path}",
                file=sys.stderr,
            )
            return 2
        resolved_paths[disp.db] = db_path

    from services.duck_adapter import connect as duck_connect

    conn_cache: dict[str, Any] = {}

    def _conn_for(alias: str, *, read_only: bool):
        if alias not in conn_cache:
            conn_cache[alias] = duck_connect(str(resolved_paths[alias]), read_only=read_only)
        return conn_cache[alias]

    exit_code = 0
    try:
        if not args.execute:
            try:
                plan_obj = plan(
                    lambda alias: _conn_for(alias, read_only=True),
                    config, args.cls, snapshot_partitions=snapshot_partitions,
                )
            except OutOfScopeCleanupConfigError as exc:
                print(f"[cleanup_out_of_scope_rows] CONFIG_ERROR: {exc}", file=sys.stderr)
                return 2
            print(format_plan(plan_obj))
            exit_code = 4 if plan_obj.unregistered else 0
        else:
            run_id = args.run_id or (
                f"cleanup_out_of_scope_rows_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
            )
            try:
                with writer_lock("cleanup_out_of_scope_rows"):
                    try:
                        plan_obj = plan(
                            lambda alias: _conn_for(alias, read_only=False),
                            config, args.cls, snapshot_partitions=snapshot_partitions,
                        )
                    except OutOfScopeCleanupConfigError as exc:
                        print(f"[cleanup_out_of_scope_rows] CONFIG_ERROR: {exc}", file=sys.stderr)
                        return 2
                    print(format_plan(plan_obj))
                    try:
                        plan_obj = execute(
                            lambda alias: _conn_for(alias, read_only=False),
                            plan_obj, run_id=run_id,
                        )
                    except CleanupMismatchError as exc:
                        print(f"[cleanup_out_of_scope_rows] MISMATCH: {exc}", file=sys.stderr)
                        return 1
            except WriterLockBusyError as exc:
                print(f"[cleanup_out_of_scope_rows] LOCK_BUSY: {exc}", file=sys.stderr)
                return 3
            exit_code = 4 if plan_obj.unregistered else 0
    finally:
        for conn in conn_cache.values():
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup, never masks the real exit code
                pass

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
