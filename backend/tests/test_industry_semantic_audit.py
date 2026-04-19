import re
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.stock_archetype_engine import build_stock_archetypes, ensure_tables as ensure_archetype_tables


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
ALLOWED_DIRECT_DIM_INDUSTRY_ACCESS = {
    Path("backend/services/db.py"),
    Path("backend/services/industry.py"),
    Path("backend/services/stock_validation.py"),
    Path("backend/routers/updater.py"),
    Path("backend/scripts/fill_missing_industry_from_sw_hist.py"),
    Path("backend/scripts/import_chatgpt_industry_history.py"),
}


def test_current_industry_reads_are_centralized() -> None:
    pattern = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+dim_stock_industry\b", re.IGNORECASE)
    unexpected = []

    for path in BACKEND_ROOT.rglob("*.py"):
        rel_path = path.relative_to(REPO_ROOT)
        if any(part in {"tests", "mlruns", "__pycache__", ".pytest_cache", "Users"} for part in rel_path.parts):
            continue
        if rel_path in ALLOWED_DIRECT_DIM_INDUSTRY_ACCESS:
            continue

        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                unexpected.append(f"{rel_path}:{line_no}:{line.strip()}")

    assert not unexpected, "发现未通过 shared industry helper 的当前行业直连:\n" + "\n".join(unexpected)


def test_frontend_has_no_sw_legacy_references() -> None:
    legacy_pattern = re.compile(r"\bsw_level[123]\b")
    js_hits = {}

    for path in (REPO_ROOT / "assets" / "js").rglob("*.js"):
        rel_path = path.relative_to(REPO_ROOT)
        matches = legacy_pattern.findall(path.read_text(encoding="utf-8"))
        if matches:
            js_hits[str(rel_path)] = matches

    assert js_hits == {}, f"前端仍有 sw_level* 兜底 (应已随 Phase 2 TDX 迁移退役): {js_hits}"


def _make_archetype_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE raw_gpcw_financial (
            stock_code TEXT,
            report_date TEXT,
            revenue REAL,
            net_profit REAL,
            operating_cashflow REAL,
            operating_profit REAL,
            inventory REAL,
            total_shares REAL,
            net_assets REAL,
            holder_count REAL,
            eps REAL
        );

        CREATE TABLE fact_financial_derived (
            stock_code TEXT,
            report_date TEXT,
            revenue_yoy REAL,
            profit_yoy REAL,
            ocf_to_profit REAL
        );

        CREATE TABLE dim_stock_quality_latest (
            stock_code TEXT PRIMARY KEY,
            debt_rank REAL,
            quality_score_v1 REAL,
            quality_profit_raw REAL,
            quality_cash_raw REAL,
            quality_growth_raw REAL,
            quality_balance_raw REAL
        );

        CREATE TABLE dim_stock_tdx_industry (
            stock_code  TEXT PRIMARY KEY,
            tdx_l1      TEXT,
            tdx_l2      TEXT,
            tdx_l3      TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            tdx_l3_name TEXT
        );
        """
    )
    return conn


def test_build_stock_archetypes_uses_shared_industry_alias_map(monkeypatch) -> None:
    conn = _make_archetype_conn()
    try:
        ensure_archetype_tables(conn)
        conn.execute(
            "INSERT INTO dim_stock_tdx_industry VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("600000", "T10", "T1010", "T101001", "电子", "半导体", "半导体设备"),
        )
        monkeypatch.setattr("services.stock_archetype_engine._load_price_data", lambda: {})
        monkeypatch.setattr("services.stock_archetype_engine._load_capital_data", lambda _conn: {})
        monkeypatch.setattr("services.stock_archetype_engine._load_gross_margin_data", lambda _conn: {})

        raw_rows = [
            ("600000", "2026-03-31", 120.0, 12.0, 13.0, 14.0, 8.0, 100.0, 180.0, 10.0, 0.12),
            ("600000", "2025-12-31", 116.0, 11.0, 12.0, 13.0, 7.8, 100.0, 176.0, 10.2, 0.11),
            ("600000", "2025-09-30", 112.0, 10.5, 11.0, 12.0, 7.6, 100.0, 172.0, 10.4, 0.10),
            ("600000", "2025-06-30", 108.0, 10.0, 10.5, 11.0, 7.4, 100.0, 168.0, 10.6, 0.09),
            ("600000", "2025-03-31", 104.0, 9.5, 10.0, 10.0, 7.2, 100.0, 164.0, 10.8, 0.08),
            ("600000", "2024-12-31", 100.0, 9.0, 9.5, 9.5, 7.0, 100.0, 160.0, 11.0, 0.07),
            ("600000", "2024-09-30", 96.0, 8.5, 9.0, 9.0, 6.8, 100.0, 156.0, 11.2, 0.06),
            ("600000", "2024-06-30", 92.0, 8.0, 8.5, 8.5, 6.6, 100.0, 152.0, 11.4, 0.05)
        ]
        conn.executemany("INSERT INTO raw_gpcw_financial VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", raw_rows)
        conn.executemany(
            "INSERT INTO fact_financial_derived VALUES (?, ?, ?, ?, ?)",
            [
                ("600000", "2026-03-31", 0.22, 0.24, 1.10),
                ("600000", "2025-12-31", 0.20, 0.22, 1.05),
                ("600000", "2025-09-30", 0.18, 0.20, 1.00),
                ("600000", "2025-06-30", 0.16, 0.18, 0.95),
            ],
        )
        conn.execute(
            "INSERT INTO dim_stock_quality_latest VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("600000", 65.0, 78.0, 20.0, 15.0, 6.0, 12.0),
        )
        conn.commit()

        inserted = build_stock_archetypes(conn, snapshot_date="2026-04-18")

        row = conn.execute(
            "SELECT tdx_l1_name, tdx_l2_name, stock_archetype FROM dim_stock_archetype_latest WHERE stock_code = ?",
            ("600000",),
        ).fetchone()
        assert inserted == 1
        assert row["tdx_l1_name"] == "电子"
        assert row["tdx_l2_name"] == "半导体"
        assert row["stock_archetype"]
    finally:
        conn.close()