"""Leakage Probe CLI — 统一泄漏检测的命令行入口 (薄壳, 逻辑在 services.leakage_detect 模块).

模块分 4 阶段 (panel_build / feature_consumer / model_output / split_discipline), 适时单用或 --stage all
全面检测。整合项目积累的泄漏检测能力与教训 (CLAUDE.md §4.1/4.2/4.5 + mythos §3 + audit_panel_leakage)。
**复核筛选器非神谕**: 输出嫌疑+证据+误报说明, 不自动封杀 (工具本身可能误判, 见 false_positive_check)。

用法:
  # 消费方特征-标签泄漏 (S3 盲区; 库内表含 label 列, 或调用方先建带 label 视图)
  PYTHONPATH=backend python backend/scripts/leakage_probe.py --stage feature-consumer \\
      --db data/smartmoney.duckdb --panel <表> --label-col <二分label> [--label-contract <builder.py>]
  # 模型产出异常 (§4.2 红线)
  ... --stage model-output --metric auc=0.78 --metric sharpe=6
  # 切分纪律
  ... --stage split --label-horizon 180 --embargo 180 --split-mode time
  # 面板构建 (编排既有 audit_panel_leakage.py)
  ... --stage panel-build --panel mart_p0a_feature_label_panel_v4
  # 教训登记表
  ... --lessons
退出码: 0 CLEAN, 1 LEAKAGE_SUSPECT/HIGH/ALARM/FAIL, 2 REVIEW (人核)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services import leakage_detect as L  # noqa: E402


def _exit_code(verdict: str) -> int:
    return {"CLEAN": 0, "PASS": 0, "REVIEW": 2}.get(verdict, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=["feature-consumer", "model-output", "split", "panel-build", "all"])
    ap.add_argument("--db", default="data/smartmoney.duckdb")  # rule-compliance: ok evidence=CLI 默认库路径参数, read_only 探针非生产 connect
    ap.add_argument("--panel"); ap.add_argument("--label-col"); ap.add_argument("--features", default="auto")
    ap.add_argument("--label-contract", default="")
    ap.add_argument("--single-feature-auc-max", type=float, default=L.SINGLE_FEATURE_AUC_MAX)
    ap.add_argument("--metric", action="append", default=[], help="model-output: name=value, 可多次")
    ap.add_argument("--label-horizon", type=int); ap.add_argument("--embargo", type=int)
    ap.add_argument("--split-mode", default="time")
    ap.add_argument("--where", default="")
    ap.add_argument("--lessons", action="store_true", help="打印泄漏教训登记表")
    ap.add_argument("--gate", action="store_true",
                    help="遍历 leakage_consumers.yaml 逐消费者跑事前探针 (强制闸; 任一 HIGH 即 exit 1)")
    args = ap.parse_args()

    if args.lessons:
        print(json.dumps(L.LEAKAGE_LESSONS, ensure_ascii=False, indent=1))
        return 0

    if args.gate:
        return _run_gate()

    def _load_df():
        import duckdb
        con = duckdb.connect(args.db, read_only=True)  # rule-compliance: ok evidence=read_only 泄漏探针
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?", [args.panel]).fetchall()]
        feats = ([c for c in cols if c != args.label_col] if args.features == "auto"
                 else [c.strip() for c in args.features.split(",")])
        where = f"WHERE {args.where}" if args.where else ""
        sel = ", ".join('"' + c + '"' for c in [args.label_col, *feats])
        df = con.execute(f"SELECT {sel} FROM {args.panel} {where}").df()
        con.close()
        return df, feats

    metrics = {m.split("=")[0]: float(m.split("=")[1]) for m in args.metric}

    if args.stage == "feature-consumer":
        df, feats = _load_df()
        dl = _discover(args.label_contract)
        rep = L.probe_feature_leakage(df, feats, args.label_col, declared_labels=dl,
                                      single_feature_auc_max=args.single_feature_auc_max)
    elif args.stage == "model-output":
        rep = L.check_metric_anomaly(metrics)
    elif args.stage == "split":
        rep = L.check_split_discipline(label_horizon_days=args.label_horizon or 0,
                                       embargo_days=args.embargo or 0, split_mode=args.split_mode)
    elif args.stage == "panel-build":
        rep = L.run_panel_build_audit(args.panel, args.db)
    elif args.stage == "all":
        df = feats = None
        if args.panel and args.label_col:
            df, feats = _load_df()
        rep = L.run_all(df=df, feature_cols=feats, label_col=args.label_col,
                        declared_labels=_discover(args.label_contract),
                        metrics=metrics or None,
                        split=({"label_horizon_days": args.label_horizon, "embargo_days": args.embargo,
                                "split_mode": args.split_mode} if args.label_horizon is not None else None),
                        panel=args.panel if args.stage == "all" and args.label_col is None else None, db=args.db)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return _exit_code(rep["overall"])
    else:
        ap.error("--stage 必选 (或 --lessons)")

    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return _exit_code(rep["verdict"])


def _run_gate() -> int:
    """强制闸: 遍历 leakage_consumers.yaml, 逐消费者跑事前探针, 任一 HIGH 即 FAIL (exit 1)."""
    import duckdb
    import pandas as pd
    import yaml
    reg_path = REPO / "backend" / "config" / "leakage_consumers.yaml"
    reg = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    db = reg.get("db", "data/smartmoney.duckdb")  # rule-compliance: ok evidence=registry yaml 取值, 默认仅 fallback, read_only
    since = reg.get("since", "2025-01-01")  # rule-compliance: ok evidence=registry yaml 探针采样窗起点, 默认仅 fallback
    results, any_high = [], False
    for c in reg.get("consumers", []):
        panel, label = c["panel"], c["label_col"]
        declared = _discover(str(REPO / c["exclude_source"])) if c.get("exclude_source") else set()
        declared |= set(c.get("panel_labels") or [])  # 注册表显式权威标签 (面板无 builder 契约时); 不信脚本手写
        con = duckdb.connect(db, read_only=True)  # rule-compliance: ok evidence=read_only 泄漏闸遍历
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?", [panel]).fetchall()]
        if label not in cols:
            results.append({"id": c["id"], "verdict": "SKIP", "reason": f"label {label} 不在 {panel}"}); con.close(); continue
        feats = [x for x in cols if x != label and x not in declared]
        sel = ", ".join('"' + x + '"' for x in [label, *feats])
        date_col = "signal_date" if "signal_date" in cols else ("date" if "date" in cols else None)
        wh = f"WHERE {date_col} >= '{since}'" if date_col else ""
        # 确定性抽样上限 30k 行: 泄漏是结构性的, 抽样足够 (0.84-AUC 泄漏在 3 万行照样现形), 控 moth 60s
        cap = "USING SAMPLE 30000 ROWS (reservoir, 20260613)" if date_col else ""
        df = con.execute(f"SELECT {sel} FROM {panel} {wh} {cap}").df(); con.close()
        df["_y"] = (pd.to_numeric(df[label], errors="coerce") > 0).astype(int)
        numf = [x for x in feats if pd.api.types.is_numeric_dtype(df[x])]
        rep = L.probe_feature_leakage(df, numf, "_y", declared_labels=declared)
        any_high = any_high or rep["verdict"] == "HIGH"
        results.append({"id": c["id"], "panel": panel, "verdict": rep["verdict"],
                        "n_high": rep["n_high"], "n_features": rep["n_features"],
                        "high_features": [f["feature"] for f in rep["flags"] if f["severity"] == "HIGH"]})
    print(json.dumps({"gate": "leakage_consumers", "overall": "FAIL" if any_high else "PASS",
                      "results": results}, ensure_ascii=False, indent=1))
    return 1 if any_high else 0


def _discover(path: str) -> set:
    if not path:
        return set()
    import importlib.util
    spec = importlib.util.spec_from_file_location("_lc", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return set(getattr(m, "MODEL_INPUT_EXCLUDED_COLS", set())) | set(getattr(m, "PIT_LABEL_COLS", set()))


if __name__ == "__main__":
    sys.exit(main())
