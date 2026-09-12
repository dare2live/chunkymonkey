"""reland_event_domain: 事件域历史重落脚本 (grain 契约 r2 §3, 施工切片 S7 + V1/V2
验收判据重落)。

用例编号 V1-V4 对应 r2 §4 S7 表里的字面用例 (archive / record / DDL, 均沿用未改动
的实现)。V4 系列里专测已删除的 ``old_subset_of_new``/输出字段
``dates_ok``/``dates_old_not_subset`` 的用例已删除 (不留墓碑) —— 它们的意图 (旧⊆新
不该把精度/措辞差异当缺失) 由下面的 T2/T4 接手, 走的是新判据
:func:`classify_old_keys`。

T1-T18 是 2026-09-12 验收判据重落 V1 (分类框架 + 结构检查 + 退出码 + 记账闸) 新增的
字面用例, 每条对应规格里给出的一个隔离条件。编号不连续 (没有 T6/T12, 那两个编号在
V2 规格里指的是下面的交易所证据层用例) 是规格本身的编号, 不是本文件遗漏。

T6/T6b-T6e/T12/T12b/T19-T25 是同日 V2 (交易所证据层: ``load_exchange_evidence``/
``canon_exchange_row`` 新签名/``exchange_key``/``gap_key``/``exchange_verdicts``/
``ceiling_compare``) 新增的字面用例。原 V5 (``canon_exchange_row`` 旧签名
``(row, *, market)``, 带 ``amount``) 与 V6 (``compare_day_three_way`` 三方比对,
--exchange-json/--date 单日路径) 的用例随对应函数一起删除 (V2 规格头注 K 点名允许
改写这两个符号, "不留墓碑") —— V5 覆盖的规范化行为 (千分位逗号/百股精度/全角括号/
HTML 标签/未知 market) 由下面新签名的 T20 (千分位) 与 canon 相关用例接手, 不是
凭空消失。

(V7-V9 测的是 ``recon_assignment_gaps.py``/``assignment_gap_recon.py`` 的 top_inst /
block_trade 段, 物理上放在 ``test_assignment_gap_recon.py`` — 它们测的是那个模块的
函数, 不是本文件的。)

全部用内存 DuckDB (``conftest.duck_mem``) / ``tmp_path``, 不连生产库、不跑网络
(本任务规则 3)。交易所证据文件全部用 ``tmp_path`` 现造 (CLAUDE.md 里格式钉死的
``data/archive/exchange_evidence/block_trade/`` 目录本身不读)。T7-T10/T16 里
``structural_checks`` 读的是仓库里真实的 ``backend/config/sync_registry.yaml``
(S2/S3 的规格明文要求"sync_registry 该域 grain", 不是调用方可注入的假 grain) ——
这是有意的耦合, 不是漏配置。
"""
from __future__ import annotations

import json

import pytest

from conftest import duck_mem

from scripts.reland_event_domain import (
    ArchiveResult,
    ClassReport,
    archive_table,
    canon_exchange_row,
    ceiling_compare,
    classify_old_keys,
    exchange_key,
    exchange_verdicts,
    gap_key,
    load_exchange_evidence,
    load_vendor_gaps,
    main,
    null_index_dates,
    prepare,
    structural_checks,
    verdict,
    verify,
    _vol_2dp,
)


def _zero_counts(**overrides) -> dict:
    counts = {
        "unverified": 0,
        "old_vendor_error": 0,
        "miaoxiang_gap_registered": 0,
        "miaoxiang_gap_unregistered": 0,
        "new_diverges_from_exchange": 0,
        "matched_at_exchange_precision": 0,
        "registered_gap_present": 0,
    }
    counts.update(overrides)
    return counts


def _all_ok_structural() -> dict:
    return {k: {"ok": True, "detail": None} for k in ("S1", "S2", "S3", "S4")}


# --------------------------------------------------------------------- V1: archive --

def test_v1_archive_table_rows_sha256_and_mismatch_raises(tmp_path):
    conn = duck_mem()
    conn.execute("CREATE TABLE t (a INT)")
    conn.execute("INSERT INTO t VALUES (1), (2), (3)")
    out = tmp_path / "t.parquet"

    result = archive_table(conn, "t", out)
    assert isinstance(result, ArchiveResult)
    assert result.rows == 3
    assert out.exists()
    assert len(result.sha256) == 64

    # 模拟"表行数与 parquet 不等": 把 out_path 指到一份行数不同的陈旧归档上。
    stale = tmp_path / "t_stale.parquet"
    conn.execute("CREATE TABLE t_other (a INT)")
    conn.execute("INSERT INTO t_other VALUES (1)")
    conn.execute(f"COPY (SELECT * FROM t_other) TO '{stale.as_posix()}' (FORMAT PARQUET)")
    with pytest.raises(ValueError, match="mismatch"):
        archive_table(conn, "t", stale)


def test_v1_archive_table_is_idempotent_on_rerun(tmp_path):
    conn = duck_mem()
    conn.execute("CREATE TABLE t (a INT)")
    conn.execute("INSERT INTO t VALUES (1), (2)")
    out = tmp_path / "t.parquet"
    first = archive_table(conn, "t", out)
    second = archive_table(conn, "t", out)
    assert first.sha256 == second.sha256
    assert first.rows == second.rows == 2


# ---------------------------------------------------------------------- V2: record --

def _block_trade_table(conn) -> None:
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade ("
        "ts_code VARCHAR, trade_date VARCHAR, price DOUBLE, vol DOUBLE, "
        "buyer VARCHAR, seller VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('600000.SH','20230103',10.0,1.0,'b1','s1'), "
        "('600000.SH','20230103',11.0,2.0,'b2','s2'), "
        "('600000.SH','20230602',12.0,3.0,'b3','s3')"
    )


