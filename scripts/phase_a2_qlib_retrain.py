"""Phase A2 — Qlib follow 模型第 4 次重训（合同负债覆盖 88.3% 后）。

跟 HANDOFF Phase 4c 同样窗口（train_end=20260301），看 IC 是否有变化。
然后再用 train_end=今天 跑一次最新窗口。
"""

import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.db import get_conn
from services.market_db import get_market_conn
from services.qlib_follow_engine import (
    FollowTrainConfig,
    train_follow_model,
    extract_training_matrix,
)


def main():
    conn = get_conn()
    mkt_conn = get_market_conn()
    cfg = FollowTrainConfig()  # 默认参数
    print(f"[配置] {cfg}\n")

    # 先做一个特征覆盖率体检（不训练）
    print("=" * 90)
    print("【Phase A2 / Step 0】特征填充率体检")
    print("=" * 90)
    samples, feature_names = extract_training_matrix(
        conn, mkt_conn,
        window_start="20230101", window_end="20260101",
    )
    print(f"训练样本数: {len(samples)}")
    print(f"特征数: {len(feature_names)}")
    print()
    print("各特征非空覆盖率（≥40% 才有用）:")
    for col in feature_names:
        non_null = sum(1 for s in samples if s.get(col) is not None)
        pct = 100 * non_null / len(samples) if samples else 0
        marker = "★" if pct < 40 else " "
        print(f"  {marker} {col:<40} {pct:>5.1f}%  ({non_null}/{len(samples)})")
    print()

    # HANDOFF Phase 4c valid=3088 对应窗口：train_end=20260101 + valid_months=3
    # (valid=202510-202601, 三季报密集期)
    print("=" * 90)
    print("【Phase A2 / Step 1】train_end=20260101 / valid=202510-202601（季报密集 valid）")
    print("=" * 90)
    res1 = train_follow_model(conn, mkt_conn, config=cfg, train_end_date="20260101")
    print(f"  status:       {res1.get('status')}")
    print(f"  n_train:      {res1.get('n_train')}")
    print(f"  n_valid:      {res1.get('n_valid')}")
    print(f"  Valid IC:     {res1.get('valid_ic')}  (HANDOFF Phase 4c: -0.0196)")
    print(f"  Valid R2:     {res1.get('valid_r2')}")
    print(f"  Valid MAE:    {res1.get('valid_mae')}")
    fi = res1.get("feature_importance") or {}
    if fi:
        top = sorted(fi.items(), key=lambda kv: -kv[1])[:15]
        print()
        print("  Top 15 特征重要性:")
        for name, imp in top:
            print(f"    {name:<40} {imp}")
    print()

    # 交叉对照：train_end=20250801（valid=202505-202508，含 202508 半年报 2567 事件）
    print("=" * 90)
    print("【Phase A2 / Step 2】train_end=20250801 / valid=202505-202508（半年报密集 valid）")
    print("=" * 90)
    res2 = train_follow_model(conn, mkt_conn, config=cfg, train_end_date="20250801")
    print(f"  status:       {res2.get('status')}")
    print(f"  n_train:      {res2.get('n_train')}")
    print(f"  n_valid:      {res2.get('n_valid')}")
    print(f"  Valid IC:     {res2.get('valid_ic')}")
    print(f"  Valid R2:     {res2.get('valid_r2')}")

    print()
    print("=" * 90)
    print("【结论】")
    print("=" * 90)
    ic1 = res1.get("valid_ic")
    ic2 = res2.get("valid_ic")
    print(f"  Step 1 (20260101 season valid): IC = {ic1}")
    print(f"  Step 2 (20250801 season valid): IC = {ic2}")
    best_ic = max((ic for ic in (ic1, ic2) if ic is not None), default=None)
    if best_ic is None:
        print("  ✗ 无可用 IC")
    elif best_ic > 0.05:
        print(f"  ✓ 最佳 IC ({best_ic:.4f}) 突破 0.05 — Qlib 升级为主力候选")
    elif best_ic > 0:
        print(f"  ⚠ 最佳 IC ({best_ic:.4f}) 转正但 < 0.05 — 仅可作为「第二意见」展示")
    else:
        print(f"  ✗ 最佳 IC ({best_ic:.4f}) 仍为负 — Qlib 不可用作合成评分，封存为骨架")

    conn.close()
    mkt_conn.close()


if __name__ == "__main__":
    main()
