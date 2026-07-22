"""v_sw_industry_pit L1 exclusivity — Tier0B taxonomy PIT (2026-07-22).

Locks: (1) sequential reclass closes old out_date at next in_date;
(2) same-in_date dual L1 keeps newer built_at only; (3) as-of day ≤1 active L1
for the four known polluted codes; (4) zero-length losers filtered from view.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "build_sw_industry_view",
    REPO / "backend" / "scripts" / "build_sw_industry_view.py",
)
bsw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(bsw)


def _seed_raw(conn) -> None:
    conn.execute(
        """
        CREATE TABLE raw_tushare_index_member_all (
            l1_code VARCHAR, l1_name VARCHAR,
            l2_code VARCHAR, l2_name VARCHAR,
            l3_code VARCHAR, l3_name VARCHAR,
            ts_code VARCHAR, name VARCHAR,
            in_date VARCHAR, out_date INTEGER, is_new VARCHAR,
            built_at TIMESTAMP
        )
        """
    )
    # 000007 deep-history fixture (builder verify spot-check shape)
    conn.executemany(
        "INSERT INTO raw_tushare_index_member_all VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("801230.SI", "综合", "801231.SI", "综合Ⅱ", "851231.SI", "综合Ⅲ",
             "000007.SZ", "全新好", "20140101", 20211231, "N", "2026-01-01"),
            ("801200.SI", "商贸零售", "801201.SI", "零售", "851201.SI", "百货",
             "000007.SZ", "全新好", "20220101", None, "Y", "2026-01-02"),
            # 002310 sequential reclass — old open out_date must close at 20260701
            ("801720.SI", "建筑装饰", "801723.SI", "基础建设", "857251.SI", "园林工程",
             "002310.SZ", "东方新能", "20170526", None, "Y", "2026-07-22 09:45:40"),
            ("801160.SI", "公用事业", "801161.SI", "电力", "851616.SI", "光伏发电",
             "002310.SZ", "东方新能", "20260701", None, "Y", "2026-07-22 09:45:35"),
            # same-in_date dual L1 — older built_at (传媒) loses to 石油石化
            ("801760.SI", "传媒", "801767.SI", "数字媒体", "857671.SI", "视频媒体",
             "000406.SZ", "石油大明(退市)", "19960628", None, "Y", "2026-06-13 09:17:31"),
            ("801960.SI", "石油石化", "801961.SI", "油气开采Ⅱ", "859611.SI", "油气开采Ⅲ",
             "000406.SZ", "石油大明(退市)", "19960628", None, "Y", "2026-07-22 09:45:46"),
            ("801760.SI", "传媒", "801767.SI", "数字媒体", "857671.SI", "视频媒体",
             "000817.SZ", "辽河油田(退市)", "19980528", None, "Y", "2026-06-13 09:17:31"),
            ("801960.SI", "石油石化", "801961.SI", "油气开采Ⅱ", "859611.SI", "油气开采Ⅲ",
             "000817.SZ", "辽河油田(退市)", "19980528", None, "Y", "2026-07-22 09:45:46"),
            ("801760.SI", "传媒", "801767.SI", "数字媒体", "857671.SI", "视频媒体",
             "000956.SZ", "中原油气(退市)", "19991110", None, "Y", "2026-06-13 09:17:31"),
            ("801960.SI", "石油石化", "801961.SI", "油气开采Ⅱ", "859611.SI", "油气开采Ⅲ",
             "000956.SZ", "中原油气(退市)", "19991110", None, "Y", "2026-07-22 09:45:46"),
            # filler so verify-style coverage stays above toy floor if reused
            ("801010.SI", "农林牧渔", "801016.SI", "种植业", "851016.SI", "种子",
             "600001.SH", "填充股", "20000101", None, "Y", "2026-01-01"),
        ],
    )


def _asof(conn, code: str, t: str) -> str | None:
    row = conn.execute(
        "SELECT l1_name FROM v_sw_industry_pit WHERE stock_code=? AND in_date<=? "
        "AND (out_date IS NULL OR out_date>?) ORDER BY in_date DESC LIMIT 1",
        [code, t, t],
    ).fetchone()
    return row[0] if row else None


def _active_l1(conn, code: str, t: str) -> int:
    return conn.execute(
        "SELECT count(DISTINCT l1_code) FROM v_sw_industry_pit "
        "WHERE stock_code=? AND in_date<=? AND (out_date IS NULL OR out_date>?)",
        [code, t, t],
    ).fetchone()[0]


def test_sw_pit_view_closes_reclass_and_same_day_dual(tmp_path, monkeypatch):
    db = tmp_path / "tushare_raw.duckdb"
    conn = connect(str(db), read_only=False)
    try:
        _seed_raw(conn)
    finally:
        conn.close()

    monkeypatch.setattr(bsw, "DB", str(db))
    bsw.build()

    conn = connect(str(db), read_only=True)
    try:
        assert _asof(conn, "000007", "20180101") == "综合"
        assert _asof(conn, "000007", "20260101") == "商贸零售"

        assert _asof(conn, "002310", "20260630") == "建筑装饰"
        assert _asof(conn, "002310", "20260701") == "公用事业"
        assert _asof(conn, "002310", "20260722") == "公用事业"
        assert _active_l1(conn, "002310", "20260630") == 1
        assert _active_l1(conn, "002310", "20260701") == 1
        assert _active_l1(conn, "002310", "20260722") == 1

        for code in ("000406", "000817", "000956"):
            assert _active_l1(conn, code, "20260722") == 1
            assert _asof(conn, code, "20260722") == "石油石化"
            # 传媒 loser must not appear as open member
            open_names = [
                r[0]
                for r in conn.execute(
                    "SELECT l1_name FROM v_sw_industry_pit "
                    "WHERE stock_code=? AND out_date IS NULL",
                    [code],
                ).fetchall()
            ]
            assert open_names == ["石油石化"]

        dups = conn.execute(
            """
            SELECT stock_code FROM v_sw_industry_pit
            WHERE out_date IS NULL
            GROUP BY stock_code
            HAVING count(DISTINCT l1_code) > 1
            """
        ).fetchall()
        assert dups == []

        # segments-style JOIN must not fan out 002310 on 20260722
        n = conn.execute(
            """
            SELECT count(*) FROM (SELECT '002310' AS stock_code, '20260722' AS trade_date) b
            LEFT JOIN v_sw_industry_pit p
              ON p.stock_code = b.stock_code AND p.in_date <= b.trade_date
             AND (p.out_date IS NULL OR p.out_date > b.trade_date)
            """
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()
