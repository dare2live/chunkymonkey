import asyncio
import sqlite3
import pandas as pd
import sys
import os

sys.path.append(os.getcwd())
from services.block_client import fetch_tdx_block_file, _normalize_block_frame, BLOCK_FILES

async def probe():
    try:
        conn = sqlite3.connect("../data/smartmoney.db")
        active_codes_df = pd.read_sql("SELECT stock_code FROM dim_active_a_stock", conn)
        active_codes = set(active_codes_df["stock_code"].tolist())
        conn.close()
    except Exception as e:
        print(f"Error reading DB: {e}")
        active_codes = set()

    target_names = ["软件服务", "半导体", "银行", "证券", "白酒", "汽车整车", "医疗服务"]
    
    for category, filename in BLOCK_FILES:
        try:
            frame, source = await fetch_tdx_block_file(filename)
            rows, _ = _normalize_block_frame(frame, category, filename, active_codes)
            df = pd.DataFrame(rows)
            if df.empty:
                print(f"File: {filename} ({category}) - Empty after normalization")
                continue
            
            unique_names = sorted(df["block_name"].unique())
            found = [n for n in target_names if n in unique_names]
            
            # Simple heuristic for industries
            industries = [n for n in unique_names if not any(x in n for x in ["指数", "上证", "深证", "沪深", "中证", "创业板", "板块", "成份", "MSCI", "880", "159", "512"])]
            
            print(f"File: {filename} ({category})")
            print(f"  Unique block_name count: {len(unique_names)}")
            print(f"  Found target names: {found}")
            print(f"  Sample 10 industry-like names: {industries[:10]}")
            print("-" * 30)
        except Exception as e:
            print(f"Error processing {filename}: {e}")

if __name__ == '__main__':
    asyncio.run(probe())
