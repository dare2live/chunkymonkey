"""check_db_invariants 单测 (S4, 2026-09-08)。

结构照 test_check_grain_uniqueness.py: importlib 装载脚本, 用 conftest.duck_mem() 注入
内存连接 (``evaluate_spec(spec, conn)`` 对应 check_grain_uniqueness 的 ``check_table``;
``run_invariants(specs, conn_for)`` 对应 ``run_checks``)。红/绿用例读**真实**
backend/config/db_invariants.yaml (``load_db_invariants()``), 不在本文件重抄一份 SQL——
抄一份就可能默默漂移, 而 R2 (fable S4 §2) 明确要求"缺陷注入到表里, 不许改 YAML 的
expect"; 用真实 spec 跑真实 sql, 只在测试里造/坏表数据, 是这条规则最直接的落地。

建表一律调 writer 自己的 ``ensure_*_schema`` / DDL 常量 (market_schema /
accepted_schema / calendar_schema / org_holding_acceptance / holders_top10_acceptance /
calendar_builder._DIM_DDL), 禁止手抄 CREATE TABLE (2026-09-06 38ecb530a 手抄桩表与
生产者漂移栽过的同一条纪律)。唯一例外: raw_tushare_adj_factor 没有固定 writer schema
函数——sync_runner.py 用 ``CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM df
LIMIT 0`` 从 DataFrame 现场推断 schema (schema-on-write), 不存在可调用的产出方 DDL；
这里按其真实列 (ts_code/trade_date/adj_factor/built_at, 已用 audit_connect 对生产库
DESCRIBE 核对) 手写唯一一张表，不是对已有 DDL 常量的另一份手抄。

calendar 一条不复制真实 data/reference.duckdb (那属于"提交前一次性实弹验收", 已用
--db-override 人工跑过, 见 PR 说明) —— 依 CLAUDE.md 反馈 "测试必须自带 fixture 不许
断言宿主环境", 常驻 pytest 只用纯合成数据 (calendar_builder._DIM_DDL 建表 + 手插
dim 行), CI/无生产数据环境下同样能跑。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from conftest import duck_mem  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_db_invariants", REPO / "backend" / "scripts" / "check_db_invariants.py"
)
cdi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdi)

_HASH64 = "a" * 64  # 只需匹配 regexp_full_match('[0-9a-f]{64}') 的形状, 内容无意义


@pytest.fixture(scope="module")
def real_specs() -> dict[str, dict]:
    """真实 backend/config/db_invariants.yaml 解析结果, id -> spec。"""
    return {s["id"]: s for s in cdi.load_db_invariants()}


def _mk_spec(sql: str, *, op: str = "==", expect=0, allow_empty: bool = False) -> dict:
    """构造一条最小合法 spec (只用于测试运行器机制本身, 不代表真实 YAML 内容)。"""
    return {
        "id": "x", "db": "d", "attach": {}, "invariant": "i", "sql": sql,
        "op": op, "expect": expect, "allow_empty": allow_empty,
        "why": "w", "fix": "f", "kill_when": "k",
    }


# ── 0. 生产 YAML 真解析非空 + 键集合合规 (照 check_grain_uniqueness "生产 registry 真解析
# 非空") ──────────────────────────────────────────────────────────────────────────

def test_load_db_invariants_production_yaml_has_all_12(real_specs):
    assert set(real_specs) == {
        "bloat_ratio_smartmoney", "bloat_ratio_market", "bloat_ratio_org_holding",
        "bloat_ratio_feature_store", "bloat_ratio_tushare_raw",
        "calendar_projection_faithful", "holders_dates_compact",
        "qfq_lineage_stamped", "qfq_factor_current", "nominal_ohlcv_accepted_sources",
        "org_pointer_rowcount_matches_canonical", "holders_pointer_rowcount_matches_canonical",
    }
    for spec_id, s in real_specs.items():
        assert re.match(r"^[a-z0-9_]+$", s["id"])
        assert s["op"] in {"==", "<", "<=", ">", ">="}
        assert isinstance(s["allow_empty"], bool)


# ── 1. bloat_ratio_* (建表函数: tmp 文件库, 用真实 sql) ───────────────────────────

def test_bloat_ratio_red_green(tmp_path, real_specs):
    """只建不删 → PASS; 20 万行建表→CHECKPOINT→DELETE 全表→CHECKPOINT → FAIL。"""
    spec = real_specs["bloat_ratio_smartmoney"]
    db_path = tmp_path / "bloat.duckdb"
    c = duck_connect(str(db_path))
    try:
        c.execute("CREATE TABLE t AS SELECT range AS i, repeat('x', 200) AS s FROM range(200000)")
        c.execute("CHECKPOINT")
        r_pass = cdi.evaluate_spec(spec, c)
        assert r_pass["status"] == "PASS"
        assert r_pass["value"] < 10

        c.execute("DELETE FROM t")
        c.execute("CHECKPOINT")
        r_fail = cdi.evaluate_spec(spec, c)
        assert r_fail["status"] == "FAIL"
        assert r_fail["value"] >= 10
    finally:
        c.close()


def test_bloat_ratio_all_five_share_identical_sql(real_specs):
    """5 条只是 db/expect 不同 (smartmoney/market/org_holding/feature_store 都 <10%,
    tushare_raw <25%)——sql 本身应逐字相同 (同一段死块占比查询), 不是 5 份独立抄写。"""
    ids = [
        "bloat_ratio_smartmoney", "bloat_ratio_market", "bloat_ratio_org_holding",
        "bloat_ratio_feature_store",
    ]
    sqls = {real_specs[i]["sql"] for i in ids}
    assert len(sqls) == 1
    assert real_specs["bloat_ratio_tushare_raw"]["sql"] == next(iter(sqls))
    assert real_specs["bloat_ratio_tushare_raw"]["expect"] == 25
    for i in ids:
        assert real_specs[i]["expect"] == 10


# ── 2. calendar_projection_faithful ──────────────────────────────────────────────

def _calendar_conn():
    from services import calendar_builder as cb
    from services.data_sources.calendar_schema import ensure_calendar_acceptance_schema

    c = duck_connect(":memory:", attach={"tr": {"path": ":memory:", "read_only": False}})
    c.execute("USE tr")
    ensure_calendar_acceptance_schema(c)
    c.execute("USE memory")
    c.execute(cb._DIM_DDL)
    return c


def _insert_calendar_canonical(c, generation_id: str, rows: list[tuple[str, int]]) -> None:
    from services.data_sources.calendar_schema import CONTRACT_VERSION as CAL_CV

    for iso_date, is_open in rows:
        c.execute(
            "INSERT INTO tr.canonical_sse_trading_calendar_generation "
            "(generation_id, exchange, cal_date, is_open, pretrade_date, "
            " source_fragment_ordinal, source_row_ordinal, source_row_hash, "
            " available_at, contract_version, config_hash, built_at) "
            "VALUES (?, 'SSE', ?, ?, NULL, 0, 0, ?, now(), ?, ?, now())",
            [generation_id, iso_date, is_open, _HASH64, CAL_CV, _HASH64],
        )


def _insert_calendar_pointer(c, generation_id: str, accepted_at: str) -> None:
    from services.data_sources.calendar_schema import CONTRACT_VERSION as CAL_CV
    from services.data_sources.calendar_schema import DATASET_ID as CAL_DATASET_ID

    c.execute(
        "INSERT INTO tr.accepted_partition "
        "(dataset_id, partition_value, batch_id, contract_version, contract_hash, config_hash, "
        " row_count, content_hash, observed_at, available_at, accepted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, now(), now(), ?)",
        [CAL_DATASET_ID, generation_id, generation_id, CAL_CV, _HASH64, _HASH64, _HASH64,
         accepted_at],
    )


def test_calendar_projection_faithful_pass_baseline(real_specs):
    """dim 与 accepted 最新代际 (SSE, is_open=1) 集合完全一致 → PASS。"""
    c = _calendar_conn()
    try:
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-05', 1)")
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-06', 1)")
        _insert_calendar_canonical(c, "g1", [("2026-01-05", 1), ("2026-01-06", 1)])
        _insert_calendar_pointer(c, "g1", "2026-01-01T00:00:00Z")
        r = cdi.evaluate_spec(real_specs["calendar_projection_faithful"], c)
        assert r == {**r, "status": "PASS", "checked": 2, "value": 0}
    finally:
        c.close()


def test_calendar_projection_faithful_fail_missing_trading_day(real_specs):
    """删一个 dim 里的交易日 (accepted 仍有) → 对称差 >0 → FAIL。"""
    c = _calendar_conn()
    try:
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-05', 1)")
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-06', 1)")
        _insert_calendar_canonical(c, "g1", [("2026-01-05", 1), ("2026-01-06", 1)])
        _insert_calendar_pointer(c, "g1", "2026-01-01T00:00:00Z")
        c.execute("DELETE FROM dim_trading_calendar WHERE trade_date = '2026-01-06'")
        r = cdi.evaluate_spec(real_specs["calendar_projection_faithful"], c)
        assert r["status"] == "FAIL" and r["value"] == 1
    finally:
        c.close()


def test_calendar_projection_faithful_fail_extra_saturday(real_specs):
    """dim 里插一个 accepted 没有的日子 (如误收周六) → FAIL。"""
    c = _calendar_conn()
    try:
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-05', 1)")
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-06', 1)")
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-10', 1)")  # 周六
        _insert_calendar_canonical(c, "g1", [("2026-01-05", 1), ("2026-01-06", 1)])
        _insert_calendar_pointer(c, "g1", "2026-01-01T00:00:00Z")
        r = cdi.evaluate_spec(real_specs["calendar_projection_faithful"], c)
        assert r["status"] == "FAIL" and r["value"] == 1
    finally:
        c.close()


def test_calendar_projection_faithful_pass_after_revision_open_to_closed(real_specs):
    """新代际把某日改 closed 且 dim 已删该日 → 仍 PASS (忠实投影, 不是等值钉)。"""
    c = _calendar_conn()
    try:
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-05', 1)")
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-07', 1)")
        _insert_calendar_canonical(c, "g1", [("2026-01-05", 1), ("2026-01-06", 1)])
        _insert_calendar_pointer(c, "g1", "2026-01-01T00:00:00Z")
        # g2 是更新的 accepted 代际: 01-06 改判 closed, 新增 01-07 open
        _insert_calendar_canonical(
            c, "g2", [("2026-01-05", 1), ("2026-01-06", 0), ("2026-01-07", 1)]
        )
        _insert_calendar_pointer(c, "g2", "2026-01-02T00:00:00Z")
        r = cdi.evaluate_spec(real_specs["calendar_projection_faithful"], c)
        assert r["status"] == "PASS" and r["value"] == 0
    finally:
        c.close()


def test_calendar_projection_faithful_unverified_when_pointer_missing(real_specs):
    """accepted_partition 里没有该 dataset_id 的指针 → value 显式 NULL → UNVERIFIED
    (不是"当前无回退所以 PASS", 也不是拿空集当 FAIL 判)。"""
    c = _calendar_conn()
    try:
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-01-05', 1)")
        _insert_calendar_canonical(c, "g1", [("2026-01-05", 1)])
        r = cdi.evaluate_spec(real_specs["calendar_projection_faithful"], c)
        assert r["status"] == "UNVERIFIED" and r["reason"] == "null_value"
    finally:
        c.close()


# ── 3. holders_dates_compact (建表函数: holders ensure_*_schema) ─────────────────

def _holders_conn():
    from services.data_sources.holders_top10_acceptance import (
        ensure_holders_top10_acceptance_schema,
    )

    c = duck_mem()
    ensure_holders_top10_acceptance_schema(c)
    return c


def _insert_holder_row(
    c, *, stock_code: str, report_date: str, notice_date: str,
    holder_rank: int = 1, row_seq: int = 1,
) -> None:
    from services.data_sources.holders_top10_schema import CONTRACT_VERSION as HOLD_CV

    c.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, holder_set, holder_rank, row_seq, holder_name, "
        " holder_code, is_holder_org, hold_ratio_float, notice_date, is_exit_row, "
        " holder_name_norm, share_class, shares_approx, change_status, hold_change_num, "
        " holder_type, available_at, ingest_batch_id, source_row_hash, contract_version, "
        " config_hash, built_at) "
        "VALUES (?, ?, 'top10', ?, ?, 'Name', NULL, NULL, NULL, ?, FALSE, "
        " NULL, NULL, NULL, NULL, NULL, NULL, now(), 'b1', 'h1', ?, 'c1', now())",
        [stock_code, report_date, holder_rank, row_seq, notice_date, HOLD_CV],
    )


def test_holders_dates_compact_pass_all_yyyymmdd(real_specs):
    c = _holders_conn()
    try:
        _insert_holder_row(c, stock_code="000001", report_date="20240331", notice_date="20240405")
        r = cdi.evaluate_spec(real_specs["holders_dates_compact"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


@pytest.mark.parametrize("bad_report_date", ["2024-03-31", "2024/03/31"])
def test_holders_dates_compact_fail_on_non_compact_format(real_specs, bad_report_date):
    c = _holders_conn()
    try:
        _insert_holder_row(c, stock_code="000001", report_date="20240331", notice_date="20240405")
        _insert_holder_row(
            c, stock_code="000002", report_date=bad_report_date, notice_date="20240405",
            holder_rank=2,
        )
        r = cdi.evaluate_spec(real_specs["holders_dates_compact"], c)
        assert r["status"] == "FAIL" and r["checked"] == 2 and r["value"] == 1
    finally:
        c.close()


def test_holders_dates_compact_unverified_when_empty(real_specs):
    c = _holders_conn()
    try:
        r = cdi.evaluate_spec(real_specs["holders_dates_compact"], c)
        assert r["status"] == "UNVERIFIED" and r["reason"] == "empty_population"
    finally:
        c.close()


# ── 4. qfq_lineage_stamped (建表函数: market_schema DDL) ─────────────────────────

def _market_conn():
    from services import market_schema

    c = duck_mem()
    c.execute(market_schema.PRICE_KLINE_QFQ_TUSHARE_DDL)
    return c


def test_qfq_lineage_stamped_pass_all_filled(real_specs):
    c = _market_conn()
    try:
        c.execute(
            "INSERT INTO price_kline_qfq_tushare VALUES "
            "('000001','2026-08-26',1,1,1,1,1,1,'b1', now(), '2026-08-26')"
        )
        r = cdi.evaluate_spec(real_specs["qfq_lineage_stamped"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_qfq_lineage_stamped_fail_on_null_factor_as_of(real_specs):
    c = _market_conn()
    try:
        c.execute(
            "INSERT INTO price_kline_qfq_tushare VALUES "
            "('000001','2026-08-26',1,1,1,1,1,1,'b1', now(), NULL)"
        )
        r = cdi.evaluate_spec(real_specs["qfq_lineage_stamped"], c)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        c.close()


# ── 5. qfq_factor_current (建表函数: market DDL + 内存 tr.raw_tushare_adj_factor;
# 后者无固定 writer schema, 见文件头注) ──────────────────────────────────────────

def _qfq_factor_conn():
    from services import market_schema

    c = duck_connect(":memory:", attach={"tr": {"path": ":memory:", "read_only": False}})
    c.execute(market_schema.PRICE_KLINE_QFQ_TUSHARE_DDL)
    c.execute(
        "CREATE TABLE tr.raw_tushare_adj_factor "
        "(ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE, built_at VARCHAR)"
    )
    return c


def test_qfq_factor_current_pass_when_date_lags_but_value_equal(real_specs):
    """日期落后但值相等 → PASS——这是与执行计划"factor_as_of>=最近除权日"写法的分水岭。"""
    c = _qfq_factor_conn()
    try:
        c.execute(
            "INSERT INTO price_kline_qfq_tushare (code, date, factor_as_of) "
            "VALUES ('000001', '2026-08-26', '2026-08-26')"
        )
        c.execute("INSERT INTO tr.raw_tushare_adj_factor VALUES ('000001.SZ','20260826',1.0,'t')")
        c.execute("INSERT INTO tr.raw_tushare_adj_factor VALUES ('000001.SZ','20260828',1.0,'t')")
        r = cdi.evaluate_spec(real_specs["qfq_factor_current"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_qfq_factor_current_fail_when_latest_value_drifts(real_specs):
    c = _qfq_factor_conn()
    try:
        c.execute(
            "INSERT INTO price_kline_qfq_tushare (code, date, factor_as_of) "
            "VALUES ('000001', '2026-08-26', '2026-08-26')"
        )
        c.execute("INSERT INTO tr.raw_tushare_adj_factor VALUES ('000001.SZ','20260826',1.0,'t')")
        c.execute("INSERT INTO tr.raw_tushare_adj_factor VALUES ('000001.SZ','20260828',1.1,'t')")
        r = cdi.evaluate_spec(real_specs["qfq_factor_current"], c)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        c.close()


def test_qfq_factor_current_fail_when_adj_factor_entirely_missing(real_specs):
    """"qfq 有、adj 没有"的股票同样判违规, 不能因为查不到就放行。"""
    c = _qfq_factor_conn()
    try:
        c.execute(
            "INSERT INTO price_kline_qfq_tushare (code, date, factor_as_of) "
            "VALUES ('000001', '2026-08-26', '2026-08-26')"
        )
        r = cdi.evaluate_spec(real_specs["qfq_factor_current"], c)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        c.close()


# ── 6. nominal_ohlcv_accepted_sources (建表函数: accepted_schema DDL) ────────────

def _nominal_conn():
    from services.data_sources.accepted_schema import ensure_accepted_evidence_schema

    c = duck_mem()
    ensure_accepted_evidence_schema(c)
    return c


def _insert_ingest_batch(c, *, batch_id: str, source_name: str, status: str) -> None:
    c.execute(
        "INSERT INTO ingest_batch (batch_id, dataset_id, contract_version, contract_hash, "
        " config_hash, writer_id, partition_value, source_name, status, request_json, "
        " fragment_outcomes_json, expected_fragment_count, completed_fragment_count, "
        " failed_fragment_count, landing_row_count, canonical_row_count, payload_hash, "
        " canonical_hash, observed_at, available_at, landed_at, validated_at, accepted_at, "
        " rejection_code, rejection_detail) "
        "VALUES (?, 'tier0.market_data.nominal_ohlcv_daily', 'v1', 'ch', 'cf', 'w', "
        " '20260101', ?, ?, '{}', '[]', 1, 1, 0, 1, 1, 'ph', 'ch2', now(), now(), now(), "
        " now(), now(), NULL, NULL)",
        [batch_id, source_name, status],
    )


def test_nominal_ohlcv_accepted_sources_pass_tdxhub_accepted(real_specs):
    c = _nominal_conn()
    try:
        _insert_ingest_batch(c, batch_id="b1", source_name="tdxhub", status="ACCEPTED")
        r = cdi.evaluate_spec(real_specs["nominal_ohlcv_accepted_sources"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_nominal_ohlcv_accepted_sources_pass_ignores_non_accepted_status(real_specs):
    """LANDED 的 akshare 不进 checked (status != ACCEPTED) → 仍 PASS——本条只守
    ACCEPTED 批次, 不是"akshare 不能出现在任何地方"。"""
    c = _nominal_conn()
    try:
        _insert_ingest_batch(c, batch_id="b1", source_name="tdxhub", status="ACCEPTED")
        _insert_ingest_batch(c, batch_id="b2", source_name="akshare", status="LANDED")
        r = cdi.evaluate_spec(real_specs["nominal_ohlcv_accepted_sources"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_nominal_ohlcv_accepted_sources_fail_on_disallowed_accepted_source(real_specs):
    c = _nominal_conn()
    try:
        _insert_ingest_batch(c, batch_id="b1", source_name="tdxhub", status="ACCEPTED")
        _insert_ingest_batch(c, batch_id="b3", source_name="akshare", status="ACCEPTED")
        r = cdi.evaluate_spec(real_specs["nominal_ohlcv_accepted_sources"], c)
        assert r["status"] == "FAIL" and r["checked"] == 2 and r["value"] == 1
    finally:
        c.close()


# ── 7. org/holders 指针 (建表函数: ensure_org_holding_acceptance_schema /
# ensure_holders_top10_acceptance_schema) ────────────────────────────────────────

def _org_conn():
    from services.data_sources.org_holding_acceptance import (
        ensure_org_holding_acceptance_schema,
    )

    c = duck_mem()
    ensure_org_holding_acceptance_schema(c)
    return c


def _insert_org_row(
    c, *, report_date: str, available_date: str, stock_code: str, holder_code: str,
) -> None:
    c.execute(
        "INSERT INTO canonical_org_holding_detail_period "
        "(report_date, available_date, stock_code, holder_code, fund_derivecode, "
        " holder_name, org_type_name, total_shares, free_shares_ratio, "
        " available_at, ingest_batch_id, source_row_hash, contract_version, config_hash, "
        " built_at) "
        "VALUES (?, ?, ?, ?, '', 'N', 'T', 1.0, 0.1, now(), 'b1', 'h1', 'v1', 'c1', now())",
        [report_date, available_date, stock_code, holder_code],
    )


def _insert_org_pointer(c, *, partition_value: str, row_count: int) -> None:
    c.execute(
        "INSERT INTO accepted_partition "
        "(dataset_id, partition_value, batch_id, contract_version, contract_hash, config_hash, "
        " row_count, content_hash, observed_at, available_at, accepted_at) "
        "VALUES ('tier0.disclosure.org_holding_detail_period', ?, ?, 'v1', 'ch1', 'cf1', ?, "
        " 'hash1', now(), now(), now())",
        [partition_value, partition_value, row_count],
    )


def test_org_pointer_pass_when_counts_match(real_specs):
    c = _org_conn()
    try:
        _insert_org_row(c, report_date="20260630", available_date="2026-06-30",
                         stock_code="000001", holder_code="H1")
        _insert_org_row(c, report_date="20260630", available_date="2026-06-30",
                         stock_code="000002", holder_code="H2")
        _insert_org_pointer(c, partition_value="2026-06-30", row_count=2)
        r = cdi.evaluate_spec(real_specs["org_pointer_rowcount_matches_canonical"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_org_pointer_fail_on_row_count_drift(real_specs):
    """07-27 9a02683e5 真事故形态: 删一行 canonical, 指针仍说旧行数。"""
    c = _org_conn()
    try:
        _insert_org_row(c, report_date="20260630", available_date="2026-06-30",
                         stock_code="000001", holder_code="H1")
        _insert_org_row(c, report_date="20260630", available_date="2026-06-30",
                         stock_code="000002", holder_code="H2")
        _insert_org_pointer(c, partition_value="2026-06-30", row_count=2)
        c.execute("DELETE FROM canonical_org_holding_detail_period WHERE stock_code = '000002'")
        r = cdi.evaluate_spec(real_specs["org_pointer_rowcount_matches_canonical"], c)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        c.close()


def test_org_pointer_fail_on_canonical_partition_without_pointer(real_specs):
    """checked 必须先 >0 (至少一个正常指针) 这条 FAIL 才有意义——0 个指针时 checked==0
    走 R1 的 empty_population UNVERIFIED, 不是这里要测的"有指针也有裸奔分区"。"""
    c = _org_conn()
    try:
        _insert_org_row(c, report_date="20260630", available_date="2026-06-30",
                         stock_code="000001", holder_code="H1")
        _insert_org_pointer(c, partition_value="2026-06-30", row_count=1)
        # 第二个 available_date 分区有 canonical 内容, 但从未生成过指针 (canonical_missing)。
        _insert_org_row(c, report_date="20260930", available_date="2026-09-30",
                         stock_code="000003", holder_code="H3")
        r = cdi.evaluate_spec(real_specs["org_pointer_rowcount_matches_canonical"], c)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        c.close()


def _holders_pointer_conn():
    return _holders_conn()


def _insert_holders_pointer(c, *, partition_value: str, row_count: int) -> None:
    c.execute(
        "INSERT INTO accepted_partition "
        "(dataset_id, partition_value, batch_id, contract_version, contract_hash, config_hash, "
        " row_count, content_hash, observed_at, available_at, accepted_at) "
        "VALUES ('tier0.disclosure.top10_float_holders_period', ?, ?, 'v1', 'ch1', 'cf1', ?, "
        " 'hash1', now(), now(), now())",
        [partition_value, partition_value, row_count],
    )


def test_holders_pointer_pass_when_counts_match(real_specs):
    c = _holders_pointer_conn()
    try:
        _insert_holder_row(c, stock_code="000001", report_date="20260630",
                            notice_date="20260705", holder_rank=1)
        _insert_holder_row(c, stock_code="000001", report_date="20260630",
                            notice_date="20260705", holder_rank=2)
        _insert_holders_pointer(c, partition_value="20260705", row_count=2)
        r = cdi.evaluate_spec(real_specs["holders_pointer_rowcount_matches_canonical"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_holders_pointer_fail_on_row_count_drift(real_specs):
    c = _holders_pointer_conn()
    try:
        _insert_holder_row(c, stock_code="000001", report_date="20260630",
                            notice_date="20260705", holder_rank=1)
        _insert_holder_row(c, stock_code="000001", report_date="20260630",
                            notice_date="20260705", holder_rank=2)
        _insert_holders_pointer(c, partition_value="20260705", row_count=2)
        c.execute("DELETE FROM canonical_top10_float_holders_period WHERE holder_rank = 2")
        r = cdi.evaluate_spec(real_specs["holders_pointer_rowcount_matches_canonical"], c)
        assert r["status"] == "FAIL" and r["value"] == 1
    finally:
        c.close()


# ── 8. R3: nominal_sources 允许集与 tushare_sunset.yaml 机械锁链 ─────────────────

def test_nominal_sources_allowed_set_locked_to_tushare_sunset(real_specs):
    """expect 允许集 == {'tushare'} ∪ {tushare_sunset.domains.daily.replacement}——
    离线断言两处相等 (R3), 不许两边各自硬编码却互不引用。"""
    sunset = yaml.safe_load(
        (REPO / "backend" / "config" / "tushare_sunset.yaml").read_text(encoding="utf-8")
    )
    replacement = sunset["domains"]["daily"]["replacement"]
    sql = real_specs["nominal_ohlcv_accepted_sources"]["sql"]
    m = re.search(r"NOT IN \(([^)]*)\)", sql)
    assert m, "nominal_ohlcv_accepted_sources.sql 里找不到 NOT IN (...) 允许集字面量"
    allowed = {item.strip().strip("'") for item in m.group(1).split(",")}
    assert allowed == {"tushare", replacement}


# ── 9. 通用行为: R1 三态判定 (checked==0 / value NULL / 列名不对 / 库不可达) ───────

def test_evaluate_spec_checked_zero_is_unverified_not_pass():
    c = duck_mem()
    try:
        r = cdi.evaluate_spec(_mk_spec("SELECT 0 AS checked, 0 AS value"), c)
        assert r["status"] == "UNVERIFIED" and r["reason"] == "empty_population"
    finally:
        c.close()


def test_evaluate_spec_checked_zero_allowed_when_allow_empty_true():
    c = duck_mem()
    try:
        r = cdi.evaluate_spec(
            _mk_spec("SELECT 0 AS checked, 0 AS value", allow_empty=True), c
        )
        assert r["status"] == "PASS"
    finally:
        c.close()


def test_evaluate_spec_value_null_is_unverified():
    c = duck_mem()
    try:
        r = cdi.evaluate_spec(_mk_spec("SELECT 5 AS checked, NULL AS value"), c)
        assert r["status"] == "UNVERIFIED" and r["reason"] == "null_value"
    finally:
        c.close()


def test_evaluate_spec_wrong_columns_is_unverified_sql_shape():
    c = duck_mem()
    try:
        r = cdi.evaluate_spec(_mk_spec("SELECT 1 AS foo, 2 AS bar"), c)
        assert r["status"] == "UNVERIFIED" and r["reason"] == "sql_shape"
    finally:
        c.close()


def test_evaluate_spec_missing_table_is_unverified_with_exception_reason():
    c = duck_mem()
    try:
        r = cdi.evaluate_spec(
            _mk_spec("SELECT count(*) AS checked, count(*) AS value FROM nope_tbl"), c
        )
        assert r["status"] == "UNVERIFIED"
        assert "CatalogException" in r["reason"] or "Catalog" in r["reason"]
    finally:
        c.close()


def test_run_invariants_conn_lock_conflict_is_unverified_and_never_exit_zero():
    def _boom(db_alias, attach):
        raise RuntimeError("Conflicting lock is held on file X")

    results = cdi.run_invariants([_mk_spec("SELECT 1 AS checked, 0 AS value")], _boom)
    assert results[0]["status"] == "UNVERIFIED"
    overall = cdi.overall_status(results)
    assert overall == "UNVERIFIED"
    assert cdi.exit_code_for(overall) == 3
    assert cdi.exit_code_for(overall) != 0


def test_run_invariants_mixed_fail_and_unverified_returns_exit_1():
    """FAIL 优先于 UNVERIFIED 定 overall——只要有一条 FAIL, 就不能靠"其它条查不了"
    把 overall 拉回没那么严重的 UNVERIFIED。"""

    def _conn_for(db_alias, attach):
        if db_alias == "lockme":
            raise RuntimeError("Conflicting lock is held")
        return duck_mem()

    specs = [
        {**_mk_spec("SELECT 1 AS checked, 1 AS value"), "id": "fails", "db": "ok"},
        {**_mk_spec("SELECT 1 AS checked, 0 AS value"), "id": "locked", "db": "lockme"},
    ]
    results = cdi.run_invariants(specs, _conn_for)
    statuses = {r["id"]: r["status"] for r in results}
    assert statuses == {"fails": "FAIL", "locked": "UNVERIFIED"}
    overall = cdi.overall_status(results)
    assert overall == "FAIL"
    assert cdi.exit_code_for(overall) == 1


def test_run_invariants_all_pass_returns_exit_0():
    results = cdi.run_invariants(
        [_mk_spec("SELECT 1 AS checked, 0 AS value")], lambda db, attach: duck_mem()
    )
    overall = cdi.overall_status(results)
    assert overall == "PASS"
    assert cdi.exit_code_for(overall) == 0


# ── 10. 加载期 fail-closed: 键集 / 别名 / op 非法都在加载期报错 (main() 返回 2) ───

def _write_yaml(tmp_path: Path, invariants: list[dict]) -> Path:
    p = tmp_path / "db_invariants.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "invariants": invariants}), encoding="utf-8")
    return p


_VALID_ENTRY = {
    "id": "x", "db": "reference", "invariant": "i",
    "sql": "SELECT 1 AS checked, 0 AS value", "op": "==", "expect": 0,
    "allow_empty": False, "why": "w", "fix": "f", "kill_when": "k",
}


def test_load_fails_closed_on_banned_plugin_bus_key(tmp_path):
    entry = {**_VALID_ENTRY, "callable": "boom"}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match="callable"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_missing_required_key(tmp_path):
    entry = {k: v for k, v in _VALID_ENTRY.items() if k != "kill_when"}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match="kill_when"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_extra_unknown_key(tmp_path):
    entry = {**_VALID_ENTRY, "notes": "not allowed"}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match="未知键"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_unknown_db_alias(tmp_path):
    entry = {**_VALID_ENTRY, "db": "not_a_real_alias"}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match="unknown database alias"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_unknown_attach_alias(tmp_path):
    entry = {**_VALID_ENTRY, "attach": {"tr": "not_a_real_alias"}}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match="unknown database alias"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_invalid_op(tmp_path):
    entry = {**_VALID_ENTRY, "op": "!="}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match="op"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_bool_expect(tmp_path):
    """expect 类型只能是 int/float/str——YAML true/false 会被 Python 解成 bool,
    而 bool 是 int 子类 (isinstance(True, int) 恒真), 必须显式排除, 否则 op '=='
    会把 True 悄悄当 1 比, 静默改变判据含义。"""
    entry = {**_VALID_ENTRY, "expect": True}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match="expect"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_duplicate_id(tmp_path):
    p = _write_yaml(tmp_path, [dict(_VALID_ENTRY), dict(_VALID_ENTRY)])
    with pytest.raises(cdi.DbInvariantsConfigError, match="duplicate"):
        cdi.load_db_invariants(p)


def test_load_fails_closed_on_bad_id_pattern(tmp_path):
    entry = {**_VALID_ENTRY, "id": "Not-Valid-ID"}
    p = _write_yaml(tmp_path, [entry])
    with pytest.raises(cdi.DbInvariantsConfigError, match=r"\^\[a-z0-9_\]"):
        cdi.load_db_invariants(p)


def test_main_returns_2_on_config_error(tmp_path, monkeypatch):
    bad_path = tmp_path / "broken.yaml"
    bad_path.write_text("version: 1\ninvariants: []\n", encoding="utf-8")
    monkeypatch.setattr(cdi, "CONFIG_PATH", bad_path)
    assert cdi.main(["--json"]) == 2


def test_main_returns_2_when_registry_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cdi, "CONFIG_PATH", tmp_path / "does_not_exist.yaml")
    assert cdi.main([]) == 2
