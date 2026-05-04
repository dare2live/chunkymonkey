import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pandas as pd
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import block_client, db


@pytest.mark.asyncio
async def test_fetch_tdx_block_file_uses_shared_quotes_pool():
    frame = pd.DataFrame(
        [
            {"blockname": "沪深300", "block_type": 2, "code_index": 0, "code": "600036"},
        ]
    )

    with mock.patch.object(
        block_client,
        "call_tdx_quotes_with_retry",
        return_value=(frame, "tdxhub_1.2.3.4:7709"),
    ) as mocked_call:
        result_frame, source = await block_client.fetch_tdx_block_file("block_zs.dat")

    assert source == "tdxhub_1.2.3.4:7709"
    assert result_frame.equals(frame)
    assert mocked_call.call_args.kwargs["action_name"] == "block[block_zs.dat]"


@pytest.mark.asyncio
async def test_sync_tdx_blocks_persists_catalog_and_members():
    with TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db_path = data_dir / "business.duckdb"

        with mock.patch.object(db, "DB_DIR", data_dir), mock.patch.object(db, "DB_PATH", db_path):
            db.init_db()
            conn = db.get_conn()
            try:
                conn.executemany(
                    "INSERT INTO dim_active_a_stock (stock_code, stock_name, market, source, updated_at) VALUES (?, ?, ?, ?, ?)",
                    [
                        ("600036", "招商银行", "SH", "test", "2026-04-13T00:00:00"),
                        ("300750", "宁德时代", "SZ", "test", "2026-04-13T00:00:00"),
                    ],
                )
                conn.commit()

                fixtures = {
                    "block_zs.dat": pd.DataFrame(
                        [
                            {"blockname": "沪深300", "block_type": 2, "code_index": 0, "code": "600036"},
                            {"blockname": "沪深300", "block_type": 2, "code_index": 1, "code": "300750"},
                            {"blockname": "沪深300", "block_type": 2, "code_index": 2, "code": "399001"},
                        ]
                    ),
                    "block_fg.dat": pd.DataFrame(
                        [
                            {"blockname": "融资融券", "block_type": 2, "code_index": 10, "code": "600036"},
                            {"blockname": "\x00600113\x006", "block_type": 12593, "code_index": 11, "code": "300750"},
                        ]
                    ),
                    "block_gn.dat": pd.DataFrame(
                        [
                            {"blockname": "新能源车", "block_type": 2, "code_index": 20, "code": "300750"},
                            {"blockname": "一带一路", "block_type": 2, "code_index": 21, "code": "600036"},
                            {"blockname": "一带一路", "block_type": 2, "code_index": 22, "code": "159001"},
                        ]
                    ),
                }

                async def _fake_fetch(block_file: str):
                    return fixtures[block_file], "tdxhub_test"

                with mock.patch.object(block_client, "fetch_tdx_block_file", side_effect=_fake_fetch):
                    result = await block_client.sync_tdx_blocks(
                        conn,
                        active_codes={"600036", "300750"},
                        excluded_codes={"600036"},
                    )

                assert result["status"] == "success"
                assert result["member_rows"] == 2
                assert result["catalog_rows"] == 2
                assert result["files"]["zs"]["kept_rows"] == 1
                assert result["files"]["fg"]["skipped_invalid_name"] == 1

                member_rows = conn.execute(
                    "SELECT stock_code, block_category, block_name FROM dim_stock_tdx_block ORDER BY block_category, stock_code"
                ).fetchall()
                assert [tuple(row) for row in member_rows] == [
                    ("300750", "gn", "新能源车"),
                    ("300750", "zs", "沪深300"),
                ]

                catalog_rows = conn.execute(
                    "SELECT block_category, block_name, member_count FROM dim_tdx_block_catalog ORDER BY block_category, block_name"
                ).fetchall()
                assert [tuple(row) for row in catalog_rows] == [
                    ("gn", "新能源车", 1),
                    ("zs", "沪深300", 1),
                ]
            finally:
                conn.close()


@pytest.mark.asyncio
async def test_sync_tdx_blocks_replaces_previous_snapshot():
    with TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db_path = data_dir / "business.duckdb"

        with mock.patch.object(db, "DB_DIR", data_dir), mock.patch.object(db, "DB_PATH", db_path):
            db.init_db()
            conn = db.get_conn()
            try:
                conn.execute(
                    "INSERT INTO dim_stock_tdx_block (stock_code, block_category, block_name, block_file, block_type, code_index, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("600036", "gn", "旧板块", "block_gn.dat", 2, 1, "legacy", "2026-04-13T00:00:00"),
                )
                conn.execute(
                    "INSERT INTO dim_tdx_block_catalog (block_category, block_name, block_file, block_type, member_count, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("gn", "旧板块", "block_gn.dat", 2, 1, "legacy", "2026-04-13T00:00:00"),
                )
                conn.commit()

                fixtures = {
                    "block_zs.dat": pd.DataFrame([], columns=["blockname", "block_type", "code_index", "code"]),
                    "block_fg.dat": pd.DataFrame([], columns=["blockname", "block_type", "code_index", "code"]),
                    "block_gn.dat": pd.DataFrame(
                        [
                            {"blockname": "数字经济", "block_type": 2, "code_index": 0, "code": "600036"},
                        ]
                    ),
                }

                async def _fake_fetch(block_file: str):
                    return fixtures[block_file], "tdxhub_test"

                with mock.patch.object(block_client, "fetch_tdx_block_file", side_effect=_fake_fetch):
                    await block_client.sync_tdx_blocks(conn, active_codes={"600036"}, excluded_codes=set())

                names = conn.execute("SELECT block_name FROM dim_stock_tdx_block").fetchall()
                assert [row[0] for row in names] == ["数字经济"]
                catalog_names = conn.execute("SELECT block_name FROM dim_tdx_block_catalog").fetchall()
                assert [row[0] for row in catalog_names] == ["数字经济"]
            finally:
                conn.close()
