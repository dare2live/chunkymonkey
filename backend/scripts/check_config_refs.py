#!/usr/bin/env python3
"""config 跨文件引用完整性门 (2026-06-23 config最小手术 Step4, owner=task#54)。

固化"层词汇单一真相源"(Step1): data_access 的 layer 值必须在 data_layers 定义
(长键 或 alias 短码), 否则悬空 = 层词汇又漂回两套。这是 Step1 alias 机制的执法门
(否则一次性改了, 下次新增 entity 写错层名又无人拦)。

扩展点 (未来): from_sync 引用 (Step3 若做) / 其他跨 config 引用 — 加 check 函数即可。

moth 断言调 `--check` (JSON, exit 1 if FAIL); 人看跑无参。
用法:
  python backend/scripts/check_config_refs.py          # 人看报告
  python backend/scripts/check_config_refs.py --check   # moth 闸 (JSON)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DATA_LAYERS = REPO / "backend" / "config" / "data_layers.yaml"
DATA_ACCESS = REPO / "backend" / "config" / "data_access.yaml"


def _valid_layer_vocab() -> set[str]:
    """data_layers 定义的合法层词汇 = 长键 ∪ alias 短码 (单一真相源)。"""
    dl = yaml.safe_load(open(DATA_LAYERS, encoding="utf-8"))
    layers = dl.get("layers", {})
    vocab: set[str] = set(layers.keys())
    for spec in layers.values():
        if isinstance(spec, dict) and spec.get("alias"):
            vocab.add(spec["alias"])
    return vocab


def audit() -> dict:
    vocab = _valid_layer_vocab()
    da = yaml.safe_load(open(DATA_ACCESS, encoding="utf-8"))
    violations = []
    for name, spec in (da.get("entities", {}) or {}).items():
        if isinstance(spec, dict) and spec.get("layer") is not None:
            if spec["layer"] not in vocab:
                violations.append({
                    "entity": name,
                    "field": "layer",
                    "value": spec["layer"],
                    "reason": "未在 data_layers 定义 (非长键也非 alias) = 层词汇悬空/漂移",
                })
    return {
        "overall": "PASS" if not violations else "FAIL",
        "valid_layer_vocab": sorted(vocab),
        "checked_entities": len((da.get("entities", {}) or {})),
        "violations": violations,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="moth 闸: 仅 JSON")
    args = ap.parse_args()
    r = audit()
    if args.check:
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r["overall"] == "PASS" else 1)
    print("=== config 跨文件引用完整性 (层词汇单一真相源) ===")
    print(f"  合法层词汇 (data_layers 长键∪alias): {r['valid_layer_vocab']}")
    print(f"  检查 data_access entity 数: {r['checked_entities']}")
    if r["violations"]:
        print(f"\n  !! {len(r['violations'])} 处悬空层引用:")
        for v in r["violations"]:
            print(f"    {v['entity']}.{v['field']} = {v['value']} — {v['reason']}")
    else:
        print("\n  PASS: data_access 全部 layer 引用在 data_layers 有定义 (层词汇未漂移)。")
    sys.exit(0 if r["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
