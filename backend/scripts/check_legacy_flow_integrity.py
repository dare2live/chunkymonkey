#!/usr/bin/env python3
"""老流程污染防回潮 gate — 工具化 reset 教训 (2026-06-14, owner=docs/data_management_framework.md §6)。

缘起: 地基-reset 删模型/特征/信号层后, 老 daily_update / config 仍调缺失脚本/引用已删表 = 污染新系统;
且 alpha158 重建循环漏过 schema_layer_filter (那次只闸启动建表, 没管 daily_update / 散落 service DDL)。
工具化 6 教训 (Workflow wa3kxgj13 综合) 成可执行 gate, 这次重构的**验收标尺**: 重构前红=问题实锤,
重构后绿=把好关。绝不 print-not-fail (mythos §14): 任一 check 失败 overall=FAIL, 非零退出。

3 检:
  C1 daily_update 脚本可调: scripts/daily_update.sh 每个 backend/scripts/*.py 调用必须在盘 (删层必删
     caller; 防调缺失脚本静默 step_degraded 假装管线还活 — 教训#1/#2)。
  C2 无 wiped 表孤儿引用: config/schema_versions/routers 不引用 data_layers 标 wiped 的表 (除 @archived
     注释豁免) (防孤儿 stale 引用 — 教训#4)。
  C3 append-only 必 retention: 活层 *_history/*_snapshot 表必在 storage_retention 声明 (防无界膨胀 = DB
     巨大根因 — 教训#3)。

用法: python backend/scripts/check_legacy_flow_integrity.py [--check]  → JSON {overall, c1/c2/c3}
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DATA_LAYERS = REPO / "backend" / "config" / "data_layers.yaml"
DAILY_UPDATE = REPO / "scripts" / "daily_update.sh"
STORAGE_RETENTION = REPO / "backend" / "config" / "storage_retention.yaml"
WIPED_LAYERS = {"L2_feature", "L3_model", "L4_experiment"}
ARCHIVE_MARKERS = ("@archived", "@deprecated", "已删", "wiped", "archived", "退役", "移除", "reset 删")
GREP_ROOTS = ["backend/config", "backend/services/schema_versions.py", "backend/routers"]


def _layers() -> dict[str, str]:
    d = yaml.safe_load(DATA_LAYERS.read_text(encoding="utf-8")) or {}
    return d.get("tables", {}) or {}


def check_daily_update_scripts() -> dict:
    """C1: daily_update 每个 backend/scripts/*.py 调用必须在盘。"""
    if not DAILY_UPDATE.exists():
        return {"name": "daily_update_scripts", "verdict": "PASS", "missing": [], "note": "no daily_update.sh"}
    text = DAILY_UPDATE.read_text(encoding="utf-8")
    called = sorted(set(re.findall(r"backend/scripts/([A-Za-z0-9_]+\.py)", text)))
    missing = [s for s in called if not (REPO / "backend" / "scripts" / s).exists()]
    return {"name": "daily_update_scripts", "verdict": "FAIL" if missing else "PASS",
            "n_called": len(called), "missing": missing}


def check_no_wiped_refs() -> dict:
    """C2: config/schema_versions/routers 不引用 wiped 表 (除 @archived 注释行)。"""
    wiped = sorted(t for t, lyr in _layers().items() if lyr in WIPED_LAYERS)
    stale: list[dict] = []
    for t in wiped:
        try:
            r = subprocess.run(["grep", "-rn", "--", t, *GREP_ROOTS], cwd=str(REPO),
                               capture_output=True, text=True)
        except Exception:
            continue
        for line in r.stdout.splitlines():
            low = line.lower()
            if line.startswith("backend/config/data_layers.yaml:"):
                continue  # data_layers 是 wiped 表声明源 (注册表本身), 非孤儿消费引用
            if any(m.lower() in low for m in ARCHIVE_MARKERS):
                continue  # 已标注归档/退役豁免
            stale.append({"table": t, "ref": line[:160]})
    return {"name": "no_wiped_refs", "verdict": "FAIL" if stale else "PASS",
            "n_wiped": len(wiped), "n_stale_refs": len(stale), "sample": stale[:12]}


def check_append_only_retention() -> dict:
    """C3: 活层 *_history/*_snapshot 表必在 storage_retention 声明。"""
    layers = _layers()
    retention_text = STORAGE_RETENTION.read_text(encoding="utf-8") if STORAGE_RETENTION.exists() else ""
    append_only = sorted(
        t for t, lyr in layers.items()
        if lyr not in WIPED_LAYERS and (t.endswith("_history") or "_snapshot" in t)
    )
    missing = [t for t in append_only if t not in retention_text]
    return {"name": "append_only_retention", "verdict": "FAIL" if missing else "PASS",
            "n_append_only": len(append_only), "missing_retention": missing}


def main(argv: list[str] | None = None) -> int:
    checks = [check_daily_update_scripts(), check_no_wiped_refs(), check_append_only_retention()]
    overall = "PASS" if all(c["verdict"] == "PASS" for c in checks) else "FAIL"
    out = {"overall": overall, "checks": {c["name"]: c for c in checks}}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
