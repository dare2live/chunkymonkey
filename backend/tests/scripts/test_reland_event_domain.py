"""reland_event_domain: 事件域历史重落脚本 (grain 契约 r2 §3, 施工切片 S7 + V1/V2
验收判据重落 + 2026-09-12 格级验收判据切片 C2)。

用例编号 V1-V4 对应 r2 §4 S7 表里的字面用例 (archive / record / DDL, 均沿用未改动
的实现)。V4 系列里专测已删除的 ``old_subset_of_new``/输出字段
``dates_ok``/``dates_old_not_subset`` 的用例已删除 (不留墓碑) —— 它们的意图 (旧⊆新
不该把精度/措辞差异当缺失) 由下面的 T2/T4 接手, 走的是新判据
:func:`classify_old_keys`。

T1-T18 是 2026-09-12 验收判据重落 V1 (分类框架 + 结构检查 + 退出码 + 记账闸) 新增的
字面用例, 每条对应规格里给出的一个隔离条件。编号不连续 (没有 T6/T12, 那两个编号在
V2 规格里指的是下面的交易所证据层用例) 是规格本身的编号, 不是本文件遗漏。

T6/T6b-T6e/T20/T23*/T24/T25 是同日 V2 (交易所证据层: ``load_exchange_evidence``/
``canon_exchange_row`` 新签名/``exchange_key``/``exchange_verdicts``) 新增的字面
用例, 沿用未改动。原 V5 (``canon_exchange_row`` 旧签名 ``(row, *, market)``, 带
``amount``) 与 V6 (``compare_day_three_way`` 三方比对, --exchange-json/--date 单日
路径) 的用例随对应函数一起删除 (V2 规格头注 K 点名允许改写这两个符号, "不留墓碑")
—— V5 覆盖的规范化行为 (千分位逗号/百股精度/全角括号/HTML 标签/未知 market) 由下面
新签名的 T20 (千分位) 与 canon 相关用例接手, 不是凭空消失。

C1-C27 是 2026-09-12 格级验收判据切片 C2 (bt_residual_classes_r1.md §6 C2 表
V1-V27) 新增的字面用例, 编号加前缀 ``c`` 避免与上面的 T 编号/V1-V6 (r2 §4) 混淆。
取代了原 T12/T12b (miaoxiang_gap 经 ``vendor_gaps.yaml`` 登记) / T14/T14b
(``load_vendor_gaps`` loader) / T19 (``registered_gap_present``) / T21 (ceiling
gap 经 ``vendor_gaps.yaml``) / T22/T22b (ceiling extra 沪/深差别对待) —— 这些
函数/字段/差别对待整个被格级登记表 (``services.exchange_cell_verdicts``) + 场所
归属 (``resolve_venue``) + 身份层 (``identity_pass``) 取代, 不留墓碑 (改动清单见
``backend/scripts/reland_event_domain.py`` 头注 J/N/O/P/Q)。``load_vendor_gaps``
自己的隔离用例 (loader 校验) 已被格级登记表 loader
(``services.exchange_cell_verdicts.load_exchange_cell_verdicts``) 的 L1-L21 隔离
用例取代, 那份在 ``tests/services/test_exchange_cell_verdicts.py`` 里, 不在本文件
重复。

(V7-V9 测的是 ``recon_assignment_gaps.py``/``assignment_gap_recon.py`` 的 top_inst /
block_trade 段, 物理上放在 ``test_assignment_gap_recon.py`` — 它们测的是那个模块的
函数, 不是本文件的。)

全部用内存 DuckDB (``conftest.duck_mem``) / ``tmp_path``, 不连生产库、不跑网络
(本任务规则 3)。交易所证据文件全部用 ``tmp_path`` 现造 (证据文件的格式钉死在
``load_exchange_evidence`` 自己的头注里, 不在任何文档小节)。T7-T10/T16 里
``structural_checks`` 读的是仓库里真实的 ``backend/config/sync_registry.yaml``
(S2/S3 的规格明文要求"sync_registry 该域 grain", 不是调用方可注入的假 grain) ——
这是有意的耦合, 不是漏配置。C1-C27 未显式传 ``cell_verdicts_path`` 的调用同样读
仓库里真实的 ``backend/config/exchange_cell_verdicts.yaml`` (其条数随人工裁决增减, 用例不许断言当时的条数,
是一份普通的、随代码一起提交的 typed YAML 配置, 不是运行时数据, 与
``structural_checks`` 读真实 ``sync_registry.yaml`` 同一性质)。
"""
from __future__ import annotations

import json

import pytest

from conftest import duck_mem

from scripts.reland_event_domain import (
    ArchiveResult,
    ClassReport,
    DOMAIN_CANON,
    archive_table,
    canon_exchange_row,
    cell_compare,
    classify_old_keys,
    emit_candidate_verdicts,
    exchange_key,
    exchange_verdicts,
    identity_pass,
    load_exchange_evidence,
    main,
    null_index_dates,
    prepare,
    resolve_venue,
    structural_checks,
    verdict,
    verify,
    _assert_residual_verdict_partition,
    _vol_2dp,
)
from services.exchange_cell_verdicts import _DOMAIN_CELL_COLS, load_exchange_cell_verdicts


