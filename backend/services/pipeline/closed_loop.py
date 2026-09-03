"""Serve→derive closed-loop helpers (single compute for process plan + gates).

Authority: 本文件 (派生新鲜度闭环法; 2026-09 文档大刀后法条正文从旧版顶层设计
文档 §5.8 原样搬入, git log --grep serve_derive_closed_loop_law)。

产品 serve 面依赖的派生 (可擦除的 L1/L2) 必须与 accepted 源在同一个日更闭环内
保持新鲜, 否则就诚实标 BLOCKED / manual。**禁止用「分区存在」或「软绿灯」冒充
完成。**

判断法典 —— 五条, 每条左边是人话、右边是机器判据:

  L1  运输完成 ≠ 产品新鲜     accepted_partition 存在**不蕴含**派生已追上
  L2  存在 ≠ 人口             只有 population gate PASS 才可 skip_current；
                               否则 under_populated_accepted
  L3  时钟 ≠ 完整             run_outcome 四态判定 (见 backend/services/
                               pipeline/run_outcome.py)；完整性观测不是
                               「等时钟」
  L4  未接线不许自称 fresh    inventory status ∈ wired* / population_gated /
                               blocked_manual，没有第四种
  L5  禁 mass 仍须诚实        人口有洞 → count probe + grain MERGE 或如实
                               观测；**不**在日更里对 count 未变的期全市场
                               重拉
  L6  回改 ≠ 前进             上游 accepted 分区或 derive_build 的时间戳晚于
                               本派生的 built_at → 本派生不新鲜, **不论 tip 在哪**

L6 为什么要单列: L1–L5 全部在问「派生追上**最新**分区了吗」—— 全是关于**前进**的。
没有一条管「tip 之下的某个历史分区被重新接受了怎么办」。一次历史回填不推动 tip,
于是所有比 tip 的检查都全绿, 而那段历史派生出来的东西已经错了。这是「标量代表不了
集合」在时间轴上的同一个洞: 拿 MAX(date) 当覆盖度, 中间挖空看不见。

L6 今天守到哪 (别把它当已闭环):
  - 已守: **阶段级**。stage_status.py 比较同一次 pipeline run 内各阶段的 started_at,
    上游重跑晚于下游 → 下游 stale (test_pipeline_stage_status.py::
    test_derived_stale_when_upstream_rerun_later)。
  - 未守: **数据集/分区级**。没有 derived_stale 门, 没有 dataset 粒度的 built_at
    与 accepted_at 对照 —— 全仓 grep derived_stale 只有上面那条测试。
    也就是说: 今晚回填一段 2023 年的历史, 没有任何一行代码会告诉你哪些派生因此过期。
    这是已知缺口, 不是「大概没事」。

三种死法 (每条都真实发生过, 写在这里是为了让下一个人认得出):
  - 感知死 —— 门禁只查「存在」不查「新鲜/人口」。partition 在, 于是全绿,
    而产品面是陈的。
  - 判断死 —— 把完整性问题叙事成「在等时钟」。前者要人去修, 后者让人安心
    等待。
  - 谄媚死 —— 为了让门变绿而调低人口或新鲜度门槛。这比不设门更糟: 它制造
    了「已验证」的假象。

L2 与 L5 合起来是一条完整约束: **薄接受不等于可用, 但补救方式不是每天全量
重拉。**

Config: backend/config/serve_derive_closed_loop.yaml
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

REPO = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO / "backend/config/serve_derive_closed_loop.yaml"
INST_AS_OF_PATH = REPO / "data/reports/institution_profile_as_of.json"


def load_closed_loop_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return dict(raw)


def read_institution_as_of(path: Path | None = None) -> str | None:
    marker = path or INST_AS_OF_PATH
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    frontier = data.get("holders_notice_frontier")
    return str(frontier) if frontier else None


def write_institution_as_of(
    holders_notice_frontier: str,
    *,
    path: Path | None = None,
    rebuild: dict[str, Any] | None = None,
) -> None:
    marker = path or INST_AS_OF_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "holders_notice_frontier": str(holders_notice_frontier),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    if rebuild:
        keep = ("period_windows", "episodes", "profiles", "open", "closed")
        payload["rebuild"] = {
            k: rebuild[k] for k in keep if k in rebuild and rebuild[k] is not None
        }
    marker.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def decide_institution_profile_action(
    *,
    holders_changed: bool,
    holders_notice_frontier: str | None,
    previous_as_of: str | None,
    force_run: bool = False,
) -> dict[str, Any]:
    """Delta-gated institution L2 rebuild decision for process_plan."""
    if force_run:
        return {"action": "run", "reason": "force_run"}
    if holders_changed:
        return {"action": "run", "reason": "holders_state_changed"}
    if previous_as_of is None:
        return {"action": "run", "reason": "inst_as_of_missing"}
    if holders_notice_frontier and str(holders_notice_frontier) != str(previous_as_of):
        return {
            "action": "run",
            "reason": "holders_frontier_ahead_of_inst",
            "holders_notice_frontier": str(holders_notice_frontier),
            "previous_as_of": str(previous_as_of),
        }
    return {
        "action": "skip",
        "reason": "inst_frontier_unchanged",
        "holders_notice_frontier": holders_notice_frontier,
        "previous_as_of": previous_as_of,
    }


def org_population_thresholds(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (cfg or load_closed_loop_config()).get("org_population") or {}
    return {
        "min_accepted_stocks": int(raw.get("min_accepted_stocks", 500)),
        "min_raw_stocks_for_ratio": int(raw.get("min_raw_stocks_for_ratio", 1000)),
        "min_accepted_over_raw_ratio": float(
            raw.get("min_accepted_over_raw_ratio", 0.5)
        ),
    }


def evaluate_org_population(
    *,
    accepted_stocks: int,
    raw_stocks: int,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Existence≠population: canary accept must not look like ok skip."""
    thr = org_population_thresholds(cfg)
    accepted_n = int(accepted_stocks or 0)
    raw_n = int(raw_stocks or 0)
    ratio = (accepted_n / raw_n) if raw_n > 0 else None
    under = False
    reasons: list[str] = []
    if accepted_n < thr["min_accepted_stocks"]:
        under = True
        reasons.append(
            f"accepted_stocks={accepted_n}<{thr['min_accepted_stocks']}"
        )
    if (
        raw_n >= thr["min_raw_stocks_for_ratio"]
        and ratio is not None
        and ratio < thr["min_accepted_over_raw_ratio"]
    ):
        under = True
        reasons.append(
            f"accepted/raw={ratio:.4f}<{thr['min_accepted_over_raw_ratio']}"
        )
    return {
        "under_populated": under,
        "accepted_stocks": accepted_n,
        "raw_stocks": raw_n,
        "accepted_over_raw_ratio": ratio,
        "reasons": reasons,
        "thresholds": thr,
    }


