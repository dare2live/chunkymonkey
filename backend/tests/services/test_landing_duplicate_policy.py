"""grain 契约 r2 §S1: registry typed 键 (duplicate_rows/multiplicity_index) +
``_prepare_batch_df`` 批内重复三态执法 + 去重台账 ``mart_data_landing_dedup``。

三态 (grain 契约 r2 §2.1/§2.2): 批内同 grain **全同行**的语义，不做"每域必填"——
不写 duplicate_rows/multiplicity_index = kind='none' = fail-closed (出现全同行即报错,
不静默 drop_duplicates(keep='last'))。event = 独立事件 (需 multiplicity_index 落地层
派生序号列); artifact = 去重伪影 (批内 drop_duplicates)。
"""
from __future__ import annotations

import json

import pytest

from services.data_sources import sync_runner as sr
from services.duck_adapter import connect


def _spec(**overrides):
    spec = {
        "domain": "demo",
        "source": "miaoxiang",
        "target_table": "raw_demo",
        "grain": ["ts_code", "trade_date", "price"],
        "batch_mode": "by_trade_date",
        "data_start": "20260101",
        "min_rows_per_batch": 1,
    }
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------
# T1-T6, T10: _write_batch / _prepare_batch_df 三态执法 (直调, 内存 DuckDB)
# ---------------------------------------------------------------------------


def test_t1_undeclared_full_duplicate_raises_and_writes_nothing():
    """未声明 duplicate_rows 时, 批内同 grain 全同行必须 fail-closed 报错, 零写入。"""
    conn = connect(":memory:")
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0},
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0},
    ]
    with pytest.raises(sr.DuplicatePolicyError, match="未声明 duplicate_rows"):
        sr._write_batch(conn, _spec(), rows)

    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='raw_demo'"
    ).fetchone()[0]
    assert exists == 0


def test_t2_undeclared_without_duplicates_writes_normally():
    """隔离: 未声明 duplicate_rows 但批内本来就无重复 grain 时, 行为不受三态影响。"""
    conn = connect(":memory:")
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0},
        {"ts_code": "600000.SH", "trade_date": "20260902", "price": 2.0},
    ]
    written = sr._write_batch(conn, _spec(), rows)
    assert written == 2


def test_t3_artifact_full_duplicate_dedups_and_records_ledger():
    """artifact 域批内全同行按 grain 去重, 去重删除数计入 mart_data_landing_dedup。"""
    conn = connect(":memory:")
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0},
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0},
    ]
    written = sr._write_batch(conn, _spec(duplicate_rows="artifact"), rows)
    assert written == 1

    ledger = conn.execute(
        "SELECT domain, target_table, batch_key, raw_rows, landed_rows, dedup_rows, "
        "duplicate_rows_policy, built_at FROM mart_data_landing_dedup"
    ).fetchall()
    assert len(ledger) == 1
    row = tuple(ledger[0])
    assert row[:7] == ("demo", "raw_demo", "20260901", 2, 1, 1, "artifact")
    assert row[7]  # built_at 非空时间戳字符串


def test_t4_artifact_content_mismatch_raises_before_any_write():
    """隔离 L1: 同 grain 但内容不同 (grain 缺版本/身份轴) 即使声明 artifact 也必须报错,
    不许 keep='last' 盲选 —— 这是版本类问题, 处置是改 grain 不是 dedup。"""
    conn = connect(":memory:")
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0, "note": "a"},
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0, "note": "b"},
    ]
    with pytest.raises(sr.DuplicatePolicyError, match="内容不一致"):
        sr._write_batch(conn, _spec(duplicate_rows="artifact"), rows)

    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='raw_demo'"
    ).fetchone()[0]
    assert exists == 0
    ledger_exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name='mart_data_landing_dedup'"
    ).fetchone()[0]
    assert ledger_exists == 0


def test_t5_event_multiplicity_index_assigns_arrival_order():
    """event 域: multiplicity_index 由落地层按到达顺序派生, 不是重复删除。"""
    conn = connect(":memory:")
    spec = _spec(
        duplicate_rows="event",
        multiplicity_index="seq",
        grain=["ts_code", "trade_date", "price", "seq"],
        write_mode="replace_partition",
        partition_by=["trade_date"],
    )
    a = {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0}
    b = {"ts_code": "600000.SH", "trade_date": "20260901", "price": 2.0}
    rows = [dict(a), dict(a), dict(b)]

    written = sr._write_batch(conn, spec, rows)
    assert written == 3

    seqs = [
        r[0]
        for r in conn.execute(
            "SELECT seq FROM raw_demo WHERE price=1.0 ORDER BY seq"
        ).fetchall()
    ]
    assert seqs == [1, 2]
    seq_b = conn.execute("SELECT seq FROM raw_demo WHERE price=2.0").fetchone()[0]
    assert seq_b == 1

    col_type = conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='raw_demo' AND column_name='seq'"
    ).fetchone()[0]
    assert col_type == "BIGINT"  # 新表由 df (pandas int64) 推断; 现存表走下面 T5b 的 DDL 分支


