import sys
import threading
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ingest_holders_tdxhub as ingest  # noqa: E402
from services.holders_resolver import ResolverResult  # noqa: E402


class _FakeRawFetcher:
    def __init__(self, texts):
        self.texts = texts
        self.closed = False

    def fetch_text(self, symbol):
        value = self.texts.get(symbol)
        if isinstance(value, Exception):
            raise value
        return value

    def stats(self):
        return {"active_server": ("fixture", 7709)}

    def close(self):
        self.closed = True


def _make_conn(*, include_holder_unique: bool = True):
    con = duckdb.connect(":memory:")
    holder_unique_tail = (
        ",\n            UNIQUE(stock_code, report_date, holder_set, source, is_exit_row, holder_rank, row_seq, share_class)"
        if include_holder_unique
        else ""
    )
    con.execute(
        f"""
        CREATE TABLE raw_tdx_f10_holder_research (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            fetched_at TIMESTAMP NOT NULL,
            page_update_date DATE,
            raw_text TEXT NOT NULL,
            raw_hash VARCHAR(64) NOT NULL,
            bytes_len INTEGER,
            server TEXT,
            f10_format TEXT,
            parser_version TEXT DEFAULT 'v1',
            PRIMARY KEY (stock_code, raw_hash)
        );
        CREATE TABLE fact_top10_holder_period (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            report_date TEXT NOT NULL,
            holder_set TEXT NOT NULL,
            holder_rank INTEGER NOT NULL,
            row_seq INTEGER NOT NULL DEFAULT 1,
            holder_name TEXT NOT NULL,
            holder_name_norm TEXT,
            share_class TEXT,
            is_secondary_class BOOLEAN DEFAULT FALSE,
            is_exit_row BOOLEAN DEFAULT FALSE,
            shares_text TEXT,
            shares_approx BIGINT,
            shares_precision TEXT,
            hold_amount REAL,
            hold_ratio_float DOUBLE,
            hold_ratio_total DOUBLE,
            hold_ratio REAL,
            hold_market_cap REAL,
            holder_type TEXT,
            share_nature TEXT,
            change_status TEXT,
            change_shares_text TEXT,
            change_shares_approx BIGINT,
            hold_change TEXT,
            hold_change_num REAL,
            notice_date TEXT,
            effective_date TEXT,
            page_update_date TEXT,
            source TEXT NOT NULL,
            source_tier SMALLINT NOT NULL,
            raw_hash TEXT,
            fetched_at TEXT,
            created_at TEXT{holder_unique_tail}
        );
        CREATE TABLE fact_controlling_shareholder (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            primary_label TEXT,
            primary_name TEXT,
            primary_ratio DOUBLE,
            primary_raw TEXT,
            actual_name TEXT,
            actual_ratio DOUBLE,
            actual_raw TEXT,
            page_update_date TEXT,
            source TEXT NOT NULL,
            source_tier SMALLINT NOT NULL,
            raw_hash TEXT,
            fetched_at TEXT,
            PRIMARY KEY (stock_code, source)
        );
        CREATE TABLE fact_shareholder_plan (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            announce_date TEXT,
            subject TEXT,
            direction TEXT,
            progress TEXT,
            start_date TEXT,
            end_date TEXT,
            target_shares_text TEXT,
            target_shares BIGINT,
            target_ratio_text TEXT,
            target_ratio DOUBLE,
            reason TEXT,
            narrative TEXT,
            page_update_date TEXT,
            source TEXT NOT NULL,
            source_tier SMALLINT NOT NULL,
            raw_hash TEXT,
            fetched_at TEXT,
            plan_seq INTEGER
        );
        CREATE TABLE fact_shareholder_trade (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            change_date TEXT,
            holder_name TEXT,
            holder_name_norm TEXT,
            shares_before_text TEXT,
            shares_before BIGINT,
            shares_change_text TEXT,
            shares_change BIGINT,
            shares_after_text TEXT,
            shares_after BIGINT,
            ratio_after DOUBLE,
            change_type TEXT,
            page_update_date TEXT,
            source TEXT NOT NULL,
            source_tier SMALLINT NOT NULL,
            raw_hash TEXT,
            fetched_at TEXT,
            trade_seq INTEGER
        );
        CREATE TABLE dim_holder_alias (
            alias TEXT,
            canonical_name TEXT
        );
        """
    )
    return con


