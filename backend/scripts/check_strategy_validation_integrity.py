"""策略验证完整性 gate — 把 8-lens 对抗复审根因 R1/R2 + 判断法典 C-WinReturn 变成可执行规格。

缘起 (2026-06-15 用户: "这些成果要反哺工具确保不再发生, 该做gate做gate"):
  R1 (验证空间 ⟂ 盈利空间): 验证闸的 null/度量建在 rank 上, 数学上看不见 long-only 绝对收益 ->
     必须有对称可交易性门 + 转正章必须看过含成本绝对收益。
  R2 (信号 != 可交易头寸): 回测引擎必须是 execution-aware (涨跌停/非对称成本/容量/T+1 open), 非 close 假成交。
  C-WinReturn: 胜率诊断量, 收益率+max_dd 目标量, 联合验收 (胜率×盈亏比)。
owner=docs/strategy_validation_contract.md (判断法典节) + analysis/design_deficiencies_extension2_20260615.md。
本 gate 是"P0 制度 = P1 引擎验收尺": engine_execution_aware 在引擎重建 (P1) 前为 FAIL = 预期的红色规格。

用法: python backend/scripts/check_strategy_validation_integrity.py [--check]  -> stdout JSON {checks, overall}。
red->green 实证: 删掉 experiment_harness 的 tradability_verdict 跑本 gate 必转 FAIL (反例守门真触发)。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _repo() -> Path:
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        if top:
            return Path(top)
    except Exception:  # rule-compliance: ok evidence=git 不可用时有意回退到 __file__ 相对路径 (非吞错, 下行确定性兜底)
        return Path(__file__).resolve().parents[2]
    return Path(__file__).resolve().parents[2]


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _has_all(text: str, markers: list[str]) -> list[str]:
    """返回缺失的 marker (空 = 全有)。"""
    return [m for m in markers if m not in text]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="输出 JSON (默认也输出 JSON, 保持与其他 gate 一致)")
    ap.parse_args(argv)
    repo = _repo()
    be = repo / "backend"

    harness = _read(be / "services" / "experiment_harness.py")
    store = _read(be / "services" / "experiment_store.py")

    checks: dict[str, dict] = {}

    # --- R1-a: 对称可交易性门 (anomaly 单边 -> 补 IC_POSITIVE_BUT_UNTRADABLE) ---
    miss = _has_all(harness, ["def tradability_verdict", "IC_POSITIVE_BUT_UNTRADABLE"])
    checks["anomaly_symmetric"] = {
        "verdict": "PASS" if not miss else "FAIL", "missing": miss,
        "what": "experiment_harness 须有对称门 tradability_verdict (IC>0 但含成本收益<=0 -> IC_POSITIVE_BUT_UNTRADABLE)",
        "deficiency": "N2/R1"}

    # --- R1-b: 转正章必须看过钱 (confirmed_by_owner=1 须带含成本绝对收益证据) ---
    miss = _has_all(store, ["_has_money_evidence", "confirmed_by_owner and not"])
    checks["promotion_needs_money"] = {
        "verdict": "PASS" if not miss else "FAIL", "missing": miss,
        "what": "experiment_store.record_verdict 须强制 confirmed_by_owner=1 带含成本 net_return 证据 (否则 raise)",
        "deficiency": "N3/R1 自欺死"}

    # --- C-WinReturn: 联合验收门 (胜率诊断量 + 收益/max_dd 目标量 + 盈亏比/期望) ---
    miss = _has_all(harness, ["def kpi_verdict", "positive_expectancy", "payoff_ratio", "诊断量"])
    checks["kpi_joint_codex"] = {
        "verdict": "PASS" if not miss else "FAIL", "missing": miss,
        "what": "experiment_harness 须有 kpi_verdict 联合门 (年化 AND max_dd AND 月胜率 AND 胜率×盈亏比期望)",
        "deficiency": "C-WinReturn"}

    # --- R2: 回测引擎 execution-aware (涨跌停/非对称成本/容量/T+1 open) ---
    #   验证器纪律 (mythos §13): 不取多文件并集 (旧 portfolio_backtest.py 残留 marker 会污染判定),
    #   要求**存在一个单文件**满足全部 4 维 (= 一个完整的 canonical execution-aware 引擎)。
    engine_files = sorted((be / "services").glob("*backtest*.py"))
    # marker 组 (每组命中任一即该维满足): 涨跌停 / 非对称成本 / 容量冲击 / T+1 open 入场
    groups = {
        "limit_board": ["limit_up", "涨停", "price_limit", "一字"],
        "asymmetric_cost": ["stamp", "印花", "asymmetric", "sell_cost", "buy_cost"],
        "capacity_impact": ["capacity", "adv", "impact", "容量", "冲击"],
        "t1_open_entry": ["open_entry", "t1_open", "vwap", "open 入场", "open价", "entry_at_open"],
    }
    best_file, best_miss = None, list(groups)
    for f in engine_files:
        t = _read(f)
        miss = [g for g, ms in groups.items() if not any(m in t for m in ms)]
        if len(miss) < len(best_miss):
            best_file, best_miss = f.name, miss
    checks["engine_execution_aware"] = {
        "verdict": "PASS" if (engine_files and not best_miss) else "FAIL",
        "missing_dims": best_miss, "best_engine_file": best_file,
        "engine_files": [f.name for f in engine_files],
        "what": "须存在单一 execution-aware 引擎: 涨跌停剔篮 + 非对称成本栈 + 容量/冲击 + T+1 open 入场 (非 close 假成交)",
        "deficiency": "N8/N10/N13/N14/R2", "closes_in_phase": "P1 (引擎重建)"}

    overall = "PASS" if all(c["verdict"] == "PASS" for c in checks.values()) else "FAIL"
    pending_p1 = [k for k, c in checks.items() if c["verdict"] == "FAIL" and c.get("closes_in_phase")]
    out = {"gate": "strategy_validation_integrity", "overall": overall, "checks": checks,
           "pending_by_phase": pending_p1,
           "note": "engine_execution_aware 在 P1 引擎重建前为 FAIL = 预期红色规格 (P0 gate 即 P1 验收尺)"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
