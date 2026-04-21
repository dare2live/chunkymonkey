"""Layer 1 · Cox Proportional Hazards 生存模型.

对 (institution_id, industry_level, industry_code) 学习 α 衰减曲线：
- 事件: chain 是否"达到目标"（tb_label='upper' 视为成功）
- 时长: chain_days（upper/lower trigger 到触发日；time hit 到 horizon）
- 协变量: entry_premium_pct / entry_inst_cost / industry_l1 / industry_l2 / ...

输出:
- α_median / α_se / α_ci_lower_90
- sharpe / max_dd_median
- alpha_halflife_days（Weibull 假设下的半衰期）
- alpha_decay_tau_star（最佳跟投期：hazard rate 最大时点）
- expert_level 0/1/2/3：按 α_median + sample_count 分桶

依赖:
- fact_chain_alpha_truth（Layer 0 + Phase I 产出）
- mart_institution_capability（写入表）

基本原则（参考 SEF §3 Layer 1 + CLAUDE.md §2 "事实层驱动"）:
- 聚合最小粒度: L2（避免 L3 噪音）
- sample_count 不足 20 的 L2 合并到 L1
- exponential baseline vs Cox AIC 对比（Phase II KPI）
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger("cm-api.sef.cox")


# ---------------------------------------------------------------------------
# 1) 数据装载
# ---------------------------------------------------------------------------


def _load_chains_for_fit(conn: sqlite3.Connection) -> list[dict]:
    """取所有已打 Triple Barrier 标签的 chain。"""
    rows = conn.execute(
        """
        SELECT
            institution_id, stock_code, research_chain_id,
            entry_date, eval_date, status,
            chain_days, tb_label,
            chain_follow_pnl, chain_follow_max_dd, chain_inst_pnl,
            industry_l1, industry_l2
        FROM fact_chain_alpha_truth
        WHERE tb_label IS NOT NULL AND chain_days IS NOT NULL AND chain_days > 0
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 事件 = 达到止盈 barrier; 时长 = chain_days
        d["event"] = 1 if d["tb_label"] == "upper" else 0
        d["duration"] = max(int(d["chain_days"]), 1)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# 2) 分组聚合
# ---------------------------------------------------------------------------


def _aggregate_group(group: list[dict]) -> dict:
    """对一个 (institution, industry) 分组计算 α 统计 + bootstrap CI."""
    follow_pnls = np.array(
        [c["chain_follow_pnl"] for c in group if c["chain_follow_pnl"] is not None],
        dtype=float,
    )
    dds = np.array(
        [c["chain_follow_max_dd"] for c in group if c["chain_follow_max_dd"] is not None],
        dtype=float,
    )
    n = len(group)
    stats = {"sample_count": n}
    if follow_pnls.size:
        stats["alpha_median"] = float(np.median(follow_pnls))
        stats["alpha_mean"] = float(np.mean(follow_pnls))
        if follow_pnls.size >= 3:
            stats["alpha_se"] = float(np.std(follow_pnls, ddof=1) / np.sqrt(follow_pnls.size))
            # 90% CI via percentile bootstrap（小样本稳）
            rng = np.random.default_rng(42)
            boots = rng.choice(follow_pnls, size=(500, follow_pnls.size), replace=True)
            medians = np.median(boots, axis=1)
            stats["alpha_ci_lower_90"] = float(np.percentile(medians, 5))
        else:
            stats["alpha_se"] = None
            stats["alpha_ci_lower_90"] = None
        if follow_pnls.size >= 2:
            std = float(np.std(follow_pnls, ddof=1))
            stats["sharpe"] = (
                float(stats["alpha_mean"] / std) if std > 1e-9 else None
            )
        else:
            stats["sharpe"] = None
    else:
        stats.update(
            {"alpha_median": None, "alpha_mean": None, "alpha_se": None,
             "alpha_ci_lower_90": None, "sharpe": None}
        )
    stats["max_dd_median"] = float(np.median(dds)) if dds.size else None
    return stats


def _group_chains(chains: list[dict], level: str) -> dict:
    """按 (institution_id, industry_level, industry_code) 分组."""
    industry_key = "industry_l1" if level == "L1" else "industry_l2"
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for c in chains:
        ind = c.get(industry_key)
        if not c.get("institution_id") or not ind:
            continue
        key = (c["institution_id"], level, ind)
        groups.setdefault(key, []).append(c)
    return groups


