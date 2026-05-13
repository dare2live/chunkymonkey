"""Phase η++++ — sentiment schema DDL (一处定义).

所有 sentiment mart 表的 CREATE TABLE / INDEX 都在这里.
ETL 脚本不允许自己写 DDL, 必须从这里 import.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────
# mart_stock_survey_features
# ─────────────────────────────────────────────────────────────────────

MART_STOCK_SURVEY_FEATURES_DDL = """
CREATE TABLE IF NOT EXISTS mart_stock_survey_features (
    stock_code        TEXT NOT NULL,
    as_of_date        TEXT NOT NULL,        -- 快照日期 (YYYY-MM-DD)
    survey_count_30d  INTEGER NOT NULL,     -- 30 自然日内调研次数
    survey_count_60d  INTEGER NOT NULL,     -- 60 自然日内调研次数 (主因子, IC=0.086)
    survey_inst_30d   INTEGER NOT NULL,     -- 30 日累计机构数
    survey_inst_60d   INTEGER NOT NULL,     -- 60 日累计机构数
    survey_bin        TEXT NOT NULL,        -- 桶标签 (冷/温/热/狂, 由 bin_assigner 派生)
    built_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_mssf_date ON mart_stock_survey_features(as_of_date);
CREATE INDEX IF NOT EXISTS idx_mssf_bin  ON mart_stock_survey_features(survey_bin, as_of_date);
"""


def get_all_ddls() -> list[tuple[str, str]]:
    """返回 [(table_name, ddl_script), ...]."""
    return [
        ("mart_stock_survey_features", MART_STOCK_SURVEY_FEATURES_DDL),
    ]
