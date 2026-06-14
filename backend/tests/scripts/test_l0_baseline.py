"""L0 裸K线基准驱动 run_formula 集成测试 — 守 3 门固化 + 合成数据 PIT-clean。

driver 的 DB I/O 不单测 (main 走真库); 核心 run_formula 用合成 by_code 测: PIT 门过 + IC 出 + anomaly 判。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location("l0d", REPO / "backend" / "scripts" / "experiment_l0_baseline.py")
l0d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(l0d)


def _synth_market(n_stocks: int = 30, n_days: int = 200, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    out = {}
    for s in range(n_stocks):
        close = np.maximum(100 + np.cumsum(rng.normal(0, 1, n_days)), 1.0)
        dates = [f"2024{(d // 21) % 12 + 1:02d}{d % 28 + 1:02d}" for d in range(n_days)]
        # 让日期跨 >12 月以触发 expanding_monthly
        dates = []
        for d in range(n_days):
            mo = d // 17  # ~每 17 交易日一月, 200 天 ~ 12 月
            yy, mm = (2024, mo + 1) if mo < 12 else (2025, mo - 11)
            dates.append(f"{yy}{mm:02d}{(d % 28) + 1:02d}")
        out[f"s{s}"] = {"date": dates, "close": close.tolist(),
                        "high": (close + 0.5).tolist(), "low": (close - 0.5).tolist()}
    return out


def test_run_formula_pit_clean_and_returns_ic():
    res = l0d.run_formula("reversal_short_term", _synth_market(), horizon=5)
    assert res["pit_clean"] is True                       # 门1 真实提取器 PIT-clean
    assert res["formula"] == "reversal_short_term"
    assert "oos_rank_ic" in res["ic"]                     # IC 结构
    assert res["anomaly"]["verdict"] in ("CLEAN", "ALARM")  # 门3 异常判
    assert res["n_obs"] > 0


def test_run_formula_anomaly_clean_for_modest_ic():
    # 随机游走 -> IC ~0 -> anomaly CLEAN (不虚高触红线)
    res = l0d.run_formula("macd_golden_cross", _synth_market(seed=11), horizon=5)
    assert res["anomaly"]["verdict"] == "CLEAN", "随机数据 IC 不该触 §4.2 红线"


def test_driver_wires_three_leakage_gates():
    # 反孤儿 (mythos §14): driver 必须真 import + 调 3 防泄露工具
    src = (REPO / "backend" / "scripts" / "experiment_l0_baseline.py").read_text()
    assert "assert_pit_clean" in src        # 门1 PIT 行为门
    assert "check_split_discipline" in src   # 门2 切分纪律
    assert "check_metric_anomaly" in src     # 门3 异常红线
