"""LHB 上榜即退出 — C组C1 主判决实验 (预注册逐字实现).

预注册 owner = analysis/prereg_lhb_exit_20260612.md (FROZEN 2026-06-12 + 修订 1)。
本脚本的判据常量必须与 prereg yaml 块逐字一致 — `--check-prereg` 机器验收
(test_experiment_lhb_exit.py 钉死)。看到结果后改任何常量/窗口/切法 = 触发谄媚死条款。

数据真相源 (全部 tushare_raw, read_only; 日历用 raw_tushare_trade_cal 避主库锁):
  事件/涨幅/事件股市值 = raw_tushare_top_list; 价格 = raw_tushare_daily.open x adj_factor
  (后复权比值口径, 比值中复权基准约掉); 对照股涨幅 = daily.pct_chg, 市值 = daily_basic.circ_mv。

用法:
  PYTHONPATH=backend python backend/scripts/experiment_lhb_exit.py            # 跑判决
  PYTHONPATH=backend python backend/scripts/experiment_lhb_exit.py --check-prereg
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

PREREG_PATH = REPO / "analysis" / "prereg_lhb_exit_20260612.md"
RAW_DB = REPO / "data" / "tushare_raw.duckdb"  # rule-compliance: ok evidence=一次性判决实验脚本, 只读 raw 库, 不入生产链路 (prereg 处置条款: 判负即除名归档)
OUT_DIR = REPO / "analysis"

# ── 预注册冻结常量 (与 prereg yaml 块逐字对应; --check-prereg 机器验收) ──
PREREG = {
    "J1_threshold_pp": 1.0,     # 净效应 >= +1.0pp 且 bootstrap 95% CI 下界 > 0
    "J2_min_positive_years": 5,  # 2020..2025 + 2026YTD 共 7 期, 同号为正 >= 5/7
    "J3_dominance_ratio": 1.5,   # 上榜组 exit_gain 均值 > 对照组均值的 1.5 倍
}
WINDOW = ("20200102", "20260529")   # prereg 实验窗, 冻结
HOLD_DAYS = 20                       # t+1 open 卖 vs 持有至 t+21 open (20 交易日窗), 冻结
PCT_BAND = 1.0                       # 混淆臂涨幅带 ±1pp, 冻结
N_CONTROLS = 3                       # 每事件对照数, 冻结
BOOTSTRAP_N = 10_000                 # bootstrap 重采样次数 (实现细节, 披露)
BOOTSTRAP_SEED = 20260612            # 固定种子保证可复现 (实现细节, 披露)


def check_prereg_consistency() -> list[str]:
    """机器验收: 脚本常量必须与 prereg 文档 yaml 块逐字一致."""
    text = PREREG_PATH.read_text(encoding="utf-8")
    problems = []
    if f"threshold_pp: {PREREG['J1_threshold_pp']}" not in text:
        problems.append(f"J1 threshold_pp={PREREG['J1_threshold_pp']} 与 prereg 不一致")
    if f">= {PREREG['J2_min_positive_years']}/7" not in text:
        problems.append(f"J2 {PREREG['J2_min_positive_years']}/7 与 prereg 不一致")
    if f"{PREREG['J3_dominance_ratio']} 倍" not in text:
        problems.append(f"J3 ratio={PREREG['J3_dominance_ratio']} 与 prereg 不一致")
    if "2020-01-02..2026-05-29" not in text:
        problems.append("实验窗与 prereg 不一致")
    m = re.search(r"t\+21", text)
    if not m:
        problems.append("持有窗 t+21 与 prereg 不一致")
    return problems


def run_gate() -> bool:
    r = subprocess.run(
        ["/Users/dp/.local/bin/sherpa", "gates", "--repo", str(REPO), "lhb_exit"],
        capture_output=True, text=True, check=False,
    )
    sys.stdout.write(r.stdout)
    return r.returncode == 0


def load_frames(con):
    """一次性取齐: 交易日序 / 事件 / 后复权 open / 对照候选面板."""
    days = [r[0] for r in con.execute(
        "SELECT cal_date FROM raw_tushare_trade_cal WHERE exchange='SSE' AND is_open='1' "
        "AND cal_date BETWEEN ? AND ? ORDER BY 1", [WINDOW[0], "20261231"],
    ).fetchall()]
    day_idx = {d: i for i, d in enumerate(days)}

    events = con.execute(
        """
        SELECT trade_date, ts_code,
               any_value(pct_change) AS pct_change,
               any_value(float_values) AS float_values
        FROM raw_tushare_top_list
        WHERE trade_date BETWEEN ? AND ?
        GROUP BY trade_date, ts_code
        """, list(WINDOW),
    ).fetchall()

    # 后复权 open: 比值口径, 复权基准约掉
    con.execute(
        """
        CREATE TEMP TABLE hfq AS
        SELECT d.trade_date, d.ts_code, d.open * a.adj_factor AS hopen
        FROM raw_tushare_daily d
        JOIN raw_tushare_adj_factor a USING (trade_date, ts_code)
        WHERE d.open IS NOT NULL AND d.open > 0 AND a.adj_factor IS NOT NULL
        """
    )
    return days, day_idx, events


def hopen_lookup(con):
    rows = con.execute("SELECT trade_date, ts_code, hopen FROM hfq").fetchall()
    table: dict[tuple[str, str], float] = {}
    for d, c, h in rows:
        table[(d, c)] = float(h)
    return table


def exit_gain(hopen, days, day_idx, t, code) -> float | None:
    i = day_idx.get(t)
    if i is None or i + 1 + HOLD_DAYS >= len(days):
        return None  # 窗尾不足
    d1, d21 = days[i + 1], days[i + 1 + HOLD_DAYS]
    o1, o21 = hopen.get((d1, code)), hopen.get((d21, code))
    if not o1 or not o21:
        return None  # 停牌/缺价
    return -(o21 / o1 - 1.0) * 100.0  # pp; 正值 = 退出占优


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-prereg", action="store_true")
    parser.add_argument("--skip-gate", action="store_true",
                        help="跳过 sherpa gate (仅测试 fixture 用; 生产禁用)")
    parser.add_argument("--db", default=str(RAW_DB))
    parser.add_argument("--out-dir", default=str(OUT_DIR),
                        help="判决 JSON 落盘目录 (测试 fixture 传 tmp_path 防污染/覆盖真实 analysis/ 判决)")
    args = parser.parse_args()

    problems = check_prereg_consistency()
    if problems:
        print("PREREG 一致性 FAIL:", problems)
        return 2
    if args.check_prereg:
        print("PREREG 一致性 PASS (常量与冻结文档逐字一致)")
        return 0
    if not args.skip_gate and not run_gate():
        print("sherpa gates lhb_exit = NO-GO, 按死亡条款拒跑 (判断死)")
        return 1

    import duckdb

    con = duckdb.connect(args.db, read_only=True)  # rule-compliance: ok evidence=read_only 判决实验, 测试传 fixture db, 不走生产 adapter
    # TEMP TABLE 需要写临时空间 → 只读连接允许 temp
    days, day_idx, events = load_frames(con)
    hopen = hopen_lookup(con)
    listed_by_day: dict[str, set[str]] = {}
    for t, c, _, _ in events:
        listed_by_day.setdefault(t, set()).add(c)

    # 对照候选面板: 全市场 (pct_chg, circ_mv 五分位)
    panel = con.execute(
        """
        SELECT d.trade_date, d.ts_code, d.pct_chg,
               ntile(5) OVER (PARTITION BY d.trade_date ORDER BY b.circ_mv) AS mv_q
        FROM raw_tushare_daily d
        JOIN raw_tushare_daily_basic b USING (trade_date, ts_code)
        WHERE d.trade_date BETWEEN ? AND ? AND d.pct_chg IS NOT NULL AND b.circ_mv IS NOT NULL
        """, list(WINDOW),
    ).fetchall()
    by_day: dict[str, list[tuple[str, float, int]]] = {}
    for t, c, p, q in panel:
        by_day.setdefault(t, []).append((c, float(p), int(q)))
    # 事件股自身的市值分位 (用同面板)
    quint = {(t, c): q for t, rows in by_day.items() for c, _, q in [(c, p, q) for c, p, q in rows]}

    n_null_float = n_tail = n_no_controls = n_no_price = 0
    samples = []      # (year, event_gain, control_mean_gain)
    sens_nullfloat = []
    for t, code, pct, fv in events:
        g = exit_gain(hopen, days, day_idx, t, code)
        if g is None:
            n_tail += 1
            continue
        if fv is None:
            n_null_float += 1
            sens_nullfloat.append(g)
            continue  # 修订 1: null-float 事件退出主判决
        eq = quint.get((t, code))
        if eq is None or pct is None:
            n_no_price += 1
            continue
        cands = [
            (c, p) for c, p, q in by_day.get(t, [])
            if q == eq and c not in listed_by_day.get(t, set()) and abs(p - float(pct)) <= PCT_BAND
        ]
        cands.sort(key=lambda x: (abs(x[1] - float(pct)), x[0]))  # 最近涨幅, code 决断 — 确定性
        ctrl_gains = []
        for c, _ in cands:
            cg = exit_gain(hopen, days, day_idx, t, c)
            if cg is not None:
                ctrl_gains.append(cg)
            if len(ctrl_gains) == N_CONTROLS:
                break
        if not ctrl_gains:
            n_no_controls += 1
            continue
        samples.append((t[:4], g, sum(ctrl_gains) / len(ctrl_gains)))
    con.close()

    n = len(samples)
    if n == 0:
        print(json.dumps({"verdict": "INVALID", "reason": "0 judged events — 数据/匹配口径有问题, 不出判决",
                          "excluded": {"null_float": n_null_float, "window_tail": n_tail,
                                       "no_price_or_quintile": n_no_price, "no_controls": n_no_controls}},
                         ensure_ascii=False))
        return 3
    ev_mean = sum(s[1] for s in samples) / n
    ct_mean = sum(s[2] for s in samples) / n
    net = ev_mean - ct_mean
    # bootstrap (固定种子, 重采样事件对)
    rng = random.Random(BOOTSTRAP_SEED)
    boots = []
    for _ in range(BOOTSTRAP_N):
        idxs = [rng.randrange(n) for _ in range(n)]
        boots.append(sum(samples[i][1] - samples[i][2] for i in idxs) / n)
    boots.sort()
    ci_low, ci_high = boots[int(0.025 * BOOTSTRAP_N)], boots[int(0.975 * BOOTSTRAP_N)]

    years = sorted({y for y, _, _ in samples})
    yearly = {}
    for y in years:
        ys = [(g, c) for yy, g, c in samples if yy == y]
        yearly[y] = {"n": len(ys), "net_pp": round(sum(g - c for g, c in ys) / len(ys), 3)}
    pos_years = sum(1 for v in yearly.values() if v["net_pp"] > 0)

    j1 = net >= PREREG["J1_threshold_pp"] and ci_low > 0
    j2 = pos_years >= PREREG["J2_min_positive_years"]
    j3 = ev_mean > PREREG["J3_dominance_ratio"] * ct_mean
    verdict = "GO" if (j1 and j2 and j3) else "REJECT"

    out = {
        "experiment": "C-C1_lhb_exit_main_verdict",
        "prereg": str(PREREG_PATH.name),
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": WINDOW, "hold_days": HOLD_DAYS,
        "n_events_judged": n,
        "excluded": {"null_float (修订1)": n_null_float, "window_tail": n_tail,
                      "no_price_or_quintile": n_no_price, "no_controls": n_no_controls},
        "event_mean_pp": round(ev_mean, 3), "control_mean_pp": round(ct_mean, 3),
        "J1": {"net_pp": round(net, 3), "ci95": [round(ci_low, 3), round(ci_high, 3)],
                "threshold_pp": PREREG["J1_threshold_pp"], "pass": j1},
        "J2": {"yearly": yearly, "positive_years": pos_years,
                "need": f">={PREREG['J2_min_positive_years']}/7", "pass": j2},
        "J3": {"event_mean": round(ev_mean, 3), "control_mean": round(ct_mean, 3),
                "ratio_need": PREREG["J3_dominance_ratio"], "pass": j3},
        "sensitivity_nullfloat": {"n": len(sens_nullfloat),
                                    "raw_exit_gain_mean_pp": round(sum(sens_nullfloat) / len(sens_nullfloat), 3) if sens_nullfloat else None},
        "bootstrap": {"n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED},
        "verdict": verdict,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = Path(args.out_dir) / f"lhb_exit_verdict_{stamp}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n判决已落盘: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
