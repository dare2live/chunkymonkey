"""dim_holder_identity_label + dim_holder_name_tag 落库测试。

**为什么手算 fixture 而不是只验行数**: 这两张表的全部价值在"哪些行能进决策查询"
(known_from 折叠) 和"哪些行会被拒绝写入"(fail-closed / 关旧开新), 行数对上不代表这两条
判据是对的。所以每个测试都断言具体值, 不只断言 COUNT。

覆盖三类问题:
  1. 折叠/解析纯函数 (known_from 折叠、日期抽取)。
  2. 证据 -> 行的转换 (跳过坏数据、fail-closed 拒收未知取值)。
  3. 落库到真实 DuckDB 引擎的结构性防线 (CHECK 约束、PRIMARY KEY 对 NULL 的处理、
     关旧开新守卫、幂等重跑 hash 相同) —— 用 conftest.duck_mem() (项目统一内存库引擎,
     红线2: 测试必须用与生产一致的 DB 引擎, 不用 sqlite3 等替身)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import duck_mem

from scripts.publish_holder_labels import (
    _NIUSAN_EXCLUDED,
    _NIUSAN_ROSTER,
    _INSTITUTION_DDL,
    _NIUSAN_DDL,
    _check_no_silent_value_rewrite,
    _fold_institution_known_from,
    load_institution_rows,
    load_niusan_rows,
)

INSTITUTION_ALL_COLS = (
    "holder_code", "name_pattern", "dim", "value", "value_cn", "sub_value",
    "valid_from", "valid_to", "valid_from_pk", "known_from", "known_from_lower_bound",
    "known_from_grade", "confidence", "evidence", "source_file", "review_state",
    "batch_id", "ingested_at",
)
NIUSAN_ALL_COLS = (
    "holder_name", "tag", "valid_from", "valid_to", "valid_from_pk", "valid_grade",
    "known_from", "known_from_grade", "identity_confidence", "identity_grade",
    "alias", "name_variant", "source_outlet", "source_url", "evidence",
    "review_state", "note", "batch_id", "ingested_at",
)

ALLOWED_DIMS = {"tier", "entity_type", "sector", "group", "region", "ownership", "decision_maker"}
ALLOWED_GRADES = {"VERIFIED_EVENT", "VERIFIED_FIRST_NOTICE", "UNVERIFIED", "REFUSED_NULL"}


# ======================================================================================
# 1. 折叠纯函数 —— m1_label_store.md §2.0 那张交叉表的字面执行
# ======================================================================================


@pytest.mark.parametrize(
    "grade,raw,expect_known_from,expect_lower_bound",
    [
        ("VERIFIED_EVENT", "20220628", "20220628", None),
        ("VERIFIED_FIRST_NOTICE", "20070413", "20070413", None),
        ("UNVERIFIED", "20040417", None, "20040417"),
        ("REFUSED_NULL", None, None, None),
    ],
)
def test_fold_known_from_matches_m1_table(grade, raw, expect_known_from, expect_lower_bound):
    known_from, lower_bound = _fold_institution_known_from(grade, raw)
    assert known_from == expect_known_from
    assert lower_bound == expect_lower_bound


def test_fold_unverified_never_leaks_a_concrete_date_into_known_from():
    """这是本表唯一的安全判据: UNVERIFIED 不管 raw 是什么, known_from 必须是 None。"""
    known_from, _ = _fold_institution_known_from("UNVERIFIED", "20260101")
    assert known_from is None, "UNVERIFIED 泄漏进 known_from = PIT 判据被绕过"


# ======================================================================================
# 2. 证据 JSON -> 行: 用 4 个手写实体覆盖 4 种 grade + 1 个坏 holder_code + 1 个 0-dim
# ======================================================================================


def _tiny_institution_json(tmp_path: Path) -> Path:
    payload = {
        "entities": [
            {
                "holder_code": "AAA",
                "known_from_grade": "VERIFIED_EVENT",
                "known_from": "20220628",
                "valid_from": "20040101",
                "valid_to": "20220628",
                "dims": {
                    "tier": {
                        "value": "local", "value_cn": "地方国资",
                        "confidence": "high", "evidence": "手写证据A",
                        "source_file": "test.json",
                    },
                },
            },
            {
                "holder_code": "BBB",
                "known_from_grade": "UNVERIFIED",
                "known_from": "20090101",  # 应该被折叠掉, 不进 known_from
                "valid_from": "20090101",
                "valid_to": None,
                "dims": {
                    "sector": {
                        "value": "semis", "confidence": "medium",
                        "evidence": "手写证据B", "source_file": "test.json",
                    },
                },
            },
            {
                "holder_code": "CCC",
                "known_from_grade": "REFUSED_NULL",
                "known_from": None,
                "valid_from": None,  # 实测推翻设计的场景: valid_from 本身也是 NULL
                "valid_to": None,
                "dims": {
                    "entity_type": {
                        "value": "unknown_org", "confidence": "low",
                        "evidence": "手写证据C", "source_file": "test.json",
                    },
                },
            },
            {
                # 坏 holder_code, 必须被跳过, 不产出任何行
                "holder_code": "None",
                "known_from_grade": "UNVERIFIED",
                "known_from": None,
                "valid_from": None,
                "valid_to": None,
                "dims": {
                    "tier": {"value": "x", "confidence": "low", "evidence": "e", "source_file": "f"},
                },
            },
            {
                # 0-dim, 全部维度已被裁决作废, 必须被跳过
                "holder_code": "DDD",
                "known_from_grade": "UNVERIFIED",
                "known_from": "20100101",
                "valid_from": "20100101",
                "valid_to": None,
                "dims": {},
            },
        ]
    }
    p = tmp_path / "tiny_labels.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_institution_rows_folds_and_skips_correctly(tmp_path):
    path = _tiny_institution_json(tmp_path)
    rows, stats = load_institution_rows(
        path,
        allowed_dims=ALLOWED_DIMS,
        allowed_grades=ALLOWED_GRADES,
        split_pending_codes=set(),
        batch_id="tb",
        ingested_at="2026-09-08T00:00:00",
    )
    assert stats["n_entities"] == 5
    assert stats["n_skipped_bad_code"] == 1
    assert stats["n_zero_dim_entities"] == 1
    assert len(rows) == 3  # AAA(tier) + BBB(sector) + CCC(entity_type)

    by_code = {r[0]: r for r in rows}
    col = {name: i for i, name in enumerate(INSTITUTION_ALL_COLS)}

    aaa = by_code["AAA"]
    assert aaa[col["known_from"]] == "20220628"
    assert aaa[col["known_from_lower_bound"]] is None
    assert aaa[col["valid_from"]] == "20040101"
    assert aaa[col["valid_from_pk"]] == "20040101"

    bbb = by_code["BBB"]
    assert bbb[col["known_from"]] is None, "UNVERIFIED 的 known_from 必须落 NULL"
    assert bbb[col["known_from_lower_bound"]] == "20090101"

    ccc = by_code["CCC"]
    assert ccc[col["valid_from"]] is None, "REFUSED_NULL 连 valid_from 本身也是 NULL(实测推翻设计)"
    assert ccc[col["valid_from_pk"]] == "", "PK 代理列必须把 None 折成空串, 不能是 NULL"
    assert ccc[col["known_from"]] is None


def test_load_institution_rows_rejects_unknown_dim_fail_closed(tmp_path):
    payload = {
        "entities": [
            {
                "holder_code": "ZZZ",
                "known_from_grade": "VERIFIED_EVENT",
                "known_from": "20200101",
                "valid_from": "20200101",
                "valid_to": None,
                "dims": {
                    "not_a_real_dim": {
                        "value": "x", "confidence": "high",
                        "evidence": "e", "source_file": "f",
                    },
                },
            },
        ]
    }
    p = tmp_path / "bad_dim.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit):
        load_institution_rows(
            p, allowed_dims=ALLOWED_DIMS, allowed_grades=ALLOWED_GRADES,
            split_pending_codes=set(), batch_id="tb", ingested_at="2026-09-08T00:00:00",
        )


def test_load_institution_rows_rejects_unknown_grade_fail_closed(tmp_path):
    payload = {
        "entities": [
            {
                "holder_code": "ZZZ",
                "known_from_grade": "TOTALLY_MADE_UP",
                "known_from": "20200101",
                "valid_from": "20200101",
                "valid_to": None,
                "dims": {"tier": {"value": "x", "confidence": "high", "evidence": "e", "source_file": "f"}},
            },
        ]
    }
    p = tmp_path / "bad_grade.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit):
        load_institution_rows(
            p, allowed_dims=ALLOWED_DIMS, allowed_grades=ALLOWED_GRADES,
            split_pending_codes=set(), batch_id="tb", ingested_at="2026-09-08T00:00:00",
        )


def test_split_pending_codes_marked_review_pending(tmp_path):
    payload = {
        "entities": [
            {
                "holder_code": "10071181",
                "known_from_grade": "UNVERIFIED",
                "known_from": "20040101",
                "valid_from": "20040101",
                "valid_to": None,
                "dims": {"tier": {"value": "foreign", "confidence": "high", "evidence": "e", "source_file": "f"}},
            },
        ]
    }
    p = tmp_path / "split.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    rows, stats = load_institution_rows(
        p, allowed_dims=ALLOWED_DIMS, allowed_grades=ALLOWED_GRADES,
        split_pending_codes={"10071181"}, batch_id="tb", ingested_at="2026-09-08T00:00:00",
    )
    col = {name: i for i, name in enumerate(INSTITUTION_ALL_COLS)}
    assert rows[0][col["review_state"]] == "review_pending"
    assert stats["n_review_pending_rows"] == 1


# ======================================================================================
# 3. 牛散名录本身的结构性回归测试 —— 业主拍板的东西, 代码不能悄悄漂移
# ======================================================================================


def test_niusan_roster_excludes_the_four_disposed_names():
    roster_names = {p["holder_name"] for p in _NIUSAN_ROSTER} | {
        p.get("alias") for p in _NIUSAN_ROSTER if p.get("alias")
    }
    excluded_names = {e["name"] for e in _NIUSAN_EXCLUDED}
    assert excluded_names == {"魏巍", "张素芬", "章盟主", "徐翔"}
    assert roster_names.isdisjoint(excluded_names), "被剔除的人不能又出现在待发布名录里"


def test_niusan_roster_is_nine_people_no_duplicates():
    names = [p["holder_name"] for p in _NIUSAN_ROSTER]
    assert len(names) == 9
    assert len(set(names)) == 9


def test_niusan_roster_known_from_grade_binding_holds_for_every_entry():
    """结构性防线本身也要对着源数据测一遍, 不能只信 DDL 里的 CHECK 字符串。"""
    for p in _NIUSAN_ROSTER:
        if p["known_from"] is not None:
            assert p["known_from_grade"] == "VERIFIED_PUBLICATION", p["holder_name"]
            assert p["source_url"], f"{p['holder_name']}: known_from 非空但没有 source_url"
        else:
            assert p["known_from_grade"] in ("UNVERIFIED", "REFUSED_NULL"), p["holder_name"]


def test_load_niusan_rows_rejects_single_person_even_if_someone_hardcodes_it(monkeypatch):
    """single_person 不许出现 —— 直接在内存里注入一条违规记录, 确认 loader 会拦。"""
    from scripts import publish_holder_labels as mod

    bad_entry = dict(mod._NIUSAN_ROSTER[0])
    bad_entry["holder_name"] = "测试人"
    bad_entry["identity_confidence"] = "single_person"
    monkeypatch.setattr(mod, "_NIUSAN_ROSTER", mod._NIUSAN_ROSTER + [bad_entry])
    with pytest.raises(SystemExit):
        mod.load_niusan_rows(
            allowed_grades={"VERIFIED_PUBLICATION", "UNVERIFIED", "REFUSED_NULL"},
            allowed_valid_grades={"SOURCED_RANGE", "SOURCED_START_ONLY", "UNKNOWN_RANGE"},
            allowed_confidence={"proven_multiple", "suspected_multiple", "no_evidence_either_way"},
            batch_id="tb", ingested_at="2026-09-08T00:00:00",
        )


def test_load_niusan_rows_produces_nine_rows():
    rows, stats = load_niusan_rows(
        allowed_grades={"VERIFIED_PUBLICATION", "UNVERIFIED", "REFUSED_NULL"},
        allowed_valid_grades={"SOURCED_RANGE", "SOURCED_START_ONLY", "UNKNOWN_RANGE"},
        allowed_confidence={"proven_multiple", "suspected_multiple", "no_evidence_either_way"},
        batch_id="tb", ingested_at="2026-09-08T00:00:00",
    )
    assert stats["n_rows"] == 9
    names = {r[0] for r in rows}
    assert names == {"杨怀定", "刘元生", "章建平", "赵强", "林园", "刘益谦", "葛卫东", "陈发树", "徐开东"}


# ======================================================================================
# 4. 落库到真实 DuckDB 引擎: CHECK 约束 + PK 对 NULL 的处理(回归测试) + 关旧开新守卫
# ======================================================================================


def _insert_institution_row(conn, **overrides):
    row = {
        "holder_code": "X1", "name_pattern": "*", "dim": "tier", "value": "local",
        "value_cn": None, "sub_value": None, "valid_from": "20200101", "valid_to": None,
        "valid_from_pk": "20200101", "known_from": "20200101", "known_from_lower_bound": None,
        "known_from_grade": "VERIFIED_EVENT", "confidence": "high", "evidence": "e",
        "source_file": "f", "review_state": "ok", "batch_id": "b1",
        "ingested_at": "2026-09-08 00:00:00",
    }
    row.update(overrides)
    values = [row[c] for c in INSTITUTION_ALL_COLS]
    placeholders = ", ".join(["?"] * len(values))
    conn.execute(f"INSERT INTO dim_holder_identity_label VALUES ({placeholders})", values)


def test_institution_ddl_allows_null_valid_from_via_pk_surrogate():
    """回归测试: m1_label_store.md 原 DDL 把 valid_from 直接放进 PK 会导致 REFUSED_NULL
    (325 实体/772 行, 12.6%) 插入时报 NOT NULL constraint 错误 —— 这是本次实现实测发现、
    两份设计文档都没预见到的 bug。valid_from_pk 代理列修好了它, 这里钉一个回归测试。
    """
    conn = duck_mem()
    conn.execute(_INSTITUTION_DDL)
    _insert_institution_row(
        conn, holder_code="X1", valid_from=None, valid_from_pk="",
        known_from=None, known_from_grade="REFUSED_NULL",
    )
    row = conn.execute("SELECT valid_from, known_from FROM dim_holder_identity_label").fetchone()
    assert tuple(row) == (None, None)


def test_institution_check_constraint_blocks_unverified_with_known_from():
    conn = duck_mem()
    conn.execute(_INSTITUTION_DDL)
    with pytest.raises(Exception):
        _insert_institution_row(
            conn, known_from="20200101", known_from_grade="UNVERIFIED",
        )


def test_institution_pk_rejects_true_duplicate():
    conn = duck_mem()
    conn.execute(_INSTITUTION_DDL)
    _insert_institution_row(conn)
    with pytest.raises(Exception):
        _insert_institution_row(conn)


def _insert_niusan_row(conn, **overrides):
    row = {
        "holder_name": "张三", "tag": "niusan", "valid_from": None, "valid_to": None,
        "valid_from_pk": "", "valid_grade": "UNKNOWN_RANGE", "known_from": None,
        "known_from_grade": "UNVERIFIED", "identity_confidence": "no_evidence_either_way",
        "identity_grade": "name_only_untrusted", "alias": None, "name_variant": None,
        "source_outlet": "测试媒体", "source_url": None, "evidence": "e",
        "review_state": "ok", "note": None, "batch_id": "b1",
        "ingested_at": "2026-09-08 00:00:00",
    }
    row.update(overrides)
    values = [row[c] for c in NIUSAN_ALL_COLS]
    placeholders = ", ".join(["?"] * len(values))
    conn.execute(f"INSERT INTO dim_holder_name_tag VALUES ({placeholders})", values)


def test_niusan_check_constraint_blocks_single_person():
    conn = duck_mem()
    conn.execute(_NIUSAN_DDL)
    with pytest.raises(Exception):
        _insert_niusan_row(conn, identity_confidence="single_person")


def test_niusan_check_constraint_requires_source_url_when_known_from_set():
    """n1_niusan_design.md §4"我最没把握的三条"#1 的建议, 本轮直接落成 CHECK。"""
    conn = duck_mem()
    conn.execute(_NIUSAN_DDL)
    with pytest.raises(Exception):
        _insert_niusan_row(
            conn, known_from="20200101", known_from_grade="VERIFIED_PUBLICATION",
            source_url=None,
        )


