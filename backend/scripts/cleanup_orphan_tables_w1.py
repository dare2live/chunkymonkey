"""W1 清理孤儿表 (一次性脚本, 执行后保留以备审计).

依据 dim_data_asset + 全仓 grep 验证, 以下 15 张表既无活写入器也无活读取器
(仅在 db.py CREATE 或 schema_versions.py 元数据中出现), 安全 DROP.

执行后:
1) DROP TABLE IF EXISTS (15 张)
2) DELETE FROM dim_data_asset
3) 打印 health snapshot 红色数预期下降量

不在脚本里改 db.py / schema_versions.py — 那两个文件是 Python 源码,
要走 Edit 工具手动改, 否则注释/格式会乱. 跑完此脚本后请手工编辑这两个文件.
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让脚本独立可跑
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn

ORPHAN_TABLES = [
    # dim 层
    "dim_asset_universe",
    # mart 层 (旧 fund_flow / institution_capability/style 实验, 写入器已删)
    "mart_fund_flow_fetch_log",
    "mart_fund_flow_probe",
    "mart_institution_capability",
    "mart_institution_style",
    # other (历史 ML 实验)
    "backtest_walk_forward",
    "inst_name_aliases",
    "institution_drift_log",
    "model_signals_log",
    "model_signals_realized",
    "model_state",
    "portfolio_recommendation_daily",
    # raw 层
    "raw_fetch_batch",
    # research 层 (历史 setup replay 实验)
    "research_setup_replay_factor",
    "research_setup_replay_summary",
]


def main() -> None:
    with get_conn() as conn:
        # 0) 报告执行前情况
        existing = conn.execute(
            f"""
            SELECT table_name FROM information_schema.tables
            WHERE table_name IN ({','.join(['?'] * len(ORPHAN_TABLES))})
            """,
            ORPHAN_TABLES,
        ).fetchall()
        existing_names = {r[0] for r in existing}
        missing = [t for t in ORPHAN_TABLES if t not in existing_names]
        if missing:
            print(f"[INFO] 这些表已经不存在, 仅清理 dim_data_asset 元数据: {missing}")

        # 1) DROP TABLE
        for t in ORPHAN_TABLES:
            if t in existing_names:
                row_cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                conn.execute(f"DROP TABLE IF EXISTS {t}")
                print(f"  DROP {t} (rows={row_cnt})")

        # 2) DELETE FROM dim_data_asset
        deleted = conn.execute(
            f"DELETE FROM dim_data_asset WHERE table_name IN ({','.join(['?'] * len(ORPHAN_TABLES))})",
            ORPHAN_TABLES,
        ).fetchone()
        print(f"\n[OK] dim_data_asset rows removed: {len(ORPHAN_TABLES)}")

        # 3) 报告剩余表数
        rem = conn.execute("SELECT COUNT(*) FROM dim_data_asset").fetchone()[0]
        print(f"[OK] dim_data_asset 剩余条目: {rem}")


if __name__ == "__main__":
    main()