# ---------------------------------------------------------------------------
# 3) Cox 生存模型拟合（全样本，协变量一次性学）
# ---------------------------------------------------------------------------


def _fit_cox_global(chains: list[dict], conn: Optional[sqlite3.Connection] = None) -> tuple[Optional[dict], Optional[dict]]:
    """拟合全样本 Cox PH model.

    严格只用 entry 时刻的协变量，不能用 follow_pnl / max_dd（它们是结果）。
    从 research_holding_chains 拉 entry_premium_pct / entry_follow_price。
    """
    try:
        import pandas as pd
        from lifelines import CoxPHFitter, ExponentialFitter
    except ImportError as e:
        logger.warning("[SEF Cox] lifelines 不可用: %s", e)
        return None, None

    # 拉 entry 时刻协变量（如失败用空 dict，Cox 仍能拟合无协变量版本）
    entry_covars: dict[tuple, dict] = {}
    if conn is not None:
        try:
            rows = conn.execute(
                """
                SELECT institution_id, stock_code, chain_id,
                       entry_premium_pct, entry_follow_price
                FROM research_holding_chains
                """
            ).fetchall()
            for r in rows:
                entry_covars[(r[0], r[1], r[2])] = {
                    "entry_premium_pct": r[3],
                    "entry_follow_price": r[4],
                }
        except sqlite3.OperationalError:
            pass

    df_rows = []
    for c in chains:
        # chains 的 key 是 research_chain_id，与 research_holding_chains.chain_id 对应
        ck = (c["institution_id"], c["stock_code"], c.get("research_chain_id"))
        ec = entry_covars.get(ck, {})
        prem = ec.get("entry_premium_pct")
        price = ec.get("entry_follow_price")
        df_rows.append(
            {
                "duration": c["duration"],
                "event": c["event"],
                "entry_premium_pct": float(prem) if prem is not None else 0.0,
                "log_entry_price": float(np.log(max(price, 0.01))) if price is not None else 0.0,
            }
        )
    if len(df_rows) < 50:
        return None, None
    df = pd.DataFrame(df_rows)

    # Exponential baseline（无协变量，纯 duration）
    try:
        exp_fit = ExponentialFitter()
        exp_fit.fit(df["duration"], event_observed=df["event"])
        exp_aic = float(exp_fit.AIC_)
    except Exception:  # noqa: BLE001
        exp_aic = None

    # Cox PH: entry-time covariates only
    try:
        cph = CoxPHFitter(penalizer=0.01)
        cols = ["duration", "event", "entry_premium_pct", "log_entry_price"]
        cph.fit(df[cols], duration_col="duration", event_col="event")
        cox_aic = float(cph.AIC_partial_)
        cox_params = cph.params_.to_dict()
    except Exception as e:  # noqa: BLE001
        logger.warning("[SEF Cox] Cox PH 拟合失败: %s", e)
        return None, {"exp_aic": exp_aic}

    return (
        {"cox_aic_partial": cox_aic, "cox_params": cox_params},
        {"exp_aic": exp_aic},
    )


# ---------------------------------------------------------------------------
# 4) α 半衰期 + τ*
# ---------------------------------------------------------------------------


def _halflife_from_durations(durations: np.ndarray, events: np.ndarray) -> tuple[Optional[float], Optional[int]]:
    """简化 Weibull / KM 非参数估计 α 半衰期 + 最优跟投期 τ*.

    返回 (halflife_days, tau_star_days).
    tau_star = 累计胜率达到 80% 或 hazard 最大的时点。
    """
    if durations.size < 5:
        return None, None
    # KM 存活曲线估计
    try:
        from lifelines import KaplanMeierFitter

        kmf = KaplanMeierFitter()
        kmf.fit(durations, event_observed=events)
        surv = kmf.survival_function_.iloc[:, 0]  # Series[timeline -> S(t)]
        # halflife: 第一个 S(t) <= 0.5 的 t
        below = surv[surv <= 0.5]
        hl = float(below.index[0]) if len(below) else None

        # τ* = S(t) <= 0.2 的时点（80% 触发）
        below2 = surv[surv <= 0.2]
        tau = int(below2.index[0]) if len(below2) else None
        return hl, tau
    except Exception:  # noqa: BLE001
        return None, None


# ---------------------------------------------------------------------------
# 5) expert_level 分档
# ---------------------------------------------------------------------------


