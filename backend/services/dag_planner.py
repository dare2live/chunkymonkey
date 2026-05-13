"""DAG 并行规划 — P2.9 (2026-04-28).

目标:
- 分析 STEPS + HARD_DEPS, 推导可并行的 "wave"
- 同一 wave 内 step 用 asyncio.gather 一起跑, 总时长 = max(wave 内最慢的)
- 不动现有 updater 主 sequential runner (5000+ 行, 风险大)
- 提供 endpoint POST /api/inst/update/parallel_sync 跑一波独立 sync 并行

工程价值评估:
- 数据获取 group: sync_qfii / sync_lhb / sync_surveys / sync_aif10_*
  全部 HARD_DEPS=[], 可并行. 顺序跑 ~3 min, 并行 ~30s.
- calc / mart group 大多有依赖, 并行价值小

不改 updater 主循环, 安全可回滚.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("cm-api.dag_planner")


def topological_waves(step_ids: list[str], deps: dict[str, list[str]]) -> list[list[str]]:
    """拓扑排序成 wave list.

    Wave i = 所有依赖都在 wave 0..i-1 的 step.
    返回 [[wave0_steps], [wave1_steps], ...].
    deps[s] = [s 依赖的所有 step].
    不在 step_ids 内的依赖被忽略 (e.g. soft / 可选 step).
    """
    pending = set(step_ids)
    completed: set[str] = set()
    waves: list[list[str]] = []
    safety = 0
    while pending and safety < 50:
        safety += 1
        wave = []
        for s in list(pending):
            s_deps = [d for d in deps.get(s, []) if d in step_ids]  # 过滤范围外
            if all(d in completed for d in s_deps):
                wave.append(s)
        if not wave:
            # 循环依赖或全部 blocked, 把剩下的放最后一波
            wave = list(pending)
        for s in wave:
            pending.remove(s)
            completed.add(s)
        waves.append(sorted(wave))
    return waves


async def run_waves_parallel(
    waves: list[list[str]],
    runner_fn,                # async (step_id) → result
    *,
    stop_check_fn=None,
    timeout_per_step: int = 600,
) -> list[dict]:
    """跑 wave 列表. 返回每个 step 的执行结果 [{step_id, status, elapsed_s, result}, ...].

    runner_fn(step_id) 必须是 async, 返回 dict.
    timeout_per_step: 每个 step 最多多久, 超时标 timeout (asyncio.wait_for).
    """
    all_results: list[dict] = []
    for wi, wave in enumerate(waves):
        if stop_check_fn and stop_check_fn():
            logger.info(f"[dag] wave {wi} 前用户停止")
            break
        wave_t0 = time.time()
        logger.info(f"[dag] wave {wi}: {len(wave)} 个并行 step: {wave}")

        async def _run_one(step_id):
            t0 = time.time()
            try:
                result = await asyncio.wait_for(runner_fn(step_id), timeout=timeout_per_step)
                return {
                    "step_id": step_id,
                    "status": "ok",
                    "elapsed_s": round(time.time() - t0, 2),
                    "result": result if isinstance(result, dict) else {"value": str(result)[:200]},
                }
            except asyncio.TimeoutError:
                return {
                    "step_id": step_id,
                    "status": "timeout",
                    "elapsed_s": round(time.time() - t0, 2),
                    "error": f"超时 {timeout_per_step}s",
                }
            except Exception as exc:
                return {
                    "step_id": step_id,
                    "status": "failed",
                    "elapsed_s": round(time.time() - t0, 2),
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }

        wave_results = await asyncio.gather(*[_run_one(s) for s in wave])
        wave_elapsed = time.time() - wave_t0
        n_ok = sum(1 for r in wave_results if r["status"] == "ok")
        logger.info(
            f"[dag] wave {wi} 完: {n_ok}/{len(wave)} ok, 耗时 {wave_elapsed:.1f}s "
            f"(顺序时若 {sum(r['elapsed_s'] for r in wave_results):.1f}s, "
            f"并行节省 {max(0, sum(r['elapsed_s'] for r in wave_results) - wave_elapsed):.1f}s)"
        )
        all_results.extend(wave_results)
    return all_results


def estimate_speedup(waves: list[list[str]]) -> dict:
    """估算并行加速比 (假设每 step 耗时 1)."""
    n_total = sum(len(w) for w in waves)
    n_waves = len(waves)
    if n_waves == 0 or n_total == 0:
        return {"sequential": 0, "parallel": 0, "speedup": 1.0}
    return {
        "n_steps": n_total,
        "n_waves": n_waves,
        "sequential_time_units": n_total,
        "parallel_time_units": n_waves,
        "theoretical_speedup": round(n_total / n_waves, 2),
    }
