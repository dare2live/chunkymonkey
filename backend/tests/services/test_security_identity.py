"""S0/S1 -- typed loader + as-of identity resolver for
backend/config/security_code_changes.yaml (asof_identity_r1.md §3.1-§3.3/§9
S0-S1). One isolated case per gating condition: each fixture satisfies every
other condition and violates exactly one.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.duck_adapter import connect as duck_connect
from services.security_identity import (
    CodeChangeEvent,
    CodeChangeSet,
    IdentityRuleInvalid,
    IdentityUnresolvedError,
    assert_identity_rule_valid,
    assert_no_unresolved,
    identity_cte_sql,
    kline_entity_duplicate_pairs,
    load_security_code_changes,
    register_code_changes_temp,
)

_BASE_EVENT = {
    "old_code": "300114.SZ",
    "new_code": "302132.SZ",
    "effective_date": "20250217",
    "exchange": "SZSE",
    "kind": "reorg_rename",
    "source_kind": "announcement",
    "source_ref": "cninfo 2025-02-15 公告编号 2025-028",
    "checked_at": "2026-09-12",
}


def _doc(events: list[dict], version: int = 1) -> dict:
    return {"version": version, "events": events}


def _write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "security_code_changes.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Fail-closed: structural
# ---------------------------------------------------------------------------


def test_extra_top_level_key_rejected(tmp_path):
    doc = _doc([dict(_BASE_EVENT)])
    doc["extra_key"] = "unexpected"
    p = _write(tmp_path, doc)
    with pytest.raises(ValueError, match=r"top-level keys must be exactly"):
        load_security_code_changes(p)


def test_version_other_than_one_rejected(tmp_path):
    """version 是契约版本: 值不是 1 就说明这份登记的形状可能已经不是 loader 认识的
    那一份, 与未知键同样 fail-closed —— 拒载, 不按旧形状猜着读。
    其它条件全部满足, 只有 version 为假。"""
    p = _write(tmp_path, _doc([dict(_BASE_EVENT)], version=2))
    with pytest.raises(ValueError, match=r"version must be 1"):
        load_security_code_changes(p)


def test_event_missing_source_ref_key_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    del ev["source_ref"]
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\] keys must be exactly"):
        load_security_code_changes(p)


# ---------------------------------------------------------------------------
# Fail-closed: per-field
# ---------------------------------------------------------------------------


def test_empty_source_ref_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["source_ref"] = ""
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.source_ref must be a non-empty string"):
        load_security_code_changes(p)


def test_unknown_kind_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["kind"] = "not_a_real_kind"
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.kind = 'not_a_real_kind' not in"):
        load_security_code_changes(p)


def test_unknown_source_kind_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["source_kind"] = "not_a_real_source_kind"
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(
        ValueError, match=r"events\[0\]\.source_kind = 'not_a_real_source_kind' not in"
    ):
        load_security_code_changes(p)


def test_code_without_exchange_suffix_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["old_code"] = "300114"
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.old_code = '300114' does not match"):
        load_security_code_changes(p)


def test_old_equals_new_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["new_code"] = ev["old_code"]
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.old_code == events\[0\]\.new_code"):
        load_security_code_changes(p)


def test_effective_date_not_a_real_calendar_day_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["effective_date"] = "20250230"  # February has no 30th
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(
        ValueError, match=r"events\[0\]\.effective_date = '20250230' is not a valid calendar date"
    ):
        load_security_code_changes(p)


# ---------------------------------------------------------------------------
# Fail-closed: cross-event (uniqueness / chain / cycle)
# ---------------------------------------------------------------------------


def test_duplicate_new_code_rejected(tmp_path):
    ev0 = dict(_BASE_EVENT)
    ev1 = dict(
        _BASE_EVENT,
        old_code="000043.SZ",
        new_code="302132.SZ",  # collides with ev0's new_code
        effective_date="20260101",
    )
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(
        ValueError, match=r"events\[1\]\.new_code '302132\.SZ' duplicates events\[0\]\.new_code"
    ):
        load_security_code_changes(p)


def test_duplicate_old_code_rejected(tmp_path):
    ev0 = dict(_BASE_EVENT)
    ev1 = dict(
        _BASE_EVENT,
        new_code="000001.SZ",  # ev1.old_code left as ev0's old_code -> collision
        effective_date="20260101",
    )
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(
        ValueError, match=r"events\[1\]\.old_code '300114\.SZ' duplicates events\[0\]\.old_code"
    ):
        load_security_code_changes(p)


def test_chain_effective_date_not_increasing_rejected(tmp_path):
    # A -> B (2025) -> C (2024): the second leg must be strictly after the
    # first, not before it. Acyclic on purpose, so cycle detection cannot
    # be what fires here.
    ev0 = dict(_BASE_EVENT, old_code="100000.SZ", new_code="200000.SZ", effective_date="20250101")
    ev1 = dict(_BASE_EVENT, old_code="200000.SZ", new_code="300000.SZ", effective_date="20240101")
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(ValueError, match=r"must be strictly greater"):
        load_security_code_changes(p)


def test_cycle_rejected(tmp_path):
    # A -> B, B -> A: a cycle regardless of dates (chosen here so that both
    # legs are individually "increasing" in file order -- if cycle detection
    # were removed, only the chain-order arithmetic contradiction would
    # eventually surface, not a dedicated cycle message).
    ev0 = dict(_BASE_EVENT, old_code="100000.SZ", new_code="200000.SZ", effective_date="20240101")
    ev1 = dict(_BASE_EVENT, old_code="200000.SZ", new_code="100000.SZ", effective_date="20250101")
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(ValueError, match=r"code-change cycle detected"):
        load_security_code_changes(p)


# ---------------------------------------------------------------------------
# Should load: the repository's real YAML
# ---------------------------------------------------------------------------


def test_repo_yaml_loads_and_parses_both_events():
    ccs = load_security_code_changes()
    assert isinstance(ccs, CodeChangeSet)
    assert len(ccs.events) == 2

    e0, e1 = ccs.events
    assert e0 == CodeChangeEvent(
        old_code="300114.SZ",
        new_code="302132.SZ",
        effective_date="20250217",
        exchange="SZSE",
        kind="reorg_rename",
        source_kind="announcement",
        source_ref=(
            "cninfo 2025-02-15 static.cninfo.com.cn/finalpage/2025-02-15/1222544408.PDF "
            "公告编号 2025-028"
        ),
        checked_at="2026-09-12",
    )
    assert e1.old_code == "000043.SZ"
    assert e1.new_code == "001914.SZ"
    assert e1.effective_date == "20191216"
    assert e1.exchange == "SZSE"
    assert e1.kind == "reorg_rename"
    assert e1.source_kind == "kline_succession_observed"
    # I7's numbers must be traceable in the source_ref lineage evidence.
    assert "20191213" in e1.source_ref
    assert "20.25" in e1.source_ref
    assert "20191216" in e1.source_ref

    assert ccs.by_old["300114.SZ"] is e0
    assert ccs.by_new["302132.SZ"] is e0
    assert ccs.by_old["000043.SZ"] is e1
    assert ccs.by_new["001914.SZ"] is e1
    assert isinstance(ccs.sha256, str)
    assert len(ccs.sha256) == 64
    int(ccs.sha256, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# S1 -- 按日证券身份解析器 (asof_identity_r1.md §3.2/§3.3/§9 S1)
#
# fixture: 内存 DuckDB (services.duck_adapter.connect(":memory:"), 项目现有
# in-memory 测试约定, 见 test_calendar_gate.py / test_data_deletion_ledger.py),
# 假 K 线表 (ts_code, trade_date DATE, close, vol, amount) + 假 raw top_inst 表
# (trade_date VARCHAR YYYYMMDD, 与生产 raw 表同形), 事件表由
# register_code_changes_temp 从直接构造的 CodeChangeSet 装 (S0 loader 已经把
# YAML 形状测过 14 遍, 这里不重新走文件 IO)。不 mock K 线/universe 判定本身。
# ---------------------------------------------------------------------------

_EVENT = CodeChangeEvent(
    old_code="300114.SZ",
    new_code="302132.SZ",
    effective_date="20250217",
    exchange="SZSE",
    kind="reorg_rename",
    source_kind="announcement",
    source_ref="test fixture (asof_identity_r1.md I7 pattern)",
    checked_at="2026-09-12",
)


def _single_event_ccs(event: CodeChangeEvent = _EVENT) -> CodeChangeSet:
    return CodeChangeSet(
        events=(event,),
        by_new={event.new_code: event},
        by_old={event.old_code: event},
        sha256="0" * 64,
    )


def _make_kline_table(con, name: str = "kline") -> None:
    con.execute(
        f"""
        CREATE TABLE {name} (
            ts_code VARCHAR NOT NULL,
            trade_date DATE NOT NULL,
            close DOUBLE NOT NULL,
            vol DOUBLE NOT NULL,
            amount DOUBLE NOT NULL
        )
        """
    )


def _insert_kline(con, name: str, rows: list[tuple[str, str, float, float, float]]) -> None:
    for ts_code, trade_date, close, vol, amount in rows:
        con.execute(
            f"INSERT INTO {name} VALUES (?, strptime(?, '%Y%m%d')::DATE, ?, ?, ?)",
            (ts_code, trade_date, close, vol, amount),
        )


def _make_top_inst_table(con, name: str = "raw_top_inst") -> None:
    con.execute(
        f"""
        CREATE TABLE {name} (
            trade_date VARCHAR NOT NULL,
            ts_code VARCHAR NOT NULL,
            exalter VARCHAR NOT NULL,
            side VARCHAR NOT NULL,
            reason VARCHAR NOT NULL,
            board_rank INTEGER,
            buy DOUBLE,
            sell DOUBLE
        )
        """
    )


def _insert_top_inst(con, name: str, rows: list[dict]) -> None:
    for r in rows:
        con.execute(
            f"INSERT INTO {name} "
            "(trade_date, ts_code, exalter, side, reason, board_rank, buy, sell) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["trade_date"],
                r["ts_code"],
                r["exalter"],
                r["side"],
                r["reason"],
                r.get("board_rank"),
                r.get("buy"),
                r.get("sell"),
            ),
        )


def _top_inst_row(
    trade_date: str,
    ts_code: str,
    *,
    exalter: str = "SEAT-X",
    side: str = "buy",
    reason: str = "REASON",
    board_rank: int | None = 1,
    buy: float | None = 100.0,
    sell: float | None = 0.0,
) -> dict:
    return {
        "trade_date": trade_date,
        "ts_code": ts_code,
        "exalter": exalter,
        "side": side,
        "reason": reason,
        "board_rank": board_rank,
        "buy": buy,
        "sell": sell,
    }


def _status_by_code(con, sql: str) -> dict[str, str]:
    return {r["ts_code"]: r["identity_status"] for r in con.execute(sql).fetchall()}


# ---------------------------------------------------------------------------
# identity_cte_sql -- gating conditions R1-R6 (K1-K8), one isolated case each
# ---------------------------------------------------------------------------


def test_k1_twin_row_is_backfill_duplicate_even_when_kline_confirms_directly():
    """K1: 双方同日全同 -> 302132 判 backfill_duplicate 且被排除在"发布行"外,
    即便 K 线自己也已经把 302132 那天的行回写了 (I2 的真实场景: K 线自己就有
    这条重复)。这条 fixture 是刻意让 K 线也能直接确认 302132, 用来暴露"R1 必须
    先于 R4 判"的顺序要求 -- 若实现把普通 K 线命中检查挪到 twin 检查之前,
    302132 会被误判成 kline_confirmed, 本用例就会转红。
    """
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("300114.SZ", "20250216", 72.0, 100.0, 1000.0),
            ("302132.SZ", "20250216", 72.0, 100.0, 1000.0),
        ],
    )
    _make_top_inst_table(con)
    _insert_top_inst(
        con,
        "raw_top_inst",
        [
            _top_inst_row("20250216", "300114.SZ"),
            _top_inst_row("20250216", "302132.SZ"),
        ],
    )
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    statuses = _status_by_code(con, sql)
    assert statuses["302132.SZ"] == "backfill_duplicate"
    assert statuses["300114.SZ"] == "kline_confirmed"

    published = con.execute(
        f"SELECT ts_code FROM ({sql}) WHERE identity_status NOT IN "
        "('backfill_duplicate', 'unresolved')"
    ).fetchall()
    assert [r["ts_code"] for r in published] == ["300114.SZ"]


def test_k2_backfill_remapped_when_old_code_has_kline_membership():
    """K2: 无 twin (300114 那天没有对应行), 但 K 线里 300114 那天有行 ->
    backfill_remapped, ts_code_asof 改判为旧码, vendor_code/ts_code 仍是供应商
    原样 302132 (红线 1/4: raw/供应商代码列一个不改)。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("300114.SZ", "20250210", 70.0, 90.0, 900.0)])
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", [_top_inst_row("20250210", "302132.SZ")])
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    row = con.execute(sql).fetchall()[0]
    assert row["identity_status"] == "backfill_remapped"
    assert row["ts_code_asof"] == "300114.SZ"
    assert row["vendor_code"] == "302132.SZ"
    assert row["ts_code"] == "302132.SZ"


