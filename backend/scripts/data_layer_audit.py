#!/usr/bin/env python3
"""数据层级执法器 (2026-06-14 地基-reset 后立, owner=docs/data_management_framework.md)。

固化"层级声明化"纪律, 根治本次 reset 暴露的"层级隐式 → 反复推导 + 耦合无法分离"问题:
  1. 每张活表必须在 backend/config/data_layers.yaml 声明 layer; 未声明 = FAIL (强制新表声明分层)。
  2. 声明的 layer 必须是已定义的 8 层之一。
  3. 按 layer 列表/统计 (供 layer-based 删除/管理, 替代脆弱的 import 闭包)。

moth 断言调 `--check` (JSON 输出 overall=PASS/FAIL); 人看跑无参。
用法:
  python backend/scripts/data_layer_audit.py            # 人看报告
  python backend/scripts/data_layer_audit.py --check    # moth 闸 (JSON)
  python backend/scripts/data_layer_audit.py --layer L2_feature   # 列某层表
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

REGISTRY = REPO / "backend" / "config" / "data_layers.yaml"
MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"


def _smartmoney_path() -> Path:
    m = yaml.safe_load(open(MANIFEST, encoding="utf-8"))
    return REPO / m["databases"]["smartmoney"]["path"]


def _live_tables() -> set[str]:
    c = duck_connect(str(_smartmoney_path()), read_only=True)
    try:
        c.execute("SET enable_progress_bar=false")
        return {r[0] for r in c.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name='smartmoney' AND schema_name='main'"
        ).fetchall()}
    finally:
        c.close()


def audit() -> dict:
    reg = yaml.safe_load(open(REGISTRY, encoding="utf-8"))
    layers = set(reg.get("layers", {}))
    tagged = reg.get("tables", {})
    live = _live_tables()

    untagged = sorted(live - set(tagged))                       # 活表未声明 layer = 违纪
    bad_layer = sorted(t for t, l in tagged.items() if l not in layers)  # 声明了不存在的层
    stale_tag = sorted(set(tagged) - live)                      # 声明了但表已不在 (清理提示, 非 FAIL)

    from collections import Counter
    by_layer = dict(Counter(l for t, l in tagged.items() if t in live))

    ok = not untagged and not bad_layer
    return {
        "overall": "PASS" if ok else "FAIL",
        "live_tables": len(live),
        "tagged": len([t for t in tagged if t in live]),
        "by_layer": by_layer,
        "untagged": untagged,           # FAIL: 新表必须声明 layer
        "bad_layer": bad_layer,         # FAIL: 用了未定义的层名
        "stale_tag": stale_tag,         # 提示: 注册表有已删表条目, 清理
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="moth 闸: 仅输出 JSON")
    ap.add_argument("--layer", help="列某层的表")
    args = ap.parse_args()
    r = audit()
    if args.layer:
        reg = yaml.safe_load(open(REGISTRY, encoding="utf-8"))
        ts = sorted(t for t, l in reg.get("tables", {}).items() if l == args.layer)
        print(f"{args.layer}: {len(ts)} 表")
        for t in ts:
            print(f"  {t}")
        return
    if args.check:
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r["overall"] == "PASS" else 1)
    # 人看报告
    print(f"=== 数据层级审计 (data_layers.yaml) ===")
    print(f"  活表 {r['live_tables']} | 已声明 {r['tagged']} | overall={r['overall']}")
    print(f"  各层: {r['by_layer']}")
    if r["untagged"]:
        print(f"\n  !! 未声明 layer 的活表 {len(r['untagged'])} (违纪, 新表必须声明): {r['untagged'][:20]}")
    if r["bad_layer"]:
        print(f"  !! 用了未定义层名: {r['bad_layer']}")
    if r["stale_tag"]:
        print(f"  注册表有已删表条目 (清理): {len(r['stale_tag'])} {r['stale_tag'][:10]}")
    if r["overall"] == "PASS":
        print("\n  PASS: 全部活表已声明 layer。")
    sys.exit(0 if r["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
