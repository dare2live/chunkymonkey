import sqlite3
import os
import sys
import time

# Add current directory to path to import services
sys.path.append(os.getcwd())

from services.financial_client import _fetch_sina_history_batch

def get_latest_batch():
    db_path = '../data/market_data.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT code FROM price_kline GROUP BY code ORDER BY MIN(date) ASC LIMIT 200")
    codes = [row[0] for row in cursor.fetchall()]
    conn.close()
    return codes

def run_test(codes, label):
    print(f"\n--- Testing batch size: {len(codes)} ({label}) ---")
    start_time = time.time()
    try:
        records, states = _fetch_sina_history_batch(codes)
        duration = time.time() - start_time
        status_counts = {}
        error_msgs = []
        for code, state in states.items():
            status = state.get("status")
            status_counts[status] = status_counts.get(status, 0) + 1
            if state.get("error"):
                error_msgs.append(state.get("error"))
        
        print(f"Status counts: {status_counts}")
        print(f"Total records retrieved: {len(records)}")
        print(f"Time taken: {duration:.2f}s")
        
        expecting_value_count = sum(1 for msg in error_msgs if "Expecting value" in str(msg) or "char 0" in str(msg))
        if expecting_value_count > 0:
            print(f"Failures with 'Expecting value / char 0': {expecting_value_count}")
            
    except Exception as e:
        print(f"Execution failed: {str(e)}")

if __name__ == "__main__":
    all_codes = get_latest_batch()
    if not all_codes:
        sys.exit(1)
        
    print(f"Total codes available: {len(all_codes)}")
    
    # Use different codes for each test to avoid local cache/block effects
    run_test(all_codes[0:12], "12 codes")
    run_test(all_codes[12:36], "24 codes")
    run_test(all_codes[36:72], "36 codes")
    run_test(all_codes[72:120], "48 codes")
    run_test(all_codes[120:184], "64 codes")
