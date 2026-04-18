import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import akshare as ak

_MARKET_TZ = ZoneInfo("Asia/Shanghai")

def get_latest_completed_trade_date(conn):
    now_local = datetime.now(_MARKET_TZ)
    anchor_date = now_local.date()
    if now_local.hour < 16:
        anchor_date -= timedelta(days=1)
    
    row = conn.execute(
        "SELECT MAX(trade_date) AS d FROM dim_trading_calendar "
        "WHERE is_trading=1 AND trade_date <= ?",
        (anchor_date.strftime("%Y-%m-%d"),)
    ).fetchone()
    return row[0]

def get_suspended_codes(trade_date):
    try:
        tfp_df = ak.stock_tfp_em(date=trade_date.replace("-", ""))
        if tfp_df is not None and not tfp_df.empty and "代码" in tfp_df.columns:
            return {str(r).strip() for r in tfp_df["代码"].tolist() if r}
    except Exception as e:
        print(f"Error fetching suspended codes: {e}")
    return set()

def run_reproduction():
    sm_db = "../data/smartmoney.db"
    mkt_db = "../data/market_data.db"
    
    conn = sqlite3.connect(sm_db)
    mkt_conn = sqlite3.connect(mkt_db)
    
    latest_trade = get_latest_completed_trade_date(conn)
    print(f"latest_completed_trade_date: {latest_trade}")
    
    stale_rows = mkt_conn.execute(
        "SELECT code, max_date FROM market_sync_state "
        "WHERE dataset='price_kline' AND freq='daily' AND adjust='qfq' "
        "AND max_date < ?",
        (latest_trade,)
    ).fetchall()
    print(f"raw stale_rows count: {len(stale_rows)}")
    
    excluded_codes = {r[0] for r in conn.execute("SELECT stock_code FROM excluded_stocks").fetchall()}
    holding_code_rows = conn.execute(
        "SELECT DISTINCT stock_code FROM inst_holdings "
        "WHERE stock_code IS NOT NULL "
        "AND stock_code NOT IN (SELECT stock_code FROM excluded_stocks)"
    ).fetchall()
    holding_codes = {r[0] for r in holding_code_rows}
    print(f"holding_codes size: {len(holding_codes)}")
    
    suspended_codes = get_suspended_codes(latest_trade)
    
    stale_count = 0
    suspended_count = 0
    intersected_suspended = []
    
    for code, max_date in stale_rows:
        if not code or code in excluded_codes or code not in holding_codes:
            continue
        if code in suspended_codes:
            suspended_count += 1
            intersected_suspended.append(code)
        else:
            stale_count += 1
            
    print(f"suspended codes intersection size: {len(intersected_suspended)}")
    print(f"final stale_count: {stale_count}")
    print(f"final suspended_count: {suspended_count}")
    
    conn.close()
    mkt_conn.close()

if __name__ == "__main__":
    run_reproduction()
