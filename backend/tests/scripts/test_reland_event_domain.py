"""reland_event_domain: 事件域历史重落脚本 (grain 契约 r2 §3, 施工切片 S7)。

用例编号 V1-V6 对应 r2 §4 S7 表里的字面用例, V4c/V4d/V5a/V5b/V5c/V6b 是 2026-09-11
主会话返工追加的 (真实交易所字段名 + `_vol_2dp` + 三边名字归一 + 旧⊆新的精度/
措辞容忍) (V7-V9 测的是 ``recon_assignment_gaps.py``/``assignment_gap_recon.py``
的 top_inst / block_trade 段, 物理上放在 ``test_assignment_gap_recon.py`` —
它们测的是那个模块的函数, 不是本文件的)。全部用内存 DuckDB
(``conftest.duck_mem``) / ``tmp_path``, 不连生产库、不跑网络 (本任务规则 3)。
"""
from __future__ import annotations

import json

import pytest

from conftest import duck_mem

from scripts.reland_event_domain import (
    DOMAIN_OLD_SUBSET_KEY,
    ArchiveResult,
    SubsetReport,
    _apply_vol_2dp,
    _vol_2dp,
    archive_table,
    canon_exchange_row,
    compare_day_three_way,
    null_index_dates,
    old_subset_of_new,
    prepare,
    verify,
)


def _stub_registry(domain, *, grain, multiplicity_index=None, source="miaoxiang"):
    """Minimal sync_registry-shaped dict, same pattern as
    ``test_recon_compare.py``'s ``_stub_registry`` — grain 契约测试不读生产 yaml
    (规则 3), 只测 compare_day_three_way 自己怎么组装三对 compare_rows。
    """
    entry: dict = {"source": source, "grain": list(grain)}
    if multiplicity_index is not None:
        entry["multiplicity_index"] = multiplicity_index
        entry["duplicate_rows"] = "event"
        entry["write_mode"] = "replace_partition"
        entry["partition_by"] = [grain[0]]
    return {"domains": {domain: entry}}


BLOCK_TRADE_GRAIN = ["ts_code", "trade_date", "price", "vol", "buyer", "seller", "seq"]


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


# ----------------------------------------------------------------- V4: old subset --

def test_v4_old_subset_of_new_ok_missing_and_short():
    old = [{"k": "A"}, {"k": "A"}]  # old {A: 2}

    ok_report = old_subset_of_new(old, [{"k": "A"}, {"k": "A"}, {"k": "A"}, {"k": "B"}], ["k"])
    assert isinstance(ok_report, SubsetReport)
    assert ok_report.ok is True
    assert ok_report.missing_keys == []
    assert ok_report.short_counts == []

    short_report = old_subset_of_new(old, [{"k": "A"}], ["k"])
    assert short_report.ok is False
    assert short_report.short_counts == ["A"]
    assert short_report.missing_keys == []

    missing_report = old_subset_of_new(old, [{"k": "B"}], ["k"])
    assert missing_report.ok is False
    assert missing_report.missing_keys == ["A"]


def test_v4_old_subset_of_new_multi_column_key():
    old = [{"a": 1, "b": "x"}]
    new = [{"a": 1, "b": "x"}, {"a": 1, "b": "x"}]
    report = old_subset_of_new(old, new, ["a", "b"])
    assert report.ok is True
    assert report.old_total == 1
    assert report.new_total == 2


