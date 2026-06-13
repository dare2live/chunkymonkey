"""统一泄漏检测模块 — 分阶段子检测, 适时单用或 run_all 全面检测 (2026-06-13, 用户收口指令).

整合项目积累的泄漏检测能力与经验教训 (CLAUDE.md §4.1/4.2/4.5 + mythos §3 + 既有
audit_panel_leakage.py)。一个模块, 四阶段, 每阶段对应数据流的一个泄漏面:

  STAGE 1 panel_build     面板构建 SQL 是否泄漏 (JOIN PIT / flat-mapping / fallback / 时序方差)
                          → 编排既有 backend/scripts/audit_panel_leakage.py (不重造其 746 行 5 检查)
  STAGE 2 feature_consumer 消费方是否把标签/前瞻列当特征 (S3 盲区, 本模块新增核心能力)
                          → 标签契约命中 + 名模式 + 单特征 AUC 探针
  STAGE 3 model_output    模型产出是否异常高 (§4.2 异常即泄漏警报)
                          → AUC/RankIC/sharpe/win_rate/年化/相对提升 红线
  STAGE 4 split_discipline 时间切分纪律 (重叠标签 embargo / 时间切非随机切)
                          → embargo>=label_horizon + split_mode==time

**定位: 复核筛选器, 非神谕** — 输出是待人核的嫌疑 + 证据 + 误报说明, 不自动封杀/删特征。
工具本身可能误判 (用户点的), 故每个 flag 标可信度 (契约命中权威 / AUC 经验强 / 名模式高误报)。

用法 (CLI 在 backend/scripts/leakage_probe.py):
  适时单阶段: leakage_probe --stage feature-consumer --panel X --label-col y
  全面:       leakage_probe --stage all ...
  查教训表:   leakage_probe --lessons
可复用入口 (实验/训练脚本事前调):
  from services.leakage_detect import probe_feature_leakage, check_metric_anomaly, check_split_discipline
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

# ── STAGE 2 常量 ──
FORWARD_NAME_PATTERNS = [
    r"^forward_", r"^follow_", r"^fwd_", r"^future_", r"^lead_",
    r"_ahead\b", r"^target_", r"_target$", r"_label$", r"^label_",
    r"_fwd\d", r"forward_ret", r"follow_net_return",
]
_FWD_RE = re.compile("|".join(FORWARD_NAME_PATTERNS))
SINGLE_FEATURE_AUC_MAX = 0.70  # 单特征原始 AUC 超此 = 泄漏嫌疑 (合法 ~0.55-0.65; 主升浪K线天花板~60%)

# ── STAGE 3 §4.2 异常高红线 (绝对) ──
METRIC_RED_LINES = {
    "auc": 0.75,                # 难的前瞻二分任务 (主升浪类); 易任务可经 --auc-redline 放宽
    "rankic": 0.30,
    "sharpe": 5.0,
    "win_rate": 0.95,
    "annual_return": 1.00,      # 100%
    "relative_improvement": 0.50,  # 相对 baseline +50% (相对红线)
}


def _name_flagged(col: str) -> bool:
    return bool(_FWD_RE.search(col))


def _single_feature_auc(values, y):
    import numpy as np
    import pandas as pd
    from sklearn.metrics import roc_auc_score
    v = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)  # NAType/Arrow→nan
    yy = pd.to_numeric(pd.Series(y), errors="coerce").to_numpy(dtype=float)
    mask = ~np.isnan(v) & ~np.isnan(yy)
    if mask.sum() < 50 or len(set(yy[mask].tolist())) < 2:
        return None
    vv, yt = v[mask], yy[mask]
    if vv.min() == vv.max():
        return 0.5
    try:
        a = roc_auc_score(yt, vv)
    except Exception:
        return None
    return max(a, 1.0 - a)


def probe_feature_leakage(df, feature_cols, label_col, *, declared_labels=None,
                          single_feature_auc_max=SINGLE_FEATURE_AUC_MAX):
    """STAGE 2 消费方特征-标签泄漏探针 (S3 盲区). 三信号分级 + 误报说明.

    in_label_contract (HIGH 权威) / single_feature_auc 超阈 (HIGH 经验强, 阈值任务相关) /
    forward_name_pattern only (REVIEW 高误报: forward_pe/guidance 类已公告合法特征)。
    """
    declared = set(declared_labels or [])
    y = df[label_col].to_numpy()
    flags = []
    for c in feature_cols:
        if c == label_col:
            continue
        reasons, fp = [], []
        if c in declared:
            reasons.append("in_label_contract")
        if _name_flagged(c):
            reasons.append("forward_name_pattern")
            fp.append("名模式易误报: 确认非 forward_pe/guidance 类已公告合法特征")
        auc = _single_feature_auc(df[c].to_numpy(), y)
        if auc is not None and auc > single_feature_auc_max:
            reasons.append(f"single_feature_auc={auc:.3f}>{single_feature_auc_max}")
            fp.append(f"AUC 阈值任务相关: 易 label 下合法特征也可能 >{single_feature_auc_max}")
        if reasons:
            hard = ("in_label_contract" in reasons) or any(r.startswith("single_feature_auc") for r in reasons)
            flags.append({"feature": c, "severity": "HIGH" if hard else "REVIEW",
                          "reasons": reasons, "single_feature_auc": round(auc, 3) if auc is not None else None,
                          "false_positive_check": fp})
    high = [f for f in flags if f["severity"] == "HIGH"]
    verdict = "HIGH" if high else ("REVIEW" if flags else "CLEAN")
    return {"stage": "feature_consumer", "verdict": verdict,
            "n_features": len([c for c in feature_cols if c != label_col]),
            "n_high": len(high), "n_review": len(flags) - len(high), "flags": flags,
            "disclaimer": "复核筛选器非神谕: HIGH=嫌疑(契约命中/AUC经验强), REVIEW=人核(名模式高误报)"}


def check_metric_anomaly(metrics: dict, *, red_lines: dict | None = None):
    """STAGE 3 模型产出异常高 = 泄漏警报 (§4.2). metrics: {auc/rankic/sharpe/win_rate/年化/...}."""
    rl = {**METRIC_RED_LINES, **(red_lines or {})}
    alarms = []
    for k, v in metrics.items():
        if v is None:
            continue
        if k in rl and float(v) > rl[k]:
            alarms.append({"metric": k, "value": float(v), "red_line": rl[k],
                           "note": f"{k}={v} > §4.2 红线 {rl[k]} — 异常高, 先怀疑泄漏不兴奋 (真实 forward 期望恒低于回测)"})
    return {"stage": "model_output", "verdict": "ALARM" if alarms else "CLEAN", "alarms": alarms}


def check_split_discipline(*, label_horizon_days: int, embargo_days: int, split_mode: str):
    """STAGE 4 切分纪律: 重叠标签 embargo>=horizon + 时间切非随机切 (§4.1 + S3 教训)."""
    problems = []
    if split_mode != "time":
        problems.append(f"split_mode={split_mode} != time — 随机切 = 泄漏 (§4.1)")
    if embargo_days < label_horizon_days:
        problems.append(f"embargo {embargo_days}d < label_horizon {label_horizon_days}d — 训练/测试前瞻窗重叠 = 标签泄漏 (S3 教训)")
    return {"stage": "split_discipline", "verdict": "FAIL" if problems else "PASS", "problems": problems}


def run_panel_build_audit(panel: str, db: str = "data/smartmoney.duckdb"):  # rule-compliance: ok evidence=默认库路径参数, 委托 audit_panel_leakage 子进程非本模块 connect
    """STAGE 1 编排既有 audit_panel_leakage.py (不重造其 SQL/flat-mapping/fallback/方差 5 检查)."""
    script = REPO / "backend" / "scripts" / "audit_panel_leakage.py"
    if not script.exists():
        return {"stage": "panel_build", "verdict": "SKIP", "reason": "audit_panel_leakage.py 不在"}
    r = subprocess.run([sys.executable, str(script), "--panel", panel, "--db", db],
                       capture_output=True, text=True, cwd=str(REPO),
                       env={"PYTHONPATH": str(REPO / "backend"), "PATH": __import__("os").environ.get("PATH", "")})
    v = "CLEAN" if r.returncode == 0 else ("HIGH" if r.returncode == 1 else "MEDIUM")
    return {"stage": "panel_build", "verdict": v, "exit_code": r.returncode,
            "delegated_to": "audit_panel_leakage.py", "tail": r.stdout[-400:]}


# ── 教训登记表: 项目踩过的每种泄漏 → 覆盖它的阶段/检测 (整合经验, 防再踩) ──
LEAKAGE_LESSONS = [
    {"pattern": "标签/前瞻列当特征 (follow_net_return/forward_ret 喂模型)", "source": "S3 2026-06-13",
     "stage": "feature_consumer", "detection": "标签契约命中 + 名模式 + 单特征 AUC>0.7"},
    {"pattern": "latest-snapshot 贴历史 (snapshot_date=MAX 而非 as-of<=t)", "source": "§4.5 / 体检HIGH",
     "stage": "panel_build", "detection": "audit_panel_leakage JOIN PIT-strict"},
    {"pattern": "in-sample 统计入 live (非 oos_* 列 / MAX(oos)GROUP BY 给未来)", "source": "§4.5 v3.2",
     "stage": "model_output+消费侧", "detection": "只读 oos_* 守门 + §4.2 异常红线"},
    {"pattern": "flat current-mapping 后视偏差 (dim_* 无 PIT 标记入 PARTITION BY)", "source": "§4.5 sector_tdx_l1_rel 92%跌",
     "stage": "panel_build", "detection": "audit_panel_leakage check3 flat-mapping + check5 时序方差"},
    {"pattern": "宽表透传泄漏列 (v3.* 未 EXCLUDE)", "source": "§4.5",
     "stage": "feature_consumer", "detection": "标签契约 + 名模式 (本模块对消费列逐个查)"},
    {"pattern": "unknown 当 0 参与 (应 NaN 让聚合排除)", "source": "mythos §3",
     "stage": "panel_build", "detection": "audit_panel_leakage / 数据审计 (本模块不覆盖, 引用)"},
    {"pattern": "随机切 train/test (应时间切)", "source": "§4.1",
     "stage": "split_discipline", "detection": "split_mode==time"},
    {"pattern": "重叠标签无 embargo (前瞻窗跨训练/测试)", "source": "S3 2026-06-13",
     "stage": "split_discipline", "detection": "embargo>=label_horizon"},
    {"pattern": "异常高数字当兴奋 (RankIC>0.3/sharpe>5/年化>100%/相对+50%)", "source": "§4.2",
     "stage": "model_output", "detection": "METRIC_RED_LINES 绝对+相对红线"},
    {"pattern": "JOIN 无 built_at<=t / as_of_date", "source": "§4.1",
     "stage": "panel_build", "detection": "audit_panel_leakage check1/2 SOURCE PIT + JOIN PIT-strict"},
]


def run_all(*, df=None, feature_cols=None, label_col=None, declared_labels=None,
            metrics=None, split=None, panel=None, db="data/smartmoney.duckdb"):  # rule-compliance: ok evidence=默认库路径参数, 委托子进程非本函数 connect
    """全面检测: 传哪个阶段的料就跑哪个阶段 (适时单用) 或全传 (每次全面)."""
    results = []
    if df is not None and feature_cols and label_col:
        results.append(probe_feature_leakage(df, feature_cols, label_col, declared_labels=declared_labels))
    if metrics:
        results.append(check_metric_anomaly(metrics))
    if split:
        results.append(check_split_discipline(**split))
    if panel:
        results.append(run_panel_build_audit(panel, db))
    bad = [r for r in results if r["verdict"] in ("HIGH", "ALARM", "FAIL")]
    return {"overall": "LEAKAGE_SUSPECT" if bad else ("REVIEW" if any(r["verdict"] == "REVIEW" for r in results) else "CLEAN"),
            "stages_run": [r["stage"] for r in results], "results": results}
