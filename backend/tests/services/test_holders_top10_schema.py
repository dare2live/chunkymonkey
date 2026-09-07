"""holders_top10_schema Phase-A (T1a) additions + T1b revert-to-HEAD proof.

Design: ``.git/cm_worklog/ingest_holders_raw/DESIGN.md`` (task T1, split into
T1a/T1b after fable review). This test file is scoped to ONE file —
``holders_top10_schema.py`` — because T2/T3/T4 (acceptance / aif10 clean /
dual-write) are separate, parallel in-flight tasks that own their own files
and their own tests. In particular this file does NOT exercise
``_validate_provider_row`` / accept-path REJECT codes (that lives in
``holders_top10_acceptance.py``, a different task's file).

T1a (kept): ``RAW_FIELDS`` (the 47-field typed raw layer for the Phase-A
fetch -> staging path) and ``RawFetch`` (the fetch/raw-writer carrier type).
Neither is part of the canonical contract.

T1b (reverted): the previous pass had also bumped ``SCHEMA_VERSION`` /
``CONTRACT_VERSION``, added ``PROVIDER_EXT_FIELDS`` / ``LINEAGE_FIELDS``, and
appended 6 fields to ``_SCHEMA_PAYLOAD`` — before Phase A's staging run had
produced any evidence for which (if any) ext fields are worth promoting onto
canonical. That is undone here: which provider fields belong on canonical is
a decision for after Phase A, not before it. This file's "backward" tests
prove that reversion mechanically (against ``git show HEAD``, not eyeballed):
every canonical-schema constant/hash is byte-identical to the pre-T1 HEAD
commit, and the two v3-only constants are gone.
"""
from __future__ import annotations

import ast
import importlib
from types import SimpleNamespace
import textwrap
import tempfile
import os
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from services.data_sources import holders_top10_schema as schema

SCHEMA_MODULE_NAME = "services.data_sources.holders_top10_schema"
CONTRACT_MODULE_NAME = "services.data_sources.holders_top10_contract"


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def _schema_relpath() -> str:
    return str(
        Path(schema.__file__).resolve().relative_to(_repo_root())
    ).replace("\\", "/")


def _git_show_head(relpath: str) -> str:
    repo_root = _repo_root()
    out = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"HEAD:{relpath}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def _source_segment(source: str, name: str) -> str:
    """Exact source text of the top-level assignment ``name = ...``."""

    tree = ast.parse(source)
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"top-level assignment {name!r} not found")


@pytest.fixture(scope="module")
def head_source() -> str:
    return _git_show_head(_schema_relpath())


# ── T1a forward: RAW_FIELDS / RawFetch exist with the designed shape ───────


def test_raw_fields_has_47_entries_with_designed_types() -> None:
    assert len(schema.RAW_FIELDS) == 47
    names = [name for name, _ in schema.RAW_FIELDS]
    assert len(names) == len(set(names)), "RAW_FIELDS must not repeat a provider key"

    by_type: dict[str, int] = {}
    for _, duckdb_type in schema.RAW_FIELDS:
        by_type[duckdb_type] = by_type.get(duckdb_type, 0) + 1
    assert by_type == {"VARCHAR": 37, "DOUBLE": 7, "BIGINT": 2, "INTEGER": 1}

    as_dict = dict(schema.RAW_FIELDS)
    # Spot-check the load-bearing type decisions called out in DESIGN.md §1.
    assert as_dict["HOLD_NUM"] == "BIGINT"  # exceeds int32 for large holders
    assert as_dict["XZCHANGE"] == "BIGINT"
    assert as_dict["HOLDER_RANK"] == "INTEGER"
    assert as_dict["HOLD_RATIO"] == "DOUBLE"
    assert as_dict["HOLD_NUM_CHANGE"] == "VARCHAR"  # polymorphic text
    assert as_dict["IS_HOLDORG"] == "VARCHAR"  # raw layer keeps provider's own '0'/'1' text
    assert as_dict["HOLDER_CODE"] == "VARCHAR"
    assert as_dict["NOTICE_DATE"] == "VARCHAR"
    assert as_dict["UPDATE_DATE"] == "VARCHAR"
    # HOLDER_NEW rides along verbatim in raw even though it is not promoted
    # to a canonical column (it is a pure derivation — COALESCE(HOLDER_CODE,
    # HOLDER_NAME), zero exceptions — CLAUDE.md 规则5).
    assert "HOLDER_NEW" in as_dict