def test_v4c_verify_reconciles_vol_precision_for_block_trade(tmp_path):
    # 旧行只有百股精度 (13.15), 重落后是妙想精确股数/1e4 (13.1476) —— 旧⊆新不该把
    # 这条纯精度差异当成"新表丢了旧数据"。走真实 verify() 路径 (不是绕过它直接调用
    # old_subset_of_new), 这样才能被 M_V4c (旧⊆新不做 _vol_2dp) 变异钉住。
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_tushare_block_trade ("
        "ts_code VARCHAR, trade_date VARCHAR, price DOUBLE, vol DOUBLE, "
        "buyer VARCHAR, seller VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('600000.SH','20230103',10.0,13.15,'b','s')"
    )
    prepare("block_trade", run_id="v4c", execute=True, conn=conn, archive_dir=tmp_path)
    # 模拟重落: 同一笔成交换成妙想精确股数, 其余列不变。
    conn.execute("DELETE FROM raw_tushare_block_trade WHERE trade_date = '20230103'")
    conn.execute(
        "INSERT INTO raw_tushare_block_trade "
        "(ts_code, trade_date, price, vol, buyer, seller, seq) VALUES "
        "('600000.SH','20230103',10.0,13.1476,'b','s',1)"
    )
    report = verify("block_trade", run_id="v4c", conn=conn, archive_dir=tmp_path)
    assert report["dates_ok"] == ["20230103"]
    assert report["dates_old_not_subset"] == []


def test_v4d_old_subset_of_new_top_inst_key_ignores_reason_wording():
    # top_inst 的旧⊆新键排除 reason: 旧行的理由措辞与新行的规范化理由字符串不是
    # 同一份文本; 同一席位可以合法同时上多个榜, 新计数 >= 旧计数即算覆盖。
    key_cols = list(DOMAIN_OLD_SUBSET_KEY["top_inst"])
    old = [{"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0", "reason": "A"}]
    new = [
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0", "reason": "B"},
        {"trade_date": "20190102", "ts_code": "600000.SH", "exalter": "甲", "side": "0", "reason": "C"},
    ]
    report = old_subset_of_new(old, new, key_cols)
    assert report.ok is True
    assert report.missing_keys == []
    assert report.short_counts == []


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


# ---------------------------------------------------------------------- V5: canon --
# 字段名 2026-09-11 主会话实测更正为真实抓取字段 (上交所逐笔 JSONP sqlId
# COMMON_SSE_XXPL_JYXXPL_DZJYXX_L_1 / 深交所协议交易逐笔 CATALOGID=1265) —— 原先
# vol_wan/jybm/cjjgnew/yyb1mc/yyb2mc 等是占位, 已全部替换。

def test_v5a_canon_sh_row_adds_suffix_and_normalizes_seller():
    row = canon_exchange_row(
        {
            "stockid": "603279",
            "tradeprice": "28",
            "tradeqty": "50",
            "tradeamount": "1400",
            "branchbuy": "机构专用",
            "branchsell": "中信证券（山东）有限责任公司龙口南山路证券营业部",
        },
        market="sh",
    )
    assert row["ts_code"] == "603279.SH"
    assert row["vol"] == 50.0
    assert row["seller"] == "中信证券(山东)有限责任公司龙口南山路证券营业部"


def test_v5b_canon_sz_row_strips_comma_and_adds_suffix():
    row = canon_exchange_row(
        {
            "zqdh": "002607",
            "cjjg": "4.57",
            "cjgsnew": "3,060.00",
            "cjjenew": "13,984.20",
            "bxwmc": "长城证券股份有限公司广东分公司",
            "sxwmc": "海通证券股份有限公司上海徐汇区柳州路证券营业部",
        },
        market="sz",
    )
    assert row["ts_code"] == "002607.SZ"
    assert row["vol"] == 3060.0
    assert row["amount"] == 13984.2


def test_v5c_vol_2dp_uses_half_up_not_banker_rounding():
    assert _vol_2dp(13.145) == 13.15
    assert _vol_2dp(131450 / 1e4) == 13.15


def test_v5_canon_strips_thousands_comma():
    row = canon_exchange_row({"cjgsnew": "3,060.00"}, market="sz")
    assert row["vol"] == 3060.0


def test_v5_canon_vol_matches_miaoxiang_precise_share_count():
    row = canon_exchange_row({"tradeqty": "13.15"}, market="sh")
    miaoxiang_vol = _vol_2dp(131476 / 1e4)
    assert row["vol"] == miaoxiang_vol == 13.15


