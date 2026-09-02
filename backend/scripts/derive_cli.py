#!/usr/bin/env python3
"""chunkyctl derive — S5/S7 independent qfq/form rebuild (no acquire/accept),
plus rally-gt: Tier3 主升浪 ground-truth 三表重建 (services.rally_gt.rebuild)。

rally-gt 是独立分支 (见 main() 里的早退 if): 它不经过 services.derive_runtime /
DERIVE_TARGETS — 那条 S5/S7 通道被 test_derive_runtime_s5.py::
test_s5_derive_targets_are_qfq_and_form_only 锁定为仅 {"qfq","form"} (accepted-only
独立于 acquire 的窄契约), 塞进去会破坏该锁定测试且语义不符 (rally_gt 不是"从
accepted canonical 重建", 是从 K 线+特征面推导 Tier3 GT)。这里只做参数解析 + 调用
services.rally_gt.rebuild(data_end=...) + 打印其返回的统计 dict — 不重写任何业务逻辑。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.derive_runtime import DERIVE_TARGETS, run_derive  # noqa: E402

RALLY_GT_TARGET = "rally-gt"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "S5/S7 derive: rebuild qfq or form from accepted/canonical inputs "
            "(default). Independent of acquire; does not run inside accept. "
            "Use --allow-legacy-fill only for authorized pre-accepted history. "
            "rally-gt: Tier3 主升浪 GT 三表重建 (services.rally_gt.rebuild), a "
            "separate branch independent of the qfq/form accepted-only contract."
        )
    )
    ap.add_argument(
        "target",
        choices=sorted(DERIVE_TARGETS) + [RALLY_GT_TARGET],
        help=(
            "derive target: qfq (price_kline_qfq_tushare), form (fact_stock_form_daily), "
            "or rally-gt (fact_rally_ground_truth/_negative/_strata, Tier3 GT rebuild)"
        ),
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--from-accepted",
        action="store_true",
        help="S7 default path: nominal from accepted canonical only (explicit no-op ok)",
    )
    mode.add_argument(
        "--allow-legacy-fill",
        action="store_true",
        help="S7 escape: canonical ∪ legacy raw_tushare_daily fill (pre-accepted history)",
    )
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="form only: full rebuild_all (default is incremental build_latest)",
    )
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="qfq only: sanity check without rebuild",
    )
    ap.add_argument(
        "--data-end",
        default=None,
        help=(
            "rally-gt only: train 窗右边界 YYYYMMDD/YYYY-MM-DD "
            "(默认 None = training_cutoff_before_holdout() 决定, 即 holdout_start 前一自然日)"
        ),
    )
    args = ap.parse_args(argv)

    if args.target == RALLY_GT_TARGET:
        if args.from_accepted or args.allow_legacy_fill or args.rebuild or args.check_only:
            ap.error("--from-accepted/--allow-legacy-fill/--rebuild/--check-only do not apply to rally-gt")
        from services.rally_gt import rebuild as rebuild_rally_gt

        try:
            result = rebuild_rally_gt(data_end=args.data_end)
        except Exception as exc:  # CLI 边界: 只报告失败原因 + 非零退出, 不吞/不改判定
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0

    if args.data_end is not None:
        ap.error("--data-end applies to rally-gt only")
    if args.target == "qfq" and args.rebuild:
        ap.error("--rebuild applies to form only")
    if args.target == "form" and args.check_only:
        ap.error("--check-only applies to qfq only")
    # Default = from_accepted (S7). Only --allow-legacy-fill opts out.
    from_accepted = not bool(args.allow_legacy_fill)
    try:
        result = run_derive(
            args.target,
            from_accepted=from_accepted,
            rebuild=bool(args.rebuild),
            check_only=bool(args.check_only),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, default=str))
    if args.target == "qfq":
        return int(result.get("returncode") or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
