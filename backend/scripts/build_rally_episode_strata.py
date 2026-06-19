"""主升浪 episode PIT 分层 -> fact_rally_episode_strata (申万sector + 市值 + 长底, 全 bottom 时点可知)。

owner=analysis/zhushenglang_hunter_plan_20260617.md + data_validation_backtest_plan_20260619.md。
缘起 (C #48 分层): D 阶段因子判别需分层 (用户核心缺口); 在 strata 内做 alpha 增强 (stage-conditional 非无条件截面)。
分层维 (全 PIT, bottom 时点可知 = 可 live conditioning):
  - 申万 sector L1/L2: as-of join raw_tushare_index_member_all (in_date<=底<out_date; is_new Y当前+N历史区间 = 真PIT, 非latest-snapshot §4.5)
  - 市值 total_mv: daily_basic 底日 (PIT) -> 微盘/小盘/中盘/大盘 桶
  - 长底 base_days: GT (底前盘整, PIT) -> 短/中/长底 桶
form/gain/offset 是 forward outcome (rally 形态), 不入本表 (留 GT join, 与 rally_gt_columns 契约一致)。
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
DST = "fact_rally_episode_strata"

# 市值桶 (万元; total_mv tushare 单位=万元). evidence: 约定 A股市值分层 30/100/500亿
CAP_SQL = ("CASE WHEN db.total_mv IS NULL THEN 'unknown' "
           "WHEN db.total_mv < 300000 THEN '微盘' "      # rule-compliance: ok evidence=<30亿
           "WHEN db.total_mv < 1000000 THEN '小盘' "      # rule-compliance: ok evidence=30-100亿
           "WHEN db.total_mv < 5000000 THEN '中盘' "      # rule-compliance: ok evidence=100-500亿
           "ELSE '大盘' END")                              # rule-compliance: ok evidence=>500亿
# 长底桶 (base_days; GT BASEMIN=40 起). evidence: 短40-60/中60-100/长>100
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
            "stock_code VARCHAR NOT NULL, bottom_date DATE NOT NULL, "
            "sw_l1_code VARCHAR, sw_l1_name VARCHAR, sw_l2_code VARCHAR, sw_l2_name VARCHAR, "
            "total_mv DOUBLE, cap_bucket VARCHAR NOT NULL, "
            "base_days INTEGER NOT NULL, base_bucket VARCHAR NOT NULL, "
            "built_at TIMESTAMP NOT NULL, PRIMARY KEY (stock_code, bottom_date))")
        conn.execute(
            f"INSERT INTO {DST} "
            f"SELECT g.stock_code, g.bottom_date, "
            f"  sec.l1_code, sec.l1_name, sec.l2_code, sec.l2_name, "
            f"  db.total_mv, {CAP_SQL}, "
            f"  g.base_days, {BASE_SQL}, "
            f"  '{built}'::TIMESTAMP "
            f"FROM fact_rally_ground_truth g "
            # 市值: daily_basic 底日 PIT
            f"LEFT JOIN tr.raw_tushare_daily_basic db "
            f"  ON SUBSTR(db.ts_code,1,6)=g.stock_code AND CAST(db.trade_date AS VARCHAR)=strftime(g.bottom_date,'%Y%m%d') "
            # 申万 sector: as-of (in_date<=底 且 out_date空或>=底; 取最近一段) PIT. in/out_date=INTEGER, CAST统一串比
            f"LEFT JOIN LATERAL ("
            f"  SELECT m.l1_code,m.l1_name,m.l2_code,m.l2_name FROM tr.raw_tushare_index_member_all m "
            f"  WHERE SUBSTR(m.ts_code,1,6)=g.stock_code "
            f"    AND CAST(m.in_date AS VARCHAR)<=strftime(g.bottom_date,'%Y%m%d') "
            f"    AND (m.out_date IS NULL OR CAST(m.out_date AS VARCHAR)>=strftime(g.bottom_date,'%Y%m%d')) "
            f"  ORDER BY m.in_date DESC LIMIT 1) sec ON TRUE")
        conn.execute(f"CREATE INDEX idx_{DST}_sector ON {DST}(sw_l1_code)")
        conn.execute(f"CREATE INDEX idx_{DST}_cap ON {DST}(cap_bucket)")
        conn.execute("CHECKPOINT")
        tot = conn.execute(f"SELECT count(*) FROM {DST}").fetchone()[0]
        cov = conn.execute(
            f"SELECT sum(CASE WHEN sw_l1_code IS NOT NULL THEN 1 ELSE 0 END), "
            f"sum(CASE WHEN total_mv IS NOT NULL THEN 1 ELSE 0 END) FROM {DST}").fetchone()
        print(f"[done] {DST}: {tot:,} episode | sector覆盖 {cov[0]:,}({cov[0]/tot*100:.0f}%) | 市值覆盖 {cov[1]:,}({cov[1]/tot*100:.0f}%)")
        print("[分布] cap_bucket:")
        for b, n in conn.execute(f"SELECT cap_bucket, count(*) FROM {DST} GROUP BY cap_bucket ORDER BY 2 DESC").fetchall():
            print(f"   {b}: {n:,}")
        print("[分布] base_bucket:")
        for b, n in conn.execute(f"SELECT base_bucket, count(*) FROM {DST} GROUP BY base_bucket ORDER BY 2 DESC").fetchall():
            print(f"   {b}: {n:,}")
        print("[分布] 申万L1 top8:")
        for b, n in conn.execute(f"SELECT sw_l1_name, count(*) FROM {DST} WHERE sw_l1_name IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 8").fetchall():
            print(f"   {b}: {n:,}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
