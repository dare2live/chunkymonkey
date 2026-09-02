"""check_contract_stamp_consistency 单测 (2026-09-01).

自带 fixture, 不断言宿主环境 (feedback-test-must-carry-its-own-fixture):
全部用临时 DuckDB 连接 / 临时目录构造, 不读 data/*.duckdb 或 data/lineage/ 的真实内容
(那些是活库/并发改动中的文件, 单测绝不依赖其当前状态)。

锁的行为:
  1. classify_hash_column: 已知角色 / out_of_scope / 新面 UNCLASSIFIED (发现式扫描配套)
  2. discover_lineage_stamp_records: 只认 dataset_id+config_hash 同时出现的 dict 节点;
     其余 hash-like key (无 dataset_id 兄弟) 不算戳记录
  3. check_pointer_vs_contract: 当前 contract_version 组 hash 不符 → FAIL;
     旧 contract_version 组即使 hash 不同也不查 (设计如此, 不是漏检)
  4. check_canonical_vs_pointer: canonical.config_hash 与其 accepted_partition 指针
     不符 → FAIL (对应今天第 1/2 次事故: 补了 pointer 漏了 canonical)
  5. check_lineage_snapshots: 未豁免的落盘快照 hash 漂移 → FAIL; 豁免文件不查
     (对应今天第 3 次事故)
  6. check_ingest_batch_derivation: payload_hash 用产线真实函数重算; 只改
     contract_hash/config_hash 而不重算 payload_hash → 重算不吻合 → FAIL
     (对应今天第 4 次事故); 全部字段一致时 PASS; 不要求 contract_hash/config_hash
     等于"当前"契约 (ingest_batch 允许停在旧值, 这是设计不是缺陷)
  7. run_all_checks 端到端: 一个自建的"处处一致"多域快照 → 全绿; 对该快照逐一注入
     四类真实事故形状的缺陷 → 每次都能在对应 check 编号下产出 FAIL
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from conftest import duck_mem  # noqa: E402

from services.data_sources.security_day_partition import (  # noqa: E402
    sha256_text,
    stable_json,
)
from services.data_sources.margin_validation import _batch_payload_hash  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_contract_stamp_consistency",
    REPO / "backend" / "scripts" / "check_contract_stamp_consistency.py",
)
ccsc = importlib.util.module_from_spec(_spec)
# 3.13 dataclass 解析 cls.__module__ 时要求它已在 sys.modules (否则
# `_is_type` 在 `sys.modules.get(cls.__module__).__dict__` 上炸 AttributeError) —
# 必须在 exec_module 之前注册, DomainSpec/Finding 两个 @dataclass 才能装饰成功。
sys.modules[_spec.name] = ccsc
_spec.loader.exec_module(ccsc)


# ============================================================================
# Helpers — 构造最小 schema 的临时表 (只含本门实际用到的列)
# ============================================================================


def _make_accepted_partition(conn):
    conn.execute(
        """
        CREATE TABLE accepted_partition (
            dataset_id VARCHAR, partition_value VARCHAR, batch_id VARCHAR,
            contract_version VARCHAR, contract_hash VARCHAR, config_hash VARCHAR,
            row_count BIGINT, content_hash VARCHAR,
            observed_at TIMESTAMPTZ, available_at TIMESTAMPTZ, accepted_at TIMESTAMPTZ
        )
        """
    )


def _make_ingest_batch(conn):
    conn.execute(
        """
        CREATE TABLE ingest_batch (
            batch_id VARCHAR, dataset_id VARCHAR, contract_version VARCHAR,
            contract_hash VARCHAR, config_hash VARCHAR, writer_id VARCHAR,
            partition_value VARCHAR, source_name VARCHAR, status VARCHAR,
            request_json VARCHAR, fragment_outcomes_json VARCHAR,
            landing_row_count BIGINT, payload_hash VARCHAR, canonical_hash VARCHAR,
            observed_at TIMESTAMPTZ, available_at TIMESTAMPTZ,
            landed_at TIMESTAMPTZ, accepted_at TIMESTAMPTZ
        )
        """
    )


def _make_canonical(conn, table: str, join_column: str):
    conn.execute(
        f'CREATE TABLE "{table}" ('
        f'{join_column} VARCHAR, config_hash VARCHAR, source_row_hash VARCHAR)'
    )


def _make_simple_landing(conn, table: str):
    conn.execute(
        f'CREATE TABLE "{table}" (batch_id VARCHAR, row_ordinal INTEGER, '
        f"request_json VARCHAR, payload_json VARCHAR, row_hash VARCHAR)"
    )


def _make_margin_landing(conn):
    conn.execute(
        "CREATE TABLE landing_tushare_margin (batch_id VARCHAR, fragment_exchange_id VARCHAR, "
        "fragment_ordinal INTEGER, row_ordinal INTEGER, request_json VARCHAR, "
        "payload_json VARCHAR, row_hash VARCHAR)"
    )


_T0 = datetime(2026, 8, 1, 9, 0, 0, tzinfo=timezone.utc)


def _stub_contract(dataset_id: str, contract_version: str, contract_hash: str, config_hash: str):
    return SimpleNamespace(
        dataset_id=dataset_id, contract_version=contract_version,
        contract_hash=contract_hash, config_hash=config_hash,
    )


def _stub_domain(**overrides):
    base = dict(
        name="widget", dataset_id="tier0.test.widget", db_alias="mem",
        loader_module="", loader_func="", canonical_table="canonical_widget",
        join_column="ingest_batch_id", payload_family="simple",
        landing_table="landing_widget",
    )
    base.update(overrides)
    return ccsc.DomainSpec(**base)


# ============================================================================
# 1. classify_hash_column — 发现式扫描配套分类器
# ============================================================================


def test_classify_hash_column_known_roles():
    assert ccsc.classify_hash_column("accepted_partition", "contract_hash") == "contract_stamp_pointer"
    assert ccsc.classify_hash_column("accepted_partition", "config_hash") == "contract_stamp_pointer"
    assert ccsc.classify_hash_column("ingest_batch", "payload_hash") == "ingest_batch_derived_hash"
    assert ccsc.classify_hash_column("canonical_margin_exchange_daily", "config_hash") == "canonical_stamp"
    assert ccsc.classify_hash_column("landing_tushare_daily", "row_hash") == "row_content_hash"


def test_classify_hash_column_out_of_scope_registered():
    assert ccsc.classify_hash_column("mart_data_lineage", "sql_hash") == "out_of_scope"


def test_classify_hash_column_unclassified_is_reported_not_silent():
    """新面: 一个既不匹配已知精确列, 也不匹配 canonical_/landing_ 前缀规则的表.列 →
    必须是 UNCLASSIFIED (调用方据此发 WARN), 不能悄悄归类成任何已知角色。"""
    assert ccsc.classify_hash_column("mart_data_source_watermark", "some_new_hash_col") == "UNCLASSIFIED"
    assert ccsc.classify_hash_column("totally_unknown_table", "weird_hash") == "UNCLASSIFIED"


# ============================================================================
# 2. discover_lineage_stamp_records — 发现式 JSON 扫描
# ============================================================================


def test_discover_lineage_stamp_records_finds_dataset_id_config_hash_pairs(tmp_path):
    f = tmp_path / "snap.json"
    f.write_text(
        json.dumps(
            {
                "frozen_at": "2026-08-01T00:00:00Z",
                "domains": {
                    "widget": {
                        "dataset_id": "tier0.test.widget",
                        "config_hash": "a" * 64,
                        "contract_hash": "b" * 64,
                        "contract_version": "1",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    by_file, errors = ccsc.discover_lineage_stamp_records(tmp_path)
    assert errors == []
    assert f in by_file
    recs = by_file[f]
    assert len(recs) == 1
    assert recs[0]["dataset_id"] == "tier0.test.widget"
    assert recs[0]["config_hash"] == "a" * 64


def test_discover_lineage_stamp_records_ignores_hash_without_dataset_id_sibling(tmp_path):
    """一个 config_hash 字段但没有 dataset_id 兄弟 key (例如 holdout/policy 类 hash) →
    不是戳记录, 不应被当成契约戳处理。"""
    f = tmp_path / "unrelated.json"
    f.write_text(
        json.dumps({"policy_hash": "c" * 64, "payload": {"config_hash": "d" * 64, "block": "B0"}}),
        encoding="utf-8",
    )
    by_file, errors = ccsc.discover_lineage_stamp_records(tmp_path)
    assert errors == []
    assert by_file == {}


def test_discover_lineage_stamp_records_reports_parse_errors(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json", encoding="utf-8")
    by_file, errors = ccsc.discover_lineage_stamp_records(tmp_path)
    assert by_file == {}
    assert len(errors) == 1 and errors[0][0] == bad


# ============================================================================
# 3. check_pointer_vs_contract — check ①
# ============================================================================


def test_check_pointer_vs_contract_pass():
    conn = duck_mem()
    try:
        _make_accepted_partition(conn)
        conn.execute(
            "INSERT INTO accepted_partition VALUES "
            "('tier0.test.widget','20260801','b1','1','h_contract','h_config',10,'h_content',?,?,?)",
            [_T0, _T0, _T0],
        )
        domain = _stub_domain()
        contract = _stub_contract("tier0.test.widget", "1", "h_contract", "h_config")
        findings = ccsc.check_pointer_vs_contract(conn, domain, contract)
        assert not any(f.severity == "FAIL" for f in findings)
    finally:
        conn.close()


def test_check_pointer_vs_contract_fail_on_current_version_drift():
    """今天第 1/2 次事故的核心形状: 当前 contract_version 组里的 stamp 与现算契约不符。"""
    conn = duck_mem()
    try:
        _make_accepted_partition(conn)
        conn.execute(
            "INSERT INTO accepted_partition VALUES "
            "('tier0.test.widget','20260801','b1','1','STALE_HASH','h_config',10,'h_content',?,?,?)",
            [_T0, _T0, _T0],
        )
        domain = _stub_domain()
        contract = _stub_contract("tier0.test.widget", "1", "h_contract_NEW", "h_config")
        findings = ccsc.check_pointer_vs_contract(conn, domain, contract)
        fails = [f for f in findings if f.severity == "FAIL"]
        assert len(fails) == 1
        assert "1_pointer" in fails[0].check
    finally:
        conn.close()


def test_check_pointer_vs_contract_ignores_old_contract_version_group():
    """设计如此, 不是漏检: 旧 contract_version 组允许保留旧 hash (margin v2/top10 v2 同型)。"""
    conn = duck_mem()
    try:
        _make_accepted_partition(conn)
        conn.execute(
            "INSERT INTO accepted_partition VALUES "
            "('tier0.test.widget','20240101','b_old','2','OLD_HASH','OLD_CONFIG',10,'h',?,?,?)",
            [_T0, _T0, _T0],
        )
        conn.execute(
            "INSERT INTO accepted_partition VALUES "
            "('tier0.test.widget','20260801','b_new','3','h_contract','h_config',10,'h',?,?,?)",
            [_T0, _T0, _T0],
        )
        domain = _stub_domain()
        contract = _stub_contract("tier0.test.widget", "3", "h_contract", "h_config")
        findings = ccsc.check_pointer_vs_contract(conn, domain, contract)
        assert not any(f.severity == "FAIL" for f in findings)
    finally:
        conn.close()


# ============================================================================
# 4. check_canonical_vs_pointer — check ②  (今天第 1/2 次事故)
# ============================================================================


def test_check_canonical_vs_pointer_pass():
    conn = duck_mem()
    try:
        _make_accepted_partition(conn)
        _make_canonical(conn, "canonical_widget", "ingest_batch_id")
        conn.execute(
            "INSERT INTO accepted_partition VALUES "
            "('tier0.test.widget','20260801','b1','1','h_contract','h_config',10,'h',?,?,?)",
            [_T0, _T0, _T0],
        )
        conn.execute("INSERT INTO canonical_widget VALUES ('b1','h_config','rowhash')")
        domain = _stub_domain()
        contract = _stub_contract("tier0.test.widget", "1", "h_contract", "h_config")
        findings = ccsc.check_canonical_vs_pointer(conn, domain, contract)
        assert not any(f.severity == "FAIL" for f in findings)
    finally:
        conn.close()


def test_check_canonical_vs_pointer_fail_when_canonical_not_restamped():
    """精确复现今天第 1/2 次事故: pointer 已经打到新契约, canonical 还留在旧 config_hash。"""
    conn = duck_mem()
    try:
        _make_accepted_partition(conn)
        _make_canonical(conn, "canonical_widget", "ingest_batch_id")
        conn.execute(
            "INSERT INTO accepted_partition VALUES "
            "('tier0.test.widget','20260801','b1','1','h_contract_NEW','h_config_NEW',10,'h',?,?,?)",
            [_T0, _T0, _T0],
        )
        conn.execute("INSERT INTO canonical_widget VALUES ('b1','h_config_OLD','rowhash')")
        domain = _stub_domain()
        contract = _stub_contract("tier0.test.widget", "1", "h_contract_NEW", "h_config_NEW")
        findings = ccsc.check_canonical_vs_pointer(conn, domain, contract)
        fails = [f for f in findings if f.severity == "FAIL"]
        assert len(fails) == 1
        assert "canonical" in fails[0].detail
    finally:
        conn.close()


def test_check_canonical_vs_pointer_missing_table_is_warn_not_silent():
    conn = duck_mem()
    try:
        _make_accepted_partition(conn)
        domain = _stub_domain(canonical_table="canonical_does_not_exist")
        contract = _stub_contract("tier0.test.widget", "1", "h_contract", "h_config")
        findings = ccsc.check_canonical_vs_pointer(conn, domain, contract)
        assert len(findings) == 1 and findings[0].severity == "WARN"
    finally:
        conn.close()


# ============================================================================
# 5. check_lineage_snapshots — check ③ (今天第 3 次事故)
# ============================================================================


def test_check_lineage_snapshots_fail_when_unexempted_drift(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "services.data_sources.widget_contract",
        SimpleNamespace(
            load_widget_contract=lambda: _stub_contract(
                "tier0.test.widget", "1", "h_contract_NEW", "h_config_NEW"
            )
        ),
    )
    domain = _stub_domain(loader_module="services.data_sources.widget_contract", loader_func="load_widget_contract")
    orig_registry = ccsc.DOMAIN_BY_DATASET_ID.copy()
    ccsc.DOMAIN_BY_DATASET_ID["tier0.test.widget"] = domain
    try:
        f = tmp_path / "stale_snapshot.json"
        f.write_text(
            json.dumps({"dataset_id": "tier0.test.widget", "config_hash": "h_config_OLD"}),
            encoding="utf-8",
        )
        findings = ccsc.check_lineage_snapshots(tmp_path, exemptions={})
        fails = [x for x in findings if x.severity == "FAIL"]
        assert len(fails) == 1
        assert "3_lineage" in fails[0].check
    finally:
        ccsc.DOMAIN_BY_DATASET_ID.clear()
        ccsc.DOMAIN_BY_DATASET_ID.update(orig_registry)


def test_check_lineage_snapshots_exempted_file_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "services.data_sources.widget_contract",
        SimpleNamespace(
            load_widget_contract=lambda: _stub_contract(
                "tier0.test.widget", "1", "h_contract_NEW", "h_config_NEW"
            )
        ),
    )
    domain = _stub_domain(loader_module="services.data_sources.widget_contract", loader_func="load_widget_contract")
    orig_registry = ccsc.DOMAIN_BY_DATASET_ID.copy()
    ccsc.DOMAIN_BY_DATASET_ID["tier0.test.widget"] = domain
    try:
        f = tmp_path / "frozen_snapshot.json"
        rel = str(f.relative_to(REPO)) if str(f).startswith(str(REPO)) else str(f)
        f.write_text(
            json.dumps({"dataset_id": "tier0.test.widget", "config_hash": "h_config_OLD"}),
            encoding="utf-8",
        )
        findings = ccsc.check_lineage_snapshots(
            tmp_path, exemptions={rel: "frozen_at test fixture, intentionally pinned"}
        )
        assert not any(x.severity == "FAIL" for x in findings)
        assert any(x.severity == "INFO" and "豁免" in x.detail for x in findings)
    finally:
        ccsc.DOMAIN_BY_DATASET_ID.clear()
        ccsc.DOMAIN_BY_DATASET_ID.update(orig_registry)


def test_check_lineage_snapshots_unregistered_dataset_id_warns():
    """发现了戳记录但 dataset_id 不在登记表里 → WARN 报出来 (今天教训: 清单不全要报告不能吞)。"""
    tmp = Path(__file__).resolve()
    # 用真实 tmp_path 风格但避免额外 fixture 参数化, 直接内联建目录
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        f = d / "unknown_domain.json"
        f.write_text(
            json.dumps({"dataset_id": "tier0.test.never_registered", "config_hash": "x" * 64}),
            encoding="utf-8",
        )
        findings = ccsc.check_lineage_snapshots(d, exemptions={})
        warns = [x for x in findings if x.severity == "WARN" and x.check == "discovery"]
        assert len(warns) == 1
        assert "tier0.test.never_registered" in warns[0].detail


# ============================================================================
# 6. check_ingest_batch_derivation — check ④ (今天第 4 次事故)
# ============================================================================


def _build_simple_family_batch(conn, *, batch_id="b1", contract_hash="h_contract", config_hash="h_config"):
    """用产线真实 stable_json/sha256_text 构造一个自洽的 ingest_batch + landing 行,
    这样测试期望值不是硬编码的魔法字符串, 而是用同一套函数派生的 (若产线公式改了,
    这个测试也会同步失败, 不会悄悄测一个过时的假设)。"""
    request = {"api": "test", "trade_date": "20260801"}
    rows = [{"a": 1}, {"a": 2}]
    signatures = []
    for ordinal, row in enumerate(rows, start=1):
        payload_json = stable_json(row)
        row_hash = sha256_text(payload_json)
        signatures.append(f"{ordinal}:{row_hash}")
        conn.execute(
            "INSERT INTO landing_widget VALUES (?, ?, ?, ?, ?)",
            [batch_id, ordinal, stable_json(request), payload_json, row_hash],
        )
    payload_hash = sha256_text(
        stable_json(
            {
                "partition": "20260801", "source": "testsrc", "contract_version": "1",
                "contract_hash": contract_hash, "config_hash": config_hash,
                "observed_at": _T0.isoformat(), "available_at": _T0.isoformat(),
                "request": request, "row_signatures": signatures,
            }
        )
    )
    conn.execute(
        "INSERT INTO ingest_batch VALUES (?, 'tier0.test.widget', '1', ?, ?, 'w', "
        "'20260801', 'testsrc', 'ACCEPTED', ?, '[]', 2, ?, NULL, ?, ?, ?, ?)",
        [batch_id, contract_hash, config_hash, stable_json(request), payload_hash, _T0, _T0, _T0, _T0],
    )
    return payload_hash


def test_check_ingest_batch_derivation_pass_when_self_consistent():
    conn = duck_mem()
    try:
        _make_ingest_batch(conn)
        _make_simple_landing(conn, "landing_widget")
        _build_simple_family_batch(conn)
        domain = _stub_domain()
        findings = ccsc.check_ingest_batch_derivation(conn, domain, sample_per_domain=10)
        assert not any(f.severity == "FAIL" for f in findings)
    finally:
        conn.close()


def test_check_ingest_batch_derivation_fail_when_config_hash_touched_without_payload_resync():
    """精确复现今天第 4 次事故: 有人把 ingest_batch.config_hash "同步"成新契约的值,
    但没有(也不可能干净地)重算 payload_hash → payload_hash 重算不吻合 → FAIL。
    这也验证了 fable 的纠偏: 门不要求 contract_hash/config_hash 等于"当前"契约,
    只要求 payload_hash 这条派生链自洽 —— 这里我们让它们等于一个全新值来模拟"被同步过"。"""
    conn = duck_mem()
    try:
        _make_ingest_batch(conn)
        _make_simple_landing(conn, "landing_widget")
        _build_simple_family_batch(conn, contract_hash="h_contract_ORIGINAL", config_hash="h_config_ORIGINAL")
        # 模拟"同步"动作: 直接 UPDATE contract_hash/config_hash, payload_hash 原样未动。
        conn.execute(
            "UPDATE ingest_batch SET contract_hash='h_contract_SYNCED', config_hash='h_config_SYNCED' "
            "WHERE batch_id='b1'"
        )
        domain = _stub_domain()
        findings = ccsc.check_ingest_batch_derivation(conn, domain, sample_per_domain=10)
        fails = [f for f in findings if f.severity == "FAIL"]
        assert len(fails) == 1
        assert "4_ingest_batch" in fails[0].check
        assert "payload_hash" in fails[0].detail
    finally:
        conn.close()


def test_check_ingest_batch_derivation_does_not_require_current_contract_match():
    """fable 的纠偏落地成断言: contract_hash/config_hash 停在旧值 (从不 sync 到当前契约)
    时, 只要 payload_hash 派生链本身自洽, 就必须 PASS —— 这是"允许", 不是"缺陷"。"""
    conn = duck_mem()
    try:
        _make_ingest_batch(conn)
        _make_simple_landing(conn, "landing_widget")
        _build_simple_family_batch(conn, contract_hash="h_contract_ANCIENT", config_hash="h_config_ANCIENT")
        domain = _stub_domain()
        findings = ccsc.check_ingest_batch_derivation(conn, domain, sample_per_domain=10)
        assert not any(f.severity == "FAIL" for f in findings)
    finally:
        conn.close()


def test_check_ingest_batch_derivation_margin_family_red_green():
    conn = duck_mem()
    try:
        _make_ingest_batch(conn)
        _make_margin_landing(conn)
        requests = [{"fragment_exchange_id": "SSE", "request": {"exchange_id": "SSE", "trade_date": "20260801"}}]
        outcomes = [{"exchange_id": "SSE", "status": "success", "row_count": 1, "error_type": None, "error_detail": None}]
        payload_json = stable_json({"close": 10.0})
        row_hash = sha256_text(payload_json)
        conn.execute(
            "INSERT INTO landing_tushare_margin VALUES ('b1','SSE',1,1,?,?,?)",
            [stable_json(requests[0]["request"]), payload_json, row_hash],
        )
        signatures = [f"1:1:{row_hash}"]
        payload_hash = _batch_payload_hash(
            partition="20260801", source="tushare", contract_version="3",
            contract_hash="h_contract", config_hash="h_config",
            observed_at=_T0, available_at=_T0, requests=requests, outcomes=outcomes,
            row_signatures=signatures,
        )
        conn.execute(
            "INSERT INTO ingest_batch VALUES ('b1', 'tier0.test.margin_widget', '3', 'h_contract', "
            "'h_config', 'w', '20260801', 'tushare', 'ACCEPTED', ?, ?, 1, ?, NULL, ?, ?, ?, ?)",
            [stable_json(requests), stable_json(outcomes), payload_hash, _T0, _T0, _T0, _T0],
        )
        domain = _stub_domain(
            name="margin_widget", dataset_id="tier0.test.margin_widget", payload_family="margin",
        )
        findings = ccsc.check_ingest_batch_derivation(conn, domain, sample_per_domain=10)
        assert not any(f.severity == "FAIL" for f in findings)

        # 造靶: 只改 config_hash, payload_hash 原样不动
        conn.execute("UPDATE ingest_batch SET config_hash='h_config_TAMPERED' WHERE batch_id='b1'")
        findings2 = ccsc.check_ingest_batch_derivation(conn, domain, sample_per_domain=10)
        fails = [f for f in findings2 if f.severity == "FAIL"]
        assert len(fails) == 1
    finally:
        conn.close()


# ============================================================================
# 7. run_all_checks 端到端: 一致快照全绿 + 四类事故逐一注入必须转红
# ============================================================================


def _build_consistent_universe(tmp_path):
    """一个自建的、处处一致的最小宇宙: 1 个 db_alias, 1 个域, accepted_partition +
    canonical + ingest_batch(+landing) 全部对齐同一份现算契约; 外加一份一致的 lineage
    快照。返回 (conn, domain, contract, lineage_dir) 供各缺陷场景复用/篡改。"""
    conn = duck_mem()
    _make_accepted_partition(conn)
    _make_ingest_batch(conn)
    _make_canonical(conn, "canonical_widget", "ingest_batch_id")
    _make_simple_landing(conn, "landing_widget")

    contract_hash, config_hash = "h_contract_v1", "h_config_v1"
    payload_hash = _build_simple_family_batch(
        conn, batch_id="b1", contract_hash=contract_hash, config_hash=config_hash
    )
    conn.execute(
        "INSERT INTO accepted_partition VALUES "
        "('tier0.test.widget','20260801','b1','1',?,?,2,'h_content',?,?,?)",
        [contract_hash, config_hash, _T0, _T0, _T0],
    )
    conn.execute("INSERT INTO canonical_widget VALUES ('b1', ?, 'rowhash')", [config_hash])

    lineage_dir = tmp_path / "lineage"
    lineage_dir.mkdir()
    (lineage_dir / "snapshot.json").write_text(
        json.dumps({"dataset_id": "tier0.test.widget", "config_hash": config_hash}),
        encoding="utf-8",
    )

    domain = _stub_domain(
        loader_module="services.data_sources.widget_contract_stub",
        loader_func="load_widget_contract",
    )
    contract = _stub_contract("tier0.test.widget", "1", contract_hash, config_hash)
    return conn, domain, contract, lineage_dir, payload_hash


@pytest.fixture
def consistent_universe(tmp_path, monkeypatch):
    conn, domain, contract, lineage_dir, payload_hash = _build_consistent_universe(tmp_path)
    monkeypatch.setitem(
        sys.modules, "services.data_sources.widget_contract_stub",
        SimpleNamespace(load_widget_contract=lambda: contract),
    )
    orig_registry = ccsc.DOMAIN_REGISTRY
    orig_by_id = ccsc.DOMAIN_BY_DATASET_ID.copy()
    ccsc.DOMAIN_REGISTRY = (domain,)
    ccsc.DOMAIN_BY_DATASET_ID.clear()
    ccsc.DOMAIN_BY_DATASET_ID[domain.dataset_id] = domain
    try:
        yield conn, domain, contract, lineage_dir, payload_hash
    finally:
        conn.close()
        ccsc.DOMAIN_REGISTRY = orig_registry
        ccsc.DOMAIN_BY_DATASET_ID.clear()
        ccsc.DOMAIN_BY_DATASET_ID.update(orig_by_id)


def _run(conn, lineage_dir, *, only=None):
    return ccsc.run_all_checks(
        only=only,
        db_override={"mem": ":inject:"},
        lineage_dir=lineage_dir,
        lineage_exemptions={},
        domains=ccsc.DOMAIN_REGISTRY,
        sample_per_domain=10,
    )


def test_run_all_checks_all_green_on_consistent_universe(consistent_universe, monkeypatch):
    conn, domain, contract, lineage_dir, _ = consistent_universe

    def fake_discover_databases(*, db_override=None, relevant_aliases=None):
        return [(domain.db_alias, "mem", conn)]

    monkeypatch.setattr(ccsc, "discover_databases", fake_discover_databases)
    findings = _run(conn, lineage_dir)
    fails = [f for f in findings if f.severity == "FAIL"]
    assert fails == [], f"expected all-green, got: {[f.to_dict() for f in fails]}"


def test_run_all_checks_catches_defect_1_canonical_not_restamped(consistent_universe, monkeypatch):
    """造第 1/2 次事故: canonical 表的 config_hash 改回旧值。"""
    conn, domain, contract, lineage_dir, _ = consistent_universe
    conn.execute("UPDATE canonical_widget SET config_hash = 'h_config_STALE_PRE_MIGRATION'")

    def fake_discover_databases(*, db_override=None, relevant_aliases=None):
        return [(domain.db_alias, "mem", conn)]

    monkeypatch.setattr(ccsc, "discover_databases", fake_discover_databases)
    findings = _run(conn, lineage_dir)
    fails = [f for f in findings if f.severity == "FAIL" and f.check == "2_canonical"]
    assert len(fails) == 1


def test_run_all_checks_catches_defect_3_lineage_snapshot_stale(consistent_universe, monkeypatch):
    """造第 3 次事故: lineage JSON 快照的 hash 改成旧值, 未登记豁免。"""
    conn, domain, contract, lineage_dir, _ = consistent_universe
    snap = lineage_dir / "snapshot.json"
    snap.write_text(
        json.dumps({"dataset_id": "tier0.test.widget", "config_hash": "h_config_STALE_SNAPSHOT"}),
        encoding="utf-8",
    )

    def fake_discover_databases(*, db_override=None, relevant_aliases=None):
        return [(domain.db_alias, "mem", conn)]

    monkeypatch.setattr(ccsc, "discover_databases", fake_discover_databases)
    findings = _run(conn, lineage_dir)
    fails = [f for f in findings if f.severity == "FAIL" and f.check == "3_lineage"]
    assert len(fails) == 1


def test_run_all_checks_catches_defect_4_ingest_batch_hash_touched(consistent_universe, monkeypatch):
    """造第 4 次事故: ingest_batch 的 config_hash 被改动 (payload_hash 未随之重算)。"""
    conn, domain, contract, lineage_dir, _ = consistent_universe
    conn.execute("UPDATE ingest_batch SET config_hash = 'h_config_TAMPERED_SYNC' WHERE batch_id='b1'")

    def fake_discover_databases(*, db_override=None, relevant_aliases=None):
        return [(domain.db_alias, "mem", conn)]

    monkeypatch.setattr(ccsc, "discover_databases", fake_discover_databases)
    findings = _run(conn, lineage_dir)
    fails = [f for f in findings if f.severity == "FAIL" and f.check == "4_ingest_batch"]
    assert len(fails) == 1


def test_run_all_checks_catches_defect_pointer_drift(consistent_universe, monkeypatch):
    """造①: 指针 (accepted_partition) 本身的 hash 被改掉。"""
    conn, domain, contract, lineage_dir, _ = consistent_universe
    conn.execute("UPDATE accepted_partition SET config_hash = 'h_config_POINTER_TAMPERED' WHERE batch_id='b1'")

    def fake_discover_databases(*, db_override=None, relevant_aliases=None):
        return [(domain.db_alias, "mem", conn)]

    monkeypatch.setattr(ccsc, "discover_databases", fake_discover_databases)
    findings = _run(conn, lineage_dir)
    fails = [f for f in findings if f.severity == "FAIL" and f.check == "1_pointer"]
    assert len(fails) == 1


# ============================================================================
# 8. check_file_digest_pins — check ⑤ (2026-09-01 补: fable 发现的第二层, 文件级摘要钉住)
# ============================================================================


def test_file_digest_pin_pass_when_unambiguous_and_matching(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"payload": "v1"}), encoding="utf-8")
    actual = __import__("hashlib").sha256(target.read_bytes()).hexdigest()

    lineage = tmp_path / "lineage"
    lineage.mkdir()
    (lineage / "pointer.json").write_text(
        json.dumps({"snapshot_hash": actual, "snapshot_relpath": str(target.relative_to(REPO)) if str(target).startswith(str(REPO)) else str(target)}),
        encoding="utf-8",
    )
    candidates, orphans = ccsc.discover_file_digest_pins(lineage)
    assert orphans == []
    assert len(candidates) == 1
    assert candidates[0].hash_value == actual


def test_file_digest_pin_fail_when_target_bytes_changed(tmp_path, monkeypatch):
    """精确复现第 5 类事故: 钉住的整文件摘要与目标文件当前字节不符。"""
    target_dir = tmp_path / "somewhere"
    target_dir.mkdir()
    target = target_dir / "snapshot.json"
    target.write_text(json.dumps({"payload": "v1"}), encoding="utf-8")
    stale_hash = "0" * 64  # 明显不等于 target 现算摘要

    lineage = tmp_path / "lineage"
    lineage.mkdir()
    rel = str(target.relative_to(tmp_path))
    (lineage / "manifest.json").write_text(
        json.dumps({"b0_artifact_hash": stale_hash, "b0_artifact_relpath": rel}),
        encoding="utf-8",
    )
    # REPO 解析是相对仓库根的; 用 monkeypatch 把 REPO 换成 tmp_path 让 target 落在其下。
    monkeypatch.setattr(ccsc, "REPO", tmp_path)
    findings = ccsc.check_file_digest_pins(lineage)
    fails = [f for f in findings if f.severity == "FAIL"]
    assert len(fails) == 1
    assert "5_file_digest" in fails[0].check


def test_file_digest_pin_pairs_by_name_affinity_with_multiple_candidates(tmp_path, monkeypatch):
    """一个节点里有 2 对 hash+path 字段 (前缀不同) → 必须按前缀各自配对, 不能瞎配。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    t1 = sub / "one.json"
    t1.write_text("A", encoding="utf-8")
    t2 = sub / "two.json"
    t2.write_text("BB", encoding="utf-8")
    h1 = __import__("hashlib").sha256(t1.read_bytes()).hexdigest()
    h2 = __import__("hashlib").sha256(t2.read_bytes()).hexdigest()

    lineage = tmp_path / "lineage"
    lineage.mkdir()
    (lineage / "multi.json").write_text(
        json.dumps(
            {
                "alpha_hash": h1, "alpha_relpath": "sub/one.json",
                "beta_hash": h2, "beta_relpath": "sub/two.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ccsc, "REPO", tmp_path)
    findings = ccsc.check_file_digest_pins(lineage)
    assert not any(f.severity == "FAIL" for f in findings)
    infos = [f for f in findings if f.severity == "INFO"]
    assert len(infos) == 2  # 两对都各自正确核对通过, 没有互相串对


def test_file_digest_pin_orphan_fields_reported_not_silent(tmp_path, monkeypatch):
    """一个 hash 形字段找不到路径形兄弟 (或反过来) → WARN 报出来, 不是判无关就吞掉
    (对应真实样本 c_b_pit_cutover_readiness.json 的 content_hash/config_hash 与 artifact
    前缀对不上, 本门 2026-09-01 实测证实那不是文件摘要而是别的语义, 但仍必须先报出来
    让人判断, 不能代码里悄悄决定"这个我不管")。"""
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    (lineage / "orphans.json").write_text(
        json.dumps({"config_hash": "a" * 64, "content_hash": "b" * 64, "artifact": "somewhere/x.json"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ccsc, "REPO", tmp_path)
    findings = ccsc.check_file_digest_pins(lineage)
    assert not any(f.severity == "FAIL" for f in findings)
    warns = [f for f in findings if f.severity == "WARN" and "配不到" in f.detail]
    assert len(warns) == 3  # config_hash 孤儿 + content_hash 孤儿 + artifact 孤儿


def test_file_digest_pin_missing_target_file_warns(tmp_path, monkeypatch):
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    (lineage / "dangling.json").write_text(
        json.dumps({"snapshot_hash": "c" * 64, "snapshot_relpath": "data/lineage/does_not_exist.json"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ccsc, "REPO", tmp_path)
    findings = ccsc.check_file_digest_pins(lineage)
    assert not any(f.severity == "FAIL" for f in findings)
    assert any(f.severity == "WARN" and "不存在" in f.detail for f in findings)


# ============================================================================
# 9. discover_databases 缺表 vs 不可达 (2026-09-02 fable 验收发现的恒绿口子修复)
#
# 此前: 库能打开但缺 accepted_partition/ingest_batch 时被静默 conn.close() 丢弃, 不产出
# 任何 finding; 下游因为 conn_by_alias 里没有这个 alias, 报一条措辞为"不可达"的 WARN,
# 而"不可达"意味着排查方向是权限/写锁 —— 完全错误 (库明明连接正常, 只是缺表), 且
# WARN + 0 FAIL 正是本门要根治的恒绿形态本身。
# ============================================================================


def test_discover_databases_missing_tables_is_not_reported_as_unreachable(tmp_path):
    """库能打开、只缺 ingest_batch → 必须是 reason='missing_tables', 详情里明确说
    "库可达但缺表", 不能是听起来无害的"不可达"。"""
    db_path = tmp_path / "only_accepted_partition.duckdb"
    conn = duckdb.connect(str(db_path))
    _make_accepted_partition(conn)
    conn.close()

    results = ccsc.discover_databases(
        db_override={"widget_db": str(db_path)}, relevant_aliases={"widget_db"}
    )
    assert len(results) == 1
    alias, path, status = results[0]
    assert alias == "widget_db"
    assert isinstance(status, ccsc.DbUnavailable)
    assert status.reason == "missing_tables"
    assert "缺表" in status.detail
    assert "不可达" not in status.detail
    assert "ingest_batch" in status.detail


def test_discover_databases_genuinely_unreachable_path_is_reason_unreachable(tmp_path):
    """路径压根打不开 (不存在/损坏) → reason 必须是 'unreachable', 不能混进 missing_tables。"""
    bogus = tmp_path / "does_not_exist_at_all.duckdb"
    results = ccsc.discover_databases(
        db_override={"widget_db": str(bogus)}, relevant_aliases={"widget_db"}
    )
    # 不存在的路径: duckdb.connect 会新建空文件而不是报错 (DuckDB 默认行为), 所以改用
    # 一个确定会报错的场景 —— 指向一个目录而非文件。
    baddir = tmp_path / "a_directory"
    baddir.mkdir()
    results = ccsc.discover_databases(
        db_override={"widget_db": str(baddir)}, relevant_aliases={"widget_db"}
    )
    assert len(results) == 1
    _alias, _path, status = results[0]
    assert isinstance(status, ccsc.DbUnavailable)
    assert status.reason == "unreachable"


def test_discover_databases_does_not_spam_irrelevant_aliases(tmp_path):
    """relevant_aliases 限定了才探测的范围: 传了 {'widget_db'} 就绝不会把 manifest 里
    与本门无关的 alias (market/reference/feature_store 等, 天生没有这两张表) 也当成
    "缺表"报出来制造噪音——它们干脆不出现在结果里。"""
    db_path = tmp_path / "only_accepted_partition.duckdb"
    conn = duckdb.connect(str(db_path))
    _make_accepted_partition(conn)
    conn.close()
    results = ccsc.discover_databases(
        db_override={"widget_db": str(db_path)}, relevant_aliases={"widget_db"}
    )
    aliases_seen = {r[0] for r in results}
    assert aliases_seen == {"widget_db"}


def test_run_all_checks_fails_closed_when_domain_db_missing_ingest_batch(tmp_path, monkeypatch):
    """端到端: 域的 db_alias 指向一个只有 accepted_partition、没有 ingest_batch 的库 →
    run_all_checks 默认必须给该域一个 FAIL (不是 WARN), 且消息说的是"缺表"不是"不可达"。
    这是 fable 验收指出的恒绿口子的直接回归锁。"""
    db_path = tmp_path / "widget_missing_ingest_batch.duckdb"
    conn = duckdb.connect(str(db_path))
    _make_accepted_partition(conn)
    conn.close()

    domain = _stub_domain(db_alias="widget_db")
    monkeypatch.setitem(
        sys.modules, "services.data_sources.widget_contract_stub2",
        SimpleNamespace(
            load_widget_contract=lambda: _stub_contract("tier0.test.widget", "1", "h", "h")
        ),
    )
    domain = _stub_domain(
        db_alias="widget_db",
        loader_module="services.data_sources.widget_contract_stub2",
        loader_func="load_widget_contract",
    )
    findings = ccsc.run_all_checks(
        only={"1", "2", "4"},
        db_override={"widget_db": str(db_path)},
        domains=(domain,),
    )
    fails = [f for f in findings if f.severity == "FAIL"]
    assert len(fails) >= 1
    msgs = " ".join(f.detail for f in fails)
    assert "缺" in msgs and "ingest_batch" in msgs
    assert not any("不可达" in f.detail for f in fails), "缺表不能被措辞成不可达"


def test_run_all_checks_allow_missing_db_downgrades_to_warn(tmp_path, monkeypatch):
    db_path = tmp_path / "widget_missing_ingest_batch2.duckdb"
    conn = duckdb.connect(str(db_path))
    _make_accepted_partition(conn)
    conn.close()

    monkeypatch.setitem(
        sys.modules, "services.data_sources.widget_contract_stub3",
        SimpleNamespace(
            load_widget_contract=lambda: _stub_contract("tier0.test.widget", "1", "h", "h")
        ),
    )
    domain = _stub_domain(
        db_alias="widget_db2",
        loader_module="services.data_sources.widget_contract_stub3",
        loader_func="load_widget_contract",
    )
    findings = ccsc.run_all_checks(
        only={"1", "2", "4"},
        db_override={"widget_db2": str(db_path)},
        domains=(domain,),
        allow_missing_db=True,
    )
    assert not any(f.severity == "FAIL" for f in findings)
    assert any(f.severity == "WARN" and "缺" in f.detail for f in findings)
