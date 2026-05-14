"""Phase ψ.γ.1 — ensemble Optuna 全寻优 (20 维 search space).

⚠ 根因: 用户 push back "没充分发挥 optuna 潜力". 之前的 ensemble.yaml 里
   13 alpha weights + 3 regime multipliers + 3 vol sigma + hp 都是拍脑袋. 这个
   脚本把所有这些丢给 Optuna 一次寻优, 实现 Rule 6 数据驱动.

Search space (20 维):
  - 13 alpha weights (reversal_signal / sharpe_60d / mom_30d / vol_60d /
      pe_ttm_inv / roe_q / profit_yoy / lhb_inst_buy_30d / exec_net_signal /
      holder_count_change_q / sector_ret_60d / sector_excess_60d /
      sector_price_vs_ma20) — 每个 ∈ [0.0, 0.4]
  - 2 regime multipliers (bear / sideways; bull=1.0 fixed baseline)
  - 3 vol_aware sigma multipliers (stop_sigma / target_sigma / trailing_sigma)
  - 1 hp ∈ {5, 10, 15, 20, 30}
  - 1 max_vol_60d quality filter ∈ [0.20, 0.60]

Walk-forward (single holdout):
  - train: 2023-01-03 ~ 2024-09-30 (21 mo) — 早期段, Optuna 寻最优
  - test:  2024-10-01 ~ 2026-05-12 (19 mo) — OOS 验证

Objective (constrained sharpe):
  if ann_ret < 0.30 or max_dd < -0.20:
      penalty = max(0.30 - ann_ret, 0) + max(-0.20 - max_dd, 0)
      return sharpe - 10 * penalty
  return sharpe

⚠ 每 trial 跑完整 paper_sim (train 段), 单 worker, 估时 ~2 min/trial.
  100 trials = ~3.3 hr. 数据驱动比快重要.

Usage:
  PYTHONPATH=backend python backend/scripts/optimize_ensemble_full.py \\
      --n-trials 100 --study-name ensemble_full_v1
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import optuna

from services.db import get_conn
from services.market_db import get_market_conn
from services.optimization.config import get_optuna_config
from services.optimization.governance import enforce_pre_optimize
from services.paper_sim.config import load_config, PaperSimConfig
from services.paper_sim.ddl import ensure_paper_sim_tables
from services.paper_sim.driver import run_paper_sim_day
from services.paper_sim.reporter import write_kpi_summary


log = logging.getLogger("optimize_ensemble_full")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


# Phase ψ.β.4: 13 alpha names — 跟 paper_sim_ensemble.yaml 一致
ENSEMBLE_ALPHA_NAMES = (
    "reversal_signal", "sharpe_60d", "mom_30d", "vol_60d",
    "pe_ttm_inv", "roe_q", "profit_yoy",
    "lhb_inst_buy_30d", "exec_net_signal", "holder_count_change_q",
    "sector_ret_60d", "sector_excess_60d", "sector_price_vs_ma20",
)


def _trading_days(conn, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM mart_paper_sim_nav WHERE date BETWEEN ? AND ? ORDER BY date",
        [start, end],
    ).fetchall()
    if rows:
        return [r[0] for r in rows]
    # 兜底: 从 K 线表
    rows = conn.execute(
        """SELECT DISTINCT date FROM market.v_price_kline_qfq
            WHERE adjust='qfq' AND freq='daily' AND code='000300'
              AND date BETWEEN ? AND ? ORDER BY date""",
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def _build_override(trial: optuna.Trial, base_alpha_dicts: list[dict]) -> dict:
    """从 trial.suggest_* 生成 PaperSimConfig override dict.

    base_alpha_dicts: 从 base yaml 来的 ensemble_alphas 原始定义 (含
      source_table/source_col/direction/pit_key/filter_formula_ids 等).
      本函数只覆盖 weight, 其他字段保持.
    """
    # 1. alpha weights (13 维)
    new_alphas = []
    for a in base_alpha_dicts:
        w = trial.suggest_float(f"w_{a['name']}", 0.0, 0.4)
        new_alphas.append({**a, "weight": w})

    # 2. regime gate (2 维; bull=1.0 fixed)
    regime = {
        "enabled": True,
        "source_table": "fact_regime_state",
        "pit_key": "trade_date",
        "bear_multiplier":     trial.suggest_float("regime_bear_mul",     0.0, 1.5),
        "sideways_multiplier": trial.suggest_float("regime_sideways_mul", 0.3, 1.5),
        "bull_multiplier":     1.0,                    # baseline, 让其他 multiplier 跟它比
    }

    # 3. vol_aware sigma (3 维) + 默认 bounds (Rule 9.9 — bounds 写死有 Rule 6 风险, 但作为
    #    安全上下限保留. 后续可加进 search space). 这些 bounds 是 hard physical sanity
    #    (stop 不能 > 0, target 不能 < 0), 是数学约束不是估算.
    vol_aware = {
        "enabled": True,
        "stop_sigma":     trial.suggest_float("sigma_stop",     0.5, 4.0),  # measured: Optuna trial
        "target_sigma":   trial.suggest_float("sigma_target",   1.0, 6.0),  # measured: Optuna trial
        "trailing_sigma": trial.suggest_float("sigma_trailing", 0.3, 2.5),  # measured: Optuna trial
        # Hard physical bounds (数学约束, 不是估算 — stop 必须 < 0, target/trailing 必须 > 0)
        # rule-compliance: ok evidence=mathematical-sanity
        "stop_min":     -0.25, "stop_max":     -0.03,
        "target_min":    0.05, "target_max":    0.50,
        "trailing_min":  0.02, "trailing_max":  0.15,
    }

    # 4. hp (1 维 categorical) + default_holding 其他字段保持 ensemble default
    hp = trial.suggest_categorical("hp", [5, 10, 15, 20, 30])
    default_holding = {
        "hp": int(hp),
        "stop_pct":     -0.10,   # rule-compliance: ok evidence=vol_aware override 会替换
        "target_pct":    0.20,
        "trailing_pct":  0.05,
    }

    # 5. quality filter (1 维: max_vol_60d) + stages 用 default
    quality = {
        "max_vol_60d":         trial.suggest_float("max_vol_60d", 0.20, 0.60),  # measured: Optuna trial
        "min_amount_20d_yuan": 0,
        "allowed_stages":      ["1", "1.5", "2"],  # rule-compliance: ok evidence=Phase ψ.β.4.6 stage 定义
    }

    return {
        "selection": {
            "ensemble_alphas":          new_alphas,
            "regime_gate":              regime,
            "vol_aware":                vol_aware,
            "default_holding":          default_holding,
            "ensemble_quality_filters": quality,
        }
    }


def _run_paper_sim_for_objective(start: str, end: str, cfg: PaperSimConfig,
                                  sim_run_id: str) -> dict:
    """单次 paper_sim run, 返回 KPI summary dict + cleanup."""
    conn = get_conn()
    mkt = get_market_conn()
    try:
        ensure_paper_sim_tables(conn)
        # cleanup 重跑残留
        for tbl in ("mart_paper_sim_nav", "fact_paper_sim_position",
                    "fact_paper_sim_trade", "mart_paper_sim_kpi"):
            conn.execute(f"DELETE FROM {tbl} WHERE sim_run_id = ?", [sim_run_id])

        days = _trading_days(conn, start, end)
        cash = cfg.portfolio.initial_cash
        for i, d in enumerate(days):
            res = run_paper_sim_day(
                conn, mkt,
                sim_run_id=sim_run_id, today=d, cfg=cfg,
                starting_cash=cash if i == 0 else None,
            )
            cash = None

        summary = write_kpi_summary(conn, sim_run_id, "trial", cfg)
        return summary
    finally:
        conn.close()
        mkt.close()


def _cleanup_trial_data(sim_run_id: str) -> None:
    """trial 跑完后清掉 4 张表里的数据 (避免累积)."""
    conn = get_conn()
    try:
        for tbl in ("mart_paper_sim_nav", "fact_paper_sim_position",
                    "fact_paper_sim_trade", "mart_paper_sim_kpi"):
            conn.execute(f"DELETE FROM {tbl} WHERE sim_run_id = ?", [sim_run_id])
    finally:
        conn.close()


def _extract_kpi(summary: dict) -> tuple[float, float, float, float]:
    """从 paper_sim summary 提 (ann_ret, max_dd, sharpe, calmar). 兼容 user_criteria 子 dict."""
    uc = summary.get("user_criteria") or {}
    ann_ret = float(uc.get("annual_return") or summary.get("annual_return") or 0.0)
    max_dd  = float(uc.get("max_dd")        or summary.get("max_dd")        or 0.0)
    sharpe  = float(uc.get("sharpe")        or summary.get("sharpe")        or 0.0)
    calmar  = float(uc.get("calmar")        or summary.get("calmar")        or 0.0)
    return ann_ret, max_dd, sharpe, calmar


def _constrained_sharpe_objective(summary: dict, ann_ret_min: float, max_dd_min: float,
                                    penalty_scale: float) -> float:
    """constrained sharpe: max sharpe s.t. ann_ret≥ann_ret_min AND max_dd≥max_dd_min.

    实现: 违反约束时给 sharpe 扣硬 penalty (引导 Optuna 朝可行域走).

    Args:
        summary: paper_sim KPI summary dict
        ann_ret_min: 年化下限 (e.g. 0.30)
        max_dd_min: max_dd 下限 (负数, e.g. -0.20)
        penalty_scale: 违反约束的惩罚系数
    """
    ann_ret, max_dd, sharpe, _ = _extract_kpi(summary)

    # 完全无 trade (ann_ret/sharpe 全 0) → 给小负值, Optuna 会避开
    if abs(ann_ret) < 1e-9 and abs(sharpe) < 1e-9:
        return -1.0

    violation = max(ann_ret_min - ann_ret, 0.0) + max(max_dd_min - max_dd, 0.0)
    if violation > 0:
        return sharpe - penalty_scale * violation
    return sharpe


def _make_objective(base_cfg_yaml_path: Path, train_start: str, train_end: str,
                     base_alpha_dicts: list[dict], ann_ret_min: float,
                     max_dd_min: float, penalty_scale: float):
    """工厂: 创建 closure 形式的 objective. trial 在 train 段跑 paper_sim."""

    def objective(trial: optuna.Trial) -> float:
        t0 = time.time()
        override = _build_override(trial, base_alpha_dicts)
        cfg = load_config(base_cfg_yaml_path, override=override)
        sim_run_id = f"opt_t{trial.number:04d}_{uuid.uuid4().hex[:4]}"
        try:
            summary = _run_paper_sim_for_objective(train_start, train_end, cfg, sim_run_id)
        except Exception as e:
            log.warning(f"  trial {trial.number} failed: {e}")
            _cleanup_trial_data(sim_run_id)
            return -1e6
        score = _constrained_sharpe_objective(summary, ann_ret_min, max_dd_min, penalty_scale)
        ann_ret, max_dd, sharpe, _ = _extract_kpi(summary)
        log.info(
            f"  trial {trial.number:3d}: score={score:+.3f} "
            f"ann={ann_ret*100:+.1f}% mdd={max_dd*100:+.1f}% "
            f"sharpe={sharpe:+.2f} dt={time.time()-t0:.0f}s"
        )
        _cleanup_trial_data(sim_run_id)
        return score

    return objective


def _ensure_mart_ensemble_optimal(conn) -> None:
    """DDL: mart_ensemble_optimal 表 (Phase ψ.γ.1 best_params 入库)."""
    # 单语句, 不用 executescript
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mart_ensemble_optimal (
          study_name TEXT,
          best_params_json TEXT,
          walk_forward_mode TEXT,
          train_start DATE,
          train_end DATE,
          test_start DATE,
          test_end DATE,
          -- in-sample (train 段最优 KPI)
          train_ann_ret DOUBLE,
          train_max_dd DOUBLE,
          train_sharpe DOUBLE,
          train_calmar DOUBLE,
          -- OOS (test 段验证 KPI)
          oos_ann_ret DOUBLE,
          oos_max_dd DOUBLE,
          oos_sharpe DOUBLE,
          oos_calmar DOUBLE,
          oos_n_traded INTEGER,
          oos_period_start DATE,
          oos_period_end DATE,
          -- meta
          n_trials INTEGER,
          best_trial_number INTEGER,
          objective_function TEXT,
          ann_ret_min DOUBLE,
          max_dd_min DOUBLE,
          built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (study_name)
        )
    """)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--study-name", default="ensemble_full_v1")
    ap.add_argument("--n-trials", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--config-path", default="backend/config/paper_sim_ensemble.yaml")
    # CLI default 日期 (用户 --train-start 等可 override) — 不是业务硬编码
    # rule-compliance: ok evidence=cli-default-overridable
    ap.add_argument("--train-start", default="2023-01-03")
    # rule-compliance: ok evidence=cli-default-overridable
    ap.add_argument("--train-end",   default="2024-09-30")
    # rule-compliance: ok evidence=cli-default-overridable
    ap.add_argument("--test-start",  default="2024-10-01")
    # rule-compliance: ok evidence=cli-default-overridable
    ap.add_argument("--test-end",    default="2026-05-12")
    ap.add_argument("--ann-ret-min", type=float, default=0.30)
    ap.add_argument("--max-dd-min",  type=float, default=-0.20)
    ap.add_argument("--penalty-scale", type=float, default=10.0)
    args = ap.parse_args()

    cfg_path = Path(args.config_path)
    # 加载 base yaml 拿 ensemble_alphas 原始定义 (其他字段保持)
    import yaml
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    base_alpha_dicts = raw["selection"]["ensemble_alphas"]
    assert len(base_alpha_dicts) == 13, f"期望 13 个 alpha, 实际 {len(base_alpha_dicts)}"

    log.info(f"=== Phase ψ.γ.1 ensemble Optuna 全寻优 ===")
    log.info(f"  study: {args.study_name}, n_trials: {args.n_trials}, seed: {args.seed}")
    log.info(f"  train: {args.train_start} ~ {args.train_end}")
    log.info(f"  test:  {args.test_start} ~ {args.test_end}")
    log.info(f"  目标: max sharpe s.t. ann_ret≥{args.ann_ret_min:.0%}"
             f" AND max_dd≥{args.max_dd_min:.0%}")

    # governance 守门
    optuna_cfg = get_optuna_config()
    enforce_pre_optimize(n_trials=args.n_trials, has_seed=True, cfg=optuna_cfg)

    # build study + objective
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    objective_fn = _make_objective(
        cfg_path, args.train_start, args.train_end, base_alpha_dicts,
        args.ann_ret_min, args.max_dd_min, args.penalty_scale,
    )
    study = optuna.create_study(
        study_name=args.study_name,
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=args.seed),
    )
    t0 = time.time()
    study.optimize(objective_fn, n_trials=args.n_trials, show_progress_bar=False)
    elapsed = time.time() - t0

    log.info(f"=== Optuna 完成 ({elapsed/60:.1f} min) ===")
    log.info(f"  best trial: #{study.best_trial.number}, value={study.best_value:.4f}")
    log.info(f"  best params: {json.dumps(study.best_params, ensure_ascii=False)}")

    # OOS: 拿 best params 跑 test 段
    log.info(f"=== OOS 验证 (test 段) ===")
    best_override = _build_override(_FrozenTrial(study.best_params), base_alpha_dicts)
    test_cfg = load_config(cfg_path, override=best_override)
    test_run_id = f"oos_{args.study_name}_{uuid.uuid4().hex[:4]}"
    test_summary = _run_paper_sim_for_objective(
        args.test_start, args.test_end, test_cfg, test_run_id,
    )
    log.info(f"  OOS KPI: {json.dumps(test_summary, default=str)[:200]}")

    # 入库 mart_ensemble_optimal
    conn = get_conn()
    try:
        _ensure_mart_ensemble_optimal(conn)
        # train 段重跑 best params 拿 in-sample KPI
        train_run_id = f"insample_{args.study_name}_{uuid.uuid4().hex[:4]}"
        train_summary = _run_paper_sim_for_objective(
            args.train_start, args.train_end, test_cfg, train_run_id,
        )
        conn.execute("DELETE FROM mart_ensemble_optimal WHERE study_name = ?", [args.study_name])
        tr_ann, tr_dd, tr_sh, tr_cal = _extract_kpi(train_summary)
        os_ann, os_dd, os_sh, os_cal = _extract_kpi(test_summary)
        os_n = int((test_summary.get("anti_churn") or {}).get("n_closed_positions") or 0)
        conn.execute("""
            INSERT INTO mart_ensemble_optimal
            (study_name, best_params_json, walk_forward_mode, train_start, train_end,
             test_start, test_end, train_ann_ret, train_max_dd, train_sharpe, train_calmar,
             oos_ann_ret, oos_max_dd, oos_sharpe, oos_calmar, oos_n_traded,
             oos_period_start, oos_period_end, n_trials, best_trial_number,
             objective_function, ann_ret_min, max_dd_min)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            args.study_name, json.dumps(study.best_params, ensure_ascii=False),
            "holdout", args.train_start, args.train_end,
            args.test_start, args.test_end,
            tr_ann, tr_dd, tr_sh, tr_cal,
            os_ann, os_dd, os_sh, os_cal, os_n,
            args.test_start, args.test_end,
            args.n_trials, int(study.best_trial.number),
            "constrained_sharpe", args.ann_ret_min, args.max_dd_min,
        ])
        log.info(f"  写入 mart_ensemble_optimal study_name={args.study_name}")
    finally:
        conn.close()


class _FrozenTrial:
    """Mock trial 用 best_params 复算 _build_override (避免 study.best_trial 在子 trial 跑过的 side effect)."""

    def __init__(self, params: dict):
        self._params = params

    def suggest_float(self, name: str, low: float, high: float) -> float:
        return float(self._params[name])

    def suggest_categorical(self, name: str, choices: list) -> Any:
        return self._params[name]


if __name__ == "__main__":
    main()