def test_niusan_check_constraint_blocks_verified_without_grade():
    conn = duck_mem()
    conn.execute(_NIUSAN_DDL)
    with pytest.raises(Exception):
        _insert_niusan_row(
            conn, known_from="20200101", known_from_grade="UNVERIFIED", source_url="http://x",
        )


# ======================================================================================
# 5. 关旧开新守卫: 内容列被改必须拒收, 证据等级列被改必须放行
# ======================================================================================


def test_silent_value_rewrite_is_rejected():
    conn = duck_mem()
    conn.execute(_INSTITUTION_DDL)
    _insert_institution_row(conn)
    new_row = list(
        (
            "X1", "*", "tier", "CHANGED", None, None, "20200101", None, "20200101",
            "20200101", None, "VERIFIED_EVENT", "high", "e", "f", "ok", "b2", "2026-09-08 01:00:00",
        )
    )
    with pytest.raises(SystemExit):
        _check_no_silent_value_rewrite(
            conn, "dim_holder_identity_label",
            pk_cols=("holder_code", "name_pattern", "dim", "valid_from_pk"),
            content_cols=("value", "value_cn", "sub_value"),
            new_rows=[tuple(new_row)],
            all_cols=INSTITUTION_ALL_COLS,
        )


def test_evidence_grade_upgrade_at_same_pk_is_allowed():
    """UNVERIFIED 补证升级成 VERIFIED_FIRST_NOTICE, value 本身不变 —— 必须放行不是拒收。

    这是脚本 docstring 最后一节说明的判断: 内容不可变性只锁 value/value_cn/sub_value,
    不锁 known_from_grade 这类"证据质量"字段。
    """
    conn = duck_mem()
    conn.execute(_INSTITUTION_DDL)
    _insert_institution_row(
        conn, known_from=None, known_from_lower_bound="20200101", known_from_grade="UNVERIFIED",
    )
    upgraded_row = (
        "X1", "*", "tier", "local", None, None, "20200101", None, "20200101",
        "20200101", None, "VERIFIED_FIRST_NOTICE", "high", "e", "f", "ok", "b2",
        "2026-09-08 01:00:00",
    )
    # 不应该抛异常
    _check_no_silent_value_rewrite(
        conn, "dim_holder_identity_label",
        pk_cols=("holder_code", "name_pattern", "dim", "valid_from_pk"),
        content_cols=("value", "value_cn", "sub_value"),
        new_rows=[upgraded_row],
        all_cols=INSTITUTION_ALL_COLS,
    )


def test_silent_rewrite_check_is_noop_when_table_absent():
    """表还没建过(全新库)时不该报错——没有历史可比不等于'检测到改写'。"""
    conn = duck_mem()
    _check_no_silent_value_rewrite(
        conn, "dim_holder_identity_label",
        pk_cols=("holder_code", "name_pattern", "dim", "valid_from_pk"),
        content_cols=("value", "value_cn", "sub_value"),
        new_rows=[],
        all_cols=INSTITUTION_ALL_COLS,
    )
