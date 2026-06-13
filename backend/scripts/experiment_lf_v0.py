"""LF V0 概念龙头-跟随者传导 — B组C3 主判决实验 (预注册逐字实现).

预注册 owner = analysis/prereg_lf_v0_20260612.md (FROZEN 2026-06-12)。
本脚本的判据常量必须与 prereg yaml 块逐字一致 — `--check-prereg` 机器验收
(test_experiment_lf_v0.py 钉死)。看到结果后改任何常量/窗口/切法 = 触发谄媚死条款。

数据真相源 (全部 tushare_raw, read_only; 概念归属 = raw_tushare_dc_member as-of t):
  龙头 = limit_list_d[limit='U'] ∩ dc_member[t].con_code; follower = 同概念非龙头未涨停;
  价格 = daily.open x adj_factor (后复权比值口径); 可成交 = t+1 open < stk_limit.up_limit。
对照 = 同日涨幅带 ±1pp 非该概念成员 (prereg 文面无 tradability 条款, 不加 — 加 = 挪门柱)。
bootstrap = 按事件聚类重采样 (follower 同事件相关, 实现细节披露)。

用法:
  PYTHONPATH=backend python backend/scripts/experiment_lf_v0.py            # 跑判决
  PYTHONPATH=backend python backend/scripts/experiment_lf_v0.py --check-prereg
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

PREREG_PATH = REPO / "analysis" / "prereg_lf_v0_20260612.md"
RAW_DB = REPO / "data" / "tushare_raw.duckdb"  # rule-compliance: ok evidence=一次性判决实验脚本, 只读 raw 库, 不入生产链路 (prereg 判负处置: theme/LF 轴封档)
SMART_DB = REPO / "data" / "smartmoney.duckdb"  # rule-compliance: ok evidence=搭车臂 BORN 副表只读计数, 失败不阻塞主判决
OUT_DIR = REPO / "analysis"

# ── 预注册冻结常量 (与 prereg yaml 块逐字对应; --check-prereg 机器验收) ──
PREREG = {
    "J1_threshold_pp": 0.55,    # 超额 >= +0.55pp 且 bootstrap 95% CI 下界 > 0 (锚 = 2x 双边成本)
    "J2_periods": ("2025H1", "2025H2", "2026H1"),  # 3/3 期同号为正
    "J3_min_events": 30,        # 事件数 >= 30, 不足 = inconclusive (非判负)
    "PIT_retention_min": 0.50,  # t-1 口径 J1 留存 >= 50%, 不满足 = 一级 NO-GO
}
COST_RT_PP = 0.27282     # from yaml: paper_sim cost round_trip 27.282bps (双边)
HOLD_DAYS = 5            # t+1 open 买, t+6 open 卖 (5 交易日窗), 冻结
PCT_BAND = 1.0           # 对照涨幅带 ±1pp, 冻结
N_CONTROLS = 3           # 每 follower 对照数, 冻结
LEADER_MIN = 2           # 事件 = 概念当日涨停龙头数 >= 2, 冻结
COOLDOWN_DAYS = 4        # 去连发: [t-4, t-1] 无事件, 冻结
MEMBER_WINDOW = 3        # 概念归属 carry-forward 平滑窗 (prereg 修订 2): 近 3 交易日见过即算成员;
                         # 只用 <= 锚点日信息 (PIT); 动机 = dc_member 薄日伪影实测 flicker 85.1%,
                         # 逐日裸快照会把真成员误判非成员 (漏 leader + 污染对照)
HOT_QUANTILE = 0.9       # 极端热日 = 全市场涨停数最高 10% 分位, 分桶报告不并桶判决
WINDOW = ("20250102", "20260630")  # dc_member 地板 20250102 (E8 探底) .. 2026H1 期末
BOOTSTRAP_N = 10_000     # bootstrap 重采样次数 (实现细节, 披露)
BOOTSTRAP_SEED = 20260612  # 固定种子保证可复现 (实现细节, 披露)


def check_prereg_consistency() -> list[str]:
    """机器验收: 脚本常量必须与 prereg 文档 yaml 块逐字一致."""
    text = PREREG_PATH.read_text(encoding="utf-8")
    problems = []
    if f"threshold_pp: {PREREG['J1_threshold_pp']}" not in text:
        problems.append(f"J1 threshold_pp={PREREG['J1_threshold_pp']} 与 prereg 不一致")
    if "3/3 期" not in text:
        problems.append("J2 3/3 期与 prereg 不一致")
    if f">= {PREREG['J3_min_events']}" not in text:
        problems.append(f"J3 事件数下限 {PREREG['J3_min_events']} 与 prereg 不一致")
    if "留存 >= 50%" not in text:
        problems.append("PIT 双锚留存线 50% 与 prereg 不一致")
    if "t+6 open" not in text:
        problems.append("持有窗 t+6 与 prereg 不一致")
    if "27.282bps" not in text:
        problems.append("双边成本 27.282bps 与 prereg 不一致")
    if ">= 2" not in text:
        problems.append(f"龙头数下限 {LEADER_MIN} 与 prereg 不一致")
    if "[t-4, t-1]" not in text:
        problems.append("去连发窗 [t-4,t-1] 与 prereg 不一致")
    if f"carry-forward 平滑窗 {MEMBER_WINDOW}" not in text:
        problems.append(f"概念归属平滑窗 {MEMBER_WINDOW} 与 prereg 修订 2 不一致")
    return problems


def run_gate() -> bool:
    r = subprocess.run(
        ["/Users/dp/.local/bin/sherpa", "gates", "--repo", str(REPO), "lf_v0"],
        capture_output=True, text=True, check=False,
    )
    sys.stdout.write(r.stdout)
    return r.returncode == 0


def load_calendar(con):
    days = [r[0] for r in con.execute(
        "SELECT cal_date FROM raw_tushare_trade_cal WHERE exchange='SSE' AND is_open='1' "
        "AND cal_date BETWEEN ? AND ? ORDER BY 1", [WINDOW[0], "20261231"],
    ).fetchall()]
    return days, {d: i for i, d in enumerate(days)}


def detect_events(con, days, day_idx, anchor_offset: int = 0):
    """事件检测: (t, concept) 当日涨停龙头数 >= LEADER_MIN, 去连发.

    anchor_offset=0 → 概念归属锚 t (面板窗 [t-2..t]); anchor_offset=1 → 锚 t-1
    (面板窗 [t-3..t-1], PIT 双锚敏感性)。归属 = 窗内任一日见过即算 (修订 2 平滑语义)。
    返回 [(t, concept, frozenset(leaders))], 按 (concept, t) 升序确定性。
    """
    con.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _cal_idx AS "
        "SELECT cal_date, row_number() OVER (ORDER BY cal_date) AS rn "
        "FROM raw_tushare_trade_cal WHERE exchange='SSE' AND is_open='1'"
    )
    hi = anchor_offset
    lo = anchor_offset + MEMBER_WINDOW - 1
    rows = con.execute(
        f"""
        SELECT l.trade_date, m.ts_code AS concept, l.ts_code AS leader
        FROM raw_tushare_limit_list_d l
        JOIN _cal_idx ct ON ct.cal_date = l.trade_date
        JOIN _cal_idx cm ON cm.rn BETWEEN ct.rn - {lo} AND ct.rn - {hi}
        JOIN raw_tushare_dc_member m
          ON m.trade_date = cm.cal_date AND m.con_code = l.ts_code
        WHERE l."limit" = 'U' AND l.trade_date BETWEEN ? AND ?
        GROUP BY 1, 2, 3
        """, list(WINDOW),
    ).fetchall()
    by_key: dict[tuple[str, str], set[str]] = {}
    for t, concept, leader in rows:
        by_key.setdefault((t, concept), set()).add(leader)
    candidates = sorted((c, t, codes) for (t, c), codes in by_key.items() if len(codes) >= LEADER_MIN)
    events: list = []
    last_kept: dict[str, int] = {}
    for concept, t, codes in candidates:
        i = day_idx.get(t)
        if i is None:
            continue
        j = last_kept.get(concept)
        if j is not None and i - j <= COOLDOWN_DAYS:
            continue  # 去连发: [t-4, t-1] 内已有事件
        last_kept[concept] = i
        events.append((t, concept, frozenset(codes)))
    return events


def members_asof(con, t: str, concept: str, days, day_idx, anchor_offset: int) -> set[str]:
    """概念归属 = 锚点窗 (MEMBER_WINDOW 个交易日, 全 <= 锚点) 内任一日见过即算 (修订 2)."""
    i = day_idx.get(t)
    if i is None:
        return set()
    end = i - anchor_offset
    if end < 0:
        return set()
    mdays = days[max(0, end - (MEMBER_WINDOW - 1)):end + 1]
    if not mdays:
        return set()
    qs = ",".join("?" * len(mdays))
    return {r[0] for r in con.execute(
        f"SELECT DISTINCT con_code FROM raw_tushare_dc_member WHERE ts_code = ? AND trade_date IN ({qs})",
        [concept, *mdays]).fetchall()}


def fwd5(hopen, days, day_idx, t, code) -> float | None:
    i = day_idx.get(t)
    if i is None or i + 1 + HOLD_DAYS >= len(days):
        return None  # 窗尾不足
    o1 = hopen.get((days[i + 1], code))
    o6 = hopen.get((days[i + 1 + HOLD_DAYS], code))
    if not o1 or not o6:
        return None  # 停牌/缺价
    return (o6 / o1 - 1.0) * 100.0 - COST_RT_PP  # pp, 含双边成本


def run_arm(con, days, day_idx, hopen, by_day, limit_up_by_day, open_by_day, uplimit_by_day,
            anchor_offset: int):
    """一条臂 (anchor t 或 t-1) 的完整样本生成. 返回 samples + 排除计数."""
    events = detect_events(con, days, day_idx, anchor_offset)
    excl = {"window_tail": 0, "no_t1_tradable": 0, "no_controls": 0, "no_pct": 0}
    samples = []   # (event_key, t, excess_pp)
    n_events_contributing = 0
    for t, concept, leaders in events:
        members = members_asof(con, t, concept, days, day_idx, anchor_offset)
        if not members:
            continue
        i = day_idx.get(t)
        if i is None or i + 1 + HOLD_DAYS >= len(days):
            excl["window_tail"] += 1
            continue
        d1 = days[i + 1]
        lim_today = limit_up_by_day.get(t, set())
        contributed = False
        for code in sorted(members - leaders - lim_today):
            pct = by_day.get(t, {}).get(code)
            if pct is None:
                excl["no_pct"] += 1
                continue
            o1 = open_by_day.get((d1, code))
            ul = uplimit_by_day.get((d1, code))
            if o1 is None or ul is None or not (o1 < ul):
                excl["no_t1_tradable"] += 1  # 盲点6: t+1 一字/无价不可成交
                continue
            f = fwd5(hopen, days, day_idx, t, code)
            if f is None:
                excl["window_tail"] += 1
                continue
            cands = sorted(
                ((c, p) for c, p in by_day.get(t, {}).items()
                 if c not in members and abs(p - pct) <= PCT_BAND),
                key=lambda x: (abs(x[1] - pct), x[0]))
            ctrl = []
            for c, _ in cands:
                cf = fwd5(hopen, days, day_idx, t, c)
                if cf is not None:
                    ctrl.append(cf)
                if len(ctrl) == N_CONTROLS:
                    break
            if not ctrl:
                excl["no_controls"] += 1
                continue
            samples.append(((t, concept), t, f - sum(ctrl) / len(ctrl)))
            contributed = True
        if contributed:
            n_events_contributing += 1
    return samples, n_events_contributing, excl


def net_of(samples) -> float:
    return sum(s[2] for s in samples) / len(samples)


def period_of(t: str) -> str:
    return f"{t[:4]}H1" if t[4:6] <= "06" else f"{t[:4]}H2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-prereg", action="store_true")
    parser.add_argument("--skip-gate", action="store_true",
                        help="跳过 sherpa gate (仅测试 fixture 用; 生产禁用)")
    parser.add_argument("--db", default=str(RAW_DB))
    parser.add_argument("--smart-db", default=str(SMART_DB))
    parser.add_argument("--out-dir", default=str(OUT_DIR),
                        help="判决 JSON 落盘目录 (测试 fixture 传 tmp_path 防污染真实 analysis/)")
    args = parser.parse_args()

    problems = check_prereg_consistency()
    if problems:
        print("PREREG 一致性 FAIL:", problems)
        return 2
    if args.check_prereg:
        print("PREREG 一致性 PASS (常量与冻结文档逐字一致)")
        return 0
    if not args.skip_gate and not run_gate():
        print("sherpa gates lf_v0 = NO-GO, 按死亡条款拒跑 (判断死)")
        return 1

    import duckdb

    con = duckdb.connect(args.db, read_only=True)  # rule-compliance: ok evidence=read_only 判决实验, 测试传 fixture db
    days, day_idx = load_calendar(con)

    # 面板: 涨幅 / 后复权 open / 当日涨停集 / t+1 open 与涨停价 (盲点6)
    by_day: dict[str, dict[str, float]] = {}
    for t, c, p in con.execute(
            "SELECT trade_date, ts_code, pct_chg FROM raw_tushare_daily "
            "WHERE trade_date BETWEEN ? AND ? AND pct_chg IS NOT NULL", list(WINDOW)).fetchall():
        by_day.setdefault(t, {})[c] = float(p)
    hopen = {(t, c): float(h) for t, c, h in con.execute(
        """
        SELECT d.trade_date, d.ts_code, d.open * a.adj_factor
        FROM raw_tushare_daily d JOIN raw_tushare_adj_factor a USING (trade_date, ts_code)
        WHERE d.open IS NOT NULL AND d.open > 0 AND a.adj_factor IS NOT NULL
          AND d.trade_date BETWEEN ? AND ?
        """, [WINDOW[0], "20261231"]).fetchall()}
    limit_up_by_day: dict[str, set[str]] = {}
    for t, c in con.execute(
            "SELECT trade_date, ts_code FROM raw_tushare_limit_list_d "
            "WHERE \"limit\"='U' AND trade_date BETWEEN ? AND ?", list(WINDOW)).fetchall():
        limit_up_by_day.setdefault(t, set()).add(c)
    open_by_day = {(t, c): float(o) for t, c, o in con.execute(
        "SELECT trade_date, ts_code, open FROM raw_tushare_daily "
        "WHERE open IS NOT NULL AND trade_date BETWEEN ? AND ?",
        [WINDOW[0], "20261231"]).fetchall()}
    uplimit_by_day = {(t, c): float(u) for t, c, u in con.execute(
        "SELECT trade_date, ts_code, up_limit FROM raw_tushare_stk_limit "
        "WHERE up_limit IS NOT NULL AND trade_date BETWEEN ? AND ?",
        [WINDOW[0], "20261231"]).fetchall()}

    # 主臂 (anchor=t) + PIT 双锚臂 (anchor=t-1)
    samples, n_events, excl = run_arm(con, days, day_idx, hopen, by_day, limit_up_by_day,
                                      open_by_day, uplimit_by_day, anchor_offset=0)
    samples_t1, n_events_t1, excl_t1 = run_arm(con, days, day_idx, hopen, by_day, limit_up_by_day,
                                               open_by_day, uplimit_by_day, anchor_offset=1)

    if not samples:
        print(json.dumps({"verdict": "INVALID", "reason": "0 follower 样本 — 数据/匹配口径有问题",
                          "n_events": n_events, "excluded": excl}, ensure_ascii=False))
        con.close()
        return 3

    net = net_of(samples)
    # cluster bootstrap: 按事件重采样 (同事件 follower 相关, 不当独立样本)
    by_event: dict = {}
    for key, _, x in samples:
        by_event.setdefault(key, []).append(x)
    event_keys = sorted(by_event)
    rng = random.Random(BOOTSTRAP_SEED)
    boots = []
    for _ in range(BOOTSTRAP_N):
        pool = []
        for _ in range(len(event_keys)):
            pool.extend(by_event[event_keys[rng.randrange(len(event_keys))]])
        boots.append(sum(pool) / len(pool))
    boots.sort()
    ci_low, ci_high = boots[int(0.025 * BOOTSTRAP_N)], boots[int(0.975 * BOOTSTRAP_N)]

    periods = {p: [] for p in PREREG["J2_periods"]}
    for _, t, x in samples:
        p = period_of(t)
        if p in periods:
            periods[p].append(x)
    j2_detail = {p: {"n": len(v), "net_pp": round(sum(v) / len(v), 3) if v else None}
                 for p, v in periods.items()}
    pos_periods = sum(1 for v in periods.values() if v and sum(v) / len(v) > 0)

    j1 = net >= PREREG["J1_threshold_pp"] and ci_low > 0
    j2 = pos_periods >= len(PREREG["J2_periods"])
    j3 = n_events >= PREREG["J3_min_events"]
    net_t1 = net_of(samples_t1) if samples_t1 else None
    retention = (net_t1 / net) if (net_t1 is not None and net > 0) else None
    pit_ok = retention is not None and retention >= PREREG["PIT_retention_min"]

    if not j3:
        verdict = "INCONCLUSIVE"  # 非判负, 等面板加厚
    else:
        verdict = "GO" if (j1 and j2 and pit_ok) else "REJECT"

    # 极端热日分桶 (报告专用, 不并桶判决)
    day_limit_counts = sorted(len(v) for v in limit_up_by_day.values())
    hot_cut = day_limit_counts[int(HOT_QUANTILE * len(day_limit_counts))] if day_limit_counts else None
    hot = [x for _, t, x in samples if len(limit_up_by_day.get(t, ())) >= (hot_cut or 1 << 30)]
    normal = [x for _, t, x in samples if len(limit_up_by_day.get(t, ())) < (hot_cut or 1 << 30)]

    # 搭车臂 B组C1: BORN 副表资格核证 (只读, 失败不阻塞)
    born_n = None
    try:
        scon = duckdb.connect(args.smart_db, read_only=True)  # rule-compliance: ok evidence=搭车臂只读计数
        # source 列 = 数据族 ('dc'), raw/snapshot 之分在 as_of_mode (reconstructed/observed)
        born_n = scon.execute("SELECT count(*) FROM fact_concept_event "
                              "WHERE event_type='concept_born' AND as_of_mode='reconstructed'").fetchone()[0]
        scon.close()
    except Exception as e:
        born_n = f"unavailable: {str(e)[:60]}"

    out = {
        "experiment": "B-C3_lf_v0_main_verdict",
        "prereg": str(PREREG_PATH.name),
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": WINDOW, "hold_days": HOLD_DAYS, "cost_rt_pp": COST_RT_PP,
        "n_events": n_events, "n_follower_samples": len(samples),
        "excluded": excl,
        "J1": {"net_pp": round(net, 3), "ci95": [round(ci_low, 3), round(ci_high, 3)],
               "threshold_pp": PREREG["J1_threshold_pp"], "pass": j1},
        "J2": {"periods": j2_detail, "positive": pos_periods,
               "need": f"{len(PREREG['J2_periods'])}/{len(PREREG['J2_periods'])}", "pass": j2},
        "J3": {"n_events": n_events, "need": f">={PREREG['J3_min_events']}", "pass": j3},
        "PIT_dual_anchor": {"net_t1_pp": round(net_t1, 3) if net_t1 is not None else None,
                            "n_t1": len(samples_t1), "retention": round(retention, 3) if retention is not None else None,
                            "need": f">={PREREG['PIT_retention_min']}", "pass": pit_ok},
        "hot_day_bucket": {"hot_cut_limit_count": hot_cut,
                           "hot": {"n": len(hot), "net_pp": round(sum(hot) / len(hot), 3) if hot else None},
                           "normal": {"n": len(normal), "net_pp": round(sum(normal) / len(normal), 3) if normal else None}},
        "side_arm_born_count": born_n,
        "bootstrap": {"n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED, "cluster": "event"},
        "verdict": verdict,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = Path(args.out_dir) / f"lf_v0_verdict_{stamp}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n判决已落盘: {out_path}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
