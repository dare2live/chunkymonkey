"""P0a label panel DDL.

`mart_p0a_label_panel`: 训练 label 主表, 每行 = (stock_code, signal_date).
PIT 保证: signal_date 是 alpha158 panel date, entry/exit 都是未来 trade day —
**仅作为训练 label 计算时使用 forward 信息**, 不能进 feature pipeline.
"""
from __future__ import annotations

LABEL_PANEL_DDL = """
CREATE TABLE IF NOT EXISTS mart_p0a_label_panel (
    stock_code         TEXT NOT NULL,
    signal_date        DATE NOT NULL,
    entry_date         DATE,
    entry_vwap         DOUBLE,
    unable_at_entry    BOOLEAN,
    exit_date_5d       DATE,
    exit_vwap_5d       DOUBLE,
    unable_at_exit_5d  BOOLEAN,
    fwd_cost_after_5d  DOUBLE,
    exit_date_10d      DATE,
    exit_vwap_10d      DOUBLE,
    unable_at_exit_10d BOOLEAN,
    fwd_cost_after_10d DOUBLE,
    exit_date_20d      DATE,
    exit_vwap_20d      DOUBLE,
    unable_at_exit_20d BOOLEAN,
    fwd_cost_after_20d DOUBLE,
    round_trip_cost_pct DOUBLE,
    label_version      TEXT NOT NULL,
    built_at           TEXT NOT NULL,
    PRIMARY KEY (stock_code, signal_date)
);
"""

LABEL_PANEL_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_p0a_label_signal_date
    ON mart_p0a_label_panel(signal_date);
CREATE INDEX IF NOT EXISTS idx_p0a_label_stock
    ON mart_p0a_label_panel(stock_code, signal_date);
"""


def create_label_panel_ddl(conn) -> None:
    """Create mart_p0a_label_panel + indexes (idempotent)."""
    conn.execute(LABEL_PANEL_DDL)
    conn.execute(LABEL_PANEL_INDEX_DDL)
