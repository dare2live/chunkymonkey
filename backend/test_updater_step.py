import asyncio
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock
import routers.updater as updater
import services.security_master
import services.northbound_client

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO)

async def run_test():
    # Setup in-memory sqlite DB
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    cursor.execute("""
        CREATE TABLE step_status (
            step_id TEXT PRIMARY KEY,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            records INTEGER
        )
    """)
    
    # Insert mock data
    trading_date = "2023-10-27"
    cursor.execute("INSERT INTO dim_trading_calendar (trade_date, is_trading) VALUES (?, ?)", (trading_date, 1))
    # Note: _step_sync_northbound calls _update_step with "sync_northbound"
    # and _update_step uses step_id as the primary key.
    cursor.execute("INSERT INTO step_status (step_id, status) VALUES (?, ?)", ("sync_northbound", "pending"))
    conn.commit()

    # Monkeypatch
    services.security_master.get_active_a_stock_codes = MagicMock(return_value={'600001'})
    
    # Mock success payload
    # Based on the code: detail.update(result)
    mock_payload = {
        "status": "success",
        "written_rows": 123,
        "detail": {"some": "info"}
    }
    services.northbound_client.sync_northbound_daily = AsyncMock(return_value=mock_payload)

    # Execute step
    try:
        count = await updater._step_sync_northbound(conn)
    except Exception as e:
        print(f"Execution failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Fetch results
    cursor.execute("SELECT error, records FROM step_status WHERE step_id = 'sync_northbound'")
    row = cursor.fetchone()
    error_json = row['error'] if row else None
    records = row['records'] if row else None
    
    print(f"Count: {count}")
    print(f"Records: {records}")
    print(f"Error JSON: {error_json}")
    
    # Check if detail matches payload
    if error_json:
        try:
            saved_data = json.loads(error_json)
            # The updater updates its internal 'detail' dict with the result of sync_northbound_daily.
            # Internal detail starts with status, requested_start_date, requested_end_date, etc.
            expected_subset = {
                "status": "success",
                "written_rows": 123,
                "requested_end_date": trading_date
            }
            is_match = all(saved_data.get(k) == v for k, v in expected_subset.items())
            
            if is_match:
                print("Summary: Updater step writes the expected detail payload.")
            else:
                print("Summary: Updater step writes a different detail payload.")
        except Exception as e:
            print(f"Error parsing JSON: {e}")
    else:
        print("Summary: No detail payload found in error column.")

if __name__ == "__main__":
    asyncio.run(run_test())
