#!/usr/bin/env python3
"""数据层级执法器 (owner=本文件 + data_layers.yaml)。

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
import re
import sys
from pathlib import Path

import yaml

# Type B 泄漏列模式 (2026-06-28 加工分层 A/B): Type A 层(确定性PIT重排)表禁现这些 = 防 Type B(策略派生)伪装混入。
# 实测对当前 Type A 表 0 假阳性 (basic 不被 \bic\b 命中)。
_TYPE_A_LEAK_RE = re.compile(
    r"forward|_fwd|fwd_|label|_oos|oos_|rank_ic|_ic_|_ic$|\bic\b|score|signal|predicted|pred_|win_rate|sharpe|\btarget", re.I
)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

REGISTRY = REPO / "backend" / "config" / "data_layers.yaml"
MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"


# 受层级框架管理的业务库 (持声明-layer 的活表): smartmoney 业务控制面 + feature_store L2 面板。
# market(canonical_source)/tushare_raw(L0 vendor 镜像)/experiment_store(L4 transient) 各有独立 retention 语义,
# 不进 layer 声明执法 (2026-06-15: feature_store 接入, 否则 L2 分区静默不受管 = 框架本意落空)。
# 2026-06-27 §9 reference 拆库: reference 库 (4 dim 真相源, L1_foundation) 接入受管域 —
#   dim_active/trading_calendar/all_ever_listed/listing_status 物删迁 reference 后须在此声明执法,
#   否则 dim 分区静默不受管 (同 feature_store 2026-06-15 接入理由)。reference 仅这 4 dim, 全已声明。
MANAGED_DBS = ("smartmoney", "feature_store", "reference")  # rule-compliance: ok evidence=database_manifest.yaml 业务/特征/基础维度库 (untagged 检查域: 每表必声明)
# stale 检查(声明了但不在live)扫当前 manifest 中承载受管表的库；退役 ETF 文件只留删除证据，
# 不再冒充 active alias。untagged 检查仍只 MANAGED_DBS。
# (不要求声明 market/etf 的 raw 镜像表)。2026-06-27 §9: reference 加入 (4 dim live 在此)。
# 2026-08-26: org_holding 加入 stale 扫描 (landing/canonical/raw 迁出 smartmoney 后须仍算 live)。
STALE_SCAN_DBS = ("smartmoney", "feature_store", "market", "reference", "org_holding")


def _db_path(key: str) -> Path:
    m = yaml.safe_load(open(MANIFEST, encoding="utf-8"))
    return REPO / m["databases"][key]["path"]


def _live_tables(dbs=MANAGED_DBS) -> set[str]:
    live: set[str] = set()
    for key in dbs:
        path = _db_path(key)
        if not path.exists():
            continue  # planned/未建库跳过 (建后自动纳管)
        c = duck_connect(str(path), read_only=True)
        try:
            c.execute("SET enable_progress_bar=false")
            # 排除 _ 前缀瞬态表 (pipeline_lock 的 _lock_probe/_rw_probe 锁探针, 建/即删) —
            # 否则审计偶遇会误判 untagged → moth data-layer-integrity flicker (2026-06-26 实测)
            live |= {r[0] for r in c.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name='main'"
            ).fetchall() if not r[0].startswith("_")}
        finally:
            c.close()
    return live


def _columns_map(dbs=STALE_SCAN_DBS) -> dict[str, list[str]]:
    """全库 table_name → 列名 list (用于 Type A 列纯度门; 同名表取首遇库)。"""
    out: dict[str, list[str]] = {}
    for key in dbs:
        path = _db_path(key)
        if not path.exists():
            continue
        c = duck_connect(str(path), read_only=True)
        try:
            c.execute("SET enable_progress_bar=false")
            rows = c.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='main'"
            ).fetchall()
        finally:
            c.close()
        for tbl, col in rows:
            out.setdefault(tbl, []).append(col)
    return out


def audit() -> dict:
    reg = yaml.safe_load(open(REGISTRY, encoding="utf-8"))
    layers = set(reg.get("layers", {}))
    tagged = reg.get("tables", {})
    live = _live_tables()

    live_all = _live_tables(STALE_SCAN_DBS)                      # 2026-06-22 P2: stale 检查扫全库 (含 market/etf)
    untagged = sorted(live - set(tagged))                       # 活表未声明 layer = 违纪 (仅 MANAGED_DBS)
    bad_layer = sorted(t for t, l in tagged.items() if l not in layers)  # 声明了不存在的层
    stale_tag = sorted(set(tagged) - live_all)                  # 声明了但表全库都不在 = 真wiped (清理提示, 非 FAIL); 非误判 market/etf 驻留

    from collections import Counter
    by_layer = dict(Counter(l for t, l in tagged.items() if t in live))

    # Type A 列纯度门 (2026-06-28 加工分层 A/B): asset_class=A 的层 (L1_foundation/L1k/display) 表
    #   禁含 forward/label/score/signal/ic/predicted 等 Type B 列 = 防策略派生伪装成确定性 PIT 重排混入平台。
    layer_class = {ln: (spec.get("asset_class") if isinstance(spec, dict) else None)
                   for ln, spec in reg.get("layers", {}).items()}
    colmap = _columns_map()
    type_a_leak = []
    for t, l in tagged.items():
        if t not in live_all or layer_class.get(l) != "A":
            continue
        leaks = [c for c in colmap.get(t, []) if _TYPE_A_LEAK_RE.search(c)]
        if leaks:
            type_a_leak.append({"table": t, "layer": l, "leak_cols": sorted(leaks)})

    ok = not untagged and not bad_layer and not type_a_leak
    return {
        "overall": "PASS" if ok else "FAIL",
        "live_tables": len(live),
        "tagged": len([t for t in tagged if t in live]),
        "by_layer": by_layer,
        "untagged": untagged,           # FAIL: 新表必须声明 layer
        "bad_layer": bad_layer,         # FAIL: 用了未定义的层名
        "type_a_leak": type_a_leak,     # FAIL: Type A 层表含 Type B(前瞻/策略)列 — 伪装混入
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
