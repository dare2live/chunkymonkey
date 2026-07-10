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
    # 2026-07-10 修审计环空转(全栈审计MEDIUM, owner=analysis/gap_root_cause_20260708.md 第四轮节):
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
