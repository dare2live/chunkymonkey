"""② 清洗 (Clean) — 当前 qfq 派生分析面 + post-sync 校验。

qfq 是 analysis/serving compatibility surface，不是 nominal execution truth 或 AcceptedPartition。
它在 store 水位刷新前构建，因此仍位于当前手工管线的 clean 阶段。
"""
from __future__ import annotations

from .context import PipelineContext


def run_clean(ctx: PipelineContext) -> None:
    ctx.log("=== ② 清洗 CLEAN (L0→L1 复权归一+校验) ===")

    # Step 2.96: 构建 qfq 派生分析面 (price_kline_qfq_tushare)
    # S7: accepted daily now covers 20190102→frontier, so clean uses the same
    # accepted-only default as `chunkyctl derive qfq` (no silent legacy fill).
    # Escape remains: run the script with --allow-legacy-fill if needed.
    # latest-adj rebase 须全量。
    if not ctx.skip_sync and not ctx.dry:
        ctx.run_script(
            "backend/scripts/build_price_kline_qfq_tushare.py",
            ["--from-accepted"],
            degraded_msg="qfq analysis/serving build 失败 — 研究读面将 stale",
        )
    else:
        ctx.log("DRY/skip-sync: 跳过 qfq analysis build")

    # Step 3c: data_audit post-sync (宪法第六条: sync 后必跑审计 = 清洗的校验环)
    if not ctx.dry:
        ctx.step(_data_audit, degraded_msg="data_audit 失败")
    else:
        ctx.log("DRY: 跳过 data_audit")


def _data_audit() -> None:
    # 2026-07-10 修审计环空转（历史证据=git log --grep gap_root_cause 第四轮节）:
    # 原实现 FAIL 只 print 不上报 — run_and_record 判 check_pass 只看 ctx.degraded_msgs 是否
    # 新增, 审计 FAIL 时 clean 仍记 check_pass, stage_runner 上游门照常放行 process 在审计不过
    # 的数据上加工, 无 flag 无告警 = "审计环是纯观测非门"。改为 FAIL 即 raise, 由外层 ctx.step
    # 捕获转 degraded(保持续跑语义不中断链, 但 clean 记 check_fail + 进 degraded 汇总 + flag)。
    # 另: 原 os.environ["DATA_AUDIT_STRICT"]="0" 是死设置(data_audit.py 实际读的是 AUDIT_STRICT,
    # 且 strict=False 参数已支配), 一并删除。
    from services.data_audit import run_post_sync_audit
    r = run_post_sync_audit("clean_post_sync", strict=False)
    checks = r.get("checks", [])
    n_pass = sum(1 for c in checks if c["status"] == "PASS")
    n_fail = len(checks) - n_pass
    print(f"data_audit: {n_pass} PASS, {n_fail} FAIL")
    for c in checks:
        if c["status"] != "PASS":
            print(f"  FAIL: {c['name']}: {c['detail'][:60]}")
    if n_fail > 0:
        raise RuntimeError(f"data_audit {n_fail} 项 FAIL — clean 记 check_fail, 链继续但结果必须可见")
