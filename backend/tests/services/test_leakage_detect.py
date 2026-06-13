"""统一泄漏检测模块单测 — 抓真泄漏 + 不误报合法特征 + 误报控制 + 各阶段 + 教训表."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services import leakage_detect as L


def _synth(n=2000, seed=7):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.2).astype(int)
    return pd.DataFrame({
        "y": y,
        "ret_60d": rng.normal(0, 1, n) + 0.15 * y,          # 合法弱特征 (AUC ~0.55)
        "vol_z20d": rng.normal(0, 1, n),                     # 合法无关
        "follow_net_return_90d": y * 3 + rng.normal(0, 0.3, n),  # 标签泄漏 (名模式+高AUC)
        "forward_pe": rng.normal(20, 5, n),                   # 名带 forward 但合法 (低AUC) → 误报控制
    })


def test_catches_label_leakage_high():
    df = _synth()
    r = L.probe_feature_leakage(df, ["ret_60d", "vol_z20d", "follow_net_return_90d", "forward_pe"], "y")
    flagged = {f["feature"]: f["severity"] for f in r["flags"]}
    assert flagged.get("follow_net_return_90d") == "HIGH", "标签泄漏特征必须 HIGH"
    assert r["verdict"] == "HIGH"


def test_no_false_positive_on_legit_features():
    df = _synth()
    r = L.probe_feature_leakage(df, ["ret_60d", "vol_z20d", "follow_net_return_90d", "forward_pe"], "y")
    flagged = {f["feature"]: f for f in r["flags"]}
    # 合法弱特征不该被标
    assert "ret_60d" not in flagged and "vol_z20d" not in flagged, "合法特征不可误报"
    # forward_pe 名带 forward 但 AUC 低 → 应是 REVIEW (高误报警示) 不是 HIGH (误报控制核心)
    if "forward_pe" in flagged:
        assert flagged["forward_pe"]["severity"] == "REVIEW", "名模式低AUC = REVIEW 非 HIGH (防误杀合法 forward_pe)"
        assert flagged["forward_pe"]["false_positive_check"], "REVIEW 必带误报说明"


def test_declared_label_contract_authoritative():
    df = _synth()
    # builder 契约命中 = HIGH 权威 (即使名/AUC 不中)
    r = L.probe_feature_leakage(df, ["vol_z20d"], "y", declared_labels={"vol_z20d"})
    assert r["flags"][0]["severity"] == "HIGH" and "in_label_contract" in r["flags"][0]["reasons"]


def test_metric_anomaly_redlines():
    r = L.check_metric_anomaly({"auc": 0.82, "sharpe": 3.0, "win_rate": 0.6})
    assert r["verdict"] == "ALARM" and any(a["metric"] == "auc" for a in r["alarms"])
    assert L.check_metric_anomaly({"auc": 0.62, "sharpe": 1.5})["verdict"] == "CLEAN"


def test_split_discipline():
    assert L.check_split_discipline(label_horizon_days=180, embargo_days=180, split_mode="time")["verdict"] == "PASS"
    bad = L.check_split_discipline(label_horizon_days=180, embargo_days=20, split_mode="random")
    assert bad["verdict"] == "FAIL" and len(bad["problems"]) == 2


def test_lessons_registry_covers_known_patterns():
    pats = " ".join(l["pattern"] for l in L.LEAKAGE_LESSONS)
    for kw in ("标签", "latest-snapshot", "embargo", "异常高", "随机切", "flat current-mapping"):
        assert kw in pats, f"教训表缺 {kw}"
    # 每条必有 stage + detection
    for l in L.LEAKAGE_LESSONS:
        assert l.get("stage") and l.get("detection")
