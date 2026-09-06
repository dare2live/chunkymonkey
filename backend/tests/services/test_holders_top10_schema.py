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
    assert len(schema._SCHEMA_PAYLOAD["fields"]) == 21
    assert "raw_fields" not in schema._SCHEMA_PAYLOAD


# ── T1b backward: canonical schema is byte-identical to pre-T1 git HEAD ────


def test_schema_version_and_contract_version_match_head() -> None:
    assert schema.SCHEMA_VERSION == "2"
    assert schema.CONTRACT_VERSION == "3"


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
    assert "holder_code" not in schema.CANONICAL_ROW_FIELDS


def test_holder_new_is_not_promoted_to_a_canonical_field() -> None:
    """holder_new is derivable (COALESCE(holder_code, holder_name), probe:
    zero exceptions) -> CLAUDE.md 规则5 forbids storing it as a column."""

    assert "holder_new" not in schema.CANONICAL_ROW_FIELDS
    field_names = {f["name"] for f in schema.SCHEMA_CONTRACT["fields"]}
    assert "holder_new" not in field_names


def test_schema_payload_field_count_is_21_not_27() -> None:
    assert len(schema._SCHEMA_PAYLOAD["fields"]) == 21


@pytest.mark.parametrize("name", ["PROVIDER_FIELDS", "ENRICHMENT_FIELDS", "GRAIN"])
def test_legacy_constant_is_byte_identical_to_git_head(name: str, head_source: str) -> None:
    current_source = Path(schema.__file__).read_text(encoding="utf-8")
    old_segment = _source_segment(head_source, name)
    new_segment = _source_segment(current_source, name)
    assert old_segment == new_segment, (
        f"{name} changed from git HEAD — holders_top10_schema.py must not "
        "touch PROVIDER_FIELDS/ENRICHMENT_FIELDS/GRAIN"
    )


def test_schema_payload_fields_are_byte_identical_to_head(head_source: str) -> None:
    """Machine proof that the canonical contract did not change: every
    entry in ``_SCHEMA_PAYLOAD['fields']`` — count, order, and dict content —
    matches the pre-T1 committed version exactly (21 items, not 27)."""

    old_module = types.ModuleType(SCHEMA_MODULE_NAME)
    exec(compile(head_source, str(Path(schema.__file__).resolve()), "exec"), old_module.__dict__)  # noqa: S102

    old_fields = [dict(f) for f in old_module.SCHEMA_CONTRACT["fields"]]
    new_fields = [dict(f) for f in schema.SCHEMA_CONTRACT["fields"]]

    assert len(old_fields) == 21
    assert new_fields == old_fields


def test_contract_hash_is_byte_identical_to_head(head_source: str) -> None:
    """load_holders_top10_contract()'s contract_hash must be UNCHANGED from
    pre-T1 HEAD — this is the machine proof that no undone schema change
    rode into the tree. Computed by actually re-running the unmodified
    holders_top10_contract.py against a stand-in module built from git
    HEAD's schema source (not a hand-rolled re-implementation of the
    hashing logic)."""

    # 2026-09-06: 原实现把替身模块塞进 sys.modules 再 import 契约模块, 即使 finally 还原,
    # 还原方式是**重新 import** —— 生成新的模块对象, 而下游已 `from ... import` 过符号的模块
    # 仍持有旧引用。实测污染: 本文件先跑, 之后 test_holders_top10_acceptance.py 红 6 条;
    # 单独跑却全绿。换 sys.modules 这类机制天生全局, 还原不干净 —— 改成子进程隔离。
    with tempfile.TemporaryDirectory() as td:
        head_path = Path(td) / "head_schema.py"
        head_path.write_text(head_source, encoding="utf-8")
        probe = textwrap.dedent(f"""
            import json, sys, types, importlib
            src = open({str(head_path)!r}, encoding="utf-8").read()
            m = types.ModuleType({SCHEMA_MODULE_NAME!r})
            exec(compile(src, {str(Path(schema.__file__).resolve())!r}, "exec"), m.__dict__)
            sys.modules[{SCHEMA_MODULE_NAME!r}] = m
            c = importlib.import_module({CONTRACT_MODULE_NAME!r}).load_holders_top10_contract()
            print(json.dumps({{
                "contract_version": c.contract_version,
                "schema_hash": c.schema_hash,
                "contract_hash": c.contract_hash,
                "config_hash": c.config_hash,
            }}))
            """)
        env = dict(os.environ, PYTHONPATH="backend")
        res = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(Path(schema.__file__).resolve().parents[3]), capture_output=True, text=True, env=env,
        )
        assert res.returncode == 0, f"HEAD 契约探针子进程失败: {res.stderr[-800:]}"
        old_contract = SimpleNamespace(**json.loads(res.stdout.strip().splitlines()[-1]))

    from services.data_sources.holders_top10_contract import load_holders_top10_contract

    new_contract = load_holders_top10_contract()

    assert old_contract.contract_version == "3"
    assert new_contract.contract_version == "3"
    assert new_contract.schema_hash == old_contract.schema_hash
    assert new_contract.contract_hash == old_contract.contract_hash
    assert new_contract.config_hash == old_contract.config_hash
