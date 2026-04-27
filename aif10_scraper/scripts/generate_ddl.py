"""一键生成 DDL: 对一批 reportName 拉样本 → 推断 schema → 写入 schema/<name>.sql.

用法:
    # 单个
    python scripts/generate_ddl.py RPT_F10_EH_FREEHOLDERS

    # 整个 module
    python scripts/generate_ddl.py --module shareholder

    # 全部 (慢, 需要 ~5 分钟)
    python scripts/generate_ddl.py --all

    # 指定 dialect
    python scripts/generate_ddl.py --module financial --dialect postgres

输出: schema/<name>.sql 一个 reportName 一文件.
失败的 (404 / 解析错误) 输出 schema/_failed.txt.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aif10_scraper import REPORTS, REPORT_BY_NAME, get_report
from aif10_scraper.batch import fetch_all_pages
from aif10_scraper.client import AIF10Client
from aif10_scraper.orm import generate_ddl
from aif10_scraper.registry import reports_by_module


SCHEMA_DIR = ROOT / "schema"
SCHEMA_DIR.mkdir(exist_ok=True)


def fetch_sample(report_name: str, *, page_size: int, client: AIF10Client) -> list[dict]:
    """拉一页样本.

    部分 reportName 不带 SECUCODE 会返回空 (例如题材 / 高管必须按股查),
    这种情况自动 fallback 到 600519.SH.
    """
    spec = get_report(report_name)
    rows = []
    try:
        rows = fetch_all_pages(
            report_name,
            page_size=page_size,
            max_pages=1,
            client=client,
        )
    except Exception as exc:
        rows = []

    if not rows:
        # fallback: 用茅台试探
        try:
            rows = fetch_all_pages(
                report_name,
                secucode="600519.SH",
                page_size=page_size,
                max_pages=1,
                client=client,
            )
        except Exception:
            pass

    return rows


def generate_one(
    report_name: str,
    *,
    dialect: str,
    page_size: int,
    client: AIF10Client,
) -> tuple[bool, str]:
    """生成一个 report 的 DDL, 写入文件. 返回 (成功?, 信息)."""
    out_path = SCHEMA_DIR / f"{report_name.lower()}.sql"
    rows = fetch_sample(report_name, page_size=page_size, client=client)
    if not rows:
        return False, "样本为空 (可能需特殊参数 / 接口已下线)"

    try:
        ddl = generate_ddl(report_name, rows, dialect=dialect)
        out_path.write_text(ddl, encoding="utf-8")
        return True, f"{len(rows)} 行 → {out_path.name}"
    except Exception as exc:
        return False, f"生成 DDL 失败: {exc}"


def run_batch(report_names: list[str], dialect: str, page_size: int) -> None:
    client = AIF10Client(retry=2)
    print(f"=== DDL 生成 ({dialect}, {len(report_names)} reports) ===\n")

    failed: list[tuple[str, str]] = []
    ok_count = 0
    t0 = time.time()
    for i, name in enumerate(report_names, 1):
        try:
            spec = get_report(name)
            label = f"[{i:>2}/{len(report_names)}] {spec.module:>14}/{name}"
        except KeyError:
            label = f"[{i:>2}/{len(report_names)}] (未注册)/{name}"

        ok, msg = generate_one(
            name, dialect=dialect, page_size=page_size, client=client,
        )
        status = "✓" if ok else "✗"
        print(f"  {label}: {status} {msg}", flush=True)
        if ok:
            ok_count += 1
        else:
            failed.append((name, msg))

    client.close()

    elapsed = time.time() - t0
    print()
    print(f"=== 汇总 ===")
    print(f"  成功: {ok_count}/{len(report_names)}")
    print(f"  耗时: {elapsed:.1f}s")
    if failed:
        fail_log = SCHEMA_DIR / "_failed.txt"
        fail_log.write_text(
            "\n".join(f"{n}\t{m}" for n, m in failed),
            encoding="utf-8",
        )
        print(f"  失败列表: {fail_log}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", help="reportName")
    parser.add_argument("--module", help="只跑某模块")
    parser.add_argument("--all", action="store_true", help="全部 reportName")
    parser.add_argument("--dialect", default="duckdb",
                        choices=["duckdb", "sqlite", "postgres"])
    parser.add_argument("--page-size", type=int, default=200,
                        help="样本量, 越大类型推断越准 (默认 200)")
    args = parser.parse_args()

    if args.all:
        names = [r.name for r in REPORTS]
    elif args.module:
        names = [r.name for r in reports_by_module(args.module)]
        if not names:
            print(f"模块 {args.module} 没有 reports", file=sys.stderr)
            return 1
    elif args.report:
        names = [args.report]
    else:
        parser.print_help()
        return 1

    run_batch(names, args.dialect, args.page_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
