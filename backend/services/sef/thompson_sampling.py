"""Layer 5 · Thompson Sampling Bandit.

防止系统老龄化（只用历史最强 N 个机构），给新机构/不确定机构预留探索预算。

每个机构 (institution_id) 建一个 Beta-Binomial 模型:
- α_i = prior_α + realized_wins
- β_i = prior_β + realized_losses
- win 定义: 该机构触发的信号对应 chain_follow_pnl > 5pp（或 tb_label='upper'）
- 每次决策时从 Beta(α_i, β_i) 抽样，抽样值作为"预期胜率"

勘探预算分配:
- Exploit 机构（历史胜率显著）: 85% 权重池
- Explore 机构（样本少但 Beta 方差大）: 15% 权重池
- 新机构需 prior ≥ L1 同行业中位数

输出表 mart_exploration_bandit:
- institution_id, alpha, beta, sampled_mean, is_explore_candidate, last_updated
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger("cm-api.sef.bandit")


def _ensure_table(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mart_exploration_bandit (
            institution_id      TEXT PRIMARY KEY,
            alpha_wins          REAL NOT NULL,
            beta_losses         REAL NOT NULL,
            posterior_mean      REAL,
            posterior_var       REAL,
            sampled_score       REAL,
            is_explore_candidate INTEGER DEFAULT 0,
            total_signals       INTEGER,
            last_updated        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bandit_explore
          ON mart_exploration_bandit(is_explore_candidate);
        """
    )
    conn.commit()


def update_bandit_state(
    conn: sqlite3.Connection,
    *,
    win_threshold_pct: float = 5.0,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    explore_budget_share: float = 0.15,
    random_state: int = 42,
) -> dict:
    """从 fact_chain_alpha_truth 历史更新每个机构的 Beta-Binomial 后验."""
    _ensure_table(conn)
    rng = np.random.default_rng(random_state)

    rows = conn.execute(
        """
        SELECT institution_id,
               SUM(CASE WHEN chain_follow_pnl > ? THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN chain_follow_pnl <= ? THEN 1 ELSE 0 END) AS losses,
               COUNT(*) AS total
        FROM fact_chain_alpha_truth
        WHERE chain_follow_pnl IS NOT NULL
        GROUP BY institution_id
        """,
        (win_threshold_pct, win_threshold_pct),
    ).fetchall()

    # 清空再重建
    conn.execute("DELETE FROM mart_exploration_bandit")
    now = datetime.utcnow().isoformat(timespec="seconds")

    scored: list[tuple[str, float, float, int]] = []  # (inst, mean, var, total)
    for r in rows:
        inst_id = r[0]
        wins = float(r[1] or 0)
        losses = float(r[2] or 0)
        total = int(r[3] or 0)
        a = prior_alpha + wins
        b = prior_beta + losses
        mean = a / (a + b)
        var = a * b / ((a + b) ** 2 * (a + b + 1))
        sampled = float(rng.beta(a, b))
        conn.execute(
            """
            INSERT INTO mart_exploration_bandit(
                institution_id, alpha_wins, beta_losses, posterior_mean,
                posterior_var, sampled_score, total_signals, last_updated
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (inst_id, a, b, mean, var, sampled, total, now),
        )
        scored.append((inst_id, mean, var, total))

    # 标记 explore 候选: Total < 20 且 sampled_score 在 top 40%
    # 或 posterior_var 大（不确定性高）
    if scored:
        medians = np.median([s[1] for s in scored])
        low_sample = [s for s in scored if s[3] < 20]
        # 排序: 按 posterior_mean + sigma * 1.5 (upper confidence)
        low_sample.sort(
            key=lambda x: x[1] + 1.5 * np.sqrt(x[2]) if x[3] > 0 else 0,
            reverse=True,
        )
        # 取前 N 作为 explore 候选（总预算 15%，每个机构平均 3%，故取 5 个）
        n_explore = max(1, int(len(scored) * explore_budget_share / 3))
        explore_insts = [s[0] for s in low_sample[:n_explore]]
        for inst in explore_insts:
            conn.execute(
                "UPDATE mart_exploration_bandit SET is_explore_candidate=1 WHERE institution_id=?",
                (inst,),
            )
    conn.commit()

    # 统计
    summary_row = conn.execute(
        "SELECT COUNT(*), SUM(is_explore_candidate), AVG(posterior_mean) "
        "FROM mart_exploration_bandit"
    ).fetchone()
    report = {
        "institutions_scored": summary_row[0],
        "explore_candidates": summary_row[1],
        "avg_posterior_mean": round(float(summary_row[2] or 0), 4),
        "win_threshold_pct": win_threshold_pct,
        "explore_budget_share": explore_budget_share,
    }
    logger.info("[SEF Bandit] %s", report)
    return report


def sample_allocation(
    conn: sqlite3.Connection,
    *,
    total_budget: float = 1.0,
    explore_share: float = 0.15,
    top_k_exploit: int = 20,
    top_k_explore: int = 5,
    random_state: int = 42,
) -> dict:
    """基于当前 bandit 状态，生成机构权重分配建议.

    Exploit: 采样分数最高 + total_signals >= 20 的 top_k_exploit 个机构
    Explore: is_explore_candidate=1 的前 top_k_explore 个（按 UCB）
    """
    rng = np.random.default_rng(random_state)

    exploit = conn.execute(
        """
        SELECT institution_id, alpha_wins, beta_losses, posterior_mean
        FROM mart_exploration_bandit
        WHERE total_signals >= 20 AND is_explore_candidate=0
        ORDER BY sampled_score DESC LIMIT ?
        """,
        (top_k_exploit,),
    ).fetchall()

    explore = conn.execute(
        """
        SELECT institution_id, alpha_wins, beta_losses, posterior_mean
        FROM mart_exploration_bandit
        WHERE is_explore_candidate=1
        ORDER BY sampled_score DESC LIMIT ?
        """,
        (top_k_explore,),
    ).fetchall()

    # 重新采样作为权重 seed
    exploit_scores = [float(rng.beta(r[1], r[2])) for r in exploit]
    explore_scores = [float(rng.beta(r[1], r[2])) for r in explore]

    ex_total = sum(exploit_scores) or 1.0
    exp_total = sum(explore_scores) or 1.0
    exploit_weights = {
        r[0]: s / ex_total * (1 - explore_share) * total_budget
        for r, s in zip(exploit, exploit_scores)
    }
    explore_weights = {
        r[0]: s / exp_total * explore_share * total_budget
        for r, s in zip(explore, explore_scores)
    }
    allocation = {**exploit_weights, **explore_weights}
    return {
        "total_budget": total_budget,
        "explore_share": explore_share,
        "n_exploit": len(exploit),
        "n_explore": len(explore),
        "allocation": allocation,
    }