def wired_process_steps(cfg: dict[str, Any] | None = None) -> list[str]:
    """Process step names that must appear in plan_process_steps for wired surfaces."""
    data = cfg or load_closed_loop_config()
    out: list[str] = []
    for surf in data.get("surfaces") or []:
        if str(surf.get("status") or "").startswith("wired") and surf.get(
            "process_step"
        ):
            out.append(str(surf["process_step"]))
    return out


def seed_institution_as_of_from_holders(
    *,
    holders_conn: Optional[Any] = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Seed as_of from live holders notice frontier so process won't surprise-rebuild.

    Does not rebuild episodes/profiles — only writes the frontier marker when a
    holders notice date is readable.
    """
    from services.duck_adapter import connect
    from services.database_manifest import get_database_manifest

    own = holders_conn is None
    conn = holders_conn
    if conn is None:
        db = get_database_manifest().path_for("smartmoney")
        conn = connect(str(db), read_only=True)
    try:
        row = conn.execute(
            "SELECT MAX(notice_date) FROM canonical_top10_float_holders_period"
        ).fetchone()
    finally:
        if own and conn is not None:
            conn.close()
    frontier = str(row[0]) if row and row[0] else None
    if not frontier:
        return {"status": "skipped", "reason": "no_holders_notice"}
    write_institution_as_of(frontier, path=path)
    return {"status": "seeded", "holders_notice_frontier": frontier}