def test_k3_backfill_candidate_without_old_kline_membership_is_unresolved():
    """K3: 无 twin 且旧码那天在 K 线里也没有行 -> unresolved, assert_no_unresolved
    raise (R2 分支失败直接走 R6, 不看 K 线前沿 -- 规则表写的是"否则 R6" 不是
    "否则看前沿")。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("300114.SZ", "20250101", 60.0, 50.0, 500.0)])
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", [_top_inst_row("20250210", "302132.SZ")])
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    row = con.execute(sql).fetchall()[0]
    assert row["identity_status"] == "unresolved"
    with pytest.raises(IdentityUnresolvedError, match=r"unresolved identity row"):
        assert_no_unresolved(con, ident_sql=sql)


def test_k4_natural_key_mismatch_is_not_a_twin_both_rows_judged_individually():
    """K4: 自然键差一列 (board_rank 1 vs 2) -> 不算 twin, 两行都保留, 各自判。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("300114.SZ", "20250210", 70.0, 90.0, 900.0),
            ("302132.SZ", "20250210", 71.0, 91.0, 910.0),
        ],
    )
    _make_top_inst_table(con)
    _insert_top_inst(
        con,
        "raw_top_inst",
        [
            _top_inst_row("20250210", "300114.SZ", board_rank=1),
            _top_inst_row("20250210", "302132.SZ", board_rank=2),
        ],
    )
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    statuses = _status_by_code(con, sql)
    assert len(statuses) == 2
    assert statuses["300114.SZ"] == "kline_confirmed"
    assert statuses["302132.SZ"] == "backfill_remapped"


