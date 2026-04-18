import asyncio
import sqlite3
import pandas as pd
from services.block_client import BLOCK_FILES, fetch_tdx_block_file, _normalize_block_frame

async def probe():
    try:
        conn = sqlite3.connect("../data/smartmoney.db")
        active_codes_df = pd.read_sql("SELECT stock_code FROM dim_active_a_stock", conn)
        active_codes = set(active_codes_df["stock_code"].tolist())
        conn.close()
    except Exception as e:
        print(f"Error reading DB: {e}")
        active_codes = set()

    if not active_codes:
        print("Report: active_codes is empty or DB error.")
        return

    print(f"Loaded {len(active_codes)} active stock codes.")

    for block_category, block_file in BLOCK_FILES:
        try:
            frame, source = await fetch_tdx_block_file(block_file)
            rows, stats = _normalize_block_frame(
                frame,
                block_category=block_category,
                block_file=block_file,
                active_codes=active_codes
            )
            
            kept_df = pd.DataFrame(rows)
            sample_names = []
            if not kept_df.empty:
                sample_names = kept_df["block_name"].unique()[:10].tolist()
            
            print(f"Block File: {block_file} ({block_category})")
            print(f"  Raw Rows: {stats['raw_rows']}")
            print(f"  Kept Rows: {stats['kept_rows']}")
            print(f"  Sample Names: {sample_names}")
            print("-" * 20)
        except Exception as e:
            print(f"Error processing {block_file}: {e}")

if __name__ == "__main__":
    asyncio.run(probe())
