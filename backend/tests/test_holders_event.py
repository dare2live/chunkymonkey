"""fact_holder_event 派生层测试.

验证 lag() 派生 五种 event_type 的正确性, 用最小内存 fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.holders_event import rebuild_holder_events


def _seed_holder_period(conn) -> None:
    """最小 schema: fact_top10_holder_period (按 db.py 主 schema 子集) + fact_holder_event."""

    conn.executescript("""
        CREATE TABLE fact_top10_holder_period (
            stock_code        TEXT NOT NULL,
            stock_name        TEXT,
            market            TEXT,
            report_date       TEXT NOT NULL,
            holder_set        TEXT NOT NULL,
            holder_rank       INTEGER NOT NULL,
            row_seq           INTEGER NOT NULL DEFAULT 1,
            holder_name       TEXT NOT NULL,
            holder_name_norm  TEXT,
            share_class       TEXT,
            is_secondary_class BOOLEAN DEFAULT FALSE,
            is_exit_row       BOOLEAN DEFAULT FALSE,
            shares_text       TEXT,
            shares_approx     BIGINT,
            shares_precision  TEXT,
            hold_amount       REAL,
            hold_ratio_float  DOUBLE,
            hold_ratio_total  DOUBLE,
            hold_ratio        REAL,
            hold_market_cap   REAL,
            holder_type       TEXT,
            share_nature      TEXT,
            change_status     TEXT,
            change_shares_text TEXT,
            change_shares_approx BIGINT,
            hold_change       TEXT,
            hold_change_num   REAL,
            notice_date       TEXT,
            effective_date    TEXT,
            page_update_date  TEXT,
            source            TEXT NOT NULL,
            source_tier       SMALLINT NOT NULL,
            raw_hash          TEXT,
            fetched_at        TEXT,
            created_at        TEXT
        );
        CREATE TABLE fact_holder_event (
            stock_code        TEXT NOT NULL,
            stock_name        TEXT,
            holder_name       TEXT NOT NULL,
            holder_name_norm  TEXT NOT NULL,
            share_class       TEXT,
            report_date       TEXT NOT NULL,
            prev_report_date  TEXT,
            event_type        TEXT NOT NULL,
            shares_before     BIGINT,
            shares_after      BIGINT,
            shares_delta      BIGINT,
            ratio_float_before DOUBLE,
            ratio_float_after  DOUBLE,
            ratio_total_before DOUBLE,
            ratio_total_after  DOUBLE,
            holder_type       TEXT,
            holder_set        TEXT NOT NULL,
            source            TEXT NOT NULL,
            source_tier       SMALLINT NOT NULL,
            raw_hash          TEXT,
            created_at        TEXT,
            PRIMARY KEY (stock_code, holder_name_norm, share_class, report_date, event_type, holder_set)
        );
    """)


def _insert(conn, **kw):
    """简化插入: 缺省字段填默认."""

    defaults = {
        "stock_code": "600519", "stock_name": "贵州茅台", "market": "SH",
        "holder_set": "free", "holder_rank": 1, "row_seq": 1,
        "holder_name_norm": kw.get("holder_name", "X"),
        "share_class": "A",
        "is_secondary_class": False, "is_exit_row": False,
        "source": "tdx_f10", "source_tier": 1,
        "shares_text": str(kw.get("shares_approx", 0)),
        "shares_precision": "股",
        "hold_amount": float(kw.get("shares_approx", 0)),
        "hold_ratio": kw.get("hold_ratio_float"),
    }
    defaults.update(kw)
    cols = list(defaults.keys())
    placeholders = ",".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO fact_top10_holder_period ({','.join(cols)}) VALUES ({placeholders})",
        tuple(defaults[c] for c in cols),
    )


def test_new_entry_then_unchanged_then_increase_then_decrease_then_exit():
    """5 个连续报告期, 同一股东走完五种状态."""

    conn = duck_mem()
    _seed_holder_period(conn)

    # 期 t1: 新进 (无 t0)
    _insert(conn, holder_name="X", report_date="20250331",
            shares_approx=10000, hold_ratio_float=1.0)
    # 期 t2: 不变 (持股相同)
    _insert(conn, holder_name="X", report_date="20250630",
            shares_approx=10000, hold_ratio_float=1.0)
    # 期 t3: 增持
    _insert(conn, holder_name="X", report_date="20250930",
            shares_approx=15000, hold_ratio_float=1.5)
    # 期 t4: 减持
    _insert(conn, holder_name="X", report_date="20251231",
            shares_approx=12000, hold_ratio_float=1.2)
    # 期 t5: 退出 (is_exit_row=TRUE, 无主表)
    _insert(conn, holder_name="X", report_date="20260331",
            shares_approx=12000, hold_ratio_float=1.2, is_exit_row=True)

    rebuild_holder_events(conn)

    rows = conn.execute("""
        SELECT report_date, event_type, shares_before, shares_after, shares_delta
        FROM fact_holder_event
        WHERE stock_code='600519' AND holder_name_norm='X' AND holder_set='free'
        ORDER BY report_date, event_type
    """).fetchall()
    assert [(r["report_date"], r["event_type"]) for r in rows] == [
        ("20250331", "new_entry"),
        ("20250630", "unchanged"),
        ("20250930", "increase"),
        ("20251231", "decrease"),
        ("20260331", "exit"),
    ]
    # delta 带符号
    delta_by = {r["event_type"]: r["shares_delta"] for r in rows}
    assert delta_by["new_entry"] == 10000
    assert delta_by["unchanged"] == 0
    assert delta_by["increase"] == 5000
    assert delta_by["decrease"] == -3000
    assert delta_by["exit"] == -12000


def test_unchanged_within_tolerance():
    """4 位小数显示精度: 6.81 亿持仓 vs 6.8128 亿 应该判 unchanged (差 ~3000 股)."""

    conn = duck_mem()
    _seed_holder_period(conn)

    _insert(conn, holder_name="moutai_group", report_date="20250331",
            shares_approx=681280000, hold_ratio_float=54.4)
    # 万分之一 tolerance = 681280000 * 0.0001 = 68128. 差 3000 股远小于阈值.
    _insert(conn, holder_name="moutai_group", report_date="20250630",
            shares_approx=681282935, hold_ratio_float=54.4)

    rebuild_holder_events(conn)
    rows = conn.execute("""
        SELECT report_date, event_type FROM fact_holder_event
        WHERE holder_name_norm='moutai_group' ORDER BY report_date
    """).fetchall()
    assert [(r["report_date"], r["event_type"]) for r in rows] == [
        ("20250331", "new_entry"),
        ("20250630", "unchanged"),
    ]


def test_a_h_share_class_partitioned_independently():
    """同股东 A 类 + H 类应分别派生事件 (不互相干扰)."""

    conn = duck_mem()
    _seed_holder_period(conn)

    # A 类 q1 + q2 → unchanged
    _insert(conn, holder_name="zte_xin", report_date="20250930",
            share_class="A", shares_approx=958940000, hold_ratio_float=23.81)
    _insert(conn, holder_name="zte_xin", report_date="20251231",
            share_class="A", shares_approx=958940000, hold_ratio_float=23.81)
    # H 类 q1 + q2 → increase
    _insert(conn, holder_name="zte_xin", report_date="20250930",
            share_class="H", shares_approx=2030000, hold_ratio_float=0.27,
            holder_rank=1, row_seq=2, is_secondary_class=False)  # primary leg of H
    _insert(conn, holder_name="zte_xin", report_date="20251231",
            share_class="H", shares_approx=2038000, hold_ratio_float=0.27,
            holder_rank=1, row_seq=2, is_secondary_class=False)

    rebuild_holder_events(conn)
    rows = conn.execute("""
        SELECT report_date, share_class, event_type FROM fact_holder_event
        WHERE holder_name_norm='zte_xin'
        ORDER BY report_date, share_class
    """).fetchall()
    # q1: 两类都 new_entry; q2: A unchanged, H increase
    types = {(r["report_date"], r["share_class"]): r["event_type"] for r in rows}
    assert types[("20250930", "A")] == "new_entry"
    assert types[("20250930", "H")] == "new_entry"
    assert types[("20251231", "A")] == "unchanged"
    assert types[("20251231", "H")] == "increase"


def test_holder_set_free_and_all_partitioned_independently():
    """holder_set='free' vs 'all' 应该分别派生."""

    conn = duck_mem()
    _seed_holder_period(conn)

    _insert(conn, holder_name="X", report_date="20250331",
            holder_set="free", shares_approx=10000)
    _insert(conn, holder_name="X", report_date="20250331",
            holder_set="all", shares_approx=10000)
    _insert(conn, holder_name="X", report_date="20250630",
            holder_set="free", shares_approx=15000)
    _insert(conn, holder_name="X", report_date="20250630",
            holder_set="all", shares_approx=20000)

    rebuild_holder_events(conn)
    n_free = conn.execute(
        "SELECT count(*) c FROM fact_holder_event WHERE holder_set='free'"
    ).fetchone()["c"]
    n_all = conn.execute(
        "SELECT count(*) c FROM fact_holder_event WHERE holder_set='all'"
    ).fetchone()["c"]
    assert n_free == 2  # new_entry + increase
    assert n_all == 2


def test_null_share_class_uses_underscore_placeholder():
    """share_class=NULL 写入时应被替代为 '_' (PK 不允许 NULL)."""

    conn = duck_mem()
    _seed_holder_period(conn)
    _insert(conn, holder_name="N", report_date="20250331",
            share_class=None, shares_approx=10000)

    rebuild_holder_events(conn)
    row = conn.execute(
        "SELECT share_class FROM fact_holder_event WHERE holder_name_norm='N'"
    ).fetchone()
    assert row["share_class"] == "_"


def test_idempotent_double_run():
    """连续重建两次应得到相同结果, 不重复也不缺失."""

    conn = duck_mem()
    _seed_holder_period(conn)
    _insert(conn, holder_name="A", report_date="20250331", shares_approx=10000)
    _insert(conn, holder_name="A", report_date="20250630", shares_approx=12000)

    rebuild_holder_events(conn)
    n1 = conn.execute("SELECT count(*) c FROM fact_holder_event").fetchone()["c"]
    rebuild_holder_events(conn)
    n2 = conn.execute("SELECT count(*) c FROM fact_holder_event").fetchone()["c"]
    assert n1 == n2 == 2  # new_entry + increase
