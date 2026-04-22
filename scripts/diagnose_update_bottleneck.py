"""诊断智能更新各 step 的耗时，不经 HTTP，不并发 32 只股票同步（那是外部慢）。

用法:
    python3 scripts/diagnose_update_bottleneck.py

只跑"计算/聚合"类 step（不拉外部数据），精准找内部瓶颈。
输出各 step 耗时 + 返回值。
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend"))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# 关键日志提升回 INFO
logging.getLogger("cm-api").setLevel(logging.INFO)


def timed(name, fn, *args, **kwargs):
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        ok = True
        err = None
    except Exception as e:  # noqa: BLE001
        result = None
        ok = False
        err = f"{type(e).__name__}: {e}"
    elapsed = time.time() - t0
    print(f"{'✓' if ok else '✗'} {name:32s} {elapsed:8.2f}s  result={result if ok else err}")
    return result, elapsed, ok, err


def main():
    from services.db import get_conn

    conn = get_conn(timeout=120)

    print("=" * 70)
    print("智能更新计算步骤耗时诊断")
    print("=" * 70)

    # 事件计算 + 收益（审计 5.5 增量化后应该很快）
    from services.return_engine import calculate_returns
    timed("calculate_returns (incremental)", calculate_returns, conn)

    # 画像 / 行业统计 / 趋势
    from routers.updater import (
        _step_build_profiles_sync,
        _step_build_industry_stat_sync,
        _step_build_trends_sync,
    )
    from services.holdings import build_current_relationship
    from services.financial_client import calc_financial_derived
    from services.stock_stage_engine import build_stock_stage_features
    from services.stock_forecast_engine import build_stock_forecast_features
    from services.external_attention import sync_external_attention_snapshot

    timed("build_current_rel", build_current_relationship, conn)
    timed("build_profiles", _step_build_profiles_sync, conn)
    timed("build_industry_stat", _step_build_industry_stat_sync, conn)
    timed("build_trends", _step_build_trends_sync, conn)
    timed("calc_financial_derived", calc_financial_derived, conn)
    timed("build_stage_features", build_stock_stage_features, conn)
    timed("build_forecast_features", build_stock_forecast_features, conn)
    timed("build_external_attention", sync_external_attention_snapshot, conn)

    # 评分
    from services.scoring import calculate_institution_scores, calculate_stock_scores
    timed("calc_inst_scores", calculate_institution_scores, conn)
    timed("calc_stock_scores", calculate_stock_scores, conn)

    conn.close()
    print("=" * 70)


if __name__ == "__main__":
    main()
