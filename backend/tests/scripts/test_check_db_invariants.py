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
        "qfq_lineage_stamped", "qfq_anchor_is_own_last_bar", "nominal_ohlcv_accepted_sources",
        "section9_dims_live_in_reference", "reference_dims_have_primary_key",
        "section9_dims_absent_from_smartmoney", "dc_member_no_truncation_signature",
        "calendar_floor_not_truncated",
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
            "INSERT INTO price_kline_qfq_tushare "
            "(code, date, batch_id, ingested_at, factor_as_of) "
            "VALUES ('000001','2026-08-26','b1', now(), '2026-08-26')"
        )
        r = cdi.evaluate_spec(real_specs["qfq_lineage_stamped"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_qfq_lineage_stamped_fail_on_null_factor_as_of(real_specs):
    c = _market_conn()
    try:
        c.execute(
            "INSERT INTO price_kline_qfq_tushare "
            "(code, date, batch_id, ingested_at, factor_as_of) "
            "VALUES ('000001','2026-08-26','b1', now(), NULL)"
        )
        r = cdi.evaluate_spec(real_specs["qfq_lineage_stamped"], c)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        c.close()


# ── 5. qfq_anchor_is_own_last_bar (建表函数: 只需 market DDL —— 判据不再碰任何
# 供应商表, 2026-09-08 换心后复权因子自算, 见 db_invariants.yaml 该条 why) ──────

def _qfq_anchor_conn():
    from services import market_schema

    c = duck_connect(":memory:")
    c.execute(market_schema.PRICE_KLINE_QFQ_TUSHARE_DDL)
    return c


def _put(c, code: str, date: str, anchor: str) -> None:
    c.execute(
        "INSERT INTO price_kline_qfq_tushare (code, date, factor_as_of) VALUES (?, ?, ?)",
        [code, date, anchor],
    )


def test_qfq_anchor_pass_when_anchor_is_own_last_bar(real_specs):
    """锚点 = 该股自己最后一根 —— 含"早已退市、锚点停在退市那天"这种正常情形。"""
    c = _qfq_anchor_conn()
    try:
        _put(c, "000001", "2026-08-27", "2026-08-28")
        _put(c, "000001", "2026-08-28", "2026-08-28")
        # 退市股: 末根 2024-03-05, 锚点也在 2024-03-05 → 合规 (不因为"日期老"就判红)
        _put(c, "000005", "2024-03-04", "2024-03-05")
        _put(c, "000005", "2024-03-05", "2024-03-05")
        r = cdi.evaluate_spec(real_specs["qfq_anchor_is_own_last_bar"], c)
        assert r == {**r, "status": "PASS", "checked": 2, "value": 0}
    finally:
        c.close()


def test_qfq_anchor_fail_when_anchor_drifts_past_own_last_bar(real_specs):
    """旧全局 max 锚点的形态: 股票 2024-03-05 就没了, 锚点却飘到 2024-04-25
    (600069.SH 真实案例: 2020-08-20 退市, nominal 0.28 而旧 qfq 2.72, 差 89.7%)。"""
    c = _qfq_anchor_conn()
    try:
        _put(c, "000005", "2024-03-04", "2024-04-25")
        _put(c, "000005", "2024-03-05", "2024-04-25")
        r = cdi.evaluate_spec(real_specs["qfq_anchor_is_own_last_bar"], c)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        c.close()


def test_qfq_anchor_fail_when_one_stock_carries_two_anchors(real_specs):
    """增量 append 的形态: 旧行留上次锚、新行写当次锚 —— 一只股半旧半新。
    切换前实测 5,447 只里 5,193 只是这个形态。"""
    c = _qfq_anchor_conn()
    try:
        _put(c, "000001", "2026-08-26", "2026-08-26")   # 上一次 build 写的
        _put(c, "000001", "2026-08-28", "2026-08-28")   # 增量 append 写的
        r = cdi.evaluate_spec(real_specs["qfq_anchor_is_own_last_bar"], c)
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


def test_nominal_ohlcv_accepted_sources_pass_fuyao_accepted(real_specs):
    """2026-09-16 刀2: daily 域二次换源 tdxhub -> fuyao, 允许集加 fuyao (不删 tdxhub,
    见 test_nominal_sources_allowed_set_locked_to_tushare_sunset 的更正说明)。"""
    c = _nominal_conn()
    try:
        _insert_ingest_batch(c, batch_id="b1", source_name="fuyao", status="ACCEPTED")
        r = cdi.evaluate_spec(real_specs["nominal_ohlcv_accepted_sources"], c)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        c.close()


def test_nominal_ohlcv_accepted_sources_fail_on_akshare_even_alongside_fuyao(real_specs):
    """反向验证: 加了 fuyao 不等于允许集变宽松到接受任意新源——akshare 仍 FAIL。"""
    c = _nominal_conn()
    try:
        _insert_ingest_batch(c, batch_id="b1", source_name="fuyao", status="ACCEPTED")
        _insert_ingest_batch(c, batch_id="b2", source_name="akshare", status="ACCEPTED")
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
    """expect 允许集 == {'tushare', 'tdxhub'} ∪ {tushare_sunset.domains.daily.
    replacement}——离线断言两处相等 (R3), 不许两边各自硬编码却互不引用。

    2026-09-16 刀2 更正 (daily 域二次换源 tdxhub -> fuyao): 原断言 `{"tushare",
    replacement}` 假设一个域一生只换源一次, 这个假设现在被证伪了——daily 已经历
    tushare -> tdxhub -> fuyao 两跳, 而 R2 (全历史不设窗, db_invariants.yaml 同条
    invariant 注释) 要求早已落地的 2026-08-31 tdxhub ACCEPTED 批次永远留在允许集里,
    不能因为 replacement 字段只能记录"当前"这一任供货商就被挤出去 (那会让这条门对着
    自己的真实历史数据永久 FAIL)。'tdxhub' 在此硬编码为已冻结的历史成员 (它不会再是
    任何未来 replacement 的取值——本域已经再次换源离开它), 不是回到"两边各自硬编码"：
    'tushare'/当前 replacement 仍从 tushare_sunset.yaml 现读, 机械锁链只对"当前在职
    供货商"那一格生效, 历史格由人工在两处 (这里 + db_invariants.yaml) 同步追加。"""
    sunset = yaml.safe_load(
        (REPO / "backend" / "config" / "tushare_sunset.yaml").read_text(encoding="utf-8")
    )
    replacement = sunset["domains"]["daily"]["replacement"]
    sql = real_specs["nominal_ohlcv_accepted_sources"]["sql"]
    m = re.search(r"NOT IN \(([^)]*)\)", sql)
    assert m, "nominal_ohlcv_accepted_sources.sql 里找不到 NOT IN (...) 允许集字面量"
    allowed = {item.strip().strip("'") for item in m.group(1).split(",")}
    assert allowed == {"tushare", "tdxhub", replacement}


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


# ── 7. 2026-09-08 从 moth 迁入的 5 条 (S7 前置)。搬家理由见 db_invariants.yaml 顶部
# 那段注释: 它们问的是「数据现在对不对」, 却装在提交路径上。 ────────────────────

def _ref_conn(*, with_active=True, with_calendar=True, calendar_floor="2005-01-04"):
    """最小 reference 库: 两张 §9 dim, 都带 PRIMARY KEY (合法基线)。"""
    c = duck_connect(":memory:")
    if with_active:
        c.execute("CREATE TABLE dim_active_a_stock (code VARCHAR PRIMARY KEY)")
        c.execute("INSERT INTO dim_active_a_stock VALUES ('000001')")
    if with_calendar:
        c.execute("CREATE TABLE dim_trading_calendar "
                  "(trade_date VARCHAR PRIMARY KEY, is_trading INTEGER)")
        c.execute("INSERT INTO dim_trading_calendar VALUES (?, 1), ('2026-01-05', 1)",
                  [calendar_floor])
    return c


def test_section9_dims_present_passes(real_specs):
    c = _ref_conn()
    try:
        r = cdi.evaluate_spec(real_specs["section9_dims_live_in_reference"], c)
        assert r == {**r, "status": "PASS", "checked": 2, "value": 0}
    finally:
        c.close()


def test_section9_dims_missing_one_fails(real_specs):
    """population 是「期望的两个名字」, 不是「库里有几张表」—— 所以少一张时
    checked 仍是 2, value 是 1。判据形状要能说清"少了几个", 不是"数出来不等于 2"。"""
    c = _ref_conn(with_active=False)
    try:
        r = cdi.evaluate_spec(real_specs["section9_dims_live_in_reference"], c)
        assert r["status"] == "FAIL" and r["checked"] == 2 and r["value"] == 1
    finally:
        c.close()


def test_reference_dims_missing_pk_fails(real_specs):
    """与上一条分开: 表在但没 PK 是 DDL 漏约束(要 ALTER), 表不在是迁移丢失(要重建),
    处置不同, 混进一个 value 里红了说不清该干什么。"""
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE dim_active_a_stock (code VARCHAR PRIMARY KEY)")
        c.execute("CREATE TABLE dim_trading_calendar (trade_date VARCHAR, is_trading INTEGER)")
        r = cdi.evaluate_spec(real_specs["reference_dims_have_primary_key"], c)
        assert r["status"] == "FAIL" and r["checked"] == 2 and r["value"] == 1
    finally:
        c.close()


def test_section9_dims_absent_from_smartmoney(real_specs):
    """三联的第三条。只查 reference 有、不排除 smartmoney 也有 = 双写不是拆库。"""
    spec = real_specs["section9_dims_absent_from_smartmoney"]
    clean = duck_connect(":memory:")
    try:
        clean.execute("CREATE TABLE unrelated (x INTEGER)")
        r = cdi.evaluate_spec(spec, clean)
        assert r == {**r, "status": "PASS", "checked": 2, "value": 0}
    finally:
        clean.close()
    dirty = duck_connect(":memory:")
    try:
        dirty.execute("CREATE TABLE dim_active_a_stock (code VARCHAR)")
        r = cdi.evaluate_spec(spec, dirty)
        assert r["status"] == "FAIL" and r["checked"] == 2 and r["value"] == 1
    finally:
        dirty.close()


def _dc_conn(days):
    c = duck_connect(":memory:")
    c.execute("CREATE TABLE raw_tushare_dc_member (trade_date VARCHAR, con_code VARCHAR)")
    for d, n in days:
        c.execute("INSERT INTO raw_tushare_dc_member "
                  "SELECT ?, CAST(i AS VARCHAR) FROM range(?) t(i)", [d, n])
    return c


def test_dc_member_truncation_signature_fails(real_specs):
    """整 5000 倍 = 分页未翻到底的签名 (2026-06-12 实测的静默截断)。"""
    c = _dc_conn([("20260101", 5000), ("20260102", 4321)])
    try:
        r = cdi.evaluate_spec(real_specs["dc_member_no_truncation_signature"], c)
        assert r["status"] == "FAIL" and r["checked"] == 2 and r["value"] == 1
    finally:
        c.close()


def test_dc_member_normal_days_pass(real_specs):
    c = _dc_conn([("20260102", 4321), ("20260103", 4999)])
    try:
        r = cdi.evaluate_spec(real_specs["dc_member_no_truncation_signature"], c)
        assert r == {**r, "status": "PASS", "checked": 2, "value": 0}
    finally:
        c.close()


def test_dc_member_empty_table_is_unverified_not_pass(real_specs):
    """空对账不算过 —— 原 moth 版是"数出来等于 0", 表清空时它会判通过。
    这正是整个 db_invariants 存在的理由。"""
    c = _dc_conn([])
    try:
        r = cdi.evaluate_spec(real_specs["dc_member_no_truncation_signature"], c)
        assert r["status"] == "UNVERIFIED" and r["checked"] == 0
    finally:
        c.close()


def test_calendar_floor_truncation_fails(real_specs):
    """本条是弱形式(钉字面量), 留着的理由是同库那条"更强"的看不见这类缺陷:
    calendar_projection_faithful 的 floor 取自 dim 自己, dim 被削掉一截时 floor
    跟着动, 对称差仍为 0。副本注入实测: 删日历 2010 前 1,215 行 → 本条 FAIL,
    那条 PASS(checked 5343->4128)。"""
    ok = _ref_conn(calendar_floor="2005-01-04")
    try:
        r = cdi.evaluate_spec(real_specs["calendar_floor_not_truncated"], ok)
        assert r == {**r, "status": "PASS", "checked": 1, "value": 0}
    finally:
        ok.close()
    cut = _ref_conn(calendar_floor="2010-01-04")
    try:
        r = cdi.evaluate_spec(real_specs["calendar_floor_not_truncated"], cut)
        assert r["status"] == "FAIL" and r["checked"] == 1 and r["value"] == 1
    finally:
        cut.close()