def test_v2_prepare_execute_records_deletion_and_is_idempotent(tmp_path):
    conn = duck_mem()
    _block_trade_table(conn)

    plan = prepare("block_trade", run_id="r2", execute=True, conn=conn, archive_dir=tmp_path)
    assert plan["archive"]["rows"] == 3

    rows = conn.execute(
        "SELECT deletion_run_id, table_name, delete_scope, verification_json "
        "FROM mart_data_deletion_record"
    ).fetchall()
    assert len(rows) == 1
    run_id, table_name, delete_scope, verification_json = rows[0]
    assert run_id == "r2"
    assert table_name == "raw_tushare_block_trade"
    assert delete_scope == "rows_replaced_by_partition_reland"
    verification = json.loads(verification_json)
    assert verification["old_rows"] == 3
    assert len(verification["archive_sha256"]) == 64
    assert verification["per_year"] == {"2023": 3}

    # 再跑一次 (幂等): INSERT OR REPLACE 同 record_id, 不多出一行。
    prepare("block_trade", run_id="r2", execute=True, conn=conn, archive_dir=tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
    assert count == 1


# ------------------------------------------------------------------------ V3: DDL --

def test_v3_prepare_ddl_adds_typed_columns_idempotently(tmp_path):
    conn = duck_mem()
    _block_trade_table(conn)

    prepare("block_trade", run_id="r3", execute=True, conn=conn, archive_dir=tmp_path)
    info = {
        row[1]: row[2]
        for row in conn.execute('PRAGMA table_info("raw_tushare_block_trade")').fetchall()
    }
    assert info["seq"].upper() == "INTEGER"
    assert info["security_type"].upper() == "VARCHAR"
    assert info["trade_unit"].upper() == "VARCHAR"

    # 再跑一次不报错 (列已存在则跳过)。
    prepare("block_trade", run_id="r3", execute=True, conn=conn, archive_dir=tmp_path)
    info2 = {
        row[1]: row[2]
        for row in conn.execute('PRAGMA table_info("raw_tushare_block_trade")').fetchall()
    }
    assert info2 == info


def test_v3_prepare_ddl_for_top_inst_domain(tmp_path):
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_top_inst ("
        "trade_date VARCHAR, ts_code VARCHAR, exalter VARCHAR, side VARCHAR, reason VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_top_inst VALUES ('20190102','600000.SH','甲','0','R1')"
    )
    prepare("top_inst", run_id="rti", execute=True, conn=conn, archive_dir=tmp_path)
    info = {
        row[1]: row[2]
        for row in conn.execute('PRAGMA table_info("raw_tushare_top_inst")').fetchall()
    }
    assert info["board_rank"].upper() == "INTEGER"
    assert info["stat_days"].upper() == "VARCHAR"
    assert info["seat_code"].upper() == "VARCHAR"


# --------------------------------------------------------------- null_index_dates --

def test_null_index_dates_lists_unrelanded_days():
    conn = duck_mem()
    conn.execute("CREATE TABLE raw_tushare_block_trade (trade_date VARCHAR, seq INTEGER)")
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('20230103', 1), ('20230103', 2), ('20230602', NULL), ('20230603', NULL)"
    )
    dates = null_index_dates(conn, "raw_tushare_block_trade", "seq")
    assert dates == ["20230602", "20230603"]


# ---------------------------------------------------------------------- V2: canon --
# 字段名 2026-09-11 主会话实测更正为真实抓取字段 (上交所逐笔 JSONP sqlId
# COMMON_SSE_XXPL_JYXXPL_DZJYXX_L_1 / 深交所协议交易逐笔 CATALOGID=1265)。签名
# 2026-09-12 改为 (market, trade_date, row) (V2 头注 K), 不再返回 amount。

def test_v2_canon_sh_row_adds_suffix_trade_date_and_normalizes_seller():
    row = canon_exchange_row(
        "sh", "20230103",
        {
            "stockid": "603279",
            "tradeprice": "28",
            "tradeqty": "50",
            "branchbuy": "机构专用",
            "branchsell": "中信证券（山东）有限责任公司龙口南山路证券营业部",
        },
    )
    assert row["ts_code"] == "603279.SH"
    assert row["trade_date"] == "20230103"
    assert row["vol"] == 50.0
    assert row["seller"] == "中信证券(山东)有限责任公司龙口南山路证券营业部"
    assert "amount" not in row


def test_v2_canon_sz_row_strips_comma_and_adds_suffix():
    row = canon_exchange_row(
        "sz", "20230103",
        {
            "zqdh": "002607",
            "cjjg": "4.57",
            "cjgsnew": "3,060.00",
            "bxwmc": "长城证券股份有限公司广东分公司",
            "sxwmc": "海通证券股份有限公司上海徐汇区柳州路证券营业部",
        },
    )
    assert row["ts_code"] == "002607.SZ"
    assert row["vol"] == 3060.0


def test_v2_vol_2dp_uses_half_up_not_banker_rounding():
    assert _vol_2dp(13.145) == 13.15
    assert _vol_2dp(131450 / 1e4) == 13.15


def test_v2_canon_normalizes_fullwidth_brackets():
    row = canon_exchange_row("sz", "20230103", {"bxwmc": "中信证券（山东）"})
    assert row["buyer"] == "中信证券(山东)"


def test_v2_canon_unknown_market_raises():
    with pytest.raises(ValueError, match="market"):
        canon_exchange_row("bj", "20230103", {})


def test_v2_canon_strips_html_tags_from_string_fields():
    row = canon_exchange_row(
        "sh", "20230103",
        {"stockid": "<b>603279</b>", "branchbuy": "<span>机构专用</span>", "branchsell": "乙"},
    )
    assert row["ts_code"] == "603279.SH"
    assert row["buyer"] == "机构专用"


def test_v2_canon_rounds_price_to_two_decimal_places():
    # 基金价格妙想可能 3 位, 交易所网页 2 位 (CLAUDE.md 512480.SH 案例) —— canon
    # 后价格也要归到 2 位, 不能只归 vol。
    row = canon_exchange_row("sh", "20240410", {"stockid": "512480", "tradeprice": "0.665"})
    assert row["price"] == 0.67


# --------------------------------------------------------------------- prepare --
# 非 V/T-编号: prepare(execute=False) 的最小接线冒烟 (dry-run 不连库不落盘)。

def test_prepare_dry_run_does_not_touch_db_or_disk(tmp_path):
    plan = prepare("block_trade", run_id="dry1", execute=False, archive_dir=tmp_path)
    assert plan["dry_run"] is True
    assert plan["would_add_columns"] == ["seq INTEGER", "security_type VARCHAR", "trade_unit VARCHAR"]
    assert not (tmp_path / "raw_tushare_block_trade_pre_reland_dry1.parquet").exists()


# ============================================================= T1-T18: V1 验收判据 ==

# ------------------------------------------------------------ T1/T1b/T2/T4: canon --

def _bt_row(**overrides) -> dict:
    row = {
        "ts_code": "600000.SH", "trade_date": "20230103", "price": 10.0,
        "vol": 5.0, "buyer": "b", "seller": "s",
    }
    row.update(overrides)
    return row


def test_t1_block_trade_bracket_style_normalizes_to_a_match():
    old = [_bt_row(buyer="中信证券（山东）")]
    new = [_bt_row(buyer="中信证券(山东)")]
    report = classify_old_keys("block_trade", old, new)
    assert isinstance(report, ClassReport)
    assert sum(report.matched.values()) == 1
    assert report.residual == []