def test_v5_canon_normalizes_fullwidth_brackets():
    row = canon_exchange_row({"bxwmc": "中信证券（山东）"}, market="sz")
    assert row["buyer"] == "中信证券(山东)"


def test_v5_canon_amount_is_direct_no_unit_conversion():
    # 深市金额本来就是万元, 直接比, 这里只验证去逗号不额外换算。
    row = canon_exchange_row({"cjjenew": "13,984.20"}, market="sz")
    assert row["amount"] == 13984.20


def test_v5_canon_unknown_market_raises():
    with pytest.raises(ValueError, match="market"):
        canon_exchange_row({}, market="bj")


def test_v5_canon_strips_html_tags_from_string_fields():
    row = canon_exchange_row(
        {"stockid": "<b>603279</b>", "branchbuy": "<span>机构专用</span>", "branchsell": "乙"},
        market="sh",
    )
    assert row["ts_code"] == "603279.SH"
    assert row["buyer"] == "机构专用"


# ------------------------------------------------------------------ V6: three-way --

def test_v6_compare_day_three_way_all_pairs_identity_with_a_duplicate_pair():
    registry = _stub_registry(
        "block_trade", grain=BLOCK_TRADE_GRAIN, multiplicity_index="seq"
    )
    row_a = {
        "ts_code": "600000.SH",
        "trade_date": "20260827",
        "price": 10.0,
        "vol": 1.48,
        "buyer": "甲",
        "seller": "乙",
    }
    # "含一对全同行": 三边都放两笔内容完全相同的独立成交 (event 域的合法形态)。
    local = [dict(row_a, seq=1), dict(row_a, seq=2)]
    miaoxiang = [dict(row_a), dict(row_a)]
    exch_row = canon_exchange_row(
        {
            "zqdh": "600000.SH",
            "cjjg": "10.00",
            "cjgsnew": "1.48",
            "cjjenew": "14.8",
            "bxwmc": "甲",
            "sxwmc": "乙",
        },
        market="sz",
    )
    exch_row["trade_date"] = "20260827"
    exchange = [dict(exch_row), dict(exch_row)]

    out = compare_day_three_way(local, miaoxiang, exchange, registry=registry)
    assert set(out) == {"local_vs_miaoxiang", "local_vs_exchange", "miaoxiang_vs_exchange"}
    for name, pair in out.items():
        assert pair["identity"] is True, f"{name}: {pair}"
        assert pair["right_collapse"] == 1 or pair["left_collapse"] == 1


def test_v6_compare_day_three_way_rounds_vol_before_comparing():
    registry = _stub_registry(
        "block_trade", grain=BLOCK_TRADE_GRAIN, multiplicity_index="seq"
    )
    precise = {
        "ts_code": "600000.SH",
        "trade_date": "20260827",
        "price": 10.0,
        "vol": 13.1476,
        "buyer": "甲",
        "seller": "乙",
        "seq": 1,
    }
    exch_row = canon_exchange_row({"tradeqty": "13.15"}, market="sh")
    exch_row.update(
        {"ts_code": "600000.SH", "trade_date": "20260827", "price": 10.0,
         "buyer": "甲", "seller": "乙"}
    )
    out = compare_day_three_way([precise], [dict(precise)], [exch_row], registry=registry)
    assert out["local_vs_exchange"]["identity"] is True