def _assign_expert_level(alpha_median: Optional[float], sample_count: int,
                        ci_lower_90: Optional[float]) -> int:
    """0: 样本不足 / 1: 有能力 / 2: 显著 / 3: 超群 ."""
    if alpha_median is None or sample_count < 5:
        return 0
    if sample_count >= 20 and (ci_lower_90 is not None and ci_lower_90 > 10.0) and alpha_median > 20.0:
        return 3
    if sample_count >= 10 and alpha_median > 10.0:
        return 2
    if sample_count >= 5 and alpha_median > 0.0:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 6) 主入口：build_capability
# ---------------------------------------------------------------------------


def build_institution_capability(
    conn: sqlite3.Connection,
    *,
    min_sample_l2: int = 5,
    fallback_to_l1: bool = True,
) -> dict:
    """Layer 1 主接口：写入 mart_institution_capability."""
    chains = _load_chains_for_fit(conn)
    logger.info("[SEF Cox] 样本装载 %d chains", len(chains))

    # global Cox vs Exponential 基线
    cox_info, baseline_info = _fit_cox_global(chains, conn=conn)

    now = datetime.utcnow().isoformat(timespec="seconds")
    written = 0
    skipped_small = 0

    # 先清空再重建（小表、幂等）
    conn.execute("DELETE FROM mart_institution_capability")

    # 按 L2 聚合
    l2_groups = _group_chains(chains, "L2")
    l1_groups = _group_chains(chains, "L1")

    per_inst_industries: dict[tuple[str, str, str], dict] = {}
    for (inst, level, ind), group in l2_groups.items():
        if len(group) < min_sample_l2:
            skipped_small += 1
            continue
        stats = _aggregate_group(group)
        durations = np.array([c["duration"] for c in group], dtype=float)
        events = np.array([c["event"] for c in group], dtype=int)
        hl, tau = _halflife_from_durations(durations, events)
        stats["alpha_halflife_days"] = hl
        stats["alpha_decay_tau_star"] = tau
        stats["expert_level"] = _assign_expert_level(
            stats.get("alpha_median"), stats["sample_count"], stats.get("alpha_ci_lower_90")
        )
        per_inst_industries[(inst, level, ind)] = stats

    # L1 聚合（无论 L2 是否写入，都写入一份 L1 作为 fallback）
    for (inst, level, ind), group in l1_groups.items():
        if len(group) < min_sample_l2:
            continue
        stats = _aggregate_group(group)
        durations = np.array([c["duration"] for c in group], dtype=float)
        events = np.array([c["event"] for c in group], dtype=int)
        hl, tau = _halflife_from_durations(durations, events)
        stats["alpha_halflife_days"] = hl
        stats["alpha_decay_tau_star"] = tau
        stats["expert_level"] = _assign_expert_level(
            stats.get("alpha_median"), stats["sample_count"], stats.get("alpha_ci_lower_90")
        )
        per_inst_industries[(inst, level, ind)] = stats

    # 写入
    for (inst, level, ind), stats in per_inst_industries.items():
        conn.execute(
            """
            INSERT INTO mart_institution_capability(
                institution_id, industry_level, industry_code,
                alpha_median, alpha_se, alpha_ci_lower_90,
                sample_count, sharpe, max_dd_median,
                expert_level, alpha_halflife_days, alpha_decay_tau_star,
                last_updated
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                inst, level, ind,
                stats.get("alpha_median"), stats.get("alpha_se"),
                stats.get("alpha_ci_lower_90"),
                stats["sample_count"], stats.get("sharpe"), stats.get("max_dd_median"),
                stats["expert_level"],
                stats.get("alpha_halflife_days"),
                stats.get("alpha_decay_tau_star"),
                now,
            ),
        )
        written += 1
    conn.commit()

    # 统计摘要
    top_experts = conn.execute(
        "SELECT COUNT(*) FROM mart_institution_capability WHERE expert_level>=2"
    ).fetchone()[0]

    report = {
        "chains_used": len(chains),
        "groups_l1": len(l1_groups),
        "groups_l2": len(l2_groups),
        "skipped_small_l2": skipped_small,
        "written": written,
        "expert_level_ge_2": top_experts,
        "global_cox": cox_info,
        "baseline": baseline_info,
        "cox_beats_baseline": (
            cox_info is not None
            and baseline_info is not None
            and baseline_info.get("exp_aic") is not None
            and cox_info.get("cox_aic_partial") is not None
            and cox_info["cox_aic_partial"] < baseline_info["exp_aic"]
        ),
    }
    logger.info("[SEF Cox] 完成: %s", {k: v for k, v in report.items() if k != "global_cox"})
    return report