def test_t1b_block_trade_bracket_style_normalizes_to_a_match_reversed():
    old = [_bt_row(buyer="中信证券(山东)")]
    new = [_bt_row(buyer="中信证券（山东）")]
    report = classify_old_keys("block_trade", old, new)
    assert sum(report.matched.values()) == 1
    assert report.residual == []


def test_t2_block_trade_vol_precision_reconciles_to_a_match():
    old = [_bt_row(vol=13.15)]
    new = [_bt_row(vol=13.1476)]
    report = classify_old_keys("block_trade", old, new)
    assert sum(report.matched.values()) == 1
    assert report.residual == []


def test_t4_top_inst_zero_null_amount_reconciles_to_a_match():
    old = [
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0",
         "buy": None, "sell": 100.004}
    ]
    new = [
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0",
         "buy": 0.0, "sell": 100.0}
    ]
    report = classify_old_keys("top_inst", old, new)
    assert sum(report.matched.values()) == 1
    assert report.residual == []


# ------------------------------------------------------------------- T3: merged --

def test_t3_two_parts_sum_within_tol_is_explained_merged():
    old = [_bt_row(vol=20.5)]
    new = [_bt_row(vol=10.24), _bt_row(vol=10.25)]
    report = classify_old_keys("block_trade", old, new)
    assert len(report.explained_merged) == 1
    assert report.residual == []


def test_t3b_two_parts_short_of_tol_stays_residual():
    old = [_bt_row(vol=20.5)]
    new = [_bt_row(vol=10.24), _bt_row(vol=10.24)]
    report = classify_old_keys("block_trade", old, new)
    assert report.explained_merged == []
    assert len(report.residual) == 1


def test_t3c_single_new_row_cannot_satisfy_min_parts_stays_residual():
    old = [_bt_row(vol=20.5)]
    new = [_bt_row(vol=20.51)]
    report = classify_old_keys("block_trade", old, new)
    assert report.explained_merged == []
    assert len(report.residual) == 1


def test_t3d_extra_unrelated_new_row_breaks_group_total_stays_residual():
    old = [_bt_row(vol=20.5)]
    new = [_bt_row(vol=10.24), _bt_row(vol=10.25), _bt_row(vol=30.0)]
    report = classify_old_keys("block_trade", old, new)
    assert report.explained_merged == []
    assert len(report.residual) == 1


def test_t3e_new_row_count_short_of_old_stays_residual_per_leftover():
    old = [_bt_row(vol=10.0), _bt_row(vol=10.0)]
    new = [_bt_row(vol=20.0)]
    report = classify_old_keys("block_trade", old, new)
    assert report.explained_merged == []
    assert len(report.residual) == 2


def test_t3e_isolated_merge_requires_new_rows_not_fewer_than_old():
    """条件 (c) 隔离用例: 组内 4 笔旧行 (30.0/1.0/1.0/1.0, 合计 33.0) 对 3 笔新行
    (10.0/20.0/3.0, 合计 33.0) —— 没有任何旧/新行 vol 完全相同, 所以四笔旧行全部
    留在同一组的 leftover 里 (不会被直接 key 匹配掉)。对这唯一一组:
      (a) 子集和成立: 10.0+20.0+3.0=33.0 == Σ旧, 且 3 笔 >= min_parts(2);
      (b) 组级 Σ旧(33.0) == Σ新(33.0), 在容差 0.01×4=0.04 内;
      (c) 不成立: len(新)=3 < len(旧)=4。
    只有 (c) 挡住合笔 —— 复核者把 (c) 写死成 True 时, (a)(b) 仍然成立, 会把这一整
    组 4 笔旧行全部错判为 explained_merged, 本用例必须变红 (钉死 mutation_cond_c)。
    """
    old = [_bt_row(vol=30.0), _bt_row(vol=1.0), _bt_row(vol=1.0), _bt_row(vol=1.0)]
    new = [_bt_row(vol=10.0), _bt_row(vol=20.0), _bt_row(vol=3.0)]
    report = classify_old_keys("block_trade", old, new)
    assert report.explained_merged == []
    assert len(report.residual) == 4
    residual_vols = sorted(r["key"][3] for r in report.residual)
    assert residual_vols == [1.0, 1.0, 1.0, 30.0]


def test_t3e_control_merge_succeeds_when_new_rows_not_fewer_than_old():
    """F1 的"该放行"对照 (防假杀): 同一份旧行 (30.0/1.0/1.0/1.0), 换一组同样合计
    33.0、笔数也是 4 (>= 旧笔数, 满足条件 c) 的新行 (10.0/20.0/2.5/0.5) —— 刻意
    不让任何一个 vol 与旧行重复, 免得先被直接 key 匹配掉、干扰"组内 4 笔对 4 笔"
    的隔离。(a)(b)(c) 三个条件此时都成立, 应整组判为 explained_merged; residual
    应为空。这条必须在条件 (c) 被写死成 True 时仍然保持绿 (它本来就该通过, 写死
    (c) 对已经为 True 的分支没有影响), 用来证明收紧 F1 的判据没有连带误杀这类
    合法的合笔。
    """
    old = [_bt_row(vol=30.0), _bt_row(vol=1.0), _bt_row(vol=1.0), _bt_row(vol=1.0)]
    new = [_bt_row(vol=10.0), _bt_row(vol=20.0), _bt_row(vol=2.5), _bt_row(vol=0.5)]
    report = classify_old_keys("block_trade", old, new)
    assert report.residual == []
    assert sum(report.matched.values()) == 0
    assert len(report.explained_merged) == 4


def test_t3f_top_inst_has_no_merged_rule_so_stays_residual():
    old = [
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0",
         "buy": 20.5, "sell": 0.0}
    ]
    new = [
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0",
         "buy": 10.24, "sell": 0.0},
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0",
         "buy": 10.26, "sell": 0.0},
    ]
    report = classify_old_keys("top_inst", old, new)
    assert len(report.residual) == 1


# ------------------------------------------------------------------ T5: invariant --

def test_t5_partition_invariant_raises_when_a_key_is_double_counted(monkeypatch):
    import scripts.reland_event_domain as red

    old = [_bt_row()]
    new = [_bt_row()]
    monkeypatch.setattr(red, "_matched_count", lambda old_n, new_n: min(old_n, new_n) + 1)
    with pytest.raises(AssertionError):
        classify_old_keys("block_trade", old, new)


