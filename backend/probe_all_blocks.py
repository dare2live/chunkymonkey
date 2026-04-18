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
    except:
        active_codes = set()

    target_names = ["软件服务", "半导体", "银行", "证券", "白酒", "汽车整车", "医疗服务"]
    
    # Handle BLOCK_FILES as list of tuples or dict
    if isinstance(BLOCK_FILES, dict):
        items = BLOCK_FILES.items()
    else:
        items = BLOCK_FILES

    for filename, category in items:
        try:
            frame, source = await fetch_tdx_block_file(filename)
            rows, _ = _normalize_block_frame(frame, category, filename, active_codes)
            df = pd.DataFrame(rows)
            if df.empty: continue
            
            unique_names = sorted(df["block_name"].unique())
            found = [n for n in target_names if n in unique_names]
            
            print(f"File: {filename} ({category})")
            print(f"  Unique count: {len(unique_names)}")
            if found:
                print(f"  Found targets: {found}")
            
            industries = [n for n in unique_names if not any(x in n for x in ["指数", "上证", "深证", "沪深", "中证", "创业板", "板块", "成份"])]
            print(f"  Sample 5 industries: {industries[:5]}")
            print("-" * 20)
        except:
            pass

if __name__ == "__main__":
    asyncio.run(probe())
