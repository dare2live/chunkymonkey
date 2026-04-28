import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import services.institution_write as institution_write


def _make_conn():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT,
            display_name TEXT,
            type TEXT,
            enabled INTEGER,
            aliases TEXT,
            created_at TEXT,
            updated_at TEXT,
            blacklisted INTEGER,
            manual_type TEXT,
            merged_into TEXT
        );
        CREATE TABLE inst_holdings (institution_id TEXT);
        CREATE TABLE fact_institution_event (institution_id TEXT);
        CREATE TABLE mart_current_relationship (institution_id TEXT);
        CREATE TABLE mart_institution_profile (institution_id TEXT);
        CREATE TABLE mart_institution_industry_stat (institution_id TEXT);
        CREATE TABLE excluded_stocks (
            stock_code TEXT NOT NULL,
            category TEXT NOT NULL,
            stock_name TEXT,
            reason TEXT,
            created_at TEXT,
            PRIMARY KEY (stock_code, category)
        );
        CREATE TABLE dim_active_a_stock (
            stock_code TEXT,
            stock_name TEXT
        );
        CREATE TABLE fact_top10_holder_period (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            report_date TEXT NOT NULL,
            notice_date TEXT,
            holder_set TEXT NOT NULL,
            holder_rank INTEGER NOT NULL,
            holder_name TEXT NOT NULL,
            is_secondary_class INTEGER DEFAULT 0,
            is_exit_row INTEGER DEFAULT 0,
            source TEXT NOT NULL,
            source_tier INTEGER NOT NULL
        );
        """
    )
    return conn


def test_institution_write_crud_and_cascade_delete():
    conn = _make_conn()

    inst_id = institution_write.create_institution_record(
        conn,
        "高瓴资本",
        display_name="高瓴",
        institution_type="fund",
        aliases=["Hillhouse"],
        now="2026-04-16T10:00:00",
    )

    row = conn.execute("SELECT * FROM inst_institutions WHERE id = ?", (inst_id,)).fetchone()
    assert inst_id == "inst_高瓴资本"
    assert row["display_name"] == "高瓴"
    assert row["type"] == "fund"
    assert row["aliases"] == '["Hillhouse"]'

    updated = institution_write.update_institution_record(
        conn,
        inst_id,
        {"display_name": "高瓴资本", "enabled": 0, "aliases": ["HH"]},
        now="2026-04-16T11:00:00",
    )
    assert updated is True
    assert institution_write.update_institution_record(conn, inst_id, {}) is False

    row = conn.execute("SELECT * FROM inst_institutions WHERE id = ?", (inst_id,)).fetchone()
    assert row["display_name"] == "高瓴资本"
    assert row["enabled"] == 0
    assert row["aliases"] == '["HH"]'
    assert row["updated_at"] == "2026-04-16T11:00:00"

    for table in [
        "inst_holdings",
        "fact_institution_event",
        "mart_current_relationship",
        "mart_institution_profile",
        "mart_institution_industry_stat",
    ]:
        conn.execute(f"INSERT INTO {table} (institution_id) VALUES (?)", (inst_id,))
    conn.commit()

    institution_write.delete_institution_record(conn, inst_id)

    assert conn.execute("SELECT COUNT(*) FROM inst_institutions").fetchone()[0] == 0
    for table in [
        "inst_holdings",
        "fact_institution_event",
        "mart_current_relationship",
        "mart_institution_profile",
        "mart_institution_industry_stat",
    ]:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_blacklist_write_helpers_resolve_name_and_defaults():
    conn = _make_conn()
    conn.execute("INSERT INTO dim_active_a_stock (stock_code, stock_name) VALUES (?, ?)", ("600519", ""))
    conn.execute(
        "INSERT INTO fact_top10_holder_period "
        "(stock_code, stock_name, report_date, notice_date, holder_set, holder_rank, "
        " holder_name, is_secondary_class, is_exit_row, source, source_tier) "
        "VALUES (?, ?, ?, ?, 'free', 1, 'TestHolder', 0, 0, 'tdx_f10', 1)",
        ("600519", "贵州茅台", "20260331", "20260410"),
    )
    conn.commit()

    stock_name = institution_write.upsert_manual_stock_blacklist(
        conn,
        "600519",
        stock_name="",
        reason="",
        now="2026-04-16T12:00:00",
    )

    row = conn.execute(
        "SELECT stock_code, category, stock_name, reason, created_at FROM excluded_stocks WHERE stock_code = ?",
        ("600519",),
    ).fetchone()
    assert stock_name == "贵州茅台"
    assert row["category"] == "MANUAL"
    assert row["stock_name"] == "贵州茅台"
    assert row["reason"] == "手工拉黑"
    assert row["created_at"] == "2026-04-16T12:00:00"

    removed_name = institution_write.delete_manual_stock_blacklist(conn, "600519")
    assert removed_name == "贵州茅台"
    assert conn.execute("SELECT COUNT(*) FROM excluded_stocks").fetchone()[0] == 0


def test_batch_create_institution_records_skips_blank_names():
    conn = _make_conn()

    created = institution_write.batch_create_institution_records(
        conn,
        [
            {"name": "淡马锡", "display_name": "Temasek", "type": "sovereign", "aliases": ["Temasek"]},
            {"name": "  "},
            {"name": "景林资产"},
        ],
        now="2026-04-16T13:00:00",
    )

    assert created == 2
    rows = conn.execute("SELECT id, name, created_at FROM inst_institutions ORDER BY id").fetchall()
    assert [row["name"] for row in rows] == ["景林资产", "淡马锡"]
    assert all(row["created_at"] == "2026-04-16T13:00:00" for row in rows)


def test_upsert_watchlist_entry_persists_active_row():
    conn = _make_conn()
    conn.execute(
        """
        CREATE TABLE stock_watchlist (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            added_date TEXT,
            added_price REAL,
            added_reason TEXT,
            source_institution TEXT,
            source_event_type TEXT,
            status TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()

    institution_write.upsert_watchlist_entry(
        conn,
        {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "added_price": 1680.5,
            "added_reason": "手工加入",
            "source_institution": "高瓴",
            "source_event_type": "increase",
        },
        now="2026-04-16T14:00:00",
    )

    row = conn.execute("SELECT * FROM stock_watchlist WHERE stock_code = ?", ("600519",)).fetchone()
    assert row["stock_name"] == "贵州茅台"
    assert row["added_date"] == "2026-04-16"
    assert row["added_price"] == 1680.5
    assert row["status"] == "active"
    assert row["updated_at"] == "2026-04-16T14:00:00"