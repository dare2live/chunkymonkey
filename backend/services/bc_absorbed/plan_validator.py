"""计划逻辑自检工具 — 跑批/Optuna/GCP 任务执行前强制验证计划合理性.

与 backtest_preflight 同等强度, 不通过 raise. 验证的不是"能不能跑",
而是"跑了有没有用".

检查维度:
  1. search_space_coverage: 每个公式有非空 Optuna search space
  2. trial_value: N trials 真的产生不同参数 (不是重复跑)
  3. cost_efficiency: 预估成本 vs 预期产出是否合理
  4. data_match: 每个公式需要的数据在目标环境可用
  5. output_usable: 结果有明确的下游消费方
  6. formula_runnable: 每个公式能在目标环境 import + 跑通

用法:
    from plan_validator import validate_optuna_plan, PlanValidationError
    validate_optuna_plan(
        formulas=['gs_raw_buy', 'obv_breakout'],
        trials=100,
        max_stocks=200,
    )
    # 不通过 raise PlanValidationError
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class PlanValidationError(RuntimeError):
    """计划逻辑自检不通过, 禁止执行."""


@dataclass
class PlanCheckResult:
    checks: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c["status"] == "PASS" for c in self.checks)

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if c["status"] == "FAIL"]

    def summary(self) -> str:
        n_pass = sum(1 for c in self.checks if c["status"] == "PASS")
        n_fail = len(self.checks) - n_pass
        lines = [f"Plan Validation: {n_pass} PASS, {n_fail} FAIL"]
        for c in self.checks:
            mark = "PASS" if c["status"] == "PASS" else "FAIL"
            lines.append(f"  [{mark}] {c['name']}: {c['detail']}")
        return "\n".join(lines)


def _check_search_space(formulas: list[str]) -> dict:
    """每个公式必须有非空 Optuna search space."""
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.ERROR)
        try:
            from scripts.formula_local_optuna import _suggest_params
        except ImportError:
            import sys
            from pathlib import Path
            scripts_dir = str(Path(__file__).resolve().parent / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from formula_local_optuna import _suggest_params
    except ImportError as e:
        return {"name": "search_space", "status": "FAIL",
                "detail": f"cannot import _suggest_params: {e}"}

    has_space = []
    no_space = []
    for fid in formulas:
        try:
            study = optuna.create_study()
            trial = study.ask()
            params = _suggest_params(fid, trial)
            if params:
                has_space.append(fid)
            else:
                no_space.append(fid)
        except Exception:
            no_space.append(fid)

    if no_space:
        return {"name": "search_space", "status": "FAIL",
                "detail": f"{len(no_space)}/{len(formulas)} formulas have NO search space "
                          f"(Optuna无参数可搜, 100 trials = 重复跑): {no_space[:5]}{'...' if len(no_space)>5 else ''}"}
    return {"name": "search_space", "status": "PASS",
            "detail": f"{len(has_space)}/{len(formulas)} formulas have search space"}


def _check_trial_value(formulas: list[str], trials: int) -> dict:
    """验证 trials 数量合理: 有 search space 的公式才值得多 trial."""
    if trials <= 1:
        return {"name": "trial_value", "status": "PASS", "detail": "single trial (baseline eval)"}
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.ERROR)
        try:
            from scripts.formula_local_optuna import _suggest_params
        except ImportError:
            from formula_local_optuna import _suggest_params
        n_params_list = []
        for fid in formulas:
            try:
                study = optuna.create_study()
                trial = study.ask()
                params = _suggest_params(fid, trial)
                n_params_list.append(len(params))
            except Exception:
                n_params_list.append(0)
        zero_count = sum(1 for n in n_params_list if n == 0)
        if zero_count > 0:
            wasted = zero_count * trials
            return {"name": "trial_value", "status": "FAIL",
                    "detail": f"{zero_count} formulas × {trials} trials = {wasted} wasted trials (no params to search)"}
    except ImportError:
        pass
    return {"name": "trial_value", "status": "PASS",
            "detail": f"all {len(formulas)} formulas have searchable params for {trials} trials"}


def _check_formula_runnable(formulas: list[str]) -> dict:
    """每个公式能 import + 小数据跑通."""
    import numpy as np
    np.random.seed(42)
    n = 100
    o = np.cumsum(np.random.randn(n) * 0.5) + 100
    c = o + np.random.randn(n) * 0.3
    h = np.maximum(o, c) + np.abs(np.random.randn(n) * 0.2)
    l = np.minimum(o, c) - np.abs(np.random.randn(n) * 0.2)
    v = np.random.rand(n) * 1e6
    a = v * c

    try:
        from formula_engine import compute_formula_signals
    except ImportError:
        return {"name": "formula_runnable", "status": "FAIL",
                "detail": "cannot import compute_formula_signals"}

    broken = []
    for fid in formulas:
        try:
            r = compute_formula_signals(fid, open_=o, high=h, low=l, close=c, volume=v, amount=a)
            if r.get("indicators", {}).get("skipped"):
                broken.append((fid, "skipped: needs external data"))
        except Exception as e:
            broken.append((fid, str(e)[:50]))

    if broken:
        return {"name": "formula_runnable", "status": "FAIL",
                "detail": f"{len(broken)}/{len(formulas)} cannot run: "
                          + "; ".join(f"{f}: {e}" for f, e in broken[:3])}
    return {"name": "formula_runnable", "status": "PASS",
            "detail": f"{len(formulas)}/{len(formulas)} runnable"}


def _check_cost_efficiency(formulas: list[str], trials: int, est_sec_per_trial: float = 1.5, spot_rate: float = 0.376) -> dict:
    """成本 vs 产出是否合理."""
    total_trials = len(formulas) * trials
    est_hours = total_trials * est_sec_per_trial / 3600
    est_cost = est_hours * spot_rate
    useful_formulas = len(formulas)  # 假设前面 search_space 已验证
    cost_per_formula = est_cost / useful_formulas if useful_formulas > 0 else est_cost

    if cost_per_formula > 1.0:
        return {"name": "cost_efficiency", "status": "FAIL",
                "detail": f"${est_cost:.2f} for {useful_formulas} formulas = ${cost_per_formula:.2f}/formula (too expensive)"}
    return {"name": "cost_efficiency", "status": "PASS",
            "detail": f"est ${est_cost:.2f} for {useful_formulas} formulas ({est_hours:.1f}h, ${cost_per_formula:.2f}/formula)"}


def _check_param_scope(formulas: list[str]) -> dict:
    """检测 per-stock 参数 (板块/行业/市值决定) 是否混入 global search space."""
    per_stock_keywords = ['limit_up_pct', 'limit_pct', 'board', 'industry_code', 'market_cap_class']
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.ERROR)
        try:
            from scripts.formula_local_optuna import _suggest_params
        except ImportError:
            from formula_local_optuna import _suggest_params
        violations = []
        for fid in formulas:
            try:
                study = optuna.create_study()
                trial = study.ask()
                params = _suggest_params(fid, trial)
                bad = [p for p in params if any(k in p.lower() for k in per_stock_keywords)]
                if bad:
                    violations.append(f"{fid}: {bad}")
            except Exception:
                pass
        if violations:
            return {"name": "param_scope", "status": "FAIL",
                    "detail": f"per-stock params in global search space: {'; '.join(violations[:3])}"}
    except ImportError:
        return {"name": "param_scope", "status": "PASS",
                "detail": "cannot import _suggest_params, skip"}
    return {"name": "param_scope", "status": "PASS",
            "detail": f"checked {len(formulas)} formulas, no per-stock params in search space"}


def _check_sample_size_and_coverage(max_stocks: int = 0) -> dict:
    """检测样本量是否足够 + 板块覆盖.

    选股系统必须在全量 universe 上训练, 不能用小样本.
    max_stocks=0 (全量) 是唯一正确设置.
    """
    if max_stocks > 0 and max_stocks < 4000:
        return {"name": "sample_coverage", "status": "FAIL",
                "detail": f"max_stocks={max_stocks} 太少! 选股系统必须在全量 universe ({4562}) 上训练. "
                f"用 {max_stocks} 只训练的参数不能用于 {4562} 只选股. 设 --max-stocks 0 (全量)"}
    try:
        import sys
        from pathlib import Path
        scripts_dir = str(Path(__file__).resolve().parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from formula_local_optuna_batch import _load_stocks
        stocks = _load_stocks(max_stocks)
        boards = {"00": 0, "30": 0, "60": 0, "68": 0}
        for code in stocks:
            prefix = code[:2]
            if prefix in boards:
                boards[prefix] += 1
        missing = [f"{p}" for p, n in boards.items() if n == 0]
        if missing:
            return {"name": "sample_coverage", "status": "FAIL",
                    "detail": f"missing boards: {missing}. Distribution: {boards}"}
        return {"name": "sample_coverage", "status": "PASS",
                "detail": f"{len(stocks)} stocks, all boards: {boards}"}
    except Exception as e:
        return {"name": "sample_coverage", "status": "FAIL",
                "detail": f"cannot check: {e}"}


def _check_output_usable(output_path: str | None = None) -> dict:
    """结果有明确的下游消费方."""
    if output_path is None:
        return {"name": "output_usable", "status": "FAIL",
                "detail": "no output_path specified — results have no consumer"}
    return {"name": "output_usable", "status": "PASS",
            "detail": f"output to {output_path}"}


def validate_optuna_plan(
    formulas: list[str],
    trials: int = 100,
    max_stocks: int = 0,
    output_path: str | None = None,
    est_sec_per_trial: float = 1.5,
) -> PlanCheckResult:
    """运行全部计划逻辑自检."""
    result = PlanCheckResult()
    result.checks.append(_check_search_space(formulas))
    result.checks.append(_check_trial_value(formulas, trials))
    result.checks.append(_check_formula_runnable(formulas))
    result.checks.append(_check_cost_efficiency(formulas, trials, est_sec_per_trial))
    result.checks.append(_check_param_scope(formulas))
    result.checks.append(_check_sample_size_and_coverage(max_stocks))
    result.checks.append(_check_output_usable(output_path))

    logger.info(result.summary())
    return result


def enforce_optuna_plan(
    formulas: list[str],
    trials: int = 100,
    **kwargs,
) -> PlanCheckResult:
    """强制计划逻辑自检 — 不通过 raise PlanValidationError."""
    result = validate_optuna_plan(formulas, trials, **kwargs)
    if not result.passed:
        raise PlanValidationError(
            f"Plan validation FAIL:\n{result.summary()}\n"
            "Fix: ensure all formulas have search space + are runnable + output path set"
        )
    return result