# ---------------------------------------------------------- T7-T10: structural --

def test_t7_s1_null_index_col_fails_and_verdict_is_3():
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade (ts_code VARCHAR, trade_date VARCHAR, "
        "price DOUBLE, vol DOUBLE, buyer VARCHAR, seller VARCHAR, seq INTEGER)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('600000.SH','20230103',10.0,1.0,'b1','s1',1), "
        "('600000.SH','20230103',11.0,2.0,'b2','s2',2), "
        "('600000.SH','20230602',12.0,3.0,'b3','s3',NULL)"
    )
    structural = structural_checks(conn, "block_trade", "raw_tushare_block_trade")
    assert structural["S1"]["ok"] is False
    assert structural["S1"]["detail"] == ["20230602"]
    assert structural["S2"]["ok"] is True
    report = {"structural": structural, "residual_verdict_counts": _zero_counts()}
    assert verdict(report) == 3


def test_t8_s2_null_grain_column_fails_and_verdict_is_3():
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade (ts_code VARCHAR, trade_date VARCHAR, "
        "price DOUBLE, vol DOUBLE, buyer VARCHAR, seller VARCHAR, seq INTEGER)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('600000.SH','20230103',10.0,NULL,'b1','s1',1)"
    )
    structural = structural_checks(conn, "block_trade", "raw_tushare_block_trade")
    assert structural["S1"]["ok"] is True
    assert structural["S2"]["ok"] is False
    assert structural["S2"]["detail"] == {"vol": 1}
    report = {"structural": structural, "residual_verdict_counts": _zero_counts()}
    assert verdict(report) == 3


def test_t9_s3_duplicate_full_grain_rows_fails_and_verdict_is_3():
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade (ts_code VARCHAR, trade_date VARCHAR, "
        "price DOUBLE, vol DOUBLE, buyer VARCHAR, seller VARCHAR, seq INTEGER)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('600000.SH','20230103',10.0,5.0,'b','s',1), "
        "('600000.SH','20230103',10.0,5.0,'b','s',1)"
    )
    structural = structural_checks(conn, "block_trade", "raw_tushare_block_trade")
    assert structural["S3"]["ok"] is False
    report = {"structural": structural, "residual_verdict_counts": _zero_counts()}
    assert verdict(report) == 3


def test_t10_s4_top_inst_board_rank_gap_fails_and_verdict_is_3():
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_top_inst (trade_date VARCHAR, ts_code VARCHAR, "
        "reason VARCHAR, side VARCHAR, board_rank INTEGER)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_top_inst VALUES "
        "('20190102','600000.SH','R1','0',1), "
        "('20190102','600000.SH','R1','0',3)"
    )
    structural = structural_checks(conn, "top_inst", "raw_tushare_top_inst")
    assert structural["S3"]["ok"] is True
    assert structural["S4"]["ok"] is False
    report = {"structural": structural, "residual_verdict_counts": _zero_counts()}
    assert verdict(report) == 3


def test_t11_verdict_is_2_when_residual_present_and_structural_all_ok():
    report = {"structural": _all_ok_structural(), "residual_verdict_counts": _zero_counts(unverified=1)}
    assert verdict(report) == 2


# ------------------------------------------------------------ T13/T13b: record --

def test_t13_record_raises_on_nonzero_exit_and_writes_no_verified_row(tmp_path):
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade (ts_code VARCHAR, trade_date VARCHAR, "
        "price DOUBLE, vol DOUBLE, buyer VARCHAR, seller VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES ('600000.SH','20230103',10.0,5.0,'b','s')"
    )
    prepare("block_trade", run_id="t13", execute=True, conn=conn, archive_dir=tmp_path)
    # 模拟重落: 这一天变成一笔完全不相关的成交 (residual, kind=none), 但 seq 已填
    # (S1-S4 结构层面干净) —— exit_code 应为 2 (unverified>0), record 必须拒绝写账。
    conn.execute("DELETE FROM raw_tushare_block_trade WHERE trade_date = '20230103'")
    conn.execute(
        "INSERT INTO raw_tushare_block_trade "
        "(ts_code, trade_date, price, vol, buyer, seller, seq) VALUES "
        "('600001.SH','20230103',20.0,9.0,'x','y',1)"
    )
    with pytest.raises(RuntimeError):
        verify("block_trade", run_id="t13", record=True, conn=conn, archive_dir=tmp_path)
    scopes = {
        r[0] for r in conn.execute("SELECT delete_scope FROM mart_data_deletion_record").fetchall()
    }
    assert "rows_replaced_verified" not in scopes


def test_t13b_record_writes_verified_row_with_expected_verification_keys(tmp_path):
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade (ts_code VARCHAR, trade_date VARCHAR, "
        "price DOUBLE, vol DOUBLE, buyer VARCHAR, seller VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES ('600000.SH','20230103',10.0,5.0,'b','s')"
    )
    prepare("block_trade", run_id="t13b", execute=True, conn=conn, archive_dir=tmp_path)
    conn.execute("UPDATE raw_tushare_block_trade SET seq = 1 WHERE trade_date = '20230103'")

    report = verify("block_trade", run_id="t13b", record=True, conn=conn, archive_dir=tmp_path)
    assert report["exit_code"] == 0

    rows = conn.execute(
        "SELECT verification_json FROM mart_data_deletion_record "
        "WHERE delete_scope = 'rows_replaced_verified'"
    ).fetchall()
    assert len(rows) == 1
    verification = json.loads(rows[0][0])
    assert set(verification.keys()) == {
        "classes", "residual_verdict_counts", "structural_ok",
        "vendor_gaps_sha256", "exchange_files", "ceiling",
    }
    assert verification["exchange_files"] == []
    assert verification["ceiling"]["days_checked"] == []
    assert verification["ceiling"]["not_checked"] == []
    assert len(verification["vendor_gaps_sha256"]) == 64


# --------------------------------------------------------------- T14/T14b: loader --

def _valid_gap_item(**overrides) -> dict:
    item = {
        "domain": "block_trade",
        "trade_date": "20230103",
        "ts_code": "600000.SH",
        "key": ["600000.SH", "20230103", "10.0", "5.0", "b", "s"],
        "evidence": "上交所逐笔查询核对无此笔",
        "checked_at": "2026-09-12",
    }
    item.update(overrides)
    return item


def _write_gaps_yaml(tmp_path, doc) -> "Path":
    import yaml

    p = tmp_path / "vendor_gaps.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


def test_t14_loader_rejects_extra_top_level_key(tmp_path):
    p = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [], "extra": 1})
    with pytest.raises(ValueError):
        load_vendor_gaps(p)