def test_k4b_null_natural_key_column_still_matches_as_twin():
    """K4 的 NULL 变体 (§9 S1 变异清单钉子): 自然键里 board_rank 两边都是 NULL
    时仍必须判为 twin (NULL IS NOT DISTINCT FROM NULL = true)。若实现把
    IS NOT DISTINCT FROM 换成裸 `=`, NULL = NULL 是 NULL/false, 302132 会从
    backfill_duplicate 变成 backfill_remapped, 本用例转红。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("300114.SZ", "20250210", 70.0, 90.0, 900.0)])
    _make_top_inst_table(con)
    _insert_top_inst(
        con,
        "raw_top_inst",
        [
            _top_inst_row("20250210", "300114.SZ", board_rank=None),
            _top_inst_row("20250210", "302132.SZ", board_rank=None),
        ],
    )
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    statuses = _status_by_code(con, sql)
    assert statuses["302132.SZ"] == "backfill_duplicate"


def test_k5_ordinary_row_confirmed_by_direct_kline_membership():
    """K5: 普通行, 当日有 K 线 -> kline_confirmed, ts_code_asof = 供应商代码
    本身 (没有任何事件相关)。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("000001.SZ", "20250210", 10.0, 20.0, 200.0)])
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", [_top_inst_row("20250210", "000001.SZ")])
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    row = con.execute(sql).fetchall()[0]
    assert row["identity_status"] == "kline_confirmed"
    assert row["ts_code_asof"] == "000001.SZ"


