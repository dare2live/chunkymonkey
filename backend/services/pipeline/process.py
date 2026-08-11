"""③ 加工 (Process) — 手工管线中的现有跨 Tier 派生步骤。

本阶段当前会依次刷新：DC legacy 分类快照、Tier1 segment/context、Tier2 market sensing、
Tier1 stock form。它不是“纯数据只剩一步”，也不代表这些旧表已经满足新 contract manifest；
Phase 1/2 将按 owner contract 拆分后再收紧编排边界。

CX-1: consume typed ``ctx.delta_manifest.process_plan`` — skip DC rebuild only when
frontier-unchanged; market_pulse late window ALWAYS runs (kill criterion).
"""
from __future__ import annotations

import time

from .context import PipelineContext
from .delta_manifest import (
    decide_dc_action,
    empty_manifest,
    plan_process_steps,
    probe_dc_source_frontier,
    read_dc_as_of,
    write_dc_as_of,
)


def run_process(ctx: PipelineContext) -> None:
    ctx.log("=== ③ 加工 PROCESS (L1 dim 物化) ===")
    if ctx.dry:
        ctx.log("DRY: 跳过加工 (全是写操作)")
        return

    manifest = ctx.delta_manifest or empty_manifest(run_date=ctx.date)
    process_plan = dict(manifest.get("process_plan") or {})
    if not process_plan:
        # Standalone process stage / skip_sync path: re-decide from live frontier.
        current = probe_dc_source_frontier()
        dc_decision = decide_dc_action(
            current_frontier=current,
            previous_frontier=read_dc_as_of(),
            advanced_partitions=list((manifest.get("delta") or {}).get("advanced_partitions") or []),
        )
        process_plan = plan_process_steps(
            dc_decision=dc_decision,
            state_changes=(manifest.get("delta") or {}).get("state_changes"),
        )
        delta = dict(manifest.get("delta") or {})
        delta["dc_source_frontier"] = current
        delta["dc_frontier_advanced"] = dc_decision.get("dc_frontier_advanced")
        delta["late_window_policy"] = "always_run"
        manifest["delta"] = delta
        manifest["process_plan"] = process_plan
        ctx.delta_manifest = manifest

    outcomes: dict = {}

    # DC namespace 的 legacy 分类快照；不得与 SW namespace 拼成同一历史或互作 fallback。
    dc_plan = dict(process_plan.get("dc_industry_view") or {})
    if not ctx.skip_sync:
        if str(dc_plan.get("action") or "run") == "skip":
            ctx.log(
                f"[delta] skip build_dc_industry_view reason={dc_plan.get('reason')}"
            )
            outcomes["dc_industry_view"] = {
                "action": "skip",
                "reason": dc_plan.get("reason"),
                "elapsed_s": 0.0,
            }
        else:
            t0 = time.perf_counter()
            ok = ctx.run_script(
                "backend/scripts/build_dc_industry_view.py",
                degraded_msg="DC namespace legacy 分类快照物化失败 — 对应展示将 stale",
            )
            elapsed = round(time.perf_counter() - t0, 3)
            outcomes["dc_industry_view"] = {
                "action": "run",
                "reason": dc_plan.get("reason") or "dc_rebuild",
                "elapsed_s": elapsed,
                "ok": bool(ok),
            }
            if ok:
                frontier = (manifest.get("delta") or {}).get("dc_source_frontier")
                if frontier:
                    write_dc_as_of(str(frontier))

    # Tier1 context: 股票分层 dim_stock_segment_daily 增量 (历史编号 B1):
    #   市值/换手当日分位段 + PIT 行业, 所有策略 cell/画像/筛选器的单一计算点)
    def _seg_latest():
        from services.segments import build_latest
        result = build_latest()
        ctx.log(f"[segments] {result}")
        outcomes["segments"] = {"action": "run", "result": result}

    ctx.step(_seg_latest, degraded_msg="股票分层增量失败 — segment 标签将 stale (策略 cell/筛选器缺当日)")

    # Tier2 market sensing: mart_sector/market_pulse_daily 增量 (历史编号 B4/C4):
    #   必须在 segments 之后 — B1 表是 sw 链广度/涨跌停聚合的输入, 顺序不可反)
    # CX-1 kill criterion: late window MUST always run — never skip this step.
    def _pulse_latest():
        from services.market_pulse import build_latest
        result = build_latest()
        ctx.log(f"[market_pulse] {result}")
        outcomes["market_pulse"] = {
            "action": "run",
            "reason": "late_window_mandatory",
            "result": {
                k: result.get(k)
                for k in (
                    "dc_added_days",
                    "sw_added_days",
                    "market_added_days",
                    "late_refreshed_days",
                )
                if isinstance(result, dict)
            },
        }

    ctx.step(_pulse_latest, degraded_msg="Tier2 市场感知增量失败 — pulse 面板与研究上下文将 stale")

    # Tier1 state: 形态识别 fact_stock_form_daily 增量 (历史编号 B2):
    #   必须在 segments 之后 — E 轴消费 B1 的 rv_pctile/vol_regime 列, 缺列 fail loud)
    #   S7: library default = accepted-only nominal (same escape as derive --allow-legacy-fill).
    def _form_latest():
        from services.technical_states import build_latest
        result = build_latest(from_accepted=True)
        ctx.log(f"[technical_states] {result}")
        outcomes["technical_states"] = {"action": "run", "result": result}

    ctx.step(_form_latest, degraded_msg="Tier1 形态状态增量失败 — form 标签与 Tier3 研究输入将 stale")

    # Closed-loop: institution episode→profile must track holders land (not manual-only).
    # Authority: docs/MASTER_TOPLEVEL_DESIGN.md §5.8 (派生新鲜度闭环法)
    inst_plan = dict(process_plan.get("institution_profile") or {})
    if str(inst_plan.get("action") or "run") == "skip":
        ctx.log(
            f"[delta] skip institution_profile reason={inst_plan.get('reason')}"
        )
        outcomes["institution_profile"] = {
            "action": "skip",
            "reason": inst_plan.get("reason"),
            "elapsed_s": 0.0,
        }
    else:
        def _inst_rebuild():
            from services.institution_profile import rebuild_all
            from .closed_loop import write_institution_as_of

            t0 = time.perf_counter()
            result = rebuild_all()
            frontier = inst_plan.get("holders_notice_frontier")
            if not frontier:
                holders = ((manifest.get("delta") or {}).get("state_changes") or {}).get(
                    "holders"
                ) or {}
                frontier = holders.get("as_of")
            if frontier:
                write_institution_as_of(str(frontier), rebuild=result if isinstance(result, dict) else None)
            elapsed = round(time.perf_counter() - t0, 3)
            ctx.log(f"[institution_profile] {result} elapsed_s={elapsed}")
            outcomes["institution_profile"] = {
                "action": "run",
                "reason": inst_plan.get("reason") or "holders_delta",
                "elapsed_s": elapsed,
                "result": result if isinstance(result, dict) else {"raw": result},
            }

        ctx.step(
            _inst_rebuild,
            degraded_msg="机构档案 institution_profile 重建失败 — dossier deep-link / episode 将 stale",
        )

    manifest["process_outcome"] = outcomes
    ctx.delta_manifest = manifest