def test_raw_fields_names_are_provider_verbatim_uppercase() -> None:
    # Distinguishes RAW_FIELDS (provider's own casing) from every other
    # tuple in this module (our lowercase canonical names) — a reviewer
    # confusing the two would silently break the raw<->canonical mapping.
    for name, _ in schema.RAW_FIELDS:
        assert name == name.upper(), f"{name!r} should be provider verbatim casing"


def test_raw_fetch_dataclass_has_documented_fields() -> None:
    fields = schema.RawFetch.__dataclass_fields__
    assert set(fields) == {"fetch_id", "stock_code", "request", "rows"}
    fetch = schema.RawFetch(
        fetch_id="600519:run1",
        stock_code="600519",
        request={"api": schema.API, "secucode": "600519.SH"},
        rows=({"SECURITY_CODE": "600519", "HOLDER_NAME": "x"},),
    )
    assert fetch.fetch_id == "600519:run1"
    assert fetch.rows[0]["SECURITY_CODE"] == "600519"
    with pytest.raises(Exception):
        fetch.fetch_id = "mutate"  # frozen dataclass


def test_raw_fields_not_folded_into_schema_payload() -> None:
    """T1b guard: RAW_FIELDS is a Phase-A staging constant, not part of the
    canonical contract. A future edit that pastes it (or a lowercased
    projection of it) into ``_SCHEMA_PAYLOAD['fields']`` is exactly the
    undone-schema-bump regression T1b reverted — this must catch it before
    SCHEMA_VERSION quietly moves again.
    """

    schema_field_names = {f["name"] for f in schema._SCHEMA_PAYLOAD["fields"]}
    raw_field_names = {name for name, _ in schema.RAW_FIELDS}
    # RAW_FIELDS keys are the provider's own verbatim UPPERCASE names;
    # _SCHEMA_PAYLOAD fields are always our lowercase canonical names — a
    # healthy schema has zero exact-string overlap between the two sets.
    assert schema_field_names.isdisjoint(raw_field_names)
    # 2026-09-07 删掉这里的 `== 21`: 字段数的唯一判据是
    # test_schema_payload_field_count_is_23, 抄第二份迟早一处改了另一处没改
    # (本次就是这样红的)。本测试的真判据是上面那行 disjoint —— RAW_FIELDS 没被折进契约。
    assert "raw_fields" not in schema._SCHEMA_PAYLOAD


# ── T1b backward: 未经证据的 schema 抢跑不许再发生 ──────────────────────────
#
# 2026-09-07: 删掉 test_schema_version_and_contract_version_match_head
# (原断言 SCHEMA_VERSION == "2" / CONTRACT_VERSION == "3")。版本号的唯一判据现在是
# test_identity_promotion_bumped_both_versions —— 两处各写一份版本号, 就是本次红的原因。
# 下面这条 (PROVIDER_EXT_FIELDS / LINEAGE_FIELDS 不得复活) 仍然有效且与本次改动无关:
# 本次只加了两个能指着实测数字说清理由的列, 没有把 T1b 那 6 个 ext 字段带回来。


def test_provider_ext_and_lineage_constants_were_removed() -> None:
    """T1b explicitly took PROVIDER_EXT_FIELDS / LINEAGE_FIELDS back out —
    lock that in so a later edit can't silently reintroduce them without a
    test update (and, per the module docstring, without redoing the
    Phase-A-evidence-first decision they were reverted for)."""

    assert not hasattr(schema, "PROVIDER_EXT_FIELDS")
    assert not hasattr(schema, "LINEAGE_FIELDS")
    assert "PROVIDER_EXT_FIELDS" not in schema.__all__
    assert "LINEAGE_FIELDS" not in schema.__all__


