"""血缘漂移门 (M5-T2) — 重生 vs 提交版 diff (剔时间戳), 漂移=拦 (mythos §13 派生物连跑两次必稳)。

闭环第2腿 "机器对账现实": graph.json 是 registry+代码的投影, 现实改了但 graph 没重生 = 漂移。
commit 时拦 (wired into safe_commit) — 改 schema/registry/feature_registry/builder/消费方后须 lineage build 重生。

退出码: 0=一致 (无漂移) / 2=漂移 (须 chunkyctl lineage build 重生并提交) / 3=graph.json 缺失。
比对剔除 meta.generated_at (唯一必然波动字段, mythos §13); 其余图体须逐字一致。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.lineage import build_lineage_graph  # noqa: E402

GRAPH_PATH = REPO / "data" / "lineage" / "graph.json"


def _comparable(payload: dict) -> str:
    """剔除 meta.generated_at, 其余确定性序列化供逐字比对。"""
    body = {k: v for k, v in payload.items() if k != "meta"}
    meta = {k: v for k, v in (payload.get("meta") or {}).items() if k != "generated_at"}
    body["meta"] = meta
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def main() -> int:
    if not GRAPH_PATH.exists():
        print(f"[lineage-drift] FAIL: {GRAPH_PATH.relative_to(REPO)} 缺失 — 先跑 chunkyctl lineage build", file=sys.stderr)
        return 3
    committed = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    fresh = build_lineage_graph().to_dict(generated_at=None)

    if _comparable(committed) == _comparable(fresh):
        print(f"[lineage-drift] PASS: graph.json 与现实一致 ({committed.get('meta', {}).get('node_count')} 节点)")
        return 0

    # 漂移 — 报节点/边计数差 (定位用)
    cm, fm = committed.get("meta", {}), fresh.get("meta", {})
    print("[lineage-drift] FAIL: graph.json 漂移 (registry/schema/消费方 改了但血缘未重生)", file=sys.stderr)
    print(f"  committed: {cm.get('node_count')} 节点 / {cm.get('edge_count')} 边", file=sys.stderr)
    print(f"  现实重生 : {fm.get('node_count')} 节点 / {fm.get('edge_count')} 边", file=sys.stderr)
    print("  修复: chunkyctl lineage build (重生) 并提交 data/lineage/graph.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
