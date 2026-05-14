"""CLI 工具: 算 observed sharpe 在跨 study N trials 下的 Deflated SR p-value.

Bailey & López de Prado (2014) "The Deflated Sharpe Ratio".

用途: 跑 Optuna study 后, 把 best OOS sharpe + 项目至今累积 trials 输入,
       判断这个 sharpe 是真 alpha 还是 multiple testing 选出来的运气数字.

用法 (当前 baseline 例子):
    PYTHONPATH=backend python backend/scripts/check_deflated_sharpe.py \\
        --sharpe 0.29 --trials 200 --obs 509

输出 exit code: 0 = 显著 / 1 = 不显著.
"""
from __future__ import annotations

import argparse
import sys

# evidence: 公式 from Bailey & López de Prado (2014) eq. 12 + eq. 9
from services.optimization.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_sharpe_for_significance,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deflated Sharpe Ratio check (Bailey & López de Prado 2014)"
    )
    parser.add_argument("--sharpe", type=float, required=True,
                        help="Observed OOS Sharpe Ratio")
    parser.add_argument("--trials", type=int, required=True,
                        help="Cumulative N trials across all studies")
    parser.add_argument("--obs", type=int, required=True,
                        help="N observations (trade days / months / trades)")
    parser.add_argument("--variance", type=float, default=1.0,
                        help="V[SR] prior (default 1.0)")
    parser.add_argument("--skew", type=float, default=0.0,
                        help="Returns skewness (default 0 = normal)")
    parser.add_argument("--kurt", type=float, default=3.0,
                        help="Returns kurtosis (default 3 = normal)")
    parser.add_argument("--threshold", type=float, default=0.95,
                        help="Decision threshold (default 0.95)")
    args = parser.parse_args()

    e_max = expected_max_sharpe(args.trials, args.variance)
    p = deflated_sharpe_ratio(
        args.sharpe, args.trials, args.obs,
        sharpe_variance=args.variance,
        skewness=args.skew,
        kurtosis=args.kurt,
    )
    min_sr = min_sharpe_for_significance(
        args.trials, args.obs, args.threshold, args.variance
    )

    print("━━━ Deflated Sharpe Ratio (Bailey & López de Prado 2014) ━━━")
    print(f"  Observed SR:            {args.sharpe:.4f}")
    print(f"  Cumulative N trials:    {args.trials}")
    print(f"  Observations T:         {args.obs}")
    print(f"  V[SR] prior:            {args.variance:.2f}")
    print(f"  Skewness / Kurtosis:    {args.skew:.2f} / {args.kurt:.2f}")
    print()
    print(f"  Expected max SR (H0):   {e_max:.4f}    ← N 随机策略 best 期望")
    print(f"  Deflated SR p-value:    {p:.4f}    ← p > {args.threshold} 才显著")
    print(f"  Min SR for p>={args.threshold:.2f}:    {min_sr:.4f}    ← observed 需 >= 此值")
    print()
    if p < args.threshold:
        gap = min_sr - args.sharpe
        print(f"  [DECISION] ❌ p={p:.4f} < {args.threshold} — "
              f"observed SR 大概率是 multiple testing 噪音, 不是真 alpha")
        print(f"             差距: observed {args.sharpe:.4f} vs min {min_sr:.4f} "
              f"(短 {gap:.4f})")
        return 1
    print(f"  [DECISION] ✓ p={p:.4f} >= {args.threshold} — observed SR 统计显著")
    return 0


if __name__ == "__main__":
    sys.exit(main())
