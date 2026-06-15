#!/usr/bin/env python3
"""L0 裸K线基准驱动 (Tier-1 RankIC, owner=analysis/l0_bare_kline_baseline_spec_20260614.md §5)。

链路: v_price_kline_qfq (PIT 复权 K线) -> active 公式连续 PIT 特征 -> 前向收益标注 ->
walk-forward expanding_monthly OOS RankIC -> 写 experiment_store (consumer_id=L0_baseline_<formula>)。

**防泄露固化** (用户: "注意固化在流程里使用防泄露工具") — 三道门内联, 非手动非可跳:
  门1 PIT 行为门 (pit_guard.assert_pit_clean): feature[i] 对追加未来 bar 不变, 否则 BLOCK (lookahead 死)。
  门2 切分纪律 (leakage_detect.check_split_discipline): embargo>=horizon + 时间切, FAIL 则 BLOCK。
  门3 异常红线 (leakage_detect.check_metric_anomaly): RankIC>0.3 = §4.2 警报, ALARM 不自动采信 -> 走核查。
每门结果落 experiment_pit_audit_log (每步留档非仅最终)。L0 已注册 leakage_consumers.yaml (safe_commit 拦改动)。

本驱动用**默认参数**测基准 = 测量 (非 Optuna 寻优, 不触 grill-gate)。寻优 RUN (best-OOS-params)
留 task#17 (pre-reg + grill 后)。用法:
  python backend/scripts/experiment_l0_baseline.py --start 2023-01-01 --horizon 5 --run-id l0_v1
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.formula_engine.features import ACTIVE_FORMULAS, extract_feature  # noqa: E402
from services.leakage_detect import check_metric_anomaly, check_split_discipline  # noqa: E402
from services.optimization.formula_param_search import search_formula  # noqa: E402
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic  # noqa: E402
from services.portfolio_walk_forward.pit_guard import assert_pit_clean  # noqa: E402

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"
RANKIC_RED_LINE = 0.30  # evidence: CLAUDE.md §4.2 absolute 红线 RankIC>0.3 = leakage 警报


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db(alias: str) -> Path:
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return REPO / m["databases"][alias]["path"]


def load_kline(start: str, end: str | None, limit_stocks: int) -> dict[str, dict]:
    """v_price_kline_qfq -> {code: {date,close,high,low,open,volume,amount}} (按 code,date 升序, PIT)。

    2026-06-15 (N9 修): 补 open/volume/amount —— execution-aware 引擎需 open (T+1 入场价) + volume/amount
    (容量诊断) + 涨跌停一字板判定。close/high/low 保持兼容老消费者 (additive, 不破坏 per_stage_ic 等)。
    """
    src = _db("market")
    where = f"date >= '{start}'" + (f" AND date <= '{end}'" if end else "")
    conn = duck_connect(str(src), read_only=True)
    try:
        if limit_stocks > 0:
            codes = [r[0] for r in conn.execute(
                f"SELECT DISTINCT code FROM v_price_kline_qfq WHERE {where} ORDER BY code LIMIT {limit_stocks}"
            ).fetchall()]
            where += " AND code IN ('" + "','".join(codes) + "')"
        rows = conn.execute(
            f"SELECT code, date, close, high, low, open, volume, amount FROM v_price_kline_qfq WHERE {where} "
            "ORDER BY code, date"
        ).fetchall()
    finally:
        conn.close()
    by_code: dict[str, dict] = defaultdict(
        lambda: {"date": [], "close": [], "high": [], "low": [], "open": [], "volume": [], "amount": []})
    for code, date, close, high, low, open_, volume, amount in rows:
        d = by_code[code]
        d["date"].append(date)
        d["close"].append(close)
        d["high"].append(high)
        d["low"].append(low)
        d["open"].append(open_)
        d["volume"].append(volume)
        d["amount"].append(amount)
    return dict(by_code)


def run_formula(formula: str, by_code: dict[str, dict], horizon: int, embargo: int = 0) -> dict:
    """单公式: PIT 门 -> 特征+前向收益 -> panel -> oos_rank_ic(embargo) + 异常门。返回结果 + 门判。"""
    # 门1 PIT 行为门: 抽样 50 股核证 (active 公式是单股确定性函数, 函数级 PIT 与股无关, 50 足;
    # 假设: L0 公式必须单股 PIT-legal 不依赖股身份/跨股统计。若未来加跨股特征须扩抽样 + 加集成测试)
    sample = [c for c in list(by_code)[:50] if len(by_code[c]["close"]) >= 30]  # evidence: 单股确定性函数抽样核证
    pit_reports = [assert_pit_clean(lambda b: extract_feature(formula, b), by_code[c]) for c in sample]
    pit_clean = all(r["clean"] for r in pit_reports)

    panel: list[PanelRow] = []
    if pit_clean:
        for code, bars in by_code.items():
            if len(bars["close"]) < horizon + 2:
                continue
            feat = extract_feature(formula, bars)
            fwd = forward_returns(bars["date"], bars["close"], horizon)
            for i, date in enumerate(bars["date"]):
                if feat[i] is not None and fwd[i] is not None:
                    panel.append(PanelRow(date=date, code=code, feature=feat[i], fwd_ret=fwd[i]))
    ic = oos_rank_ic(panel, embargo_days=embargo) if panel else {"oos_rank_ic": None, "ic_ir": None, "n_windows": 0, "n_days": 0}
    anomaly = check_metric_anomaly({"rankic": ic["oos_rank_ic"]}, red_lines={"rankic": RANKIC_RED_LINE})
    return {"formula": formula, "pit_clean": pit_clean,
            "pit_violations": [v for r in pit_reports for v in r["violations"]][:3],
            "ic": ic, "anomaly": anomaly, "n_obs": len(panel)}


def _run_search(by_code, formulas, horizon, embargo, run_id, data_snapshot, store,
                prereg_hash, split) -> int:
    """寻参模式 (#17): 每公式网格寻 best-OOS-params (受 plan_validator 闸 + DSR), 同 3 门固化。"""
    out_rows = []
    for f in formulas:
        sample = [c for c in list(by_code)[:50] if len(by_code[c]["close"]) >= 30]
        pit_clean = all(assert_pit_clean(lambda b, _f=f: extract_feature(_f, b), by_code[c])["clean"]
                        for c in sample)
        sr = search_formula(f, by_code, horizon=horizon, embargo=embargo) if pit_clean else {}
        anomaly = check_metric_anomaly({"rankic": sr.get("best_oos_rank_ic")},
                                       red_lines={"rankic": RANKIC_RED_LINE})
        out_rows.append({"formula": f, "pit_clean": pit_clean, "sr": sr, "anomaly": anomaly})

    leaked = [r["formula"] for r in out_rows if not r["pit_clean"]]
    if leaked:
        print(f"[BLOCK] 门1 PIT 行为门: {leaked} lookahead, 不写库")
        return 3

    conn = duck_connect(str(store), read_only=False)
    try:
        conn.execute("SET enable_progress_bar=false")
        for r in out_rows:
            sr, cid = r["sr"], f"L0_search_{r['formula']}"
            for metric, val in [("oos_rank_ic", sr.get("best_oos_rank_ic")), ("ic_ir", sr.get("best_ic_ir"))]:
                conn.execute(
                    "INSERT OR REPLACE INTO fact_consumer_alpha_ic_scan "
                    "(data_snapshot, consumer_id, metric, value, n_windows, run_id, built_at) VALUES (?,?,?,?,?,?,?)",
                    [data_snapshot, cid, metric, val, sr.get("n_trials"), run_id, _utc()])
            for step, passed, detail in [
                    ("pit_behavioral", r["pit_clean"], {"clean": r["pit_clean"]}),
                    ("metric_anomaly", r["anomaly"]["verdict"] == "CLEAN", r["anomaly"]),
                    ("dsr_deflate", (sr.get("dsr_pvalue") or 0) > 0.95, {"dsr_pvalue": sr.get("dsr_pvalue"), "n_trials": sr.get("n_trials")})]:
                conn.execute(
                    "INSERT OR REPLACE INTO experiment_pit_audit_log (log_id, run_id, step, check_name, passed, detail_json, ts) VALUES (?,?,?,?,?,?,?)",
                    [f"{run_id}_{r['formula']}_{step}", run_id, step, f"{r['formula']}:{step}",
                     1 if passed else 0, json.dumps(detail, ensure_ascii=False, default=str), _utc()])
        conn.execute(
            "INSERT OR REPLACE INTO experiment_pit_audit_log (log_id, run_id, step, check_name, passed, detail_json, ts) VALUES (?,?,?,?,?,?,?)",
            [f"{run_id}_split", run_id, "split_discipline", "embargo>=horizon", 1, json.dumps(split, ensure_ascii=False), _utc()])
        anomalies = [r["formula"] for r in out_rows if r["anomaly"]["verdict"] == "ALARM"]
        verdict = "L0_SEARCH_ANOMALY_REVIEW" if anomalies else "L0_SEARCH"
        judges = {r["formula"]: {"best_params": r["sr"].get("best_params"),
                                 "best_oos_rank_ic": r["sr"].get("best_oos_rank_ic"),
                                 "best_ic_ir": r["sr"].get("best_ic_ir"),
                                 "dsr_pvalue": r["sr"].get("dsr_pvalue"),
                                 "n_trials": r["sr"].get("n_trials")} for r in out_rows}
        conn.execute(
            "INSERT OR REPLACE INTO fact_experiment_verdict (verdict_id, family, run_id, verdict, ts, prereg_hash, judges_json, gate_blockers_json, confirmed_by_owner) VALUES (?,?,?,?,?,?,?,?,?)",
            [run_id, "consumer_alpha_validation", run_id, verdict, _utc(), prereg_hash or "",
             json.dumps(judges, ensure_ascii=False, default=str),
             json.dumps({"anomaly_review": anomalies}, ensure_ascii=False), 0])
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    out_dir = store.parent if store.parent.name != "data" else REPO / "analysis"  # 测试用temp store不污染analysis/
    out = out_dir / f"consumer_alpha_verdict_{run_id}.json"
    out.write_text(json.dumps({"run_id": run_id, "verdict": verdict, "ts": _utc(),
                               "data_snapshot": data_snapshot, "prereg_hash": prereg_hash,
                               "horizon": horizon, "embargo": embargo,
                               "results": [{"formula": r["formula"], **r["sr"]} for r in out_rows]},
                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[OK] {verdict} run_id={run_id} (寻参 + 3 门 + DSR)")
    for r in out_rows:
        sr = r["sr"]
        dsr = sr.get("dsr_pvalue")
        sig = "" if dsr is None else (" [DSR显著]" if dsr > 0.95 else " [selection_noise_risk]")
        print(f"  {r['formula']:24s} best OOS RankIC={sr.get('best_oos_rank_ic')} IC_IR={sr.get('best_ic_ir')} "
              f"params={sr.get('best_params')} n_trials={sr.get('n_trials')} DSR_p={dsr}{sig}")
    print(f"  留档: experiment_store (L0_search_*) + {out.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L0 裸K线基准 Tier-1 RankIC (默认参数测量 / --search 寻参; 防泄露三门固化)")
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=goal.md KPI 回测起点 (CLI 默认可覆盖)
    ap.add_argument("--end", default=None)
    ap.add_argument("--horizon", type=int, default=5, help="前向收益天数 (label)")  # rule-compliance: ok evidence=spec forward 期 (CLI 可覆盖, 寻优入 search space)
    ap.add_argument("--embargo", type=int, default=None, help="默认=horizon (embargo>=horizon)")
    ap.add_argument("--limit-stocks", type=int, default=0, help="0=全市场")
    ap.add_argument("--formulas", nargs="*", default=list(ACTIVE_FORMULAS))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--store", default=None)
    ap.add_argument("--search", action="store_true", help="寻参模式 (#17): 网格寻 best-OOS-params")
    ap.add_argument("--prereg-hash", default="", help="冻结判据 hash (写入 verdict, 谄媚死锚)")
    args = ap.parse_args(argv)
    embargo = args.embargo if args.embargo is not None else args.horizon
    mode = "search" if args.search else "baseline"
    run_id = args.run_id or f"l0_{mode}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    # 门2 切分纪律 (run 级, 先于数据): embargo>=horizon + 时间切
    split = check_split_discipline(label_horizon_days=args.horizon, embargo_days=embargo, split_mode="time")
    if split["verdict"] == "FAIL":
        print(f"[BLOCK] 门2 切分纪律 FAIL: {split['problems']}")
        return 2

    print(f"[L0] 加载 K线 start={args.start} limit={args.limit_stocks or 'all'} ...")
    by_code = load_kline(args.start, args.end, args.limit_stocks)
    data_snapshot = max((d for b in by_code.values() for d in b["date"]), default=args.start)
    print(f"[L0] {len(by_code)} 股, data_snapshot={data_snapshot}, mode={mode}")

    if args.search:
        store = Path(args.store) if args.store else _db("experiment_store")
        return _run_search(by_code, args.formulas, args.horizon, embargo, run_id,
                           data_snapshot, store, args.prereg_hash, split)

    results = [run_formula(f, by_code, args.horizon, embargo) for f in args.formulas]

    # 门1 汇总: 任一公式 PIT 泄漏 -> BLOCK (不写库)
    leaked = [r["formula"] for r in results if not r["pit_clean"]]
    if leaked:
        print(f"[BLOCK] 门1 PIT 行为门: {leaked} feature 用未来 bar (lookahead 死), 不写库")
        return 3

    store = Path(args.store) if args.store else _db("experiment_store")
    conn = duck_connect(str(store), read_only=False)
    try:
        conn.execute("SET enable_progress_bar=false")
        for r in results:
            cid = f"L0_baseline_{r['formula']}"
            for metric, val in [("oos_rank_ic", r["ic"]["oos_rank_ic"]), ("ic_ir", r["ic"]["ic_ir"])]:
                conn.execute(
                    "INSERT OR REPLACE INTO fact_consumer_alpha_ic_scan "
                    "(data_snapshot, consumer_id, metric, value, n_windows, run_id, built_at) VALUES (?,?,?,?,?,?,?)",
                    [data_snapshot, cid, metric, val, r["ic"]["n_windows"], run_id, _utc()])
            for step, gate in [("pit_behavioral", {"clean": r["pit_clean"]}),
                               ("metric_anomaly", r["anomaly"])]:
                passed = gate.get("clean", gate.get("verdict") == "CLEAN")
                conn.execute(
                    "INSERT INTO experiment_pit_audit_log (log_id, run_id, step, check_name, passed, detail_json, ts) "
                    "VALUES (?,?,?,?,?,?,?)",
                    [f"{run_id}_{r['formula']}_{step}", run_id, step, f"{r['formula']}:{step}",
                     1 if passed else 0, json.dumps(gate, ensure_ascii=False, default=str), _utc()])
        # split 门留档 (run 级)
        conn.execute(
            "INSERT INTO experiment_pit_audit_log (log_id, run_id, step, check_name, passed, detail_json, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            [f"{run_id}_split", run_id, "split_discipline", "embargo>=horizon",
             1, json.dumps(split, ensure_ascii=False), _utc()])
        anomalies = [r["formula"] for r in results if r["anomaly"]["verdict"] == "ALARM"]
        verdict = "BASELINE_ANOMALY_REVIEW" if anomalies else "BASELINE"
        conn.execute(
            "INSERT OR REPLACE INTO fact_experiment_verdict "
            "(verdict_id, family, run_id, verdict, ts, prereg_hash, judges_json, gate_blockers_json, confirmed_by_owner) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [run_id, "consumer_alpha_validation", run_id, verdict, _utc(), "",
             json.dumps({"formulas": {r["formula"]: r["ic"] for r in results}}, ensure_ascii=False, default=str),
             json.dumps({"anomaly_review": anomalies}, ensure_ascii=False), 0])
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    out_dir = store.parent if store.parent.name != "data" else REPO / "analysis"  # 测试用temp store不污染analysis/
    out = out_dir / f"consumer_alpha_verdict_{run_id}.json"
    out.write_text(json.dumps({
        "run_id": run_id, "verdict": verdict, "ts": _utc(), "data_snapshot": data_snapshot,
        "horizon": args.horizon, "embargo": embargo, "n_stocks": len(by_code),
        "split_gate": split, "results": results,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"\n[OK] {verdict} run_id={run_id} (3 门固化: PIT/split/anomaly)")
    for r in results:
        a = " [ANOMALY]" if r["anomaly"]["verdict"] == "ALARM" else ""
        print(f"  {r['formula']:24s} OOS RankIC={r['ic']['oos_rank_ic']} IC_IR={r['ic']['ic_ir']} "
              f"n_win={r['ic']['n_windows']} n_obs={r['n_obs']}{a}")
    print(f"  留档: experiment_store + {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