def test_k6_row_beyond_kline_frontier_is_pending():
    """K6: d 在 K 线 MAX(trade_date) 之后 -> kline_pending, 不 raise (暂发,
    等 K 线追上再复核)。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("000001.SZ", "20250210", 10.0, 20.0, 200.0)])
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", [_top_inst_row("20250211", "000001.SZ")])
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    row = con.execute(sql).fetchall()[0]
    assert row["identity_status"] == "kline_pending"
    assert row["ts_code_asof"] == "000001.SZ"
    counts = assert_no_unresolved(con, ident_sql=sql)
    assert counts == {"kline_pending": 1}


def test_k7_row_within_frontier_without_kline_or_event_is_unresolved():
    """K7: d <= K 线前沿, 当天无 K 线行, 也不牵涉任何事件 -> unresolved,
    assert_no_unresolved raise (红线 3: 缺失只能传播为缺失, 不静默剔除)。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("000001.SZ", "20250211", 10.0, 20.0, 200.0)])
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", [_top_inst_row("20250210", "000002.SZ")])
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    with pytest.raises(IdentityUnresolvedError, match=r"unresolved identity row"):
        assert_no_unresolved(con, ident_sql=sql)


def test_k8_old_code_reappearing_after_effective_date_is_unresolved():
    """K8: 旧码在 effective 之后还出现 (R3 的场景) -> 没有独立分支, 走普通路径:
    K 线里那天不会再有旧码的行 (它已经改名了) -> 不是 R4 -> 未超前沿 -> R6
    unresolved -> raise。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("302132.SZ", "20250301", 75.0, 60.0, 700.0)])
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", [_top_inst_row("20250218", "300114.SZ")])
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    row = con.execute(sql).fetchall()[0]
    assert row["identity_status"] == "unresolved"
    with pytest.raises(IdentityUnresolvedError, match=r"unresolved identity row"):
        assert_no_unresolved(con, ident_sql=sql)


# ---------------------------------------------------------------------------
# assert_identity_rule_valid / kline_entity_duplicate_pairs -- §3.2 前提锁
# (K9-K11), 阈值钉子 (K9b)
# ---------------------------------------------------------------------------


def test_kline_entity_duplicate_pairs_returns_typed_tuples():
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("500001.SZ", "20240101", 10.0, 100.0, 1000.0),
            ("500002.SZ", "20240101", 10.0, 100.0, 1000.0),
            ("500001.SZ", "20240102", 11.0, 110.0, 1100.0),
            ("500002.SZ", "20240102", 11.0, 110.0, 1100.0),
        ],
    )
    pairs = kline_entity_duplicate_pairs(con, kline_sql="kline")
    assert pairs == [("500001.SZ", "500002.SZ", 2, "20240101", "20240102")]


def test_k9_unregistered_kline_duplicate_pair_invalidates_precondition():
    """K9: K 线里一对未登记的全同 3 天 -> IdentityRuleInvalid (前提锁)。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("500001.SZ", "20240101", 10.0, 100.0, 1000.0),
            ("500002.SZ", "20240101", 10.0, 100.0, 1000.0),
            ("500001.SZ", "20240102", 11.0, 110.0, 1100.0),
            ("500002.SZ", "20240102", 11.0, 110.0, 1100.0),
            ("500001.SZ", "20240103", 12.0, 120.0, 1200.0),
            ("500002.SZ", "20240103", 12.0, 120.0, 1200.0),
        ],
    )
    with pytest.raises(IdentityRuleInvalid, match=r"unregistered K-line entity-duplicate pair"):
        assert_identity_rule_valid(con, _single_event_ccs(), kline_sql="kline")


