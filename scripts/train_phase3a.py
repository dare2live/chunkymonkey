"""Phase 3a A/B 对照训练：TDX 一级行业 one-hot 开 / 关两组。

对比同样 universe + hyperparams 下，加入行业 one-hot 特征对模型 IC / RankIC 的影响。

用法：
    python3 scripts/train_phase3a.py          # 完整 A/B 两组（~30 min）
    python3 scripts/train_phase3a.py --off-only    # 仅跑关组（对照 baseline）
    python3 scripts/train_phase3a.py --on-only     # 仅跑开组（审计前行为）
    python3 scripts/train_phase3a.py --json-out results.json   # 结果落地

结果同时落到 qlib_model_state（含两条新 model_id）和 stdout 对照表。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("phase3a")


def _base_params() -> dict:
    # 与 baseline lgb_20260413_105427 对齐：active_a_stock universe + 默认超参 + 所有自定义因子
    return {
        "universe_source": "active_a_stock",
        "sample_stock_limit": 0,
        "num_boost_round": 500,
        "early_stopping_rounds": 50,
        "num_leaves": 64,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "use_alpha158": True,
        "use_financial": True,
        "use_institution": True,
        "use_turtle": True,
        "use_quality": True,
        "use_stage": True,
    }


def _run_arm(conn, *, arm_name: str, use_industry_onehot: bool) -> dict:
    from services.qlib_full_engine import train_full_model

    params = dict(_base_params(), use_industry_onehot=use_industry_onehot)
    logger.info("=== 训练 arm=%s (use_industry_onehot=%s) ===", arm_name, use_industry_onehot)
    result = train_full_model(conn, params=params)
    summary = {
        "arm": arm_name,
        "use_industry_onehot": use_industry_onehot,
        "model_id": result.get("model_id"),
        "status": result.get("status"),
        "stock_count": result.get("stock_count"),
        "factor_count": result.get("factor_count"),
        "ic_mean": result.get("ic_mean"),
        "rank_ic_mean": result.get("rank_ic_mean"),
        "test_top50_avg_return": result.get("test_top50_avg_return"),
    }
    logger.info("arm=%s done: %s", arm_name, summary)
    return summary


def _compare(on: Optional[dict], off: Optional[dict]) -> dict:
    """简单对照：计算 on-off 差值，说明开启行业 one-hot 的增益."""
    diff: dict = {}
    for k in ("ic_mean", "rank_ic_mean", "test_top50_avg_return", "factor_count", "stock_count"):
        on_v = (on or {}).get(k)
        off_v = (off or {}).get(k)
        if on_v is not None and off_v is not None:
            diff[f"{k}_on_minus_off"] = round(on_v - off_v, 5) if isinstance(on_v, (int, float)) else None
    return diff


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--on-only", action="store_true", help="只跑开组")
    group.add_argument("--off-only", action="store_true", help="只跑关组（对照 baseline）")
    parser.add_argument("--json-out", default="", help="结果 JSON 落地路径")
    args = parser.parse_args()

    from services.db import get_conn

    conn = get_conn(timeout=1200)
    results: dict = {"on": None, "off": None, "diff": None}
    try:
        if not args.off_only:
            results["on"] = _run_arm(conn, arm_name="on", use_industry_onehot=True)
        if not args.on_only:
            results["off"] = _run_arm(conn, arm_name="off", use_industry_onehot=False)
    finally:
        conn.close()

    results["diff"] = _compare(results.get("on"), results.get("off"))

    # 对照表 stdout
    print("\n" + "=" * 72)
    print("A/B 对照：行业 one-hot 开 / 关")
    print("=" * 72)
    for arm in ("on", "off"):
        r = results.get(arm)
        if r:
            print(f"[{arm:3s}] model={r['model_id']}  n_stocks={r['stock_count']}  "
                  f"IC={r['ic_mean']}  RankIC={r['rank_ic_mean']}  "
                  f"top50_ret={r['test_top50_avg_return']}")
    if results["diff"]:
        print("\n差值（on - off）:")
        for k, v in results["diff"].items():
            print(f"  {k}: {v}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已写入: {args.json_out}")


if __name__ == "__main__":
    main()