def test_t14_loader_rejects_gap_item_missing_evidence(tmp_path):
    item = _valid_gap_item()
    del item["evidence"]
    p = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [item]})
    with pytest.raises(ValueError):
        load_vendor_gaps(p)


def test_t14_loader_rejects_bad_checked_at_format(tmp_path):
    p = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [_valid_gap_item(checked_at="2026/09/12")]})
    with pytest.raises(ValueError):
        load_vendor_gaps(p)


def test_t14_loader_rejects_unknown_domain(tmp_path):
    p = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [_valid_gap_item(domain="not_a_domain")]})
    with pytest.raises(ValueError):
        load_vendor_gaps(p)


def test_t14b_loader_returns_frozenset_with_the_registered_key(tmp_path):
    item = _valid_gap_item()
    p = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [item]})
    gaps = load_vendor_gaps(p)
    assert isinstance(gaps, frozenset)
    assert (item["domain"], tuple(item["key"])) in gaps


# ---------------------------------------------------------------------- T15: CLI --

def test_t15_main_verify_returns_the_verify_exit_code(monkeypatch, capsys):
    import scripts.reland_event_domain as red

    monkeypatch.setattr(
        red, "verify", lambda *a, **k: {"domain": "block_trade", "exit_code": 2}
    )
    rc = main(["verify", "--domain", "block_trade", "--run-id", "t15"])
    assert rc == 2
    out = capsys.readouterr().out
    assert '"exit_code": 2' in out