def test_k9b_unregistered_single_day_duplicate_still_invalidates_precondition():
    """K9b (阈值钉子, §9 S1 变异清单): 仅 1 天的未登记全同也必须 raise。若
    _DUPLICATE_DAY_THRESHOLD 被改成 2, kline_entity_duplicate_pairs 就不会
    再报出这一对 (1 天 < 2), assert_identity_rule_valid 不再 raise -- 本用例
    转绿而 K9 (3 天) 仍然转红, 由此把阈值钉在 1 天。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("500001.SZ", "20240101", 10.0, 100.0, 1000.0),
            ("500002.SZ", "20240101", 10.0, 100.0, 1000.0),
        ],
    )
    with pytest.raises(IdentityRuleInvalid, match=r"unregistered K-line entity-duplicate pair"):
        assert_identity_rule_valid(con, _single_event_ccs(), kline_sql="kline")


def test_k10_registered_pair_whose_interval_reaches_effective_date_invalidates_precondition():
    """K10: 已登记, 但重复区间延伸到了 effective_date 当天 (未严格早于) ->
    IdentityRuleInvalid (区间必须 ⊆ [K 线首日, effective_date))。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("300114.SZ", "20250216", 72.0, 100.0, 1000.0),
            ("302132.SZ", "20250216", 72.0, 100.0, 1000.0),
            ("300114.SZ", "20250217", 72.18, 100.0, 1000.0),
            ("302132.SZ", "20250217", 72.18, 100.0, 1000.0),
        ],
    )
    with pytest.raises(IdentityRuleInvalid, match=r"does not end before effective_date"):
        assert_identity_rule_valid(con, _single_event_ccs(), kline_sql="kline")