def test_canonical_row_fields_is_provider_plus_enrichment_only() -> None:
    assert schema.CANONICAL_ROW_FIELDS == schema.PROVIDER_FIELDS + schema.ENRICHMENT_FIELDS
    assert "raw_row_hash" not in schema.CANONICAL_ROW_FIELDS
    # 2026-09-07 反号: 此前断言 "holder_code" **不在** canonical —— 那是 T1b 回退留下的闸,
    # 意思是「Phase A 拿出证据之前不许提升」。证据已到 (见下方 test_identity_columns_...),
    # 业主已拍板, 故这里改成断言它**在**。
    assert "holder_code" in schema.CANONICAL_ROW_FIELDS
    assert "is_holder_org" in schema.CANONICAL_ROW_FIELDS


def test_holder_new_is_not_promoted_to_a_canonical_field() -> None:
    """holder_new is derivable (COALESCE(holder_code, holder_name), probe:
    zero exceptions) -> CLAUDE.md 规则5 forbids storing it as a column."""

    assert "holder_new" not in schema.CANONICAL_ROW_FIELDS
    field_names = {f["name"] for f in schema.SCHEMA_CONTRACT["fields"]}
    assert "holder_new" not in field_names


def test_schema_payload_field_count_is_23() -> None:
    """21 -> 23 (2026-09-07, schema v3): +holder_code +is_holder_org。

    仍然不是 T1b 那次想加的 27 —— 那次一口气加了 6 个 ext 字段, 没有任何证据说明
    哪些值得上 canonical。这次只加两个, 且两个都能指着实测数字说清为什么。
    """
    assert len(schema._SCHEMA_PAYLOAD["fields"]) == 23


# ── 身份列: 固定形状断言, 不比 git HEAD ───────────────────────────────────────
#
# 2026-09-07 退役三个 "byte_identical_to_git_head" 测试
# (test_legacy_constant_is_byte_identical_to_git_head[PROVIDER_FIELDS/ENRICHMENT_FIELDS/GRAIN]
#  / test_schema_payload_fields_are_byte_identical_to_head
#  / test_contract_hash_is_byte_identical_to_head)。
#
# 它们锚在 `git show HEAD`, 也就是**会移动的**基准: 改动一提交, HEAD 就等于工作树,
# 三个断言全部变成同义反复, 永远绿。它们真正起过的作用只有一次 —— 拦住 T1b 那次
# 「Phase A 证据到齐之前就往 canonical 加 6 个 ext 字段」的抢跑。那个用途已经完成:
# Phase A 跑完了, 证据在下面这个测试里, 业主拍了板。
#
# 留一个锚在移动基准上的闸 = 门问的问题(和上次提交比有没有变)不是它想守的东西
# (canonical 契约有没有被无证据地改)。换成固定形状断言: 不依赖 git, 任何一次
# 意外改动 —— 包括把这两列删掉、改可空性、改 null 语义 —— 都会红。