def test_v6b_compare_day_three_way_normalizes_names_on_all_three_sides():
    # 2026-09-11 主会话复核: 之前只有交易所一侧过 normalize_cn_name —— 本地/妙想
    # 两侧的名字来源也可能带全角括号 (同一家营业部在不同披露渠道格式不统一)。
    registry = _stub_registry(
        "block_trade", grain=BLOCK_TRADE_GRAIN, multiplicity_index="seq"
    )
    local_row = {
        "ts_code": "600000.SH",
        "trade_date": "20260827",
        "price": 10.0,
        "vol": 1.48,
        "buyer": "甲",
        "seller": "中信证券（山东）分公司",
        "seq": 1,
    }
    miaoxiang_row = {k: v for k, v in local_row.items() if k != "seq"}
    exch_row = canon_exchange_row(
        {
            "zqdh": "600000.SH",
            "cjjg": "10",
            "cjgsnew": "1.48",
            "cjjenew": "14.8",
            "bxwmc": "甲",
            "sxwmc": "中信证券(山东)分公司",
        },
        market="sz",
    )
    exch_row["trade_date"] = "20260827"

    out = compare_day_three_way([local_row], [miaoxiang_row], [exch_row], registry=registry)
    for name, pair in out.items():
        assert pair["identity"] is True, f"{name}: {pair}"


# --------------------------------------------------------------------- prepare/verify --
# 非 V-编号: prepare(execute=False) 与 verify() 的最小接线冒烟 (r2 §4 S7 的字面用例表
# 没有单独给它们编号 —— 它们是编排层, 组合上面已测过的部件; 这里只保证接线不炸,
# 不重复穷举内部每个分支)。

def test_prepare_dry_run_does_not_touch_db_or_disk(tmp_path):
    plan = prepare("block_trade", run_id="dry1", execute=False, archive_dir=tmp_path)
    assert plan["dry_run"] is True
    assert plan["would_add_columns"] == ["seq INTEGER", "security_type VARCHAR", "trade_unit VARCHAR"]
    assert not (tmp_path / "raw_tushare_block_trade_pre_reland_dry1.parquet").exists()


def test_verify_smoke_reports_null_dates_and_old_subset(tmp_path):
    conn = duck_mem()
    _block_trade_table(conn)
    prepare("block_trade", run_id="rv1", execute=True, conn=conn, archive_dir=tmp_path)

    # 模拟 chunkyctl sync 已完成 20230103 / 20230602 两天的 replace_partition 重落
    # (DELETE 旧分区 + INSERT 新行, seq 已填, 新行是旧内容的超集); 20260101 是全新
    # 一天, 表里从未有过 —— 天然 seq NULL, 代表"还没重落到这天"。
    conn.execute(
        "DELETE FROM raw_tushare_block_trade WHERE trade_date IN ('20230103','20230602')"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade "
        "(ts_code, trade_date, price, vol, buyer, seller, seq) VALUES "
        "('600000.SH','20230103',10.0,1.0,'b1','s1',1), "
        "('600000.SH','20230103',11.0,2.0,'b2','s2',1), "
        "('600001.SH','20230103',20.0,5.0,'b4','s4',1), "
        "('600000.SH','20230602',12.0,3.0,'b3','s3',1)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_block_trade "
        "(ts_code, trade_date, price, vol, buyer, seller, seq) VALUES "
        "('600002.SH','20260101',30.0,6.0,'b5','s5', NULL)"
    )
    report = verify("block_trade", run_id="rv1", conn=conn, archive_dir=tmp_path)
    assert set(report["dates_ok"]) == {"20230103", "20230602"}
    assert report["dates_old_not_subset"] == []
    assert report["dates_null"] == ["20260101"]
    assert report["three_way"] is None


def test_verify_record_writes_second_ledger_row(tmp_path):
    conn = duck_mem()
    _block_trade_table(conn)
    prepare("block_trade", run_id="rv2", execute=True, conn=conn, archive_dir=tmp_path)
    verify("block_trade", run_id="rv2", record=True, conn=conn, archive_dir=tmp_path)
    rows = conn.execute(
        "SELECT delete_scope FROM mart_data_deletion_record ORDER BY delete_scope"
    ).fetchall()
    scopes = {r[0] for r in rows}
    assert scopes == {"rows_replaced_by_partition_reland", "rows_replaced_verified"}