def test_k11_registered_pair_with_interval_fully_before_effective_date_is_valid():
    """K11: 已登记且区间合法 (整段早于 effective_date) -> 放行, 不 raise。"""
    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("300114.SZ", "20250216", 72.0, 100.0, 1000.0),
            ("302132.SZ", "20250216", 72.0, 100.0, 1000.0),
        ],
    )
    assert assert_identity_rule_valid(con, _single_event_ccs(), kline_sql="kline") is None


# ---------------------------------------------------------------------------
# R0 composition + row-count invariant (K12-K13)
# ---------------------------------------------------------------------------


def test_k12_universe_filter_runs_upstream_of_identity_resolution():
    """K12: R0 (920/11/20 等非白名单前缀) 由 apply_universe_serve_filter 在喂
    给 identity_cte_sql 之前先排除, 计入 evidence.excluded_row_count; 这些行
    不出现在 ident 里 (不是被 ident 判成某种 status 后再过滤掉, 是压根没进去)。
    """
    from services.data_sources.universe_serve_filter import apply_universe_serve_filter
    from services.universe import UNIVERSE_POLICY

    raw_rows = [
        _top_inst_row("20250210", "000001.SZ"),
        _top_inst_row("20250210", "920819.BJ"),  # 92 前缀, 北交所
        _top_inst_row("20250210", "113050.SH"),  # 11 前缀, 可转债
        _top_inst_row("20250210", "200029.SZ"),  # 20 前缀, B 股
    ]
    kept, evidence = apply_universe_serve_filter(
        raw_rows, policy=UNIVERSE_POLICY, filter_column="ts_code"
    )
    assert evidence.excluded_row_count == 3
    assert {row["ts_code"] for row in kept} == {"000001.SZ"}

    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(con, "kline", [("000001.SZ", "20250210", 10.0, 20.0, 200.0)])
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", kept)
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    ts_codes = {r["ts_code"] for r in con.execute(sql).fetchall()}
    assert ts_codes == {"000001.SZ"}


