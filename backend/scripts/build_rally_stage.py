"""主升浪 episode 阶段切分 -> fact_rally_stage (起涨/主升/顶部 = 鱼头/鱼身/鱼尾, per-date stage 标签)。

owner=backend/config/rally_stage.yaml + analysis/zhushenglang_hunter_plan_20260617.md。
缘起 (C #48 step2, 用户核心缺口"没研究鱼头鱼尾"): 把每个 episode [bottom, peak] 时间轴切 3 阶段,
供 D 阶段 stage-conditional 因子研究 (出场>延续>买点)。

stage 标签 = POST-HOC (依赖 peak 事后才知) = 结果倒推分析用 (研究各阶段 PIT 特征, 特征仍 <=t),
非 live conditioning。切法: progress=(close-bottom)/(peak-bottom) 首次跨阈 (launch_end/main_end) 划
**连续时间段** (单调, 防 pullback 日错标)。每个 rally 期日一行 (code, date, stage)。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.data_loaders import load_kline  # noqa: E402
from services.database_manifest import get_database_manifest  # noqa: E402
from services.duck_adapter import connect  # noqa: E402

_MANIFEST = get_database_manifest()
SMARTMONEY_DB = str(_MANIFEST.path_for("smartmoney"))
DST = "fact_rally_stage"
_CFG = yaml.safe_load((REPO / "backend" / "config" / "rally_stage.yaml").read_text(encoding="utf-8"))
LAUNCH_END = _CFG["stage_thresholds"]["launch_end"]
MAIN_END = _CFG["stage_thresholds"]["main_end"]
NM = _CFG["stage_names"]


def segment_episode(dates: list, closes: list, bi: int, pi: int) -> list[tuple]:
    """episode [bi,pi] -> [(date, stage, progress, days_from_bottom)]. progress 首次跨阈划连续段。"""
    b, p = closes[bi], closes[pi]
    if b in (None, 0) or p in (None, 0) or p <= b:
        return []
    span = p - b
    i30 = i85 = pi
    for k in range(bi, pi + 1):
        c = closes[k]
        if c is None:
            continue
        prog = (c - b) / span
        if i30 == pi and prog >= LAUNCH_END:
            i30 = k
        if i85 == pi and prog >= MAIN_END:
            i85 = k
    if i30 > i85:        # 单调 clamp (LAUNCH_END<MAIN_END 通常 i30<=i85, 防 gap 异常)
        i30 = i85
    out = []
    for k in range(bi, pi + 1):
        c = closes[k]
        if c is None:
            continue
        prog = (c - b) / span
        stage = NM["launch"] if k < i30 else (NM["main"] if k < i85 else NM["top"])
        out.append((dates[k], stage, round(prog, 4), k - bi))
    return out


def main() -> int:
    by_code = load_kline("2019-01-01", None, 0)  # rule-compliance: ok evidence=K线 tushare-qfq/GT 同起点(全 episode)
    print(f"[load] K线 {len(by_code):,} 股", flush=True)
    rconn = connect(SMARTMONEY_DB, read_only=True)
    try:
        eps = rconn.execute(
            "SELECT stock_code, CAST(bottom_date AS VARCHAR), CAST(peak_date AS VARCHAR) "
            "FROM fact_rally_ground_truth").fetchall()
    finally:
        rconn.close()
    built = datetime.now(timezone.utc).isoformat()
    rows = []
    skipped = 0
    for code, bd, pk in eps:
        bars = by_code.get(code)
        if not bars:
            skipped += 1
            continue
        dates, closes = bars["date"], bars["close"]
        try:
            bi, pi = dates.index(bd), dates.index(pk)
        except ValueError:
            skipped += 1
            continue
        for date, stage, prog, dfb in segment_episode(dates, closes, bi, pi):
            rows.append((code, date, bd, stage, prog, dfb, built))
    print(f"[build] {len(rows):,} (code,date,stage) 行, {len(eps)-skipped}/{len(eps)} episode (skip {skipped})", flush=True)

    wconn = connect(SMARTMONEY_DB, read_only=False)
    try:
        wconn.execute(f"DROP TABLE IF EXISTS {DST}")
        wconn.execute(
            f"CREATE TABLE {DST} ("
            "stock_code VARCHAR NOT NULL, date VARCHAR NOT NULL, episode_bottom DATE NOT NULL, "
            "stage VARCHAR NOT NULL, progress DOUBLE NOT NULL, days_from_bottom INTEGER NOT NULL, "
            "built_at TIMESTAMP NOT NULL)")
        wconn.executemany(f"INSERT INTO {DST} VALUES (?,?,?,?,?,?,?)", rows)
        wconn.execute(f"CREATE INDEX idx_{DST}_cd ON {DST}(stock_code, date)")
        wconn.execute(f"CREATE INDEX idx_{DST}_stage ON {DST}(stage)")
        wconn.execute("CHECKPOINT")
        n = wconn.execute(f"SELECT count(*), count(DISTINCT stock_code||date) FROM {DST}").fetchone()
        dist = wconn.execute(f"SELECT stage, count(*) FROM {DST} GROUP BY stage ORDER BY 2 DESC").fetchall()
    finally:
        wconn.close()
    print(f"[done] {DST}: {n[0]:,} 行 (unique code×date {n[1]:,})")
    print("[分布] stage 天数:")
    for s, c in dist:
        print(f"   {s}: {c:,} ({c/n[0]*100:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