def test_t5b_existing_table_missing_index_col_gets_integer_not_varchar():
    """追加 A (主会话裁定): 现存表补 multiplicity_index 列必须是 INTEGER, 不是缺省 VARCHAR
    —— 否则 check_grain_uniqueness 的 MIN/MAX 连续性判据按字典序比较 ('10' < '2')。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_demo (ts_code VARCHAR, trade_date VARCHAR, price DOUBLE, "
        "built_at VARCHAR)"
    )
    conn.execute("INSERT INTO raw_demo VALUES ('600000.SH', '20260831', 9.0, 'old')")

    spec = _spec(
        duplicate_rows="event",
        multiplicity_index="seq",
        grain=["ts_code", "trade_date", "price", "seq"],
        write_mode="replace_partition",
        partition_by=["trade_date"],
    )
    a = {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0}
    b = {"ts_code": "600000.SH", "trade_date": "20260901", "price": 2.0}
    rows = [dict(a), dict(a), dict(b)]

    sr._write_batch(conn, spec, rows)

    seq_type = conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='raw_demo' AND column_name='seq'"
    ).fetchone()[0]
    assert seq_type == "INTEGER"

    old_row = conn.execute(
        "SELECT price, seq FROM raw_demo WHERE trade_date='20260831'"
    ).fetchone()
    assert old_row[0] == 9.0
    assert old_row[1] is None


def test_t6_event_provider_returns_index_col_name_raises():
    """multiplicity_index 是落地层派生列, 供应商若已经带同名列必须拒绝 (不是观测量)。"""
    conn = connect(":memory:")
    spec = _spec(
        duplicate_rows="event",
        multiplicity_index="seq",
        grain=["ts_code", "trade_date", "price", "seq"],
        write_mode="replace_partition",
        partition_by=["trade_date"],
    )
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0, "seq": 9},
    ]
    with pytest.raises(ValueError, match="同名列"):
        sr._write_batch(conn, spec, rows)


# ---------------------------------------------------------------------------
# T7a-f: domain_spec()/_duplicate_policy() 校验隔离 (每条只违反一条)
# ---------------------------------------------------------------------------


def _registry_with(entry: dict) -> dict:
    return {
        "defaults": {},
        "sources": {"miaoxiang": {"target_db": "tushare_raw"}},
        "domains": {"demo": entry},
    }


def _t7_base(**overrides) -> dict:
    entry = {
        "source": "miaoxiang",
        "target_table": "raw_demo",
        "grain": ["ts_code", "trade_date", "price", "seq"],
        "duplicate_rows": "event",
        "multiplicity_index": "seq",
        "write_mode": "replace_partition",
    }
    entry.update(overrides)
    return entry


def test_t7a_event_without_index_raises():
    entry = _t7_base(multiplicity_index=None)
    with pytest.raises(ValueError, match="multiplicity_index"):
        sr.domain_spec(_registry_with(entry), "demo")


def test_t7b_index_not_last_element_raises():
    entry = _t7_base(grain=["ts_code", "trade_date", "seq", "price"])
    with pytest.raises(ValueError, match="最后"):
        sr.domain_spec(_registry_with(entry), "demo")


def test_t7c_event_requires_replace_partition_raises():
    entry = _t7_base(write_mode="merge_grain")
    with pytest.raises(ValueError, match="replace_partition"):
        sr.domain_spec(_registry_with(entry), "demo")


def test_t7d_index_without_duplicate_rows_raises():
    entry = _t7_base(duplicate_rows=None)
    with pytest.raises(ValueError, match="duplicate_rows"):
        sr.domain_spec(_registry_with(entry), "demo")


def test_t7e_invalid_duplicate_rows_value_raises():
    entry = _t7_base(duplicate_rows="artefact", multiplicity_index=None)
    with pytest.raises(ValueError, match="duplicate_rows"):
        sr.domain_spec(_registry_with(entry), "demo")


def test_t7f_artifact_with_index_raises():
    entry = _t7_base(duplicate_rows="artifact")
    with pytest.raises(ValueError, match="artifact"):
        sr.domain_spec(_registry_with(entry), "demo")


# ---------------------------------------------------------------------------
# T8: 生产 registry 全域必须过 domain_spec(); block_trade/top_inst 具体断言
# ---------------------------------------------------------------------------


def test_t8_production_registry_domains_pass_and_match_contract():
    registry = sr.load_registry()
    for domain in registry["domains"]:
        sr.domain_spec(registry, domain)  # 不得抛

    limit_list_d = sr.domain_spec(registry, "limit_list_d")
    assert sr._duplicate_policy(limit_list_d).kind == "artifact"

    block_trade = sr.domain_spec(registry, "block_trade")
    policy = sr._duplicate_policy(block_trade)
    assert policy.kind == "event"
    assert policy.index_col == "seq"
    assert block_trade["grain"][-1] == "seq"
    assert block_trade["write_mode"] == "replace_partition"
    assert "page_limit" not in block_trade
    assert block_trade["universe_filter_col"] == "ts_code"
    assert block_trade["source"] == "miaoxiang"

    top_inst = sr.domain_spec(registry, "top_inst")
    policy_ti = sr._duplicate_policy(top_inst)
    assert policy_ti.kind == "none"
    assert top_inst["grain"] == ["trade_date", "ts_code", "reason", "side", "board_rank"]
    assert top_inst["write_mode"] == "replace_partition"


# ---------------------------------------------------------------------------
# T9: run_domain 接线 —— DuplicatePolicyError 走 BatchCompletenessError 既有路由
# ---------------------------------------------------------------------------


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


def test_t9_run_domain_routes_duplicate_policy_error_to_failed_batches(monkeypatch):
    conn = connect(":memory:")
    reg = {
        "defaults": {
            "fetch_timeout_seconds": 120,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]},
            "execution_policy": {"mode": "enabled", "reason": "active"},
        },
        "domains": {"demo": _spec(api="demo")},
    }

    class _Adapter:
        def fetch_raw(self, api, **params):
            return [
                {"ts_code": "600000.SH", "trade_date": params["trade_date"], "price": 1.0},
                {"ts_code": "600000.SH", "trade_date": params["trade_date"], "price": 1.0},
            ]

    recorded: dict = {}
    monkeypatch.setattr(sr, "_adapter", lambda source: _Adapter())
    monkeypatch.setattr(sr, "_target_conn", lambda spec: _NoClose(conn))
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _NoClose(conn))
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: ["20260901"])
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, conn=None: None)
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kwargs: recorded.update(kwargs))
    monkeypatch.setattr(sr.time, "sleep", lambda seconds: None)

    result = sr.run_domain("demo", registry=reg)

    assert result["ok"] is False
    assert result["failed_batches"] == 1
    assert recorded["ok"] is False
    failed_detail = json.loads(recorded["error"])
    assert failed_detail[0]["suspect"] == "batch_incomplete"

    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='raw_demo'"
    ).fetchone()[0]
    assert exists == 0


# ---------------------------------------------------------------------------
# M6 回归锁: 去重台账必须与目标表写入同事务 (目标表写失败 → 台账不得有行)
# ---------------------------------------------------------------------------


def test_t3b_ledger_write_is_atomic_with_target_table_insert():
    """M6 回归锁: 若台账 INSERT 被搬到目标表写事务之外 (如 COMMIT 后/独立提交), 目标表写
    失败时台账会残留孤儿行。正确实现里台账写在同一事务、INSERT 之后 COMMIT 之前, 目标表
    写失败必然回滚台账。"""
    inner = connect(":memory:")

    class _FailInsert:
        _con = inner._con

        def execute(self, sql, params=None):
            if str(sql).lstrip().upper().startswith("INSERT INTO RAW_DEMO"):
                raise RuntimeError("simulated disk write failure")
            return inner.execute(sql, params)

    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0},
        {"ts_code": "600000.SH", "trade_date": "20260901", "price": 1.0},
    ]
    with pytest.raises(RuntimeError, match="simulated disk write failure"):
        sr._write_batch(_FailInsert(), _spec(duplicate_rows="artifact"), rows)

    ledger_exists = inner.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name='mart_data_landing_dedup'"
    ).fetchone()[0]
    # 表可能因 CREATE TABLE IF NOT EXISTS 已执行而存在 (同事务内, 应随 ROLLBACK 撤销);
    # 无论表是否存在, 关键不变量是: 里面不得有行。
    if ledger_exists:
        rows_left = inner.execute("SELECT COUNT(*) FROM mart_data_landing_dedup").fetchone()[0]
        assert rows_left == 0
    with pytest.raises(Exception):
        inner.execute("SELECT * FROM df").fetchall()