def test_identity_columns_are_on_canonical_with_measured_null_semantics() -> None:
    """holder_code / is_holder_org 的形状与 null 语义, 连同它们的实测依据。

    为什么要有 holder_code (Phase A 实测, 2018-12-31 起全市场 1,449,322 行):
      - 同一 code 用过多个名字: 4,467 个 code / 291,819 行 (35.2%)。
        只按 holder_name 聚合会把**一个**实体拆成多个 —— code 10671586「香港中央结算」
        有 9 种写法 (含繁体「結算」、(A股)/(沪股通) 后缀、"中心"/"公司"), 51,499 行;
        code 510500 有 9 种, 其中一种把连字符写成汉字「一」。
      - 同一名字对应多个 code: 116 个名字 / 60,832 行 (7.3%)。
        只按名字聚合会把**不同**实体并成一个 (Morgan Stanley 2 个码)。

    为什么 holder_code 可空、而 is_holder_org 不可空:
      供应商只给机构编码, 个人恒空, 边界干净无例外 ——
      IS_HOLDORG=1 的 829,249 行里 holder_code 空 0 行 (0.0%);
      IS_HOLDORG=0 的 620,073 行里空 620,073 行 (100.0%)。
      所以那 42.8% 的空**不是**「测不出」, 是「个人本来就没有」。
      没有 is_holder_org 这一列, 两者分不开 —— 红线 3 要求缺失可辨识。
    """
    fields = {f["name"]: f for f in schema.SCHEMA_CONTRACT["fields"]}

    code = fields["holder_code"]
    assert code["duckdb_type"] == "VARCHAR"
    assert code["nullable"] is True
    assert code["origin"] == "provider"
    assert code["null_semantics"] == "structural_absent_for_natural_person", (
        "holder_code 的空必须标成结构性缺席; 标成 unknown/forbidden 都会让"
        "「个人无编码」和「机构没取到」分不开"
    )

    org = fields["is_holder_org"]
    assert org["duckdb_type"] == "BOOLEAN"
    # 可空只为 v2 遗留行 (生产库现有 610,414 行, 当时供应商判别没被记录, 填任何值都是猜)。
    # v3 起写进来的行必然非空 —— 约束在 accept 侧 (_validate_provider_row 要求 bool,
    # 否则 INVALID_HOLDER_ORG_FLAG), 不在 DDL 上。为了加一列 drop 生产表是不可逆的, 不做。
    assert org["nullable"] is True
    assert org["null_semantics"] == "legacy_pre_v3_row_identity_unrecorded"
    assert org["origin"] == "provider"

    # 两列都在 PROVIDER 段而不是 ENRICHMENT 段: 它们是供应商原样给的, 不是我们算的。
    assert "holder_code" in schema.PROVIDER_FIELDS
    assert "is_holder_org" in schema.PROVIDER_FIELDS
    assert "holder_code" not in schema.ENRICHMENT_FIELDS
    assert "is_holder_org" not in schema.ENRICHMENT_FIELDS


def test_identity_promotion_bumped_both_versions() -> None:
    """改 canonical 形状必须同时抬 schema 与 contract 版本, 否则下游分不清两份数据。"""
    assert schema.SCHEMA_VERSION == "4"
    assert schema.CONTRACT_VERSION == "5"




# ── 分区键必须在身份里 (2026-09-08) ─────────────────────────────────────────
#
# 根因形态: 分区替换 (accept 的 delete_scope) 只在「粒度 → 分区是函数」时成立。
# 分区键若不在 GRAIN 里, 供应商一改它, 同一粒度就搬了分区, 分区内 DELETE 够不到旧行,
# 主键撞车 —— 而日更是**整日批**, 一条撞车 = 整天 ConstraintException 事务回滚,
# 批次留 LANDED, 错误只进 errors 列表, 进程照常 exit 0。
#
# 实测代价 (备份 pre_holders_backfill_20260907):
#   20260818 landing 1,460 行 -> canonical 615 行 / 50 只股(应 146), 17 天
#   20260828 landing 7,356 行 -> canonical **0 行**(半年报高峰 516 只), 10 天
#   两个批次 rejection_code 都是 None。发生率 0.13%, 每财报季约 3-6 个整天。


@pytest.mark.parametrize(
    "module_name",
    [
        "services.data_sources.holders_top10_schema",
        "services.data_sources.org_holding_schema",
        "services.data_sources.stk_holdertrade_schema",
    ],
)
def test_partition_field_is_in_grain(module_name: str) -> None:
    """三张 disclosure schema 的 PARTITION_FIELD 都必须在 GRAIN 里。

    2026-09-08 之前: holders_top10 与 org_holding 红, stk_holdertrade 绿
    (它早就把 ann_date 放进了 GRAIN, 是唯一没病的那个)。
    """
    import importlib

    mod = importlib.import_module(module_name)
    assert mod.PARTITION_FIELD in mod.GRAIN, (
        f"{module_name}: PARTITION_FIELD={mod.PARTITION_FIELD!r} 不在 GRAIN={mod.GRAIN!r} 里 —— "
        "供应商改这个字段时同一粒度会搬分区, 分区内 DELETE 够不到旧行, 整批撞车"
    )
