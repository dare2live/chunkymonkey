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
        print("Error: active_codes empty")
        return

    # Look specifically for block_zs.dat
    zs_file = "block_zs.dat"
    block_category = "指数"
    
    try:
        frame, source = await fetch_tdx_block_file(zs_file)
        rows, stats = _normalize_block_frame(
            frame,
            block_category=block_category,
            block_file=zs_file,
            active_codes=active_codes
        )
        
        df = pd.DataFrame(rows)
        if df.empty:
            print("No rows kept for block_zs.dat")
            return
            
        unique_names = sorted(df["block_name"].unique())
        print(f"Unique count: {len(unique_names)}")
        
        target_names = ["软件服务", "半导体", "银行", "证券", "白酒", "汽车整车", "医疗服务"]
        found_targets = [name for name in target_names if name in unique_names]
        print(f"Found target names: {found_targets}")
        
        # Heuristic for industries: avoid '指数', '板块', '成分', numbers, broad terms like '上证'
        # Often industry indices in TDX are like '软件服务', '半导体', etc.
        industries = [n for n in unique_names if not any(x in n for x in ["指数", "上证", "深证", "沪深", "中证", "创业板", "板块"])]
        print(f"Sample 20 industry-like names: {industries[:20]}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(probe())