def test_t15b_main_returns_two_for_unverified_residual(monkeypatch, tmp_path, capsys):
    """T15 把 ``verify`` 整个打桩, 感觉不到 ``verdict()`` 里任何分支的变异 (复核者
    指出 V-m 变异是靠别的用例杀掉的)。本用例不桩 ``verify``, 只桩 ``connect``/
    ``db_path``/``ARCHIVE_DIR`` (main() 走的是生产路径, 这三个是唯一的注入点),
    让 :func:`main` 真正跑一遍 ``prepare -> verify -> verdict`` 的完整路径: S1-S4
    结构检查全过、恰好 1 条 residual (旧行 vol=5.0, 新行同一笔换成 vol=999.0,
    key 对不上也套不进合笔规则)、不传 ``--exchange-dir`` (没有交易所证据文件,
    residual 照 V1 语义全落 unverified)。删掉 ``verdict()`` 里
    ``unverified>0 -> 2`` 这条分支时, 这里断言的返回码 2 必须变红 (钉死
    mutation_verdict_unverified)。
    """
    import scripts.reland_event_domain as red

    old_rows = [dict(ts_code="600000.SH", trade_date="20230103", price=10.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [dict(ts_code="600000.SH", trade_date="20230103", price=10.0, vol=999.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "t15b", old_rows, new_rows)

    monkeypatch.setattr(red, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(red, "db_path", lambda *a, **k: tmp_path / "unused.duckdb")
    monkeypatch.setattr(red, "ARCHIVE_DIR", tmp_path)

    rc = main(["verify", "--domain", "block_trade", "--run-id", "t15b"])

    out = json.loads(capsys.readouterr().out)
    assert out["structural"]["S1"]["ok"] is True
    assert out["structural"]["S2"]["ok"] is True
    assert out["structural"]["S3"]["ok"] is True
    assert out["structural"]["S4"]["ok"] is True
    assert out["residual_verdict_counts"]["unverified"] == 1
    assert len(out["residual"]) == 1
    assert rc == 2


# --------------------------------------------------------------------- T16: exit0 --

def test_t16_three_identical_old_rows_all_matched_gives_exit_0(tmp_path):
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade (ts_code VARCHAR, trade_date VARCHAR, "
        "price DOUBLE, vol DOUBLE, buyer VARCHAR, seller VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('600000.SH','20230103',10.0,5.0,'b','s'), "
        "('600000.SH','20230103',10.0,5.0,'b','s'), "
        "('600000.SH','20230103',10.0,5.0,'b','s')"
    )
    prepare("block_trade", run_id="t16", execute=True, conn=conn, archive_dir=tmp_path)
    conn.execute("DELETE FROM raw_tushare_block_trade")
    conn.execute(
        "INSERT INTO raw_tushare_block_trade "
        "(ts_code, trade_date, price, vol, buyer, seller, seq) VALUES "
        "('600000.SH','20230103',10.0,5.0,'b','s',1), "
        "('600000.SH','20230103',10.0,5.0,'b','s',2), "
        "('600000.SH','20230103',10.0,5.0,'b','s',3)"
    )
    report = verify("block_trade", run_id="t16", record=True, conn=conn, archive_dir=tmp_path)
    assert report["exit_code"] == 0
    assert report["classes"]["matched"] == 3
    assert report["classes"]["old_total"] == 3
    count = conn.execute(
        "SELECT COUNT(*) FROM mart_data_deletion_record WHERE delete_scope = 'rows_replaced_verified'"
    ).fetchone()[0]
    assert count == 1


# ------------------------------------------------------------- T17/T18: residual --

def test_t17_top_inst_amount_within_tol_is_candidate_amount_close():
    old = [
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0",
         "buy": 6445400.0, "sell": 0.0}
    ]
    new = [
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0",
         "buy": 6445445.84, "sell": 0.0}
    ]
    report = classify_old_keys("top_inst", old, new)
    assert len(report.residual) == 1
    assert report.residual[0]["kind"] == "candidate_amount_close"


def test_t18_block_trade_residual_kind_candidate_and_none():
    old = [_bt_row(buyer="甲", seller="乙")]

    same_stock_diff_parties = [_bt_row(buyer="丙", seller="丁")]
    report_candidate = classify_old_keys("block_trade", old, same_stock_diff_parties)
    assert len(report_candidate.residual) == 1
    assert report_candidate.residual[0]["kind"] == "candidate"

    other_stock_no_rows = [_bt_row(ts_code="600001.SH")]
    report_none = classify_old_keys("block_trade", old, other_stock_no_rows)
    assert len(report_none.residual) == 1
    assert report_none.residual[0]["kind"] == "none"


# ===================================================== T6-T25: 交易所证据层 (V2) ==
# 每条对应 CLAUDE.md「切片 V2」里给出的一个隔离条件。编号不连续 (没有 T6a/T7-T11/
# T13-T18, 那些编号在 V2 规格里就没有分给交易所证据层) 是规格本身的编号。
#
# 证据文件字段名 (钉死): 上交所 stockid/tradeprice/tradeqty/branchbuy/branchsell;
# 深交所 zqdh/cjjg/cjgsnew/bxwmc/sxwmc —— 与 V2 canon 测试用的字段一致
# (:func:`canon_exchange_row` 的 ``_EXCHANGE_FIELDS``)。

def _exch_raw_row(market: str, code: str, price, vol, buyer: str, seller: str) -> dict:
    if market == "sh":
        return {
            "stockid": code, "tradeprice": str(price), "tradeqty": str(vol),
            "branchbuy": buyer, "branchsell": seller,
        }
    return {
        "zqdh": code, "cjjg": str(price), "cjgsnew": str(vol),
        "bxwmc": buyer, "sxwmc": seller,
    }


def _residual_item(ts_code, trade_date, price, vol, buyer, seller, kind="none") -> dict:
    return {
        "trade_date": trade_date,
        "key": [ts_code, trade_date, price, vol, buyer, seller],
        "kind": kind,
        "candidates": [],
    }


def _write_exchange_file(dir_path, market, date, exchange_rows, **extra) -> "Path":
    import json as _json
    from pathlib import Path as _Path

    doc = {"market": market, "trade_date": date, "exchange_rows": exchange_rows, **extra}
    p = _Path(dir_path) / f"exch_{market}_{date}.json"
    p.write_text(_json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def _setup_block_trade_reland(dir_path, run_id, old_rows, new_rows):
    """建旧 schema 表 (无 seq/security_type) -> 灌 old_rows -> ``prepare(execute=True)``
    归档 -> 清空 -> 按 new_rows (可选 seq/security_type, 缺省 EQA) 重新灌注, 模拟
    "重落完成"。``old_rows``/``new_rows`` 每条是 ``{ts_code, trade_date, price, vol,
    buyer, seller}`` (+ 新表可选 ``seq``/``security_type``)。返回打开的连接; 归档
    parquet 落在 ``dir_path`` (调用方同一个 tmp_path 也用来放交易所证据文件/
    vendor_gaps.yaml, 文件名各不相同不冲突)。
    """
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade ("
        "ts_code VARCHAR, trade_date VARCHAR, price DOUBLE, vol DOUBLE, "
        "buyer VARCHAR, seller VARCHAR)"
    )
    for r in old_rows:
        conn.execute(
            "INSERT INTO raw_tushare_block_trade "
            "(ts_code, trade_date, price, vol, buyer, seller) VALUES (?,?,?,?,?,?)",
            [r["ts_code"], r["trade_date"], r["price"], r["vol"], r["buyer"], r["seller"]],
        )
    prepare("block_trade", run_id=run_id, execute=True, conn=conn, archive_dir=dir_path)
    conn.execute("DELETE FROM raw_tushare_block_trade")
    # seq (multiplicity_index) 按 grain-去-seq 的分组从 1 开始编号 (S3 的连续性检查
    # 要求每组恰为 1..count), 不是全表流水号 —— 两笔内容不同的成交各自都是独立的组,
    # 各自的 seq 都该是 1, 不能编成 1/2。
    seq_counter: dict[tuple, int] = {}
    for r in new_rows:
        if "seq" in r:
            seq = r["seq"]
        else:
            gkey = (r["ts_code"], r["trade_date"], r["price"], r["vol"], r["buyer"], r["seller"])
            seq_counter[gkey] = seq_counter.get(gkey, 0) + 1
            seq = seq_counter[gkey]
        conn.execute(
            "INSERT INTO raw_tushare_block_trade "
            "(ts_code, trade_date, price, vol, buyer, seller, seq, security_type) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                r["ts_code"], r["trade_date"], r["price"], r["vol"], r["buyer"], r["seller"],
                seq, r.get("security_type", "EQA"),
            ],
        )
    return conn


# --------------------------------------------------------- T6/T6b-T6e: exchange_verdicts --

def test_t6_code_not_in_exchange_and_new_multiset_equals_exchange_is_old_vendor_error():
    exch_row = canon_exchange_row("sh", "20230103", _exch_raw_row("sh", "600001", "10.00", "5.00", "b", "s"))
    new_rows = [dict(ts_code="600001.SH", trade_date="20230103", price=10.0, vol=5.0,
                      buyer="b", seller="s", security_type="EQA")]
    # K (旧行) 价格跟交易所对不上 (10 vs 11), 但新表这个码的多重集恰好等于交易所。
    residual = [_residual_item("600001.SH", "20230103", 11.0, 5.0, "b", "s")]
    out = exchange_verdicts(residual, new_rows, [exch_row], "sh")
    assert len(out) == 1
    assert out[0]["reason"] == "old_vendor_error"


def test_t6b_code_in_exchange_but_missing_from_new_is_miaoxiang_gap():
    exch_row = canon_exchange_row("sh", "20230103", _exch_raw_row("sh", "600001", "10.00", "5.00", "b", "s"))
    residual = [_residual_item("600001.SH", "20230103", 10.0, 5.0, "b", "s")]
    # 该码新表零行 (只在交易所出现 -> _covered_codes 按后缀直接算覆盖)。
    out = exchange_verdicts(residual, [], [exch_row], "sh")
    assert len(out) == 1
    assert out[0]["reason"] == "miaoxiang_gap"


def test_t6c_code_not_in_exchange_and_new_multiset_diverges_is_new_diverges():
    exch_row = canon_exchange_row("sh", "20230103", _exch_raw_row("sh", "600001", "10.00", "5.00", "b", "s"))
    new_rows = [
        dict(ts_code="600001.SH", trade_date="20230103", price=10.0, vol=5.0,
             buyer="b", seller="s", security_type="EQA"),
        dict(ts_code="600001.SH", trade_date="20230103", price=20.0, vol=8.0,
             buyer="x", seller="y", security_type="EQA"),
    ]
    residual = [_residual_item("600001.SH", "20230103", 11.0, 5.0, "b", "s")]
    out = exchange_verdicts(residual, new_rows, [exch_row], "sh")
    assert len(out) == 1
    assert out[0]["reason"] == "new_diverges_from_exchange"


def test_t6d_bd0_bj_and_codes_whitelist_are_not_covered_and_produce_no_verdict():
    # BD0: 新表该码 security_type=BD0, 不覆盖 (即便后缀是 .SZ)。
    exch_row_bond = canon_exchange_row("sz", "20230103", _exch_raw_row("sz", "127001", "100.00", "5.00", "b", "s"))
    new_rows_bd0 = [dict(ts_code="127001.SZ", trade_date="20230103", price=100.0, vol=5.0,
                          buyer="b", seller="s", security_type="BD0")]
    residual_bd0 = [_residual_item("127001.SZ", "20230103", 100.0, 5.0, "b", "s")]
    assert exchange_verdicts(residual_bd0, new_rows_bd0, [exch_row_bond], "sz") == []

    # .BJ 后缀: 永远不属于 sh/sz 覆盖范围。
    residual_bj = [_residual_item("430001.BJ", "20230103", 100.0, 5.0, "b", "s")]
    assert exchange_verdicts(residual_bj, [], [exch_row_bond], "sz") == []

    # codes 白名单排除: 文件只覆盖 000001, K 是 000002.SZ。
    exch_row_other = canon_exchange_row("sz", "20230103", _exch_raw_row("sz", "000002", "5.00", "1.00", "b", "s"))
    residual_other = [_residual_item("000002.SZ", "20230103", 5.0, 1.0, "b", "s")]
    out = exchange_verdicts(residual_other, [], [exch_row_other], "sz", codes=["000001"])
    assert out == []


def test_t6e_sh_fund_three_decimal_price_matches_at_exchange_precision():
    # CLAUDE.md 案例: 512480.SH 妙想 3 位小数价格, 交易所网页 2 位。
    exch_row = canon_exchange_row("sh", "20240410", _exch_raw_row("sh", "512480", "0.67", "300", "b", "s"))
    new_rows = [dict(ts_code="512480.SH", trade_date="20240410", price=0.665, vol=300.0,
                      buyer="b", seller="s", security_type="FDO")]
    residual = [_residual_item("512480.SH", "20240410", 0.67, 300.0, "b", "s")]
    out = exchange_verdicts(residual, new_rows, [exch_row], "sh")
    assert len(out) == 1
    assert out[0]["reason"] == "matched_at_exchange_precision"


# ------------------------------------------------------------------------- T20 --

def test_t20_sz_thousands_comma_vol_matches_via_exchange_key():
    exch_row = canon_exchange_row("sz", "20230103", _exch_raw_row("sz", "002607", "4.57", "3,060.00", "b", "s"))
    new_rows = [dict(ts_code="002607.SZ", trade_date="20230103", price=4.57, vol=3060.0,
                      buyer="b", seller="s", security_type="EQA")]
    # 旧行本身价格对不上交易所 (差一分), 单独验证"千分位没挡住 NC==EX 的匹配"。
    residual = [_residual_item("002607.SZ", "20230103", 4.57, 3059.99, "b", "s")]
    out = exchange_verdicts(residual, new_rows, [exch_row], "sz")
    assert len(out) == 1
    assert out[0]["reason"] == "old_vendor_error"


# --------------------------------------------------------------------- T23: loader --

def _write_evidence_json(path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_t23_loader_rejects_unknown_key(tmp_path):
    p = tmp_path / "exch_sh_20230103.json"
    _write_evidence_json(p, {"market": "sh", "trade_date": "20230103", "exchange_rows": [], "bogus": 1})
    with pytest.raises(ValueError):
        load_exchange_evidence(p)


def test_t23_loader_rejects_recordcount_mismatch(tmp_path):
    p = tmp_path / "exch_sh_20230103.json"
    _write_evidence_json(p, {"market": "sh", "trade_date": "20230103", "exchange_rows": [], "recordcount": 1})
    with pytest.raises(ValueError):
        load_exchange_evidence(p)


def test_t23_loader_rejects_trade_date_not_matching_filename(tmp_path):
    p = tmp_path / "exch_sh_20230103.json"
    _write_evidence_json(p, {"market": "sh", "trade_date": "20230104", "exchange_rows": []})
    with pytest.raises(ValueError):
        load_exchange_evidence(p)


def test_t23_loader_rejects_illegal_market(tmp_path):
    p = tmp_path / "exch_sh_20230103.json"
    _write_evidence_json(p, {"market": "bj", "trade_date": "20230103", "exchange_rows": []})
    with pytest.raises(ValueError):
        load_exchange_evidence(p)


def test_t23_loader_rejects_source_recordcount_relation(tmp_path):
    p = tmp_path / "exch_sh_20230103.json"
    _write_evidence_json(p, {
        "market": "sh", "trade_date": "20230103", "exchange_rows": [{}],
        "recordcount": 1, "source_recordcount": 5, "excluded_b_share_rows": 1,
    })
    with pytest.raises(ValueError):
        load_exchange_evidence(p)


def test_t23_loader_accepts_valid_file_with_source_recordcount_and_excluded_rows(tmp_path):
    p = tmp_path / "exch_sh_20230103.json"
    _write_evidence_json(p, {
        "market": "sh", "trade_date": "20230103", "exchange_rows": [{}, {}],
        "recordcount": 2, "source_recordcount": 3, "excluded_b_share_rows": 1,
        "codes": ["600001"],
    })
    doc = load_exchange_evidence(p)
    assert doc["recordcount"] == 2
    assert doc["codes"] == ["600001"]


# --------------------------------------------------------------------- T24: CLI --

def test_t24_top_inst_with_exchange_dir_raises_and_main_returns_1(tmp_path):
    with pytest.raises(ValueError):
        verify("top_inst", run_id="t24", exchange_dir=tmp_path)
    rc = main(["verify", "--domain", "top_inst", "--run-id", "t24", "--exchange-dir", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------- T12/T12b: miaoxiang_gap + vendor_gaps --

def test_t12_miaoxiang_gap_unregistered_gives_exit_3(tmp_path):
    old_rows = [dict(ts_code="600001.SH", trade_date="20230103", price=10.0, vol=5.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "t12", old_rows, [])  # 重落后这笔彻底消失
    _write_exchange_file(tmp_path, "sh", "20230103",
                          [_exch_raw_row("sh", "600001", "10.00", "5.00", "b", "s")])
    gaps_path = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": []})

    report = verify("block_trade", run_id="t12", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, vendor_gaps_path=gaps_path)
    assert report["exit_code"] == 3
    assert report["residual_verdict_counts"]["miaoxiang_gap_unregistered"] == 1


def test_t12b_miaoxiang_gap_registered_gives_exit_0(tmp_path):
    old_rows = [dict(ts_code="600001.SH", trade_date="20230103", price=10.0, vol=5.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "t12b", old_rows, [])
    _write_exchange_file(tmp_path, "sh", "20230103",
                          [_exch_raw_row("sh", "600001", "10.00", "5.00", "b", "s")])
    gap_item = {
        "domain": "block_trade", "trade_date": "20230103", "ts_code": "600001.SH",
        "key": ["600001.SH", "20230103", "10.00", "5.00", "b", "s"],
        "evidence": "上交所逐笔查询核对无此笔", "checked_at": "2026-09-12",
    }
    gaps_path = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [gap_item]})

    report = verify("block_trade", run_id="t12b", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, vendor_gaps_path=gaps_path)
    assert report["exit_code"] == 0
    assert report["residual_verdict_counts"]["miaoxiang_gap_registered"] == 1
    assert report["residual_verdict_counts"]["unverified"] == 0


# --------------------------------------------------------------- T19: registered_gap_present --

def test_t19_registered_gap_reappearing_in_new_table_gives_exit_3(tmp_path):
    row = dict(ts_code="600006.SH", trade_date="20230109", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "t19", [row], [row])  # 旧新完全一致, residual=0
    gap_item = {
        "domain": "block_trade", "trade_date": "20230109", "ts_code": "600006.SH",
        # 之前登记的"缺口" key, 现在实际又出现在新表里了 (供应商回补/重落把它填回来了)。
        "key": ["600006.SH", "20230109", "10.00", "5.00", "b", "s"],
        "evidence": "误登记, 实际未缺", "checked_at": "2026-09-01",
    }
    gaps_path = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [gap_item]})
    empty_exchange_dir = tmp_path / "no_evidence_today"
    empty_exchange_dir.mkdir()

    report = verify("block_trade", run_id="t19", conn=conn, archive_dir=tmp_path,
                     exchange_dir=empty_exchange_dir, vendor_gaps_path=gaps_path)
    assert report["exit_code"] == 3
    assert report["residual_verdict_counts"]["registered_gap_present"] == 1


# --------------------------------------------------------------------- T21: ceiling gap --

def test_t21_ceiling_gap_unregistered_then_registered(tmp_path):
    old_rows = [dict(ts_code="600003.SH", trade_date="20230106", price=10.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [dict(ts_code="600003.SH", trade_date="20230106", price=10.0, vol=5.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "t21", old_rows, new_rows)
    _write_exchange_file(
        tmp_path, "sh", "20230106",
        [
            _exch_raw_row("sh", "600003", "10.00", "5.00", "b", "s"),  # 跟新/旧都匹配
            _exch_raw_row("sh", "600003", "20.00", "8.00", "x", "y"),  # 新旧都没有的额外一笔
        ],
    )
    gaps_empty = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": []})

    report = verify("block_trade", run_id="t21", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, vendor_gaps_path=gaps_empty)
    assert report["exit_code"] == 3
    assert report["ceiling"]["gap_unregistered"] == 1
    assert report["residual"] == []  # 这个缺口跟 residual/old 表完全无关, 纯 ceiling 层面

    gap_item = {
        "domain": "block_trade", "trade_date": "20230106", "ts_code": "600003.SH",
        "key": ["600003.SH", "20230106", "20.00", "8.00", "x", "y"],
        "evidence": "沪核对无此笔", "checked_at": "2026-09-12",
    }
    gaps_registered = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": [gap_item]})
    report2 = verify("block_trade", run_id="t21", conn=conn, archive_dir=tmp_path,
                      exchange_dir=tmp_path, vendor_gaps_path=gaps_registered)
    assert report2["exit_code"] == 0
    assert report2["ceiling"]["gap_registered"] == 1
    assert report2["ceiling"]["gap_unregistered"] == 0


# ------------------------------------------------------------------- T22: ceiling extra --

def test_t22_ceiling_extra_in_sh_market_gives_exit_3(tmp_path):
    old_rows = [dict(ts_code="600004.SH", trade_date="20230107", price=10.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [
        dict(ts_code="600004.SH", trade_date="20230107", price=10.0, vol=5.0, buyer="b", seller="s"),
        dict(ts_code="600004.SH", trade_date="20230107", price=30.0, vol=9.0, buyer="p", seller="q"),
    ]
    conn = _setup_block_trade_reland(tmp_path, "t22sh", old_rows, new_rows)
    _write_exchange_file(tmp_path, "sh", "20230107",
                          [_exch_raw_row("sh", "600004", "10.00", "5.00", "b", "s")])
    gaps_path = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": []})

    report = verify("block_trade", run_id="t22sh", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, vendor_gaps_path=gaps_path)
    assert report["exit_code"] == 3
    assert report["ceiling"]["extra_sh"] >= 1


def test_t22b_ceiling_extra_in_sz_market_gives_exit_2(tmp_path):
    old_rows = [dict(ts_code="000005.SZ", trade_date="20230108", price=10.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [
        dict(ts_code="000005.SZ", trade_date="20230108", price=10.0, vol=5.0, buyer="b", seller="s"),
        dict(ts_code="000005.SZ", trade_date="20230108", price=30.0, vol=9.0, buyer="p", seller="q"),
    ]
    conn = _setup_block_trade_reland(tmp_path, "t22sz", old_rows, new_rows)
    _write_exchange_file(tmp_path, "sz", "20230108",
                          [_exch_raw_row("sz", "000005", "10.00", "5.00", "b", "s")])
    gaps_path = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": []})

    report = verify("block_trade", run_id="t22sz", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, vendor_gaps_path=gaps_path)
    assert report["exit_code"] == 2
    assert report["ceiling"]["extra_sz"] >= 1


# ---------------------------------------------------------- T25: explained_merged proved --

def test_t25_explained_merged_marked_proved_when_new_multiset_matches_exchange(tmp_path):
    old_rows = [dict(ts_code="600002.SH", trade_date="20230105", price=10.0, vol=20.5, buyer="b", seller="s")]
    new_rows = [
        dict(ts_code="600002.SH", trade_date="20230105", price=10.0, vol=10.24, buyer="b", seller="s"),
        dict(ts_code="600002.SH", trade_date="20230105", price=10.0, vol=10.25, buyer="b", seller="s"),
    ]
    conn = _setup_block_trade_reland(tmp_path, "t25", old_rows, new_rows)
    _write_exchange_file(
        tmp_path, "sh", "20230105",
        [
            _exch_raw_row("sh", "600002", "10.00", "10.24", "b", "s"),
            _exch_raw_row("sh", "600002", "10.00", "10.25", "b", "s"),
        ],
    )
    gaps_path = _write_gaps_yaml(tmp_path, {"version": 1, "gaps": []})

    report = verify("block_trade", run_id="t25", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, vendor_gaps_path=gaps_path)
    assert report["classes"]["explained_merged"] == 1
    assert report["classes"]["explained_merged_proved"] == 1
    assert len(report["explained_merged"]) == 1
    assert report["explained_merged"][0].get("proved") is True
