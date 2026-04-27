"""压力测试: 探测妙想 F10 单 IP 并发 + QPS 上限.

用 RPT_F10_EH_HOLDERNUM (1473 页 / 73 万行) 做基准, 因为它最大.

测试矩阵:
- concurrency: 1, 2, 5, 10, 20, 30, 50
- 每组拉前 50 页 (够大, 但不耗时)
- 记录: 总耗时, 平均 page time, 失败数, rc=100 (rate-limit)

输出建议:
- 最高 QPS 不触发 rc=100 / 或者 < 5% 失败率
- 推荐 concurrency
"""
from __future__ import annotations

import sys
from pathlib import Path

# 加 aif10_scraper 到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
from aif10_scraper import AIF10Client, fetch_all_pages_concurrent
from aif10_scraper.client import default_client


REPORT = "RPT_F10_EH_HOLDERNUM"
PAGE_SIZE = 500
TEST_PAGES = 50      # 每组测试拉的页数
RATE_LIMITS = [0.0]  # 不限速

# 注: 用本地 default_client 共享 Session, 模拟真实使用


def test_concurrency_levels(levels: list[int] = None) -> dict:
    """逐个测试不同 concurrency 等级."""
    if levels is None:
        levels = [1, 2, 5, 10, 20]

    print(f"=== aif10 妙想 F10 并发压力测试 ===")
    print(f"目标: {REPORT} 前 {TEST_PAGES} 页 × {PAGE_SIZE} 行/页")
    print(f"并发等级: {levels}")
    print()

    results = {}
    for c in levels:
        print(f"--- concurrency={c} ---", flush=True)
        # 重新建 client, 不复用 session 避 cookie 干扰
        cli = AIF10Client(retry=2, rate_limit=0.0)

        t0 = time.time()
        try:
            rows = fetch_all_pages_concurrent(
                REPORT,
                page_size=PAGE_SIZE,
                max_pages=TEST_PAGES,
                concurrency=c,
                client=cli,
            )
            elapsed = time.time() - t0
            n_rows = len(rows)
            qps = TEST_PAGES / elapsed if elapsed > 0 else 0
            results[c] = {
                "ok": True,
                "elapsed_s": round(elapsed, 2),
                "rows": n_rows,
                "qps_pages": round(qps, 2),
                "rows_per_sec": round(n_rows / elapsed, 1),
            }
            print(f"  耗时={elapsed:.2f}s 行数={n_rows} QPS={qps:.2f} 页/秒 速度={n_rows/elapsed:.0f} 行/秒")
        except Exception as exc:
            elapsed = time.time() - t0
            results[c] = {
                "ok": False,
                "elapsed_s": round(elapsed, 2),
                "error": str(exc)[:200],
            }
            print(f"  ❌ 失败 elapsed={elapsed:.2f}s: {str(exc)[:100]}")
        finally:
            cli.close()

        # 间隔 5 秒避免 rate-limit 累积
        time.sleep(5)

    print()
    print("=== 汇总 ===")
    print(f"{'concurrency':>12} {'elapsed':>10} {'rows':>8} {'页/秒':>10} {'行/秒':>10} {'状态':>6}")
    for c, r in results.items():
        if r["ok"]:
            print(f"{c:>12} {r['elapsed_s']:>9.2f}s {r['rows']:>8} {r['qps_pages']:>10.2f} {r['rows_per_sec']:>10.0f} {'✓':>6}")
        else:
            print(f"{c:>12} {r['elapsed_s']:>9.2f}s {'-':>8} {'-':>10} {'-':>10} {'❌':>6}")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="1,2,5,10,20",
                        help="并发等级, 逗号分隔")
    parser.add_argument("--pages", type=int, default=50,
                        help="每组测试拉的页数 (默认 50)")
    args = parser.parse_args()

    if args.pages != TEST_PAGES:
        TEST_PAGES = args.pages

    levels = [int(x.strip()) for x in args.levels.split(",") if x.strip()]
    test_concurrency_levels(levels)