def _make_result():
    fetched_at = "2026-05-05T01:30:00"
    return ResolverResult(
        holders=[
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "SH",
                "report_date": "20260331",
                "holder_set": "free",
                "holder_rank": 1,
                "row_seq": 1,
                "holder_name": "Holder A",
                "share_class": "A",
                "is_secondary_class": False,
                "is_exit_row": False,
                "shares_text": "1000股",
                "shares_approx": 1000,
                "shares_precision": "股",
                "hold_ratio": 1.5,
                "holder_type_or_nature": "机构",
                "change_status": "增持",
                "change_shares_text": "100股",
                "change_shares_approx": 100,
                "page_update_date": "2026-05-04",
                "source": "tdx_f10",
                "raw_hash": "abc123",
                "fetched_at": fetched_at,
            }
        ],
        periods=[{"report_date": "20260331"}],
        raw_text="灵通V9.0 holder fixture",
        raw_hash="abc123",
        page_update_date="2026-05-04",
        server_or_endpoint="fixture:7709",
        source="tdx_f10",
        source_tier=1,
        fetched_at=fetched_at,
        controlling={
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "SH",
            "primary_shareholder_label": "控股股东",
            "primary_shareholder_name": "Holder A",
            "primary_shareholder_ratio": 1.5,
            "primary_shareholder_raw": "Holder A 1.5%",
            "source": "tdx_f10",
            "raw_hash": "abc123",
            "fetched_at": fetched_at,
        },
        plans=[
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "SH",
                "announce_date": "20260501",
                "subject": "Holder A",
                "direction": "增持计划",
                "progress": "实施",
                "source": "tdx_f10",
                "raw_hash": "abc123",
                "fetched_at": fetched_at,
            }
        ],
        trades=[
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "SH",
                "change_date": "20260502",
                "holder_name": "Holder A",
                "shares_change": 100,
                "change_type": "二级市场买入",
                "source": "tdx_f10",
                "raw_hash": "abc123",
                "fetched_at": fetched_at,
            }
        ],
    )


def test_write_one_persists_records_and_is_idempotent():
    con = _make_conn()
    lock = threading.Lock()
    try:
        result = _make_result()
        stats = ingest.write_one(
            con,
            stock_code="600519",
            stock_name="贵州茅台",
            market="SH",
            result=result,
            alias_map={"Holder A": "Holder Canon"},
            lock=lock,
        )

        assert stats["n_holders"] == 1
        holder = con.execute(
            "SELECT holder_name_norm, hold_ratio_float, hold_change, hold_change_num, notice_date, effective_date "
            "FROM fact_top10_holder_period"
        ).fetchone()
        assert holder == ("Holder Canon", 1.5, "加仓", 100.0, "20260504", "20260505")
        assert con.execute("SELECT COUNT(*) FROM raw_tdx_f10_holder_research").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_controlling_shareholder").fetchone()[0] == 1
        assert con.execute("SELECT plan_seq FROM fact_shareholder_plan").fetchone()[0] == 1
        assert con.execute("SELECT holder_name_norm, trade_seq FROM fact_shareholder_trade").fetchone() == (
            "Holder Canon",
            1,
        )

        ingest.write_one(
            con,
            stock_code="600519",
            stock_name="贵州茅台",
            market="SH",
            result=result,
            alias_map={"Holder A": "Holder Canon"},
            lock=lock,
        )
        assert con.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_shareholder_plan").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_shareholder_trade").fetchone()[0] == 1
    finally:
        con.close()


def test_write_one_persists_records_without_database_unique_constraint():
    con = _make_conn(include_holder_unique=False)
    lock = threading.Lock()
    try:
        result = _make_result()
        stats = ingest.write_one(
            con,
            stock_code="600519",
            stock_name="贵州茅台",
            market="SH",
            result=result,
            alias_map={"Holder A": "Holder Canon"},
            lock=lock,
        )

        assert stats["n_holders"] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0] == 1

        ingest.write_one(
            con,
            stock_code="600519",
            stock_name="贵州茅台",
            market="SH",
            result=result,
            alias_map={"Holder A": "Holder Canon"},
            lock=lock,
        )
        assert con.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0] == 1
    finally:
        con.close()


