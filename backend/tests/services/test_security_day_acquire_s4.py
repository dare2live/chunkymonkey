"""S4: acquire swappability at the land boundary (tushare vs local_raw).

Exit conditions (plan_reeval §6):
- fake/local acquire feeds landing → S2 accept
- accept path never imports / calls acquire
- no revived multi-source fallback registry
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.data_sources.nominal_ohlcv_schema import DOMAIN as OHLCV_DOMAIN
from services.data_sources.security_day_acquire import (
    ACQUIRE_MODE_LOCAL_LEGACY_RAW,
    ACQUIRE_MODE_PROVIDER_TUSHARE,
    SUPPORTED_ACQUIRE_MODES,
    acquire_security_day_local_raw,
    acquire_security_day_provider,
    resolve_security_day_acquire,
)
from services.data_sources.security_day_partition import SecurityDayError
from services.data_sources.stock_st_schema import DOMAIN as ST_DOMAIN
from services.duck_adapter import connect


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def _seed_daily_raw(conn, trade_date: str) -> None:
    typed = ", ".join(f"{c} VARCHAR" for c in OHLCV_DOMAIN.provider_fields)
    placeholders = ", ".join("?" for _ in OHLCV_DOMAIN.provider_fields)
    conn.execute(f"CREATE TABLE {OHLCV_DOMAIN.compatibility_table} ({typed})")
    # minimal provider-shaped row
    row = {
        "ts_code": "000001.SZ",
        "trade_date": trade_date,
        "open": "10.0",
        "high": "11.0",
        "low": "9.5",
        "close": "10.5",
        "pre_close": "10.0",
        "change": "0.5",
        "pct_chg": "5.0",
        "vol": "1000.0",
        "amount": "10500.0",
    }
    values = [row[c] for c in OHLCV_DOMAIN.provider_fields]
    conn.execute(
        f"INSERT INTO {OHLCV_DOMAIN.compatibility_table} VALUES ({placeholders})",
        values,
    )


def test_supported_modes_are_exactly_tushare_and_local_raw() -> None:
    assert SUPPORTED_ACQUIRE_MODES == {
        ACQUIRE_MODE_PROVIDER_TUSHARE,
        ACQUIRE_MODE_LOCAL_LEGACY_RAW,
    }


def test_resolve_rejects_unknown_mode(conn) -> None:
    with pytest.raises(SecurityDayError, match="unknown acquire_mode"):
        resolve_security_day_acquire(
            "plugin_bus_fallback",
            "daily",
            trade_date="20230103",
            conn=conn,
        )


def test_local_raw_acquire_reads_compatibility_table(conn) -> None:
    _seed_daily_raw(conn, "20230103")
    acquired = resolve_security_day_acquire(
        ACQUIRE_MODE_LOCAL_LEGACY_RAW,
        "daily",
        trade_date="20230103",
        conn=conn,
    )
    assert acquired.acquire_mode == ACQUIRE_MODE_LOCAL_LEGACY_RAW
    assert acquired.source_ref == OHLCV_DOMAIN.compatibility_table
    assert acquired.lineage_note.startswith("cli_from_local_raw:")
    assert len(acquired.rows) == 1
    assert acquired.rows[0]["ts_code"] == "000001.SZ"
    assert acquired.rows[0]["trade_date"] == "20230103"


def test_provider_acquire_uses_fetch_rows_once() -> None:
    calls: list[dict] = []

    def fetch_rows(params):
        calls.append(dict(params))
        return [
            {
                "ts_code": "000001.SZ",
                "trade_date": params["trade_date"],
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "pre_close": 1.0,
                "change": 0.0,
                "pct_chg": 0.0,
                "vol": 1.0,
                "amount": 1.0,
            }
        ]

    acquired = acquire_security_day_provider(
        "daily",
        trade_date="20230103",
        fetch_rows=fetch_rows,
    )
    assert acquired.acquire_mode == ACQUIRE_MODE_PROVIDER_TUSHARE
    assert acquired.source_ref.startswith("tushare:")
    assert len(calls) == 1
    assert calls[0]["trade_date"] == "20230103"
    assert len(acquired.rows) == 1


def test_stock_st_local_raw_acquire(conn) -> None:
    typed = ", ".join(f"{c} VARCHAR" for c in ST_DOMAIN.provider_fields)
    placeholders = ", ".join("?" for _ in ST_DOMAIN.provider_fields)
    conn.execute(f"CREATE TABLE {ST_DOMAIN.compatibility_table} ({typed})")
    row = {c: "" for c in ST_DOMAIN.provider_fields}
    row.update(
        {
            "ts_code": "000001.SZ",
            "trade_date": "20220104",
            "type": "ST",
            "name": "测试ST",
            "type_name": "ST",
        }
    )
    conn.execute(
        f"INSERT INTO {ST_DOMAIN.compatibility_table} VALUES ({placeholders})",
        [row[c] for c in ST_DOMAIN.provider_fields],
    )
    acquired = acquire_security_day_local_raw(
        conn, "stock_st", trade_date="20220104"
    )
    assert acquired.acquire_mode == ACQUIRE_MODE_LOCAL_LEGACY_RAW
    assert len(acquired.rows) == 1
    assert acquired.rows[0]["ts_code"] == "000001.SZ"


def test_accept_modules_do_not_import_acquire() -> None:
    """S2 accept runtimes must stay zero-acquire (kill-point isolation).

    Land transport may import mode constants for lineage labels only.
    """

    root = Path(__file__).resolve().parents[2] / "services" / "data_sources"
    banned = "security_day_acquire"
    for name in ("nominal_ohlcv_runtime.py", "stock_st_runtime.py"):
        path = root / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert banned not in alias.name, path.name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert banned not in mod, path.name
                for alias in node.names:
                    assert banned not in alias.name, path.name
        text = path.read_text(encoding="utf-8")
        assert "resolve_security_day_acquire" not in text, path.name
        assert "acquire_security_day_" not in text, path.name
    # Accept helpers on the thin transport seam must not call the resolver.
    transport = (root / "security_day_transport.py").read_text(encoding="utf-8")
    assert "resolve_security_day_acquire" not in transport
    assert "def accept_" in transport or "accept_" in transport


def test_sync_runner_land_path_routes_through_acquire_resolver() -> None:
    """Land-only CLI path must call resolve_security_day_acquire (S4 wire)."""

    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "data_sources"
        / "sync_runner.py"
    )
    text = path.read_text(encoding="utf-8")
    assert "resolve_security_day_acquire" in text
    assert "ACQUIRE_MODE_LOCAL_LEGACY_RAW" in text
    assert "ACQUIRE_MODE_PROVIDER_TUSHARE" in text
    # still ban fused production publish in sync_runner (S3)
    assert "capture_and_publish_authorized_nominal_ohlcv_partition(" not in text
    assert "capture_and_publish_authorized_stock_st_partition(" not in text
