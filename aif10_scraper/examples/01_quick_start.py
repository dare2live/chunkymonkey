"""快速上手 — 拉一个 reportName 全量数据."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from aif10_scraper import fetch_report, REPORT_BY_NAME, get_report
from aif10_scraper.registry import stats


def progress(page, total, rows):
    if total == 0 or page % 20 == 0 or page == total:
        print(f"  page {page}/{total} rows={rows}")


def main():
    print(f"=== 注册表统计 ===")
    s = stats()
    print(f"reportName 总数: {s['total']}")
    print(f"按模块: {s['by_module']}")
    print(f"按频率: {s['by_frequency']}")
    print()

    # 例 1: 估值分位 (中等量)
    print("=== 例 1: 估值分位全量 ===")
    spec = get_report("RPT_STOCKVALUATIONTANTILE")
    print(f"  {spec.subname} ({spec.name})")
    print(f"  主键: {spec.key}")
    print(f"  频率: {spec.frequency}")
    r = fetch_report(
        "RPT_STOCKVALUATIONTANTILE",
        mode="concurrent", concurrency=10,
        progress_callback=progress,
    )
    print(f"\n  → {r['total_rows']} 行 / {r['elapsed_s']}s")
    if r["rows"]:
        sample = r["rows"][0]
        print(f"  字段示例: {list(sample.keys())}")
        print(f"  样本: {sample}")
    print()

    # 例 2: 单股 16 模块全 scan (按 SECUCODE 过滤)
    print("=== 例 2: 单股 (600519) 估值 + 户数 + 同行 ===")
    for report_name in [
        "RPT_STOCKVALUATIONTANTILE",
        "RPT_F10_EH_HOLDERNUM",
        "RPT_PCF10_INDUSTRY_CVALUE",
    ]:
        spec = get_report(report_name)
        r = fetch_report(
            report_name,
            mode="sync",
            secucode="600519.SH",
        )
        print(f"  {spec.subname:20s} → {r['total_rows']} 行 / {r['elapsed_s']}s")


if __name__ == "__main__":
    main()
