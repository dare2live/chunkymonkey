"""前置 gate (非四阶段之一): experiment job 契约 sanity + watermark SLA。

旧 daily_update.sh Step 0 (job contract) + Step 1 (watermark SLA check + auto-update)。
"""
from __future__ import annotations

from .context import PipelineContext


def run_preflight(ctx: PipelineContext) -> None:
    ctx.log("--- Preflight: experiment job contract + watermark SLA ---")

    # Step 0: experiment job contract sanity (provider-neutral)
    def _job_contract():
        from services.experiment_jobs import load_experiment_job_contract
        contract = load_experiment_job_contract()
        ctx.log(f"  job contract: backends={sorted(contract.backends)} "
                f"families={sorted(contract.families)} local_active={contract.backends['local'].active}")
    ctx.step(_job_contract, degraded_msg="experiment job contract 加载失败")

    # Step 1a: watermark SLA check + auto-update (exit 2 = alert; 其他非0 = 检查器 crash 同样送达)
    args = ["backend/scripts/update_watermark_sla.py",
            "--json-output", f"data/audit/watermark_sla_{ctx.date}.json"]
    if ctx.dry:
        args.insert(1, "--dry-run")
    # update_watermark_sla 用 exit code 2 表 SLA alert → 不能当普通失败; 用专用判定
    import subprocess, sys as _sys
    from .context import REPO
    proc = subprocess.run([_sys.executable] + args, cwd=str(REPO),
                          capture_output=True, text=True, env=ctx._subprocess_env())
    if ctx._log_fh:
        ctx._log_fh.write((proc.stdout or "") + (proc.stderr or "")); ctx._log_fh.flush()
    if proc.returncode == 2:
        ctx.degraded(f"watermark SLA alert (见 data/audit/watermark_sla_{ctx.date}.json)")
    elif proc.returncode != 0:
        # 检查器本身 crash 比检查出 alert 更该送达 (旧版严重度倒挂只 log)
        ctx.degraded(f"watermark SLA 检查器 crash (exit {proc.returncode}) — SLA 体系失明")
    # 1b: K线 continuity 由 SLA(1a) + acquire 的 sync_runner 日历 gap 扫描覆盖 (builder reset 删)