def test_write_one_drops_empty_placeholder_plans():
    """P-1.4 root cause: tdxhub parser 偶尔返回 announce_date=None + subject='' + direction=''
    的占位 plan stub. ingest 必须过滤, 防 fact_shareholder_plan 写入空记录污染 audit.
    """
    con = _make_conn()
    lock = threading.Lock()
    try:
        result = _make_result()
        # 全空 stub 应被过滤 (announce/subject/direction 三字段都 empty)
        result.plans.append({
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "SH",
            "announce_date": None,
            "subject": "",
            "direction": "",
            "progress": "",
            "source": "tdx_f10",
            "raw_hash": "abc123",
            "fetched_at": "2026-05-05T01:30:00",
        })
        # 仅 announce_date 有值 (subject/direction 空) 应保留 — 三字段任一非空都算有效
        result.plans.append({
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "SH",
            "announce_date": "20260510",
            "subject": "",
            "direction": "",
            "source": "tdx_f10",
            "raw_hash": "abc123",
            "fetched_at": "2026-05-05T01:30:00",
        })
        ingest.write_one(
            con,
            stock_code="600519",
            stock_name="贵州茅台",
            market="SH",
            result=result,
            alias_map={},
            lock=lock,
        )
        # 原始 1 行 + announce-only 1 行 = 2; empty stub 被过滤
        assert con.execute("SELECT COUNT(*) FROM fact_shareholder_plan").fetchone()[0] == 2
        empty_left = con.execute(
            "SELECT COUNT(*) FROM fact_shareholder_plan "
            "WHERE (announce_date IS NULL OR announce_date='') "
            "  AND (subject IS NULL OR subject='') "
            "  AND (direction IS NULL OR direction='')"
        ).fetchone()[0]
        assert empty_left == 0
    finally:
        con.close()


def test_parse_raw_row_preserves_raw_lineage():
    raw_row = {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "market": "SH",
        "raw_text": "fixture raw",
        "raw_hash": "hash-fixture",
        "fetched_at": "2026-05-05T01:30:00",
        "page_update_date": "2026-05-04",
        "server": "fixture:7709",
    }

    def parser(raw_text, *, symbol, stock_name):
        assert raw_text == "fixture raw"
        assert symbol == "600519"
        assert stock_name == "贵州茅台"
        return {
            "page": {"page_update_date": "2026-05-03"},
            "holders": [{"report_date": "20260331", "holder_set": "free", "holder_rank": 1, "holder_name": "Holder A"}],
            "periods": [{"report_date": "20260331"}],
            "plans": [],
            "trades": [],
        }

    result = ingest.parse_tdx_raw_row(raw_row, parser=parser, hasher=lambda _text: "unused")

    assert result.raw_hash == "hash-fixture"
    assert result.page_update_date == "2026-05-04"
    assert result.server_or_endpoint == "fixture:7709"
    assert result.source == "tdx_f10"
    assert result.source_tier == 1
    assert result.holders[0]["holder_name"] == "Holder A"