def test_k13_row_count_invariant_r0_plus_r1_plus_published_equals_input():
    """K13 (不变量, §3.3): 输入行数 = R0 (venue 排除) + R1 (backfill_duplicate
    丢弃) + 发布行数。构造 10 行: 1 行非白名单前缀 (R0); 两对 twin (各 1 旧码
    行 + 1 新码回写行, 旧码行照常发布, 新码回写行被 R1 丢弃, 计 2); 5 行普通行
    各自确认发布。10 = 1(R0) + 2(R1) + 7(发布)。"""
    from services.data_sources.universe_serve_filter import apply_universe_serve_filter
    from services.universe import UNIVERSE_POLICY

    raw_rows = [
        _top_inst_row("20250210", "920819.BJ", exalter="VENUE"),
        _top_inst_row("20250210", "300114.SZ", exalter="PAIR-A"),
        _top_inst_row("20250210", "302132.SZ", exalter="PAIR-A"),
        _top_inst_row("20250211", "300114.SZ", exalter="PAIR-B"),
        _top_inst_row("20250211", "302132.SZ", exalter="PAIR-B"),
        _top_inst_row("20250101", "000001.SZ", exalter="ORD-1"),
        _top_inst_row("20250101", "000002.SZ", exalter="ORD-2"),
        _top_inst_row("20250101", "000003.SZ", exalter="ORD-3"),
        _top_inst_row("20250101", "000004.SZ", exalter="ORD-4"),
        _top_inst_row("20250101", "000005.SZ", exalter="ORD-5"),
    ]
    assert len(raw_rows) == 10

    kept, evidence = apply_universe_serve_filter(
        raw_rows, policy=UNIVERSE_POLICY, filter_column="ts_code"
    )
    assert evidence.excluded_row_count == 1

    con = duck_connect(":memory:")
    _make_kline_table(con)
    _insert_kline(
        con,
        "kline",
        [
            ("300114.SZ", "20250210", 70.0, 90.0, 900.0),
            ("300114.SZ", "20250211", 71.0, 91.0, 910.0),
            ("000001.SZ", "20250101", 1.0, 1.0, 1.0),
            ("000002.SZ", "20250101", 2.0, 2.0, 2.0),
            ("000003.SZ", "20250101", 3.0, 3.0, 3.0),
            ("000004.SZ", "20250101", 4.0, 4.0, 4.0),
            ("000005.SZ", "20250101", 5.0, 5.0, 5.0),
        ],
    )
    _make_top_inst_table(con)
    _insert_top_inst(con, "raw_top_inst", kept)
    register_code_changes_temp(con, _single_event_ccs())
    sql = identity_cte_sql("top_inst", src_alias="raw_top_inst", kline_alias="kline")

    counts = assert_no_unresolved(con, ident_sql=sql)
    r1_dropped = counts.get("backfill_duplicate", 0)
    published = sum(n for status, n in counts.items() if status != "backfill_duplicate")

    assert r1_dropped == 2
    assert published == 7
    assert evidence.excluded_row_count + r1_dropped + published == len(raw_rows)


# ---------------------------------------------------------------------------
# Misc gating conditions not in the K1-K13 list but exercised by the S1
# signature (unknown domain / idempotent temp-table registration)
# ---------------------------------------------------------------------------


def test_identity_cte_sql_rejects_unknown_domain():
    with pytest.raises(ValueError, match=r"unknown identity domain"):
        identity_cte_sql("not_a_real_domain", src_alias="x", kline_alias="y")


def test_register_code_changes_temp_is_idempotent():
    con = duck_connect(":memory:")
    register_code_changes_temp(con, _single_event_ccs())
    register_code_changes_temp(con, _single_event_ccs())
    n = con.execute("SELECT COUNT(*) FROM security_code_change").fetchall()[0][0]
    assert n == 1
