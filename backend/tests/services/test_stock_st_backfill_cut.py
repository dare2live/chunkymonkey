"""ST 历史补数 —— 施工规格附录 A/B/C 的全部新用例 (刀 1/2/3), 登记进一个文件方便
按 spec 附录逐条对照。规格: sandbox/acceptance_cuts_20260918/spec_st_backfill.md。

刀 1 (契约/规则/重打/台账) 对应 A1-A5, A11 (A6/A7 在
``tests/scripts/test_restamp_stock_st_contract.py``, A8/A9/A10/A12 在各自既有
测试文件里就地补, 见 spec §2 允许清单)。
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from services.data_sources.security_day_partition import SecurityDayValidationError

_REAL_ACQUIRE_YAML = (
    Path(__file__).resolve().parents[2] / "config" / "stock_st_acquire.yaml"
)


def _load_real_raw() -> dict:
    return yaml.safe_load(_REAL_ACQUIRE_YAML.read_text(encoding="utf-8"))


def _write_variant(tmp_path: Path, mutate) -> Path:
    raw = _load_real_raw()
    mutate(raw)
    path = tmp_path / "variant.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# A1: loader fail-closed on six malformed variants
# ---------------------------------------------------------------------------


def test_a1_real_config_loads():
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

    rules = load_stock_st_acquire_rules()
    assert "provider_baostock_isst" in rules.st_origin
    assert rules.name_snapshot_timezone == "Asia/Shanghai"


def test_a1_unknown_top_level_key_raises(tmp_path):
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

    def mutate(raw):
        raw["bogus_top_level"] = 1

    path = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match="顶层键"):
        load_stock_st_acquire_rules(path)


def test_a1_missing_reports_name_raises(tmp_path):
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

    def mutate(raw):
        del raw["st_origin"]["provider_baostock_isst"]["reports_name"]

    path = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match=r"st_origin\.provider_baostock_isst"):
        load_stock_st_acquire_rules(path)


def test_a1_dangling_coverage_reference_raises(tmp_path):
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

    def mutate(raw):
        raw["st_origin"]["provider_baostock_isst"]["coverage"] = "nope"

    path = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match=r"st_origin\.provider_baostock_isst\.coverage"):
        load_stock_st_acquire_rules(path)


def test_a1_backfill_value_not_in_st_origin_set_raises(tmp_path):
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

    def mutate(raw):
        raw["backfill_origin_by_source"]["tushare"] = "not_a_real_origin"

    path = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match="backfill_origin_by_source"):
        load_stock_st_acquire_rules(path)


def test_a1_bad_timezone_raises(tmp_path):
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

    def mutate(raw):
        raw["name_snapshot"]["timezone"] = "Mars/Olympus"

    path = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match="timezone"):
        load_stock_st_acquire_rules(path)


def test_a1_bad_cutoff_format_raises(tmp_path):
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

    def mutate(raw):
        raw["name_snapshot"]["attribution_cutoff_local"] = "9:20:00"

    path = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match="attribution_cutoff_local"):
        load_stock_st_acquire_rules(path)


# ---------------------------------------------------------------------------
# A2: schema shape
# ---------------------------------------------------------------------------


def test_a2_schema_v2_shape():
    import yaml as _yaml

    from services.data_sources.stock_st_schema import (
        CONTRACT_VERSION,
        DOMAIN,
        ENRICHMENT_FIELDS,
        SCHEMA_HASH,
        SCHEMA_VERSION,
    )

    assert SCHEMA_VERSION == CONTRACT_VERSION == "2"
    fields_by_name = {f["name"]: f for f in DOMAIN.schema_payload["fields"]}
    assert fields_by_name["name"]["nullable"] is True
    assert fields_by_name["st_origin"]["nullable"] is False
    assert fields_by_name["st_origin"]["origin"] == "system"
    assert ENRICHMENT_FIELDS == ("st_origin",)
    assert DOMAIN.enrichment_validator is not None
    assert SCHEMA_HASH != "5b1c04ad582ebc79265ec077ae35aded40b24cc937372e9dd05d2a0f994273e4"

    registry_path = Path(__file__).resolve().parents[2] / "config" / "sync_registry.yaml"
    registry = _yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    st_partition = registry["domains"]["stock_st"]["security_day_partition"]
    assert str(st_partition["schema_hash"]) == SCHEMA_HASH
    assert str(st_partition["contract_version"]) == "2"


# ---------------------------------------------------------------------------
# A3: enrichment validator
# ---------------------------------------------------------------------------


def test_a3_validator_accepts_baostock_row_without_name():
    from services.data_sources.stock_st_schema import DOMAIN

    DOMAIN.enrichment_validator({"name": None, "st_origin": "provider_baostock_isst"})


def test_a3_validator_rejects_unknown_origin():
    from services.data_sources.stock_st_schema import DOMAIN

    with pytest.raises(SecurityDayValidationError) as exc:
        DOMAIN.enrichment_validator({"name": "ST甲", "st_origin": "xx"})
    assert exc.value.code == "INVALID_ENRICHMENT"


def test_a3_validator_rejects_reports_name_true_with_null_name():
    from services.data_sources.stock_st_schema import DOMAIN

    with pytest.raises(SecurityDayValidationError):
        DOMAIN.enrichment_validator({"name": None, "st_origin": "derived_name_prefix"})


def test_a3_validator_rejects_reports_name_false_with_nonnull_name():
    from services.data_sources.stock_st_schema import DOMAIN

    with pytest.raises(SecurityDayValidationError):
        DOMAIN.enrichment_validator({"name": "ST甲", "st_origin": "provider_baostock_isst"})


# ---------------------------------------------------------------------------
# A4: nullable text field validation in _validate_provider_row
# ---------------------------------------------------------------------------


def test_a4_stock_st_v2_name_none_passes_through_as_none():
    from services.data_sources.security_day_partition import _validate_provider_row
    from services.data_sources.stock_st_schema import DOMAIN

    row = {
        "ts_code": "000001.SZ",
        "trade_date": "20260917",
        "name": None,
        "type": "ST",
        "type_name": "风险警示板",
    }
    out = _validate_provider_row(DOMAIN, row, partition="20260917")
    assert out["name"] is None


def test_a4_non_nullable_text_field_still_rejects_none():
    from services.data_sources.security_day_partition import (
        SecurityDayDomain,
        _validate_provider_row,
    )
    from services.data_sources.stock_st_schema import DOMAIN as ST_DOMAIN

    # 借一个 name nullable=False 的夹具域 (v1 形状), 证明可空性由 schema 声明
    # 决定, 不是 domain.text_fields 成员资格本身决定的。
    v1_fields = tuple(
        {**f, "nullable": False} if f["name"] == "name" else f
        for f in ST_DOMAIN.schema_payload["fields"]
    )
    v1_payload = {**ST_DOMAIN.schema_payload, "fields": v1_fields}
    fixture_domain = SecurityDayDomain(
        **{**ST_DOMAIN.__dict__, "schema_payload": v1_payload, "enrichment_fields": (),
           "enrichment_validator": None},
    )
    row = {
        "ts_code": "000001.SZ", "trade_date": "20260917",
        "name": None, "type": "ST", "type_name": "风险警示板",
    }
    with pytest.raises(SecurityDayValidationError) as exc:
        _validate_provider_row(fixture_domain, row, partition="20260917")
    assert exc.value.code == "EMPTY_TEXT"


def test_a4_empty_string_name_still_rejected_on_v2():
    from services.data_sources.security_day_partition import _validate_provider_row
    from services.data_sources.stock_st_schema import DOMAIN

    row = {
        "ts_code": "000001.SZ", "trade_date": "20260917",
        "name": "", "type": "ST", "type_name": "风险警示板",
    }
    with pytest.raises(SecurityDayValidationError) as exc:
        _validate_provider_row(DOMAIN, row, partition="20260917")
    assert exc.value.code == "EMPTY_TEXT"


# ---------------------------------------------------------------------------
# A5: end-to-end land -> accept on :memory:
# ---------------------------------------------------------------------------


def test_a5_end_to_end_land_then_accept_baostock_origin_rows():
    import duckdb

    from services.data_sources.security_day_partition import SecurityDayLandingBatch
    from services.data_sources.security_day_reader import (
        load_accepted_security_day_partition,
    )
    from services.data_sources.stock_st_contract import load_stock_st_contract
    from services.data_sources.stock_st_runtime import publish_accepted_stock_st_partition
    from services.data_sources.stock_st_schema import DOMAIN

    conn = duckdb.connect(":memory:")
    try:
        contract = load_stock_st_contract()
        observed = datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc)
        rows = [
            {
                "ts_code": "000001.SZ", "trade_date": "20260916",
                "name": None, "type": "ST", "type_name": "风险警示板",
                "st_origin": "provider_baostock_isst",
            },
            {
                "ts_code": "000002.SZ", "trade_date": "20260916",
                "name": None, "type": "ST", "type_name": "风险警示板",
                "st_origin": "provider_baostock_isst",
            },
        ]
        batch = SecurityDayLandingBatch(
            source=contract.source,
            contract_version=contract.contract_version,
            batch_id="test-a5:20260916",
            partition_value="20260916",
            observed_at=observed,
            available_at=observed,
            rows=rows,
            request={"api": "stock_st", "trade_date": "20260916"},
        )
        outcome = publish_accepted_stock_st_partition(conn, batch, contract, bootstrap=True)
        assert outcome.status == "ACCEPTED", outcome.rejection_code

        null_count = conn.execute(
            f"SELECT COUNT(*) FILTER (WHERE name IS NULL), COUNT(*) FROM {DOMAIN.canonical_table}"
        ).fetchone()
        assert null_count[0] == null_count[1] == 2

        partition = load_accepted_security_day_partition(
            conn, DOMAIN,
            observation_date=__import__("datetime").date(2026, 9, 16),
            # accepted_at 是 accept() 调用时刻的真实墙钟 (>= 本测试运行日), 决策时点
            # 必须晚于它才谈得上"可见" —— 用足够远的未来日期, 不依赖跑测试的那天。
            decision_time=datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc),
            contract_hash=contract.contract_hash,
            config_hash=contract.config_hash,
        )
        assert partition.row_count == 2
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A11 (裁剪): 主循环 09-17/18 裁定不新增 check_tushare_sunset.py 检查 10 (台账
# 整体将被 supply 词表替换, 不为设门而设门) —— check_tushare_sunset.py 与其测试
# 文件相对 BASE 零改动。本节只保留台账事实更正本身的回归断言 (真实 YAML 的
# decision/replacement/must_by 字段), 不测任何检查 10 相关函数 (已不存在)。
# ---------------------------------------------------------------------------


def test_a11_ledger_stock_st_entry_corrected_to_derive_stock_st_derive():
    """台账事实更正 (spec §8): decision replace->derive, replacement
    baostock->stock_st_derive, must_by 随 decision 改判一并删除 (检查 6 既有
    字段矩阵: derive 不允许 must_by)。真实两份 YAML (台账 + 注册表) 现在互相
    对得上, 不依赖任何新增检查来断言这件事。"""

    import yaml as _yaml

    sunset_path = Path(__file__).resolve().parents[2] / "config" / "tushare_sunset.yaml"
    registry_path = Path(__file__).resolve().parents[2] / "config" / "sync_registry.yaml"
    sunset_raw = _yaml.safe_load(sunset_path.read_text(encoding="utf-8"))
    registry_raw = _yaml.safe_load(registry_path.read_text(encoding="utf-8"))

    entry = sunset_raw["domains"]["stock_st"]
    assert entry["decision"] == "derive"
    assert entry["replacement"] == "stock_st_derive"
    assert "must_by" not in entry

    registry_source = registry_raw["domains"]["stock_st"]["source"]
    assert registry_source == entry["replacement"]


# ===========================================================================
# 刀 2 (baostock 日 K 水库) —— 附录 B1-B8
# ===========================================================================


def _reservoir_row(ts_code="600000.SH", trade_date="20260901", fetched_at=None, isst="0",
                    fields_csv="date,code,close,preclose,volume,tradestatus,isST"):
    from services.data_sources.baostock_daily_k_reservoir import ReservoirRow

    return ReservoirRow(
        ts_code=ts_code,
        trade_date=trade_date,
        fetched_at=fetched_at or datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc),
        baostock_code="sh.600000" if ts_code.endswith(".SH") else "sz.000001",
        fields_csv=fields_csv,
        payload={"date": "2026-09-01", "isST": isst, "close": "9.35"},
        fetch_context="daily_adapter:20260901",
        request_start=trade_date,
        request_end=trade_date,
    )


# --- B1: schema -------------------------------------------------------------


def test_b1_ensure_schema_creates_table_and_is_idempotent():
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import (
        TABLE,
        ensure_baostock_daily_k_schema,
    )

    conn = duckdb.connect(":memory:")
    try:
        ensure_baostock_daily_k_schema(conn)
        ensure_baostock_daily_k_schema(conn)  # no-op second call
        cols = {r[0] for r in conn.execute(f"DESCRIBE {TABLE}").fetchall()}
        assert "ts_code" in cols and "row_hash" in cols
    finally:
        conn.close()


def test_b1_ensure_schema_raises_on_extra_column():
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import (
        TABLE,
        ReservoirSchemaError,
        ensure_baostock_daily_k_schema,
    )

    conn = duckdb.connect(":memory:")
    try:
        ensure_baostock_daily_k_schema(conn)
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN extra_col VARCHAR")
        with pytest.raises(ReservoirSchemaError):
            ensure_baostock_daily_k_schema(conn)
    finally:
        conn.close()


# --- B2: idempotency / versioning --------------------------------------------


def test_b2_write_idempotent_and_versioned():
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import (
        ReservoirWriteConflictError,
        latest_rows_for_date,
        record_baostock_daily_k_rows,
    )

    conn = duckdb.connect(":memory:")
    try:
        rows = [
            _reservoir_row(ts_code=f"60000{i}.SH", trade_date="20260901")
            for i in range(3)
        ]
        out1 = record_baostock_daily_k_rows(conn, rows)
        assert (out1.rows_seen, out1.rows_inserted, out1.rows_unchanged) == (3, 3, 0)

        out2 = record_baostock_daily_k_rows(conn, rows)
        assert (out2.rows_seen, out2.rows_inserted, out2.rows_unchanged) == (3, 0, 3)

        changed = _reservoir_row(
            ts_code="600000.SH", trade_date="20260901",
            fetched_at=datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),
        )
        changed = changed.__class__(**{**changed.__dict__, "payload": {"date": "2026-09-01", "close": "9.99"}})
        out3 = record_baostock_daily_k_rows(conn, [changed])
        assert (out3.rows_seen, out3.rows_inserted, out3.rows_unchanged) == (1, 1, 0)
        latest = {r.ts_code: r for r in latest_rows_for_date(conn, "20260901")}
        assert latest["600000.SH"].payload["close"] == "9.99"

        conflict = _reservoir_row(
            ts_code="600000.SH", trade_date="20260901",
            fetched_at=datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),
        )
        conflict = conflict.__class__(**{**conflict.__dict__, "payload": {"date": "2026-09-01", "close": "0.01"}})
        with pytest.raises(ReservoirWriteConflictError):
            record_baostock_daily_k_rows(conn, [conflict])
    finally:
        conn.close()


def test_b2_same_payload_at_a_new_fetched_at_counts_as_unchanged_not_a_new_version():
    """红线7 (版本是列不是表名) 的另一半: 服务端没改口 (新一次调用给出与最新一版
    逐字节相同的 payload) 不该被当成"新版本"插进去——``latest`` 分支专门守这个,
    与"同一 (ts_code, trade_date, fetched_at) 三元组重写"(exact 分支) 是两件不同
    的事: 这里 fetched_at 是全新的, 内容却没变。"""
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import record_baostock_daily_k_rows

    conn = duckdb.connect(":memory:")
    try:
        first = _reservoir_row(
            ts_code="600000.SH", trade_date="20260901",
            fetched_at=datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc),
        )
        out1 = record_baostock_daily_k_rows(conn, [first])
        assert (out1.rows_seen, out1.rows_inserted, out1.rows_unchanged) == (1, 1, 0)

        # 全新的 fetched_at (次日再问一次), payload 逐字节相同 —— 服务端没改口。
        same_content_next_day = first.__class__(
            **{**first.__dict__, "fetched_at": datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc)}
        )
        out2 = record_baostock_daily_k_rows(conn, [same_content_next_day])
        assert (out2.rows_seen, out2.rows_inserted, out2.rows_unchanged) == (1, 0, 1)

        row_count = conn.execute(
            "SELECT COUNT(*) FROM raw_baostock_daily_k WHERE ts_code = '600000.SH'"
        ).fetchone()[0]
        assert row_count == 1  # 没多插一个"新版本"
    finally:
        conn.close()


# --- B3: reader ---------------------------------------------------------------


def test_b3_dates_with_isst_rows_requires_isst_in_fields_csv():
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import (
        dates_with_isst_rows,
        record_baostock_daily_k_rows,
    )

    conn = duckdb.connect(":memory:")
    try:
        with_isst = _reservoir_row(
            ts_code="600000.SH", trade_date="20260901",
            fields_csv="date,code,close,preclose,volume,tradestatus,isST",
        )
        without_isst = _reservoir_row(
            ts_code="000001.SZ", trade_date="20260902",
            fields_csv="date,code,close,preclose,volume,tradestatus",
        )
        record_baostock_daily_k_rows(conn, [with_isst, without_isst])
        result = dates_with_isst_rows(
            conn, ["20260901", "20260902", "20260903"], isst_field="isST"
        )
        assert result == {"20260901"}
    finally:
        conn.close()


# --- B5: sync_runner integration ----------------------------------------------


def _daily_spec():
    """真实 registry 派生的 spec (不手搓形状) —— nominal_ohlcv_contract_for_spec
    对 target_db/grain/population_scope/availability_policy 等做严格 transport
    漂移校验, 手搓字典漏一个键就在契约工厂那步炸, 离 B5 真正要测的水库落库逻辑
    还有一步之遥。"""
    from services.data_sources import sync_runner as sr

    return sr.domain_spec(sr.load_registry(), "daily")


class _FakeOutcome:
    def __init__(self, status="ACCEPTED"):
        self.status = status
        self.row_count = 1
        self.batch_id = "test-batch"
        self.partition_value = "20260916"
        self.content_hash = "hash"
        self.rejection_code = None


class _FakeAdapterWithDrain:
    def __init__(self, rows):
        self._rows = rows

    def drain_baostock_daily_k_rows(self):
        rows, self._rows = self._rows, []
        return rows


def _patch_common_publish_scaffolding(monkeypatch, *, rows_for_acquire=1):
    from types import SimpleNamespace

    from services.data_sources import sync_runner as sr
    from services.data_sources.security_day_acquire import SecurityDayAcquireResult

    monkeypatch.setattr(
        sr, "eligible_end_date",
        lambda _spec, **_kwargs: sr.DomainEligibility("20260916", False, "test"),
    )
    monkeypatch.setattr(
        sr, "resolve_operation_window",
        lambda eligibility, **kwargs: SimpleNamespace(
            effective_end=kwargs.get("requested_end") or eligibility.eligible_end
        ),
    )
    monkeypatch.setattr(sr, "apply_fetch_socket_timeout", lambda _spec: None)
    monkeypatch.setattr(
        "services.data_sources.security_day_acquire.resolve_security_day_acquire",
        lambda *_a, **_k: SecurityDayAcquireResult(
            rows=tuple({"ts_code": "600000.SH"} for _ in range(rows_for_acquire)),
            acquire_mode="provider_tushare",
            lineage_note="test",
            source_ref="fuyao:daily_k_dump",
        ),
    )


def test_b5_reservoir_evidence_ok_and_daily_accepted(monkeypatch, tmp_path):
    import duckdb

    from services.data_sources import sync_runner as sr
    from services.data_sources.baostock_daily_k_reservoir import TABLE

    _patch_common_publish_scaffolding(monkeypatch)
    fake_rows = [_reservoir_row(ts_code="600000.SH"), _reservoir_row(ts_code="000001.SZ")]
    adapter = _FakeAdapterWithDrain(fake_rows)
    monkeypatch.setattr(sr, "_adapter", lambda _src: adapter)
    monkeypatch.setattr(
        "services.data_sources.security_day_transport.land_then_accept_authorized_security_day",
        lambda *_a, **_k: _FakeOutcome(),
    )
    db_file = tmp_path / "b5.duckdb"
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: duckdb.connect(str(db_file)))

    out = sr._publish_security_day_accepted_partition(
        "daily", _daily_spec(), trade_date="20260916",
    )
    assert out["status"] == "ok"
    assert out["baostock_daily_k_reservoir"]["status"] == "ok"
    assert out["baostock_daily_k_reservoir"]["rows_inserted"] == 2
    verify = duckdb.connect(str(db_file), read_only=True)
    n = verify.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    verify.close()
    assert n == 2


def test_b5_reservoir_write_failure_does_not_block_daily_accept(monkeypatch):
    import duckdb

    from services.data_sources import sync_runner as sr

    _patch_common_publish_scaffolding(monkeypatch)
    adapter = _FakeAdapterWithDrain([_reservoir_row()])
    monkeypatch.setattr(sr, "_adapter", lambda _src: adapter)
    monkeypatch.setattr(
        "services.data_sources.security_day_transport.land_then_accept_authorized_security_day",
        lambda *_a, **_k: _FakeOutcome(),
    )
    monkeypatch.setattr(
        "services.data_sources.baostock_daily_k_reservoir.record_baostock_daily_k_rows",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("water table is on fire")),
    )
    conn = duckdb.connect(":memory:")
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: conn)

    out = sr._publish_security_day_accepted_partition(
        "daily", _daily_spec(), trade_date="20260916",
    )
    assert out["status"] == "ok"  # daily 分区不受水库失败影响
    assert out["baostock_daily_k_reservoir"]["status"] == "error"
    assert "water table is on fire" in out["baostock_daily_k_reservoir"]["error"]


def test_b5_stock_st_path_reports_not_applicable(monkeypatch):
    from types import SimpleNamespace

    from services.data_sources import sync_runner as sr

    _patch_common_publish_scaffolding(monkeypatch)
    # stock_st_derive 适配器没有 drain_baostock_daily_k_rows —— 一个裸对象即可。
    monkeypatch.setattr(sr, "_adapter", lambda _src: SimpleNamespace())
    monkeypatch.setattr(
        "services.data_sources.security_day_transport.land_then_accept_authorized_security_day",
        lambda *_a, **_k: _FakeOutcome(),
    )
    import duckdb

    conn = duckdb.connect(":memory:")
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: conn)

    st_spec = sr.domain_spec(sr.load_registry(), "stock_st")
    out = sr._publish_security_day_accepted_partition(
        "stock_st", st_spec, trade_date="20260916",
    )
    assert out["baostock_daily_k_reservoir"] == {"status": "not_applicable"}


def test_b5_land_failure_still_commits_reservoir_evidence(monkeypatch, tmp_path):
    """证据先于派生 (红线 4): daily 的 land 抛错时, 水库行已经在自己的事务里提交。"""
    import duckdb

    from services.data_sources import sync_runner as sr
    from services.data_sources.baostock_daily_k_reservoir import TABLE
    from services.data_sources.security_day_partition import SecurityDayError

    _patch_common_publish_scaffolding(monkeypatch)
    adapter = _FakeAdapterWithDrain([_reservoir_row()])
    monkeypatch.setattr(sr, "_adapter", lambda _src: adapter)
    monkeypatch.setattr(
        "services.data_sources.security_day_transport.land_then_accept_authorized_security_day",
        lambda *_a, **_k: (_ for _ in ()).throw(SecurityDayError("boom")),
    )
    db_file = tmp_path / "b5_land_fail.duckdb"
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: duckdb.connect(str(db_file)))

    out = sr._publish_security_day_accepted_partition(
        "daily", _daily_spec(), trade_date="20260916",
    )
    assert out["status"] == "error"
    assert out["baostock_daily_k_reservoir"]["status"] == "ok"
    verify = duckdb.connect(str(db_file), read_only=True)
    n = verify.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    verify.close()
    assert n == 1


# --- B8: registration ---------------------------------------------------------


def test_b8_data_layers_registers_reservoir_table():
    import yaml as _yaml

    path = Path(__file__).resolve().parents[2] / "config" / "data_layers.yaml"
    raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["tables"]["raw_baostock_daily_k"] == "L0_source"


def test_b8_database_manifest_registers_literal_pattern():
    import yaml as _yaml

    path = Path(__file__).resolve().parents[2] / "config" / "database_manifest.yaml"
    raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    patterns = raw["databases"]["tushare_raw"]["table_patterns"]
    assert "raw_baostock_daily_k" in patterns


def test_b8_lineage_builder_resolves_reservoir_table_to_tushare_raw():
    from services.lineage.builder import _registry_table_specs

    assert _registry_table_specs()["raw_baostock_daily_k"] == "tushare_raw"


# --- B7: ingest_baostock_daily_k_reservoir.py -----------------------------------


def _fill_fixture_db(path):
    import duckdb

    from services.data_sources.accepted_schema import create_accepted_evidence_tables

    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE raw_tushare_stock_basic (ts_code VARCHAR, symbol VARCHAR, "
        "name VARCHAR, market VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_stock_basic VALUES "
        "('600001.SH','600001','沪主板甲','主板'), "
        "('000001.SZ','000001','深主板乙','主板'), "
        "('300001.SZ','300001','创业板丙','创业板'), "
        "('830001.BJ','830001','北交所丁','北交所'), "
        "('900001.SH','900001','B股戊','主板')"
    )
    conn.execute(
        "CREATE TABLE canonical_nominal_ohlcv_daily (ts_code VARCHAR, trade_date DATE)"
    )
    conn.execute(
        "INSERT INTO canonical_nominal_ohlcv_daily VALUES ('000002.SZ', '2026-09-01')"
    )
    create_accepted_evidence_tables(conn)
    conn.execute(
        "CREATE TABLE canonical_stock_st_daily (ts_code VARCHAR, trade_date DATE)"
    )
    conn.close()


class _FakeBaostockSessionForFill:
    def __init__(self, rows_by_code=None, raise_by_code=None):
        self._rows_by_code = rows_by_code or {}
        self._raise_by_code = raise_by_code or {}
        self.calls = []
        self.logged_out = False

    def fetch_raw(self, api, **params):
        assert api == "query_history_k_data_plus"
        self.calls.append(dict(params))
        code = params["code"]
        if code in self._raise_by_code:
            raise self._raise_by_code[code]
        return list(self._rows_by_code.get(code, []))

    def logout(self):
        self.logged_out = True


def test_b7_dry_run_code_pool_excludes_bj_and_b_share(tmp_path):
    from scripts.ingest_baostock_daily_k_reservoir import run

    db_file = tmp_path / "fill_fixture.duckdb"
    _fill_fixture_db(db_file)

    result, exit_code = run(
        start="20260901", end="20260901", db_override=str(db_file),
        execute=False, codes_source="both",
    )
    assert exit_code == 0
    assert result["codes_requested"] == 4  # 3 沪深 (stock_basic) + 1 退市码 (canonical)


def test_b7_execute_writes_rows_and_skips_caller_error(tmp_path, monkeypatch):
    import scripts.ingest_baostock_daily_k_reservoir as fill_mod

    db_file = tmp_path / "fill_fixture.duckdb"
    _fill_fixture_db(db_file)

    fake = _FakeBaostockSessionForFill(
        rows_by_code={
            "sh.600001": [
                {"date": "2026-09-01", "code": "sh.600001", "close": "9.0",
                 "preclose": "8.9", "volume": "100", "tradestatus": "1", "isST": "0"},
            ],
            "sz.000002": [
                {"date": "2026-09-01", "code": "sz.000002", "close": "5.0",
                 "preclose": "4.9", "volume": "50", "tradestatus": "1", "isST": "1"},
            ],
        },
        raise_by_code={
            "sz.000001": __import__(
                "services.data_sources.sources.baostock", fromlist=["BaostockQueryError"]
            ).BaostockQueryError("bad code", code="10004011"),
        },
    )
    monkeypatch.setattr(
        "services.data_sources.sources.baostock.BaostockSource", lambda: fake
    )

    result, exit_code = fill_mod.run(
        start="20260901", end="20260901", db_override=str(db_file),
        execute=True, codes_source="both",
    )
    assert exit_code == 0
    assert result["rows_seen"] == 2
    assert result["rows_inserted"] == 2
    assert result["codes_ok"] == 3  # 600001/000002/300001 (300001 返回空)
    skipped = {item["ts_code"]: item["code_class"] for item in result["codes_skipped"]}
    assert skipped["000001.SZ"] == "caller_error"
    assert fake.logged_out is True

    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import TABLE

    verify = duckdb.connect(str(db_file), read_only=True)
    n = verify.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    verify.close()
    assert n == 2


def test_b7_session_level_failure_stops_and_commits_partial_rows(tmp_path, monkeypatch):
    import scripts.ingest_baostock_daily_k_reservoir as fill_mod

    db_file = tmp_path / "fill_fixture.duckdb"
    _fill_fixture_db(db_file)

    # 沪深代码池排序后第一个是 000001.SZ / 000002.SZ / 300001.SZ / 600001.SH
    # (sorted ts_code) —— 让第一个成功、第二个撞会话级错误, 验证"停取但已取行落库"。
    fake = _FakeBaostockSessionForFill(
        rows_by_code={
            "sz.000001": [
                {"date": "2026-09-01", "code": "sz.000001", "close": "3.0",
                 "preclose": "2.9", "volume": "10", "tradestatus": "1", "isST": "0"},
            ],
        },
        raise_by_code={"sz.000002": RuntimeError("connection reset by peer")},
    )
    monkeypatch.setattr(
        "services.data_sources.sources.baostock.BaostockSource", lambda: fake
    )

    result, exit_code = fill_mod.run(
        start="20260901", end="20260901", db_override=str(db_file),
        execute=True, codes_source="both",
    )
    assert exit_code == 3
    assert result["rows_inserted"] == 1
    offending = [item for item in result["codes_skipped"] if item["code_class"] == "session_level"]
    assert len(offending) == 1
    assert offending[0]["ts_code"] == "000002.SZ"
    assert fake.logged_out is True  # 熔断后仍 logout 释放会话锁


# ===========================================================================
# recon_stock_st_membership.py —— spec §7.1 离线等价性 + §6 步4/5 CLI
# ===========================================================================


def test_recon_compare_reports_bj_only_in_accepted_and_nothing_else():
    """夹具: 名称快照 3 ST (沪深) + 2 非 ST (不进 accepted 集合) + 1 .BJ ST, 水库
    5 个沪深码 isST 与名称一致 -> only_accepted == [.BJ 那只], only_reservoir == []。"""
    from scripts.recon_stock_st_membership import compare

    accepted = ["000005.SZ", "000007.SZ", "000410.SZ", "830001.BJ"]
    reservoir = ["000005.SZ", "000007.SZ", "000410.SZ"]
    out = compare(accepted, reservoir)
    assert out.only_accepted == ({"ts_code": "830001.BJ", "exchange": "BJ"},)
    assert out.only_reservoir == ()
    assert out.both_n == 3
    assert out.accepted_n == 4
    assert out.reservoir_n == 3


def test_recon_compare_does_not_swallow_a_flipped_isst_code():
    """把一只沪深码从"两边都有"改成"只在 accepted 侧"(相当于 baostock 那边
    isST 变成了 '0', 不再进 reservoir 集合) -> 该码必须原样出现在 only_accepted
    的沪深段, compare() 不吞、不当成噪音过滤掉。"""
    from scripts.recon_stock_st_membership import compare

    accepted = ["000005.SZ", "000007.SZ", "830001.BJ"]
    reservoir = ["000005.SZ"]  # 000007.SZ 的 isST 被改成 "0", 从 reservoir 集合消失
    out = compare(accepted, reservoir)
    codes = {item["ts_code"] for item in out.only_accepted}
    assert "000007.SZ" in codes
    assert "830001.BJ" in codes
    assert out.only_reservoir == ()


def test_recon_load_accepted_and_reservoir_codes_from_fixture_db():
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import record_baostock_daily_k_rows
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules
    from scripts.recon_stock_st_membership import load_accepted_codes, load_reservoir_member_codes

    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE canonical_stock_st_daily (trade_date DATE, ts_code VARCHAR, st_origin VARCHAR)"
        )
        conn.execute(
            "INSERT INTO canonical_stock_st_daily VALUES "
            "('2026-08-28', '000711.SZ', 'provider_tushare_stock_st'), "
            "('2026-08-28', '002586.SZ', 'provider_tushare_stock_st')"
        )
        rows = [
            _reservoir_row(ts_code="000711.SZ", trade_date="20260828", isst="1"),
            _reservoir_row(ts_code="002586.SZ", trade_date="20260828", isst="0"),
        ]
        record_baostock_daily_k_rows(conn, rows)
        rules = load_stock_st_acquire_rules()

        accepted_codes, accepted_origin = load_accepted_codes(conn, "20260828")
        assert accepted_codes == {"000711.SZ", "002586.SZ"}
        assert accepted_origin == ("provider_tushare_stock_st",)

        reservoir_codes = load_reservoir_member_codes(conn, "20260828", rules)
        assert reservoir_codes == {"000711.SZ"}  # 002586.SZ isST="0" 不是成员
    finally:
        conn.close()


def test_recon_probe_isst_reports_none_when_reservoir_has_no_row_for_date():
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import record_baostock_daily_k_rows
    from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules
    from scripts.recon_stock_st_membership import probe_isst

    conn = duckdb.connect(":memory:")
    try:
        record_baostock_daily_k_rows(
            conn, [_reservoir_row(ts_code="000711.SZ", trade_date="20260828", isst="1")]
        )
        rules = load_stock_st_acquire_rules()
        out = probe_isst(
            conn, [("000711.SZ", "20260828"), ("000711.SZ", "20260831")], rules
        )
        assert out == [
            {"ts_code": "000711.SZ", "date": "20260828", "isST": "1"},
            {"ts_code": "000711.SZ", "date": "20260831", "isST": None},
        ]
    finally:
        conn.close()


def test_recon_cli_run_end_to_end(tmp_path):
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import record_baostock_daily_k_rows
    from scripts.recon_stock_st_membership import run

    db_file = tmp_path / "recon_fixture.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute(
        "CREATE TABLE canonical_stock_st_daily (trade_date DATE, ts_code VARCHAR, st_origin VARCHAR)"
    )
    conn.execute(
        "INSERT INTO canonical_stock_st_daily VALUES "
        "('2026-08-28', '000711.SZ', 'provider_tushare_stock_st'), "
        "('2026-08-28', '830001.BJ', 'provider_tushare_stock_st')"
    )
    record_baostock_daily_k_rows(
        conn, [_reservoir_row(ts_code="000711.SZ", trade_date="20260828", isst="1")]
    )
    conn.close()

    result = run(trade_date="20260828", db_override=str(db_file), probe="000711.SZ:20260828")
    assert result["accepted_n"] == 2
    assert result["reservoir_n"] == 1
    assert result["only_accepted"] == [{"ts_code": "830001.BJ", "exchange": "BJ"}]
    assert result["only_reservoir"] == []
    assert result["isst_probe"] == [{"ts_code": "000711.SZ", "date": "20260828", "isST": "1"}]


# ===========================================================================
# 刀 3 (派生器/计划器) -- 附录 C5/C6 (C1-C4/C7/C8 见 test_stock_st_derive.py /
# test_pipeline.py, 同一逻辑单元的自然位置; 本节只补 sync_runner 接线两条)
# ===========================================================================


def test_c5_fetch_with_retry_reraises_unanswerable_with_zero_sleep_and_one_call():
    """C5: 适配器抛 SourceCannotAnswerDateError -> _fetch_with_retry 原样上抛、
    time.sleep 零调用 (monkeypatch 为 raise)、fetch_raw 只调 1 次。"""
    import pytest as _pytest

    from services.data_sources import sync_runner as sr
    from services.data_sources.fetch_verdict import SourceCannotAnswerDateError

    calls = {"n": 0}

    class _Adapter:
        def fetch_raw(self, api, **params):
            calls["n"] += 1
            raise SourceCannotAnswerDateError(
                trade_date="20260916", reason="no_local_source_for_date", remedy="fill it"
            )

    def _sleep_raises(*_a, **_k):
        raise AssertionError("time.sleep must not be called for SourceCannotAnswerDateError")

    orig_sleep = sr.time.sleep
    sr.time.sleep = _sleep_raises
    try:
        with _pytest.raises(SourceCannotAnswerDateError):
            sr._fetch_with_retry(
                _Adapter(), {"api": "stock_st", "source": "stock_st_derive"},
                {"trade_date": "20260916"},
            )
    finally:
        sr.time.sleep = orig_sleep
    assert calls["n"] == 1


def test_c6_publish_stock_st_returns_typed_unanswerable_zero_ingest_batch(monkeypatch, tmp_path):
    """C6: ``_publish_security_day_accepted_partition("stock_st")`` 在适配器抛
    typed 错时返回 status=='unanswerable'、failed_batches==0、不写 ingest_batch
    (不开写连接)。"""
    from types import SimpleNamespace

    import duckdb

    from services.data_sources import sync_runner as sr
    from services.data_sources.sources.stock_st_derive import StockSTUnanswerableError

    monkeypatch.setattr(
        sr, "eligible_end_date",
        lambda _spec, **_kwargs: sr.DomainEligibility("20260916", False, "test"),
    )
    monkeypatch.setattr(
        sr, "resolve_operation_window",
        lambda eligibility, **kwargs: SimpleNamespace(
            effective_end=kwargs.get("requested_end") or eligibility.eligible_end
        ),
    )
    monkeypatch.setattr(sr, "apply_fetch_socket_timeout", lambda _spec: None)

    class _Adapter:
        def fetch_raw(self, api, **params):
            raise StockSTUnanswerableError(
                trade_date="20260916", reason="no_local_source_for_date", remedy="fill it"
            )

    monkeypatch.setattr(sr, "_adapter", lambda _src: _Adapter())
    db_file = tmp_path / "c6.duckdb"
    conn_opened = {"count": 0}

    def _tracking_target_conn(spec):
        conn_opened["count"] += 1
        return duckdb.connect(str(db_file))

    monkeypatch.setattr(sr, "_target_conn", _tracking_target_conn)

    st_spec = sr.domain_spec(sr.load_registry(), "stock_st")
    out = sr._publish_security_day_accepted_partition(
        "stock_st", st_spec, trade_date="20260916",
    )
    assert out["status"] == "unanswerable"
    assert out["failed_batches"] == 0
    assert out["unanswerable_reason"] == "no_local_source_for_date"
    # 不开写连接: 适配器抛错发生在 resolve_security_day_acquire 内部, 早于
    # `conn = _target_conn(spec)` 那一行 —— _target_conn 从未被调用。
    assert conn_opened["count"] == 0


# ---------------------------------------------------------------------------
# B2 (返修 fix_st_r1.md): 多日窗口不得把 typed unanswerable 的日子聚合成
# status=="ok" —— 断言：两日窗口一可答一不可答 -> window_days_completed==1,
# partition_values 只含可答那天, unanswerable_days 含另一天, status 非 ok,
# 手动 CLI 退出码非 0。变异：把不可答计入完成 -> 红 (见本节末尾的行内变异)。
# ---------------------------------------------------------------------------


def _canned_day_result(trade_date: str, *, unanswerable: bool):
    if unanswerable:
        return {
            "domain": "stock_st",
            "status": "unanswerable",
            "batches": 0,
            "rows": 0,
            "failed_batches": 0,
            "unanswerable": True,
            "unanswerable_reason": "reservoir_coverage_incomplete",
            "remedy": "fill it",
            "partition_value": trade_date,
            "publication": "accepted_security_day",
            "transport": "accepted",
        }
    return {
        "domain": "stock_st",
        "status": "ok",
        "batches": 1,
        "rows": 5,
        "failed_batches": 0,
        "partition_value": trade_date,
        "publication": "accepted_security_day",
        "transport": "accepted",
    }


def test_b2_publish_short_window_excludes_unanswerable_day_from_completion(monkeypatch):
    from services.data_sources import sync_runner as sr

    calls: list[str] = []

    def _fake_publish(domain, spec, *, trade_date, trigger_mode="manual"):
        calls.append(trade_date)
        return _canned_day_result(trade_date, unanswerable=(trade_date == "20260916"))

    monkeypatch.setattr(sr, "_publish_security_day_accepted_partition", _fake_publish)
    st_spec = sr.domain_spec(sr.load_registry(), "stock_st")

    out = sr._publish_security_day_short_window(
        "stock_st", st_spec, trade_dates=["20260916", "20260917"],
    )

    # 两天都被处理过 (不可答不 break, 继续处理窗口里其余的日子)。
    assert calls == ["20260916", "20260917"]
    assert out["window_days_requested"] == 2
    assert out["window_days_completed"] == 1
    assert out["partition_values"] == ["20260917"]
    assert out["unanswerable_days"] == [
        {"trade_date": "20260916", "reason": "reservoir_coverage_incomplete", "remedy": "fill it"}
    ]
    assert out["status"] != "ok"
    assert out["status"] == "partial_unanswerable"
    assert out["failed_batches"] == 0  # 不可答不是失败


def test_b2_publish_short_window_status_ok_when_all_answerable(monkeypatch):
    """隔离用例的对照组: 其它全满足 (两天都可答) 时 status 仍是 ok, 不被本条
    新判据误伤。"""
    from services.data_sources import sync_runner as sr

    def _fake_publish(domain, spec, *, trade_date, trigger_mode="manual"):
        return _canned_day_result(trade_date, unanswerable=False)

    monkeypatch.setattr(sr, "_publish_security_day_accepted_partition", _fake_publish)
    st_spec = sr.domain_spec(sr.load_registry(), "stock_st")

    out = sr._publish_security_day_short_window(
        "stock_st", st_spec, trade_dates=["20260916", "20260917"],
    )
    assert out["status"] == "ok"
    assert out["unanswerable_days"] == []
    assert out["window_days_completed"] == 2


def test_b2_transport_window_land_then_accept_does_not_crash_on_unanswerable(monkeypatch):
    """`_run_security_day_transport_window` 的 land_then_accept 分支之前对
    typed unanswerable 的 land_result 直接取 ``["batch_id"]`` 会 KeyError
    (那份结果没有 batch_id) —— 同一根因 (B2), 同一文件, 一并修。"""
    from services.data_sources import sync_runner as sr

    def _fake_land(domain, spec, *, trade_date, trigger_mode="manual", from_local_raw=False):
        if trade_date == "20260916":
            return _canned_day_result(trade_date, unanswerable=True)
        return {
            "domain": "stock_st", "status": "ok", "batches": 1, "rows": 5,
            "failed_batches": 0, "batch_id": f"batch-{trade_date}",
            "partition_value": trade_date, "publication": "landed_security_day",
        }

    def _fake_accept(domain, *, batch_id, registry=None):
        return {
            "domain": "stock_st", "status": "ok", "batches": 1, "rows": 5,
            "failed_batches": 0, "partition_value": batch_id.replace("batch-", ""),
            "publication": "accepted_security_day",
        }

    monkeypatch.setattr(sr, "_land_security_day_partition", _fake_land)
    monkeypatch.setattr(sr, "accept_security_day_from_landing_batch", _fake_accept)
    monkeypatch.setattr(
        sr, "_require_authorized_short_trade_date_window",
        lambda *_a, **_k: ["20260916", "20260917"],
    )

    out = sr._run_security_day_transport_window(
        "stock_st", transport="land_then_accept", start="20260916", end="20260917",
    )
    assert out["window_days_completed"] == 1
    assert out["partition_values"] == ["20260917"]
    assert out["unanswerable_days"] == [
        {"trade_date": "20260916", "reason": "reservoir_coverage_incomplete", "remedy": "fill it"}
    ]
    assert out["status"] == "partial_unanswerable"


def _cli_args(**overrides):
    import argparse

    values = {
        "domain": "stock_st", "all_due": False, "backfill": False, "resume": False,
        "start": "20260916", "end": "20260917", "drain": False, "max_dates": None,
        "trigger_mode": "manual", "land_only": False, "accept_from_landing": False,
        "land_then_accept": False, "batch_id": None, "from_local_raw": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_b2_cli_exit_code_nonzero_when_window_has_unanswerable_day(monkeypatch):
    """手动 CLI (`chunkyctl sync --domain stock_st --start --end`) 对一个含
    不可答日子的窗口必须退出码非 0 —— 原判据只看 failed_batches (恒 0), 会报
    退出码 0 (假通过)。"""
    from services.data_sources import sync_runner as sr

    monkeypatch.setattr(
        sr, "run_domain",
        lambda d, **_kw: {
            "domain": d, "status": "partial_unanswerable", "failed_batches": 0,
            "window_days_completed": 1, "window_days_requested": 2,
            "unanswerable_days": [{"trade_date": "20260916", "reason": "x", "remedy": "y"}],
        },
    )
    reg = sr.load_registry()
    exit_code = sr._main_unlocked(_cli_args(), reg, ["stock_st"])
    assert exit_code == 1


def test_b2_cli_exit_code_zero_when_window_fully_answerable(monkeypatch):
    """对照组: 全部可答的窗口 (status ok, failed_batches 0) 退出码仍是 0, 不被
    本条新判据误伤。"""
    from services.data_sources import sync_runner as sr

    monkeypatch.setattr(
        sr, "run_domain",
        lambda d, **_kw: {"domain": d, "status": "ok", "failed_batches": 0},
    )
    reg = sr.load_registry()
    exit_code = sr._main_unlocked(_cli_args(), reg, ["stock_st"])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# B3 (返修 fix_st_r1.md): recon_stock_st_membership.run() 对合法 --trade-date
# 崩溃的根因是 spec §6 步 4/5 (本脚本) 排在契约重打 (步 6/7) **之前**——那时
# ``canonical_stock_st_daily`` 还是 v1 形状 (无 st_origin 列), 原代码
# unconditionally SELECT st_origin -> BinderException。隔离用例: 其它全满足
# (合法 trade_date、库里有数据) 只违反"表已重打" -> 原代码红, 修法 (先探列存
# 不存在) 绿。
# ---------------------------------------------------------------------------


def test_b3_recon_run_survives_pre_restamp_v1_schema_without_st_origin_column(tmp_path):
    """B3 逐字对应: 用一个**没有 st_origin 列**的夹具库 (v1 形状, 重打前的真实
    生产表就是这个样子) 跑通 ``run()``——原代码在这里 BinderException 崩溃。
    变异: 把 ``load_accepted_codes`` 的列探测去掉, 直接 SELECT st_origin ->
    本用例红。"""
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import record_baostock_daily_k_rows
    from scripts.recon_stock_st_membership import run

    db_file = tmp_path / "pre_restamp_v1.duckdb"
    conn = duckdb.connect(str(db_file))
    # v1 形状: 重打前的真实字段集, 没有 st_origin (spec §4.2 之前的样子)。
    conn.execute(
        "CREATE TABLE canonical_stock_st_daily "
        "(trade_date DATE, ts_code VARCHAR, name VARCHAR, type VARCHAR, type_name VARCHAR)"
    )
    conn.execute(
        "INSERT INTO canonical_stock_st_daily VALUES "
        "('2026-08-28', '000711.SZ', '*ST甲', 'ST', '风险警示板')"
    )
    record_baostock_daily_k_rows(
        conn, [_reservoir_row(ts_code="000711.SZ", trade_date="20260828", isst="1")]
    )
    conn.close()

    result = run(trade_date="20260828", db_override=str(db_file), probe=None)
    assert result["accepted_n"] == 1
    assert result["accepted_origin"] == ["pre_v2_schema_no_st_origin_column"]
    assert result["reservoir_n"] == 1
    assert result["only_accepted"] == []
    assert result["only_reservoir"] == []


def test_b3_recon_cli_main_exits_zero_against_pre_restamp_fixture(tmp_path, capsys):
    """同上但走真实 CLI 入口 (``main()``), 断言退出码 0 而不是异常向上抛穿。"""
    import duckdb

    from services.data_sources.baostock_daily_k_reservoir import record_baostock_daily_k_rows
    from scripts.recon_stock_st_membership import main

    db_file = tmp_path / "pre_restamp_v1_cli.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute(
        "CREATE TABLE canonical_stock_st_daily "
        "(trade_date DATE, ts_code VARCHAR, name VARCHAR, type VARCHAR, type_name VARCHAR)"
    )
    conn.execute(
        "INSERT INTO canonical_stock_st_daily VALUES ('2026-08-28', '000711.SZ', '*ST甲', 'ST', '风险警示板')"
    )
    record_baostock_daily_k_rows(
        conn, [_reservoir_row(ts_code="000711.SZ", trade_date="20260828", isst="1")]
    )
    conn.close()

    exit_code = main(["--trade-date", "20260828", "--db-override", str(db_file)])
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "pre_v2_schema_no_st_origin_column" in printed


# ---------------------------------------------------------------------------
# B4 (返修 fix_st_r1.md): isST 字段名只从配置取, 代码里 (非注释行) 不留字面
# 量副本——YAML 已声明 reservoir.isst_field, 计划器/执行器各写一份字面量会
# 分歧。静态断言覆盖三个文件; 变异: 把 dates_with_isst_rows 的 isst_field 参
# 数改回字面量 "isST" -> 本用例红。
# ---------------------------------------------------------------------------


def _non_comment_non_docstring_lines(path: Path):
    in_docstring = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.count('"""') % 2 == 1 or stripped.count("'''") % 2 == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        yield lineno, line


def test_b4_isst_field_name_has_no_literal_copy_in_production_code():
    backend_root = Path(__file__).resolve().parents[2]
    targets = [
        backend_root / "services/data_sources/baostock_daily_k_reservoir.py",
        backend_root / "services/data_sources/sources/stock_st_derive.py",
        backend_root / "services/data_sources/sync_runner.py",
        backend_root / "scripts/recon_stock_st_membership.py",
    ]
    offenders = []
    for path in targets:
        for lineno, line in _non_comment_non_docstring_lines(path):
            if "isST" in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == [], "字面量 isST 残留 (应从配置 reservoir.isst_field 取): " + "; ".join(offenders)


def test_b4_dates_with_isst_rows_isst_field_has_no_default():
    """``dates_with_isst_rows`` 的 ``isst_field`` 不能有默认值 (否则调用方悄悄
    漏传也不会报错, 字面量副本换个方式潜回来)。"""
    import inspect

    from services.data_sources.baostock_daily_k_reservoir import dates_with_isst_rows

    sig = inspect.signature(dates_with_isst_rows)
    assert sig.parameters["isst_field"].default is inspect.Parameter.empty