def test_existing_hashes_returns_tuple_keys():
    con = _make_conn()
    try:
        con.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date, raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
            VALUES ('600519', '贵州茅台', 'SH', '2026-05-05T01:30:00', '2026-05-04', 'fixture raw', 'hash-fixture', 11, 'fixture:7709', 'a_lingtong', 'v1')
            """
        )

        assert ingest.existing_hashes(con) == {("600519", "hash-fixture")}
    finally:
        con.close()


def test_fetch_raw_records_writes_only_raw_table():
    con = _make_conn()
    try:
        raw_text = "☆股东研究☆◇600519 贵州茅台 更新日期：2026-05-04◇\n灵通V9.0 holder fixture"
        stats = ingest.fetch_raw_records(
            con,
            workers=1,
            symbols="600519",
            fetcher_factory=lambda: _FakeRawFetcher({"600519": raw_text}),
        )

        assert stats["raw_written"] == 1
        assert stats["ok"] == 1
        assert con.execute("SELECT COUNT(*) FROM raw_tdx_f10_holder_research").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0] == 0
        raw_row = con.execute(
            """
            SELECT stock_code, CAST(page_update_date AS VARCHAR), f10_format, parser_version
              FROM raw_tdx_f10_holder_research
            """
        ).fetchone()
        assert raw_row == ("600519", "2026-05-04", "a_lingtong", "v1")
    finally:
        con.close()


def test_run_fetches_raw_then_replays_new_hash(monkeypatch):
    con = _make_conn()
    try:
        raw_text = "☆股东研究☆◇600519 贵州茅台 更新日期：2026-05-04◇\n灵通V9.0 holder fixture"
        raw_hash = ingest._raw_hash(raw_text)
        captured = {}

        def fake_parse(_con, *, raw_keys=None, **_kwargs):
            captured["raw_keys"] = raw_keys
            return {
                "raw_rows": len(raw_keys or []),
                "parsed": len(raw_keys or []),
                "no_data": 0,
                "errors": 0,
                "replace_facts": False,
                "elapsed_s": 0.01,
            }

        monkeypatch.setattr(ingest, "parse_raw_records", fake_parse)

        stats = ingest.run(
            workers=1,
            symbols="600519",
            no_fallback=True,
            con=con,
            fetcher_factory=lambda: _FakeRawFetcher({"600519": raw_text}),
        )

        assert captured["raw_keys"] == [("600519", raw_hash)]
        assert stats["raw_written"] == 1
        assert stats["parsed"] == 1
        assert stats["ok"] == 1
        assert con.execute("SELECT COUNT(*) FROM raw_tdx_f10_holder_research").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0] == 0
    finally:
        con.close()


def test_parse_raw_records_can_replay_and_replace_canonical_rows():
    con = _make_conn()
    try:
        con.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date, raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
            VALUES ('600519', '贵州茅台', 'SH', '2026-05-05T01:30:00', '2026-05-04', 'fixture raw', 'hash-fixture', 11, 'fixture:7709', 'a_lingtong', 'v1')
            """
        )

        def parser_two_rows(_raw_text, *, symbol, stock_name):
            return {
                "page": {"page_update_date": "2026-05-04"},
                "holders": [
                    {
                        "stock_code": symbol,
                        "stock_name": stock_name,
                        "market": "SH",
                        "report_date": "20260331",
                        "holder_set": "free",
                        "holder_rank": 1,
                        "row_seq": 1,
                        "holder_name": "Holder A",
                        "share_class": "A",
                        "hold_ratio": 1.0,
                    },
                    {
                        "stock_code": symbol,
                        "stock_name": stock_name,
                        "market": "SH",
                        "report_date": "20260331",
                        "holder_set": "free",
                        "holder_rank": 2,
                        "row_seq": 1,
                        "holder_name": "Holder B",
                        "share_class": "A",
                        "hold_ratio": 0.5,
                    },
                ],
                "periods": [{"report_date": "20260331"}],
                "plans": [],
                "trades": [],
            }

        stats = ingest.parse_raw_records(
            con,
            symbols="600519",
            parser=parser_two_rows,
            hasher=lambda _text: "hash-fixture",
        )
        assert stats["parsed"] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0] == 2

        def parser_one_row(_raw_text, *, symbol, stock_name):
            return {
                "page": {"page_update_date": "2026-05-04"},
                "holders": [
                    {
                        "stock_code": symbol,
                        "stock_name": stock_name,
                        "market": "SH",
                        "report_date": "20260331",
                        "holder_set": "free",
                        "holder_rank": 1,
                        "row_seq": 1,
                        "holder_name": "Holder A Updated",
                        "share_class": "A",
                        "hold_ratio": 2.0,
                    }
                ],
                "periods": [{"report_date": "20260331"}],
                "plans": [],
                "trades": [],
            }

        stats = ingest.parse_raw_records(
            con,
            symbols="600519",
            replace_facts=True,
            parser=parser_one_row,
            hasher=lambda _text: "hash-fixture",
        )

        assert stats["parsed"] == 1
        rows = con.execute(
            "SELECT holder_rank, holder_name, hold_ratio_float FROM fact_top10_holder_period ORDER BY holder_rank"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(1, "Holder A Updated", 2.0)]
    finally:
        con.close()


def test_parse_raw_records_can_filter_by_raw_keys():
    con = _make_conn()
    try:
        con.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date, raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
            VALUES
            ('600519', '贵州茅台', 'SH', '2026-05-05T01:30:00', '2026-05-04', 'fixture raw a', 'hash-a', 13, 'fixture:7709', 'a_lingtong', 'v1'),
            ('000001', '平安银行', 'SZ', '2026-05-05T01:31:00', '2026-05-04', 'fixture raw b', 'hash-b', 13, 'fixture:7709', 'a_lingtong', 'v1')
            """
        )

        def parser(_raw_text, *, symbol, stock_name):
            return {
                "page": {"page_update_date": "2026-05-04"},
                "holders": [
                    {
                        "stock_code": symbol,
                        "stock_name": stock_name,
                        "market": "SZ" if symbol == "000001" else "SH",
                        "report_date": "20260331",
                        "holder_set": "free",
                        "holder_rank": 1,
                        "row_seq": 1,
                        "holder_name": f"Holder {symbol}",
                        "share_class": "A",
                        "hold_ratio": 1.0,
                    }
                ],
                "periods": [{"report_date": "20260331"}],
                "plans": [],
                "trades": [],
            }

        stats = ingest.parse_raw_records(
            con,
            raw_keys=[("000001", "hash-b")],
            parser=parser,
            hasher=lambda text: f"unused-{text}",
        )

        assert stats["raw_rows"] == 1
        assert stats["parsed"] == 1
        assert con.execute("SELECT stock_code FROM fact_top10_holder_period").fetchone()[0] == "000001"
    finally:
        con.close()
