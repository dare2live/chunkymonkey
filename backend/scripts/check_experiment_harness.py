#!/usr/bin/env python3
"""G3 门#1 散落死: experiment_*.py 必须走 harness/留档层, 禁裸跑无留档。

owner = docs/conditional_alpha_program.md §4 (3 道强制门之一)。
根因 (审计 agent a3b2fbfa, 2026-06-16): 旧重型 family 被弃用沦孤儿后, 实验退化成"一脚本一实验"
(11 脚本/1 天), 且 47 个 analysis/*.json 与 24 库 verdict 留档双轨未收敛。本门防新 alpha 实验
裸跑不留档 (散落死) — 把结果只丢 analysis/*.json 而不进 experiment_store 唯一真相源。

合规判据: 每个 backend/scripts/experiment_*.py 必须出现以下任一 token (= 走 harness 或直接留档):
  phaseD_signal_eval / experiment_store / record_verdict / record_ic_cell / open_store

用法:
  python backend/scripts/check_experiment_harness.py            # 全扫 (moth/doctor/chain)
  python backend/scripts/check_experiment_harness.py --staged   # 仅 staged (safe_commit Step 3.7)
退出码: 0 全合规; 1 有裸跑脚本。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "backend" / "scripts"

# 走 harness (phaseD_signal_eval) 或直接 experiment_store 留档 API — 任一即合规。
HARNESS_TOKENS = (
    "phaseD_signal_eval", "experiment_store", "record_verdict", "record_ic_cell", "open_store",
)
# 显式豁免 (非 alpha 实验 / 纯编排, 不产 verdict)。当前空 — 实测 15/15 已合规。
# 新增豁免必须写理由 + owner review, 不许图省事白名单吞错 (mythos §14 防作弊)。
EXEMPT: set[str] = set()


def is_compliant_text(text: str, name: str) -> bool:
    """纯函数: 给定脚本文本+文件名判合规 (供测试逐字红绿)。"""
    if name in EXEMPT:
        return True
    return any(tok in text for tok in HARNESS_TOKENS)


def _is_compliant(path: Path) -> bool:
    return is_compliant_text(path.read_text(encoding="utf-8", errors="ignore"), path.name)


def _staged_experiment_files() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=REPO, capture_output=True, text=True
    ).stdout
    return [
        REPO / line for line in out.splitlines()
        if line.startswith("backend/scripts/experiment_") and line.endswith(".py")
    ]


def _all_experiment_files() -> list[Path]:
    return sorted(SCRIPTS.glob("experiment_*.py"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true", help="仅检查 staged 的 experiment_*.py")
    args = parser.parse_args(argv)

    files = _staged_experiment_files() if args.staged else _all_experiment_files()
    if not files:
        print("[experiment-harness] 0 个 experiment_*.py 待检 (PASS)")
        return 0

    missing = [f for f in files if not _is_compliant(f)]
    for f in files:
        print(f"  {'[MISS]' if f in missing else '[OK]  '} {f.name}")
    if missing:
        print(f"\nERROR: {len(missing)} 个 experiment 脚本裸跑无留档 (散落死门, docs/conditional_alpha_program.md §4 门1)。")
        print("修法: 实验走 services/phaseD_signal_eval.evaluate_signal (唯一 harness), 或直接 import experiment_store 留档。")
        print(f"合规 token 任一: {', '.join(HARNESS_TOKENS)}。")
        return 1
    print(f"[experiment-harness] {len(files)} 脚本全合规 (PASS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
