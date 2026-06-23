"""② 清洗 (Clean) — L0→L1: 复权归一 + 格式 + PIT 校验。

旧 daily_update.sh: Step 2.96 (build canonical 前复权 K线 = serving 真相源) + Step 3c (data_audit post-sync 校验)。
qfq build 必在 store 阶段 watermark 刷新前 (否则水位反映不出新 K线), 故归清洗早于加工/存储。
"""
from __future__ import annotations

from .context import PipelineContext


def run_clean(ctx: PipelineContext) -> None:
    ctx.log("=== ② 清洗 CLEAN (L0→L1 复权归一+校验) ===")

    # Step 2.96: 构建 canonical 前复权 K线 (price_kline_qfq_tushare, serving 真相源)
    # raw_tushare_daily × adj_factor → qfq; latest-adj rebase 须全量 (DuckDB CTAS 秒级)。
    if not ctx.skip_sync and not ctx.dry:
        ctx.run_script("backend/scripts/build_price_kline_qfq_tushare.py",
                       degraded_msg="canonical qfq K线 build 失败 — serving K线将 stale (fatal 级, 查 log)")
    else:
        ctx.log("DRY/skip-sync: 跳过 qfq canonical build")

    # Step 3c: data_audit post-sync (宪法第六条: sync 后必跑审计 = 清洗的校验环)
    if not ctx.dry:
        ctx.step(_data_audit, degraded_msg="data_audit 失败")
    else:
        ctx.log("DRY: 跳过 data_audit")


def _data_audit() -> None:
    import os
    os.environ["DATA_AUDIT_STRICT"] = "0"
    from services.data_audit import run_post_sync_audit
    r = run_post_sync_audit("clean_post_sync", strict=False)
    checks = r.get("checks", [])
    n_pass = sum(1 for c in checks if c["status"] == "PASS")
    n_fail = len(checks) - n_pass
    print(f"data_audit: {n_pass} PASS, {n_fail} FAIL")
    for c in checks:
        if c["status"] != "PASS":
            print(f"  FAIL: {c['name']}: {c['detail'][:60]}")