def _zero_counts(**overrides) -> dict:
    counts = {
        "unverified": 0,
        "old_vendor_error": 0,
        "old_vendor_error_absent_both": 0,
        "miaoxiang_gap_registered": 0,
        "miaoxiang_gap_unregistered": 0,
        "new_diverges_from_exchange": 0,
        "matched_at_exchange_precision": 0,
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
    assert plan["would_add_columns"] == [
        "seq INTEGER", "security_type VARCHAR", "trade_unit VARCHAR", "vendor_market VARCHAR",
    ]
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
        "registry_sha256", "consumed_cells", "stale_cells",
        "identity", "venue", "not_checked", "exchange_files",
    }
    assert verification["exchange_files"] == []
    assert verification["not_checked"] == []
    assert verification["consumed_cells"] == []
    assert verification["stale_cells"] == []
    assert verification["identity"]["skipped"] is True
    assert len(verification["registry_sha256"]) == 64


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
# 每条对应 V2 (2026-09-12 同日的交易所证据层切片) 规格里给出的一个隔离条件, 不在
# 任何 CLAUDE.md 小节 (曾经的悬空引用已改正, 见 N7)。编号不连续 (没有 T6a/T7-T11/
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
    """建旧 schema 表 (无 seq/security_type/vendor_market) -> 灌 old_rows ->
    ``prepare(execute=True)`` 归档 -> 清空 -> 按 new_rows (可选
    seq/security_type/vendor_market, 缺省 EQA/NULL) 重新灌注, 模拟"重落完成"。
    ``old_rows``/``new_rows`` 每条是 ``{ts_code, trade_date, price, vol, buyer,
    seller}`` (+ 新表可选 ``seq``/``security_type``/``vendor_market``, C2 场所归属
    测试用)。返回打开的连接; 归档 parquet 落在 ``dir_path`` (调用方同一个 tmp_path
    也用来放交易所证据文件/登记 YAML, 文件名各不相同不冲突)。
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
            "(ts_code, trade_date, price, vol, buyer, seller, seq, security_type, vendor_market) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                r["ts_code"], r["trade_date"], r["price"], r["vol"], r["buyer"], r["seller"],
                seq, r.get("security_type", "EQA"), r.get("vendor_market"),
            ],
        )
    return conn


def _write_both_exchange_files(dir_path, date, *, sh_rows=(), sz_rows=()):
    """C2 helper: 同一天 sh/sz 两份证据文件一起写 (哪怕某一侧是空列表), 避免
    ``not_checked`` 意外把 exit_code 拉到 2, 污染只想测别的条件的用例 (每个用例
    只有一个条件为假)。"""
    _write_exchange_file(dir_path, "sh", date, list(sh_rows))
    _write_exchange_file(dir_path, "sz", date, list(sz_rows))


def _write_cell_verdicts_yaml(tmp_path, entries) -> "Path":
    import yaml as _yaml

    p = tmp_path / "cell_verdicts.yaml"
    p.write_text(_yaml.safe_dump({"version": 1, "entries": entries}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def _cell_entry(
    cells, *, exchange_unmatched=(), vendor_unmatched=(), text_pairs=(),
    truth_side="unknown", evidence="test evidence", checked_at="2026-09-12", domain="block_trade",
) -> dict:
    return {
        "domain": domain,
        "cells": [list(c) for c in cells],
        "exchange_unmatched": [list(r) for r in exchange_unmatched],
        "vendor_unmatched": [list(r) for r in vendor_unmatched],
        "text_pairs": [list(p) for p in text_pairs],
        "truth_side": truth_side,
        "evidence": evidence,
        "checked_at": checked_at,
    }


def _write_code_changes_yaml(tmp_path, events) -> "Path":
    import yaml as _yaml

    p = tmp_path / "code_changes.yaml"
    p.write_text(_yaml.safe_dump({"version": 1, "events": events}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def _code_change_event(
    old_code="300114.SZ", new_code="302132.SZ", effective_date="20250217", **overrides
) -> dict:
    event = {
        "old_code": old_code, "new_code": new_code, "effective_date": effective_date,
        "exchange": "SZSE", "kind": "reorg_rename", "source_kind": "announcement",
        "source_ref": "test fixture", "checked_at": "2026-09-12",
    }
    event.update(overrides)
    return event


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


# ---------------------------------------------------------- T25: explained_merged proved --

def test_t25_explained_merged_marked_proved_when_new_multiset_matches_exchange(tmp_path):
    old_rows = [dict(ts_code="600002.SH", trade_date="20230105", price=10.0, vol=20.5, buyer="b", seller="s")]
    new_rows = [
        dict(ts_code="600002.SH", trade_date="20230105", price=10.0, vol=10.24, buyer="b", seller="s"),
        dict(ts_code="600002.SH", trade_date="20230105", price=10.0, vol=10.25, buyer="b", seller="s"),
    ]
    conn = _setup_block_trade_reland(tmp_path, "t25", old_rows, new_rows)
    _write_both_exchange_files(
        tmp_path, "20230105",
        sh_rows=[
            _exch_raw_row("sh", "600002", "10.00", "10.24", "b", "s"),
            _exch_raw_row("sh", "600002", "10.00", "10.25", "b", "s"),
        ],
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="t25", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["classes"]["explained_merged"] == 1
    assert report["classes"]["explained_merged_proved"] == 1
    assert len(report["explained_merged"]) == 1
    assert report["explained_merged"][0].get("proved") is True


# ============================================== C1-C27: 格级验收判据切片 C2 ==
# 编号 c1-c27 对应 scratchpad/bt_residual_classes_r1.md §6 C2 表里的 V1-V27 (加
# 小写前缀 c 避免与本文件已有的 test_v1_archive_*/test_v2_prepare_*/test_v2_canon_*
# (r2 §4 S7 的字面 V1-V6) 混淆 —— 两套编号来自不同文档, 恰好都叫 V<n>)。

def test_domain_cell_cols_matches_registry_declaration():
    """主会话额外要求: ``CanonSpec.cell_cols`` 与
    ``services.exchange_cell_verdicts._DOMAIN_CELL_COLS`` 是有意分开声明的两份
    真相 (该模块不连库、不导入脚本层), 但两处的取值必须逐字段相等, 否则将来一定
    会漂。变异: 把其中一处改掉一个域的格列, 本用例必须变红。"""
    canon_cell_cols = {domain: spec.cell_cols for domain, spec in DOMAIN_CANON.items()}
    assert set(canon_cell_cols) == set(_DOMAIN_CELL_COLS)
    for domain, cols in canon_cell_cols.items():
        assert tuple(cols) == tuple(_DOMAIN_CELL_COLS[domain]), (
            f"domain={domain!r}: CanonSpec.cell_cols={cols!r} != "
            f"exchange_cell_verdicts._DOMAIN_CELL_COLS={_DOMAIN_CELL_COLS[domain]!r}"
        )


def test_identity_natural_key_matches_security_identity_specs():
    """第三处双份真相: 本脚本的 ``_IDENTITY_NATURAL_KEY`` 与
    ``services.security_identity.IDENTITY_SPECS[domain].natural_key`` 是有意分开
    声明的两份 (那个模块不导入脚本层, 免得把 DB 路径解析与写锁依赖拖进纯模块),
    但两处取值必须逐字段相等 —— R1 的 twin 判定在验收层与发布层若用了不同的自然键,
    "同一笔"在两层就不是同一件事, 而这种漂移不会有任何别的判据会红。
    变异: 改掉任一处任一个域的 natural_key, 本用例必须变红。"""
    from scripts.reland_event_domain import _IDENTITY_NATURAL_KEY
    from services.security_identity import IDENTITY_SPECS

    assert set(_IDENTITY_NATURAL_KEY) == set(IDENTITY_SPECS)
    for domain, cols in _IDENTITY_NATURAL_KEY.items():
        assert tuple(cols) == tuple(IDENTITY_SPECS[domain].natural_key), (
            f"domain={domain!r}: reland_event_domain._IDENTITY_NATURAL_KEY="
            f"{tuple(cols)!r} != security_identity.IDENTITY_SPECS[{domain!r}]"
            f".natural_key={tuple(IDENTITY_SPECS[domain].natural_key)!r}"
        )


# ------------------------------------------------------------- C1-C6: venue (R-V) --

def test_c1_venue_resolved_by_suffix_when_vendor_market_absent(tmp_path):
    row = dict(ts_code="600000.SH", trade_date="20230103", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c1", [row], [row])
    _write_both_exchange_files(tmp_path, "20230103",
                                sh_rows=[_exch_raw_row("sh", "600000", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c1", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["venue"] == {
        "by_vendor_market": 0, "unresolved": 0, "conflict": 0,
        "blocked_exchange_rows": 0, "not_covered_bj": 0,
    }
    assert report["exit_code"] == 0


def test_c2_venue_resolved_by_vendor_market_when_suffix_missing(tmp_path):
    # 501054.OF 是妙想换源后才有的形态 (bt_residual_classes_r1.md N3): 后缀丢了
    # 场所信息, 但 vendor_market=CNSESH 是供应商自己给的一手场所字段。
    row = dict(ts_code="501054.OF", trade_date="20230103", price=10.0, vol=5.0,
               buyer="b", seller="s", vendor_market="CNSESH")
    conn = _setup_block_trade_reland(tmp_path, "c2", [row], [row])
    _write_both_exchange_files(tmp_path, "20230103",
                                sh_rows=[_exch_raw_row("sh", "501054", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c2", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["venue"]["by_vendor_market"] == 1
    assert report["exit_code"] == 0


def test_c3_venue_unresolved_blocks_exchange_row_without_missing(tmp_path):
    row = dict(ts_code="501054.OF", trade_date="20230103", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c3", [row], [row])
    _write_both_exchange_files(tmp_path, "20230103",
                                sh_rows=[_exch_raw_row("sh", "501054", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c3", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["venue"]["unresolved"] == 1
    assert report["venue"]["blocked_exchange_rows"] == 1
    assert report["consumption"]["totals"]["missing_unregistered"] == 0
    assert report["exit_code"] == 2


def test_c4_venue_conflict_between_suffix_and_vendor_market(tmp_path):
    row = dict(ts_code="600000.SH", trade_date="20230103", price=10.0, vol=5.0,
               buyer="b", seller="s", vendor_market="CNSESZ")
    conn = _setup_block_trade_reland(tmp_path, "c4", [row], [row])
    _write_both_exchange_files(tmp_path, "20230103",
                                sh_rows=[_exch_raw_row("sh", "600000", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c4", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["venue"]["conflict"] == 1
    assert report["exit_code"] == 2


def test_c5_bj_venue_not_covered_but_does_not_block_exit_0(tmp_path):
    matched_row = dict(ts_code="600000.SH", trade_date="20230103", price=10.0, vol=5.0, buyer="b", seller="s")
    bj_row = dict(ts_code="430001.BJ", trade_date="20230103", price=1.0, vol=1.0, buyer="x", seller="y")
    conn = _setup_block_trade_reland(tmp_path, "c5", [matched_row, bj_row], [matched_row, bj_row])
    _write_both_exchange_files(tmp_path, "20230103",
                                sh_rows=[_exch_raw_row("sh", "600000", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c5", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["venue"]["not_covered_bj"] == 1
    assert report["exit_code"] == 0


def test_c6_unknown_vendor_market_value_is_unresolved(tmp_path):
    row = dict(ts_code="501054.OF", trade_date="20230103", price=10.0, vol=5.0,
               buyer="b", seller="s", vendor_market="XX")
    conn = _setup_block_trade_reland(tmp_path, "c6", [row], [row])
    _write_both_exchange_files(tmp_path, "20230103",
                                sh_rows=[_exch_raw_row("sh", "501054", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c6", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["venue"]["unresolved"] == 1
    assert report["exit_code"] == 2


# --------------------------------------------------------- C7-C11: identity (R-I) --

def test_c7_twin_row_dropped_by_identity_layer(tmp_path):
    old_rows = [dict(ts_code="300114.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [
        dict(ts_code="300114.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s"),
        dict(ts_code="302132.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s"),
    ]
    conn = _setup_block_trade_reland(tmp_path, "c7", old_rows, new_rows)
    _write_both_exchange_files(tmp_path, "20250210",
                                sz_rows=[_exch_raw_row("sz", "300114", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])
    changes_path = _write_code_changes_yaml(tmp_path, [_code_change_event()])

    report = verify("block_trade", run_id="c7", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path, code_changes_path=changes_path)
    assert report["identity"]["dropped_new"] == 1
    assert report["identity"]["skipped"] is False
    assert report["exit_code"] == 0


def test_c8_no_twin_row_remapped_to_old_code(tmp_path):
    old_rows = [dict(ts_code="300114.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [dict(ts_code="302132.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "c8", old_rows, new_rows)
    _write_both_exchange_files(tmp_path, "20250210",
                                sz_rows=[_exch_raw_row("sz", "300114", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])
    changes_path = _write_code_changes_yaml(tmp_path, [_code_change_event()])

    report = verify("block_trade", run_id="c8", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path, code_changes_path=changes_path)
    assert report["identity"]["remapped_new"] == 1
    assert report["exit_code"] == 0


def test_c9_identity_skipped_without_code_changes_leaves_twin_as_extra(tmp_path):
    old_rows = [dict(ts_code="300114.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [
        dict(ts_code="300114.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s"),
        dict(ts_code="302132.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s"),
    ]
    conn = _setup_block_trade_reland(tmp_path, "c9", old_rows, new_rows)
    _write_both_exchange_files(tmp_path, "20250210",
                                sz_rows=[_exch_raw_row("sz", "300114", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c9", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)  # 不给 code_changes_path
    assert report["identity"]["skipped"] is True
    assert report["consumption"]["totals"]["extra_unregistered"] == 1
    assert report["exit_code"] == 2


def test_c10_row_on_or_after_effective_date_not_touched_by_identity(tmp_path):
    row = dict(ts_code="302132.SZ", trade_date="20250217", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c10", [row], [row])
    _write_both_exchange_files(tmp_path, "20250217",
                                sz_rows=[_exch_raw_row("sz", "302132", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])
    changes_path = _write_code_changes_yaml(tmp_path, [_code_change_event()])

    report = verify("block_trade", run_id="c10", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path, code_changes_path=changes_path)
    assert report["identity"]["dropped_new"] == 0
    assert report["identity"]["remapped_new"] == 0
    assert report["exit_code"] == 0


def test_c11_old_side_twin_dropped_from_archive(tmp_path):
    old_rows = [
        dict(ts_code="300114.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s"),
        dict(ts_code="302132.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s"),
    ]
    new_rows = [dict(ts_code="300114.SZ", trade_date="20250210", price=10.0, vol=5.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "c11", old_rows, new_rows)
    _write_both_exchange_files(tmp_path, "20250210",
                                sz_rows=[_exch_raw_row("sz", "300114", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])
    changes_path = _write_code_changes_yaml(tmp_path, [_code_change_event()])

    report = verify("block_trade", run_id="c11", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path, code_changes_path=changes_path)
    assert report["identity"]["dropped_old"] == 1
    assert report["classes"]["residual_none"] == 0
    assert report["exit_code"] == 0


# ------------------------------------------------------ C12-C20: 格级残差类 --

def test_c12_text_difference_unregistered_is_text_candidate(tmp_path):
    row = dict(ts_code="600010.SH", trade_date="20230110", price=10.0, vol=5.0, buyer="brokerA", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c12", [row], [row])
    _write_both_exchange_files(tmp_path, "20230110",
                                sh_rows=[_exch_raw_row("sh", "600010", "10.00", "5.00", "brokerAprime", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c12", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["text_candidate"] == 1
    assert report["exit_code"] == 2


def test_c13_text_difference_registered_via_pair_is_consumed(tmp_path):
    row = dict(ts_code="600010.SH", trade_date="20230110", price=10.0, vol=5.0, buyer="brokerA", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c13", [row], [row])
    _write_both_exchange_files(tmp_path, "20230110",
                                sh_rows=[_exch_raw_row("sh", "600010", "10.00", "5.00", "brokerAprime", "s")])
    entry = _cell_entry(
        cells=[("20230110", "sh", "600010", "10.00", "5.00")],
        exchange_unmatched=[("brokerAprime", "s", 1)],
        vendor_unmatched=[("brokerA", "s", 1)],
        text_pairs=[(0, 0, 1)],
        evidence="交易所营业部全称核对无误, 只是简称写法不同",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="c13", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["text"] == 1
    assert report["exit_code"] == 0


def test_c14_missing_unregistered_gives_exit_3(tmp_path):
    row = dict(ts_code="600011.SH", trade_date="20230111", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c14", [row], [])  # 重落后彻底消失
    _write_both_exchange_files(tmp_path, "20230111",
                                sh_rows=[_exch_raw_row("sh", "600011", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c14", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["missing_unregistered"] == 1
    assert report["exit_code"] == 3


def test_c15_missing_registered_gives_exit_0(tmp_path):
    row = dict(ts_code="600011.SH", trade_date="20230111", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c15", [row], [])
    _write_both_exchange_files(tmp_path, "20230111",
                                sh_rows=[_exch_raw_row("sh", "600011", "10.00", "5.00", "b", "s")])
    entry = _cell_entry(
        cells=[("20230111", "sh", "600011", "10.00", "5.00")],
        exchange_unmatched=[("b", "s", 1)],
        vendor_unmatched=[],
        evidence="交易所核对确有此笔, 妙想重落后确实缺失, 已知问题",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="c15", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["missing"] == 1
    assert report["exit_code"] == 0


def test_c16_duplicate_extra_unregistered_gives_exit_2(tmp_path):
    row = dict(ts_code="600012.SH", trade_date="20230112", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c16", [row, row], [row, row])  # 供应商两次报同一笔
    _write_both_exchange_files(tmp_path, "20230112",
                                sh_rows=[_exch_raw_row("sh", "600012", "10.00", "5.00", "b", "s")])  # 交易所只一笔
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c16", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["extra_unregistered"] == 1
    assert report["exit_code"] == 2


def test_c17_duplicate_extra_registered_gives_exit_0(tmp_path):
    row = dict(ts_code="600012.SH", trade_date="20230112", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c17", [row, row], [row, row])
    _write_both_exchange_files(tmp_path, "20230112",
                                sh_rows=[_exch_raw_row("sh", "600012", "10.00", "5.00", "b", "s")])
    entry = _cell_entry(
        cells=[("20230112", "sh", "600012", "10.00", "5.00")],
        exchange_unmatched=[],
        vendor_unmatched=[("b", "s", 1)],
        evidence="供应商同一笔报了两次, 已核实",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="c17", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["extra_duplicate"] == 1
    assert report["exit_code"] == 0


def test_c17b_phantom_extra_registered_gives_extra_phantom_not_duplicate(tmp_path):
    """C1 的 duplicate/phantom 子类判定 (``E[F] >= 1`` 用 ``Observed.exchange_all``)
    只在 ``_apply_exchange_evidence``/:func:`cell_compare` 把这个字段接对了才有意义
    —— 这条与 c17 (duplicate) 成对, 唯一能钉住"exchange_all 传的是交易所侧而不是
    新表自身"的用例 (把它错接成新表自身, 任何 extra 都会显得"交易所也有", 全部
    误判成 duplicate, 本用例的 extra_phantom 断言必须变红)。"""
    row = dict(ts_code="600013.SH", trade_date="20230113", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c17b", [row], [row])
    _write_both_exchange_files(tmp_path, "20230113")  # 交易所完全没有这笔 (真正 phantom)
    entry = _cell_entry(
        cells=[("20230113", "sh", "600013", "10.00", "5.00")],
        exchange_unmatched=[],
        vendor_unmatched=[("b", "s", 1)],
        evidence="盘后定价报表可查此笔, 交易所逐笔查询本就不覆盖",
        truth_side="vendor",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="c17b", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["extra_phantom"] == 1
    assert report["consumption"]["totals"]["extra_duplicate"] == 0
    assert report["exit_code"] == 0


def test_c18_phantom_extra_in_sh_market_gives_exit_2(tmp_path):
    row = dict(ts_code="600013.SH", trade_date="20230113", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c18sh", [row], [row])
    _write_both_exchange_files(tmp_path, "20230113")  # 交易所两个市场都没有这笔

    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])
    report = verify("block_trade", run_id="c18sh", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["extra_unregistered"] == 1
    assert report["exit_code"] == 2


def test_c18b_phantom_extra_in_sz_market_gives_exit_2_same_as_sh(tmp_path):
    """深市与沪市各一用例结果相同 (取代旧版沪判死/深看一眼的整市场级差别对待,
    见头注 O)。"""
    row = dict(ts_code="000013.SZ", trade_date="20230113", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c18sz", [row], [row])
    _write_both_exchange_files(tmp_path, "20230113")

    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])
    report = verify("block_trade", run_id="c18sz", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["consumption"]["totals"]["extra_unregistered"] == 1
    assert report["exit_code"] == 2


def test_c19_stale_registration_gives_exit_3(tmp_path):
    row = dict(ts_code="600011.SH", trade_date="20230111", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c19", [row], [row])  # 重落后这笔其实已经匹配上了
    _write_both_exchange_files(tmp_path, "20230111",
                                sh_rows=[_exch_raw_row("sh", "600011", "10.00", "5.00", "b", "s")])
    entry = _cell_entry(  # 登记声称这格还缺这一笔, 但观测已经 matched
        cells=[("20230111", "sh", "600011", "10.00", "5.00")],
        exchange_unmatched=[("b", "s", 1)],
        vendor_unmatched=[],
        evidence="之前核实的缺口 (现已过时)",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="c19", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert ["20230111", "sh", "600011", "10.00", "5.00"] in report["consumption"]["stale_cells"]
    assert report["exit_code"] == 3


def test_c20_contradiction_pair_referencing_wrong_string_gives_exit_3(tmp_path):
    row = dict(ts_code="600010.SH", trade_date="20230110", price=10.0, vol=5.0, buyer="brokerA", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c20", [row], [row])
    _write_both_exchange_files(tmp_path, "20230110",
                                sh_rows=[_exch_raw_row("sh", "600010", "10.00", "5.00", "brokerAprime", "s")])
    entry = _cell_entry(
        cells=[("20230110", "sh", "600010", "10.00", "5.00")],
        exchange_unmatched=[("brokerAprimeX", "s", 1)],  # 与观测差一字
        vendor_unmatched=[("brokerA", "s", 1)],
        text_pairs=[(0, 0, 1)],
        evidence="配对声明 (故意写错一个字, 触发 contradiction)",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="c20", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert ["20230110", "sh", "600010", "10.00", "5.00"] in report["consumption"]["stale_cells"]
    assert report["exit_code"] == 3


# ------------------------------------------------------- C21-C27: 收尾/整合 --

def test_c21_missing_evidence_file_for_one_day_gives_exit_2(tmp_path):
    row1 = dict(ts_code="600001.SH", trade_date="20230101", price=10.0, vol=5.0, buyer="b", seller="s")
    row2 = dict(ts_code="600002.SH", trade_date="20230102", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c21", [row1, row2], [row1, row2])
    _write_both_exchange_files(tmp_path, "20230101",
                                sh_rows=[_exch_raw_row("sh", "600001", "10.00", "5.00", "b", "s")])
    # 20230102 完全没有证据文件 (both markets)。
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c21", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["not_checked"] == [["20230102", "sh"], ["20230102", "sz"]]
    assert report["exit_code"] == 2


def test_c22_record_refuses_nonzero_exit_and_writes_new_keys_on_success(tmp_path):
    bad_row = dict(ts_code="600011.SH", trade_date="20230111", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c22", [bad_row], [])  # 缺口未登记 -> exit 3
    _write_both_exchange_files(tmp_path, "20230111",
                                sh_rows=[_exch_raw_row("sh", "600011", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    with pytest.raises(RuntimeError, match="refusing to record"):
        verify("block_trade", run_id="c22", record=True, conn=conn, archive_dir=tmp_path,
               exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    rows = conn.execute("SELECT delete_scope FROM mart_data_deletion_record").fetchall()
    assert all(r[0] != "rows_replaced_verified" for r in rows)

    # 干净的一份: 登记好这个缺口 -> exit 0 -> record 应该写入新 verification keys。
    entry = _cell_entry(
        cells=[("20230111", "sh", "600011", "10.00", "5.00")],
        exchange_unmatched=[("b", "s", 1)],
        vendor_unmatched=[],
        evidence="已核实缺口",
    )
    verdicts_path2 = _write_cell_verdicts_yaml(tmp_path, [entry])
    conn2 = _setup_block_trade_reland(tmp_path, "c22b", [bad_row], [])
    report = verify("block_trade", run_id="c22b", record=True, conn=conn2, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path2)
    assert report["exit_code"] == 0
    rows2 = conn2.execute(
        "SELECT verification_json FROM mart_data_deletion_record WHERE delete_scope = 'rows_replaced_verified'"
    ).fetchall()
    assert len(rows2) == 1
    verification = json.loads(rows2[0][0])
    assert {"registry_sha256", "consumed_cells", "identity", "venue", "not_checked"} <= set(verification.keys())


def test_c23_emit_candidates_skeleton_cannot_be_loaded_as_is(tmp_path):
    row = dict(ts_code="600011.SH", trade_date="20230111", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c23", [row], [])
    _write_both_exchange_files(tmp_path, "20230111",
                                sh_rows=[_exch_raw_row("sh", "600011", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])
    emit_path = tmp_path / "candidates.yaml"

    verify("block_trade", run_id="c23", conn=conn, archive_dir=tmp_path,
           exchange_dir=tmp_path, cell_verdicts_path=verdicts_path, emit_candidates_path=emit_path)
    assert emit_path.exists()
    with pytest.raises(ValueError, match="checked_at"):
        load_exchange_cell_verdicts(emit_path)


def test_c24_old_residual_registration_matches_cell_consumption(tmp_path):
    row = dict(ts_code="600011.SH", trade_date="20230111", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c24", [row], [])
    _write_both_exchange_files(tmp_path, "20230111",
                                sh_rows=[_exch_raw_row("sh", "600011", "10.00", "5.00", "b", "s")])
    entry = _cell_entry(
        cells=[("20230111", "sh", "600011", "10.00", "5.00")],
        exchange_unmatched=[("b", "s", 1)],
        vendor_unmatched=[],
        evidence="已核实缺口",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="c24", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["miaoxiang_gap_registered"] == 1
    assert report["consumption"]["totals"]["missing"] == 1
    assert report["exit_code"] == 0


def test_c25_partition_invariant_violation_converts_to_exit_1_via_main(tmp_path, monkeypatch, capsys):
    import scripts.reland_event_domain as red
    from services.exchange_cell_verdicts import ConsumptionReport

    row = dict(ts_code="600011.SH", trade_date="20230111", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c25", [row], [])
    _write_both_exchange_files(tmp_path, "20230111",
                                sh_rows=[_exch_raw_row("sh", "600011", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    def _bad_consume(observed, verdicts):
        zero = {f: 0 for f in (
            "text", "missing", "extra_duplicate", "extra_phantom",
            "text_candidate", "missing_unregistered", "extra_unregistered",
        )}
        return ConsumptionReport(per_cell={}, totals=zero, consumed_cells=(), stale_cells=())

    monkeypatch.setattr(red, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(red, "db_path", lambda *a, **k: tmp_path / "unused.duckdb")
    monkeypatch.setattr(red, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(red, "consume", _bad_consume)

    rc = main([
        "verify", "--domain", "block_trade", "--run-id", "c25",
        "--exchange-dir", str(tmp_path), "--cell-verdicts", str(verdicts_path),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "partition" in err


def test_c26_clean_match_with_no_registrations_or_events_gives_exit_0(tmp_path):
    rows = [
        dict(ts_code="600014.SH", trade_date="20230114", price=10.0, vol=5.0, buyer="b", seller="s"),
        dict(ts_code="600015.SH", trade_date="20230114", price=20.0, vol=8.0, buyer="x", seller="y"),
        dict(ts_code="000016.SZ", trade_date="20230114", price=30.0, vol=9.0, buyer="p", seller="q"),
    ]
    conn = _setup_block_trade_reland(tmp_path, "c26", rows, rows)
    _write_both_exchange_files(
        tmp_path, "20230114",
        sh_rows=[
            _exch_raw_row("sh", "600014", "10.00", "5.00", "b", "s"),
            _exch_raw_row("sh", "600015", "20.00", "8.00", "x", "y"),
        ],
        sz_rows=[_exch_raw_row("sz", "000016", "30.00", "9.00", "p", "q")],
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="c26", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["exit_code"] == 0
    assert all(v == 0 for v in report["consumption"]["totals"].values())
    assert report["consumption"]["consumed_cells"] == []


def test_c27_main_returns_2_for_unregistered_text_difference(tmp_path, monkeypatch):
    import scripts.reland_event_domain as red

    row = dict(ts_code="600010.SH", trade_date="20230110", price=10.0, vol=5.0, buyer="brokerA", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "c27", [row], [row])
    _write_both_exchange_files(tmp_path, "20230110",
                                sh_rows=[_exch_raw_row("sh", "600010", "10.00", "5.00", "brokerAprime", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    monkeypatch.setattr(red, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(red, "db_path", lambda *a, **k: tmp_path / "unused.duckdb")
    monkeypatch.setattr(red, "ARCHIVE_DIR", tmp_path)

    rc = main([
        "verify", "--domain", "block_trade", "--run-id", "c27",
        "--exchange-dir", str(tmp_path), "--cell-verdicts", str(verdicts_path),
    ])
    assert rc == 2


# ============================================== R1-R5: residual write-back (R) ==
# 头注 R (2026-09-12, 本片): exchange_verdicts()/consume() 算出的定性此前只以
# residual_verdict_counts 的汇总计数形式存在, report["residual"] 逐条明细里查不到
# 自己是哪一类。R1-R4 对应规格里给的四个隔离条件 (受覆盖且 old_vendor_error /
# miaoxiang_gap 且格已登记消费为 missing / miaoxiang_gap 但格已登记消费为 text
# (91 条那种形态) / 不受覆盖); R5 是分区不变量 (计数与明细逐类必须相等)。

def test_r1_old_vendor_error_row_is_traceable_in_residual_detail(tmp_path):
    old_rows = [dict(ts_code="600020.SH", trade_date="20230120", price=11.0, vol=5.0, buyer="b", seller="s")]
    new_rows = [dict(ts_code="600020.SH", trade_date="20230120", price=10.0, vol=5.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "r1", old_rows, new_rows)
    _write_both_exchange_files(tmp_path, "20230120",
                                sh_rows=[_exch_raw_row("sh", "600020", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="r1", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["old_vendor_error"] == 1
    assert len(report["residual"]) == 1
    item = report["residual"][0]
    assert item["exchange_verdict"] == "old_vendor_error"
    # 这一行自己的 (buyer, seller) 在它自己的价位格 (11.00, 与交易所/新表的 10.00
    # 不是同一格) 里没有 gap 贡献 —— 评估过, 跟这格残差无关, 不是"没被定性"。
    assert item["cell_verdict"]["status"] == "no_cell_residual"
    assert item["cell_verdict"]["row_kind"] is None


def test_r2_miaoxiang_gap_registered_as_missing_shows_registered_in_detail(tmp_path):
    row = dict(ts_code="600051.SH", trade_date="20230211", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "r2", [row], [])  # 重落后彻底消失
    _write_both_exchange_files(tmp_path, "20230211",
                                sh_rows=[_exch_raw_row("sh", "600051", "10.00", "5.00", "b", "s")])
    entry = _cell_entry(
        cells=[("20230211", "sh", "600051", "10.00", "5.00")],
        exchange_unmatched=[("b", "s", 1)],
        vendor_unmatched=[],
        evidence="交易所核对确有此笔, 妙想重落后确实缺失, 已知问题",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="r2", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["miaoxiang_gap_registered"] == 1
    assert len(report["residual"]) == 1
    item = report["residual"][0]
    assert item["exchange_verdict"] == "miaoxiang_gap_registered"
    assert item["cell_verdict"] == {
        "cell": ["20230211", "sh", "600051", "10.00", "5.00"],
        "status": "consumed",
        "row_kind": "missing",
    }


def test_r3_miaoxiang_gap_unregistered_but_cell_consumed_as_text_shows_text_kind(tmp_path):
    """这正是主会话实测的 91 条的形态: 旧行按六键在新表找不到而被判成
    miaoxiang_gap (registered=False, 计入 miaoxiang_gap_unregistered), 但格级比较
    早就把同一事实定性成 ``text`` (两侧都在, 只是席位名写法不同)。明细里必须能
    直接看出这不是真缺口。"""
    old_rows = [dict(ts_code="600052.SH", trade_date="20230212", price=10.0, vol=5.0,
                      buyer="brokerA_old", seller="s")]
    new_rows = [dict(ts_code="600052.SH", trade_date="20230212", price=10.0, vol=5.0,
                      buyer="brokerA_new", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "r3", old_rows, new_rows)
    _write_both_exchange_files(tmp_path, "20230212",
                                sh_rows=[_exch_raw_row("sh", "600052", "10.00", "5.00", "brokerA_old", "s")])
    entry = _cell_entry(
        cells=[("20230212", "sh", "600052", "10.00", "5.00")],
        exchange_unmatched=[("brokerA_old", "s", 1)],
        vendor_unmatched=[("brokerA_new", "s", 1)],
        text_pairs=[(0, 0, 1)],
        evidence="交易所营业部全称核对无误, 只是简称写法不同",
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [entry])

    report = verify("block_trade", run_id="r3", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["miaoxiang_gap_unregistered"] == 1
    assert len(report["residual"]) == 1
    item = report["residual"][0]
    assert item["exchange_verdict"] == "miaoxiang_gap_unregistered"
    assert item["cell_verdict"] == {
        "cell": ["20230212", "sh", "600052", "10.00", "5.00"],
        "status": "consumed",
        "row_kind": "text",
    }
    # 格已经被登记表完整消费掉 (text=1, 无 unregistered 残差), 不阻断退出码。
    assert report["exit_code"] == 0


def test_r4_row_not_covered_by_exchange_evidence_is_marked_unverified_not_missing_key(tmp_path):
    old_rows = [dict(ts_code="430001.BJ", trade_date="20230213", price=10.0, vol=5.0, buyer="b", seller="s")]
    conn = _setup_block_trade_reland(tmp_path, "r4", old_rows, [])  # 重落后消失, 走 residual
    _write_both_exchange_files(tmp_path, "20230213", sh_rows=[], sz_rows=[])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="r4", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["unverified"] == 1
    assert len(report["residual"]) == 1
    item = report["residual"][0]
    assert "exchange_verdict" in item and "cell_verdict" in item  # 显式取值, 不是缺键
    assert item["exchange_verdict"] == "unverified"
    assert item["cell_verdict"] == {"cell": None, "status": "not_applicable", "row_kind": None}


def test_r5a_partition_invariant_direct_mismatch_raises_with_class_name():
    residual_all = [{"exchange_verdict": "old_vendor_error"}]
    counts = _zero_counts(old_vendor_error=2)  # 明细只有 1 条 old_vendor_error, 计数却是 2
    with pytest.raises(AssertionError, match="old_vendor_error"):
        _assert_residual_verdict_partition(residual_all, counts)


def test_r5b_partition_invariant_violation_via_verify_raises_and_names_the_class(tmp_path, monkeypatch):
    """规格要求的隔离用例: monkeypatch 让某一类计数与明细条数差 1, 必须抛且消息
    含类名。这里通过一份不产生任何 residual 的干净输入 + 篡改
    ``_apply_exchange_evidence`` 返回的 counts (明细不变, 计数漂移 1) 直接触发。"""
    import scripts.reland_event_domain as red

    row = dict(ts_code="600053.SH", trade_date="20230214", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "r5b", [row], [row])  # 完全匹配, 无 residual
    _write_both_exchange_files(tmp_path, "20230214", sh_rows=[])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    original = red._apply_exchange_evidence

    def _tampered(**kwargs):
        evidence = original(**kwargs)
        evidence["counts"]["old_vendor_error"] += 1  # 明细里没有任何一条被标成这一类
        return evidence

    monkeypatch.setattr(red, "_apply_exchange_evidence", _tampered)

    with pytest.raises(AssertionError, match="old_vendor_error"):
        verify("block_trade", run_id="r5b", conn=conn, archive_dir=tmp_path,
               exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)


# ==================================== S1-S6: old_vendor_error_absent_both (S) ==
# 头注 S (2026-09-12, 主会话在真实数据上实测追加): 该 (day, market) 有交易所证据
# 文件 + 新表当天该市场至少一行数据 + 该 ts_code 在新表/交易所两边都完全查不到,
# 三个前提并且满足才落 old_vendor_error_absent_both, 缺一条仍是 unverified。

def test_s1_all_three_preconditions_met_gives_absent_both_and_excludes_unverified(tmp_path):
    present_row = dict(ts_code="600060.SH", trade_date="20230301", price=10.0, vol=5.0, buyer="b", seller="s")
    ghost_row = dict(ts_code="600061.SH", trade_date="20230301", price=20.0, vol=8.0, buyer="x", seller="y")
    # 600061.SH 只存在于旧归档 (残差), 新表/交易所当天都没有这个代码。
    conn = _setup_block_trade_reland(tmp_path, "s1", [present_row, ghost_row], [present_row])
    _write_both_exchange_files(tmp_path, "20230301",
                                sh_rows=[_exch_raw_row("sh", "600060", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="s1", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["old_vendor_error_absent_both"] == 1
    assert report["residual_verdict_counts"]["unverified"] == 0
    ghost_items = [r for r in report["residual"] if r["key"][0] == "600061.SH"]
    assert len(ghost_items) == 1
    assert ghost_items[0]["exchange_verdict"] == "old_vendor_error_absent_both"


def test_s2_no_evidence_file_for_that_day_market_stays_unverified(tmp_path):
    # 前提 2 (新表当天该市场至少一行) 特意满足 (present_row 留在新表里), 只让
    # 前提 1 (有证据文件) 失效, 才是"只缺被测那一条"的隔离用例。
    ghost_row = dict(ts_code="600062.SH", trade_date="20230302", price=20.0, vol=8.0, buyer="x", seller="y")
    present_row = dict(ts_code="600070.SH", trade_date="20230302", price=1.0, vol=1.0, buyer="p", seller="q")
    conn = _setup_block_trade_reland(tmp_path, "s2", [ghost_row, present_row], [present_row])
    # 20230302 完全没有证据文件 (both markets) —— 前提 1 不满足。
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="s2", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["old_vendor_error_absent_both"] == 0
    assert report["residual_verdict_counts"]["unverified"] == 1
    assert report["residual"][0]["exchange_verdict"] == "unverified"


def test_s3_new_table_empty_that_day_market_stays_unverified(tmp_path):
    ghost_row = dict(ts_code="600063.SH", trade_date="20230303", price=20.0, vol=8.0, buyer="x", seller="y")
    # 新表这天这个市场零行 (旧行也不例外地一起消失) —— 前提 2 (new_day_rows 非空)
    # 不满足, 即便证据文件写了别的代码。
    conn = _setup_block_trade_reland(tmp_path, "s3", [ghost_row], [])
    _write_both_exchange_files(tmp_path, "20230303",
                                sh_rows=[_exch_raw_row("sh", "600099", "1.00", "1.00", "p", "q")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="s3", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["old_vendor_error_absent_both"] == 0
    assert report["residual_verdict_counts"]["unverified"] == 1
    assert report["residual"][0]["exchange_verdict"] == "unverified"


def test_s4_code_present_in_new_table_does_not_get_absent_both_uses_original_four_way(tmp_path):
    # 600064.SH 在新表存在 (价格对不上交易所, NC==EX 触发 old_vendor_error) ——
    # 走原有四路, 不落 absent_both, 即便它本身就是 residual。
    old_row = dict(ts_code="600064.SH", trade_date="20230304", price=11.0, vol=5.0, buyer="b", seller="s")
    new_row = dict(ts_code="600064.SH", trade_date="20230304", price=10.0, vol=5.0, buyer="b", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "s4", [old_row], [new_row])
    _write_both_exchange_files(tmp_path, "20230304",
                                sh_rows=[_exch_raw_row("sh", "600064", "10.00", "5.00", "b", "s")])
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="s4", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["old_vendor_error_absent_both"] == 0
    assert report["residual_verdict_counts"]["old_vendor_error"] == 1
    assert report["residual"][0]["exchange_verdict"] == "old_vendor_error"


def test_s5_code_present_in_exchange_does_not_get_absent_both_gives_miaoxiang_gap(tmp_path):
    # 600065.SH 只在交易所出现 (新表当天该市场另有别的代码, 保证 new_day_rows
    # 非空但不含 600065.SH) —— 应落 miaoxiang_gap, 不落 absent_both。
    ghost_row = dict(ts_code="600065.SH", trade_date="20230305", price=10.0, vol=5.0, buyer="b", seller="s")
    other_row = dict(ts_code="600066.SH", trade_date="20230305", price=1.0, vol=1.0, buyer="p", seller="q")
    conn = _setup_block_trade_reland(tmp_path, "s5", [ghost_row, other_row], [other_row])
    _write_both_exchange_files(
        tmp_path, "20230305",
        sh_rows=[
            _exch_raw_row("sh", "600065", "10.00", "5.00", "b", "s"),
            _exch_raw_row("sh", "600066", "1.00", "1.00", "p", "q"),
        ],
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="s5", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["old_vendor_error_absent_both"] == 0
    ghost_items = [r for r in report["residual"] if r["key"][0] == "600065.SH"]
    assert len(ghost_items) == 1
    assert ghost_items[0]["exchange_verdict"] == "miaoxiang_gap_unregistered"


def test_s6_absent_both_alone_gives_exit_0_but_exit_2_alongside_text_candidate(tmp_path):
    ghost_row = dict(ts_code="600067.SH", trade_date="20230306", price=20.0, vol=8.0, buyer="x", seller="y")
    present_row = dict(ts_code="600068.SH", trade_date="20230306", price=10.0, vol=5.0, buyer="brokerA", seller="s")
    conn = _setup_block_trade_reland(tmp_path, "s6", [ghost_row, present_row], [present_row])
    _write_both_exchange_files(
        tmp_path, "20230306",
        sh_rows=[_exch_raw_row("sh", "600068", "10.00", "5.00", "brokerA", "s")],
    )
    verdicts_path = _write_cell_verdicts_yaml(tmp_path, [])

    report = verify("block_trade", run_id="s6", conn=conn, archive_dir=tmp_path,
                     exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report["residual_verdict_counts"]["old_vendor_error_absent_both"] == 1
    # 只有这一类残差 (600068.SH 与交易所完全一致, 无 gap/extra) -> exit 0。
    assert report["exit_code"] == 0

    # 再加一个未登记文本差异, 证明 2 分支的其它条件没有被一起放过。
    conn2 = _setup_block_trade_reland(
        tmp_path, "s6b",
        [ghost_row, present_row, dict(ts_code="600069.SH", trade_date="20230306", price=1.0, vol=1.0,
                                       buyer="brokerB", seller="s")],
        [present_row, dict(ts_code="600069.SH", trade_date="20230306", price=1.0, vol=1.0,
                            buyer="brokerB", seller="s")],
    )
    _write_both_exchange_files(
        tmp_path, "20230306",
        sh_rows=[
            _exch_raw_row("sh", "600068", "10.00", "5.00", "brokerA", "s"),
            _exch_raw_row("sh", "600069", "1.00", "1.00", "brokerBprime", "s"),
        ],
    )
    report2 = verify("block_trade", run_id="s6b", conn=conn2, archive_dir=tmp_path,
                      exchange_dir=tmp_path, cell_verdicts_path=verdicts_path)
    assert report2["residual_verdict_counts"]["old_vendor_error_absent_both"] == 1
    assert report2["consumption"]["totals"]["text_candidate"] == 1
    assert report2["exit_code"] == 2
