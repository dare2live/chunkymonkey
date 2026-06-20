"""主升浪 hard-negative PIT 分层 -> fact_rally_negative_strata (镜像 build_rally_episode_strata, 负样本侧)。

owner=analysis/zhushenglang_hunter_plan_20260617.md + data_validation_backtest_plan_20260619.md。
缘起 (2026-06-20 对抗审查 wf_c5cc441d): 正样本有 fact_rally_episode_strata 分层, 负样本(fact_rally_entry_negative
  35198)无 → 做不了"层内 主升浪 vs 同层非rally"对照。本表给负样本同口径分层 (与正样本桶定义逐字一致 = 单一真相源)。
分层维 (全 PIT, entry_signal_date 时点可知): 市值 total_mv (daily_basic 入场日) / 长底 base_days / 申万 sector (as-of)。
注 (对抗审查): 负样本年份/底长分布系统不同于正样本 (rally 聚在 rally 年) = 真实总体属性, 不在 builder 重采样
  (会丢有效负样本); year×base 匹配是消费侧分析时做 (per-analysis), 非 builder 职责。bucket 阈值复用 episode_strata。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.database_manifest import get_database_manifest  # noqa: E402
from services.duck_adapter import connect  # noqa: E402

_MANIFEST = get_database_manifest()
SMARTMONEY_DB = str(_MANIFEST.path_for("smartmoney"))
RAW_DB = str(_MANIFEST.path_for("tushare_raw"))
SRC = "fact_rally_entry_negative"
DST = "fact_rally_negative_strata"

# 桶定义与 build_rally_episode_strata 逐字一致 (单一真相源; 改一处两处同改)
CAP_SQL = ("CASE WHEN db.total_mv IS NULL THEN 'unknown' "
           "WHEN db.total_mv < 300000 THEN '微盘' "      # rule-compliance: ok evidence=<30亿
           "WHEN db.total_mv < 1000000 THEN '小盘' "      # rule-compliance: ok evidence=30-100亿
           "WHEN db.total_mv < 5000000 THEN '中盘' "      # rule-compliance: ok evidence=100-500亿
           "ELSE '大盘' END")                              # rule-compliance: ok evidence=>500亿
BASE_SQL = ("CASE WHEN g.base_days < 60 THEN '短底' "      # rule-compliance: ok evidence=40-60日
            "WHEN g.base_days < 100 THEN '中底' "          # rule-compliance: ok evidence=60-100日
            "ELSE '长底' END")                              # rule-compliance: ok evidence=>100日


def main() -> int:
    built = datetime.now(timezone.utc).isoformat()
    conn = connect(SMARTMONEY_DB, read_only=False)
    try:
        conn.execute(f"ATTACH '{RAW_DB}' AS tr (READ_ONLY)")
        conn.execute(f"DROP TABLE IF EXISTS {DST}")
        conn.execute(
            f"CREATE TABLE {DST} ("
            "stock_code VARCHAR NOT NULL, entry_signal_date DATE NOT NULL, "
            "sw_l1_code VARCHAR, sw_l1_name VARCHAR, sw_l2_code VARCHAR, sw_l2_name VARCHAR, "
            "total_mv DOUBLE, cap_bucket VARCHAR NOT NULL, "
            "base_days INTEGER NOT NULL, base_bucket VARCHAR NOT NULL, "
            "built_at TIMESTAMP NOT NULL, PRIMARY KEY (stock_code, entry_signal_date))")
        conn.execute(
            f"INSERT INTO {DST} "
            f"SELECT g.stock_code, g.entry_signal_date, "
            f"  sec.l1_code, sec.l1_name, sec.l2_code, sec.l2_name, "
            f"  db.total_mv, {CAP_SQL}, "
            f"  g.base_days, {BASE_SQL}, "
            f"  '{built}'::TIMESTAMP "
            f"FROM {SRC} g "
            f"LEFT JOIN tr.raw_tushare_daily_basic db "
            f"  ON SUBSTR(db.ts_code,1,6)=g.stock_code AND CAST(db.trade_date AS VARCHAR)=strftime(g.entry_signal_date,'%Y%m%d') "
            f"LEFT JOIN LATERAL ("
            f"  SELECT m.l1_code,m.l1_name,m.l2_code,m.l2_name FROM tr.raw_tushare_index_member_all m "
            f"  WHERE SUBSTR(m.ts_code,1,6)=g.stock_code "
            f"    AND CAST(m.in_date AS VARCHAR)<=strftime(g.entry_signal_date,'%Y%m%d') "
            f"    AND (m.out_date IS NULL OR CAST(m.out_date AS VARCHAR)>=strftime(g.entry_signal_date,'%Y%m%d')) "
            f"  ORDER BY m.in_date DESC LIMIT 1) sec ON TRUE")
        conn.execute(f"CREATE INDEX idx_{DST}_cap ON {DST}(cap_bucket)")
        conn.execute(f"CREATE INDEX idx_{DST}_sector ON {DST}(sw_l1_code)")
        conn.execute("CHECKPOINT")
        tot = conn.execute(f"SELECT count(*) FROM {DST}").fetchone()[0]
        cov = conn.execute(
            f"SELECT sum(CASE WHEN sw_l1_code IS NOT NULL THEN 1 ELSE 0 END), "
            f"sum(CASE WHEN total_mv IS NOT NULL THEN 1 ELSE 0 END) FROM {DST}").fetchone()
        print(f"[done] {DST}: {tot:,} hard-neg | sector覆盖 {cov[0]:,}({cov[0]/tot*100:.0f}%) | 市值覆盖 {cov[1]:,}({cov[1]/tot*100:.0f}%)")
        for b, n in conn.execute(f"SELECT cap_bucket, count(*) FROM {DST} GROUP BY cap_bucket ORDER BY 2 DESC").fetchall():
            print(f"   cap {b}: {n:,}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
