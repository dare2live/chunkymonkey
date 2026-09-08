"""血缘路由中枢 CLI (M5-T2) — 数据模块的字典+总指挥查询入口。

用法 (经 chunkyctl lineage <cmd> 转发, 或直接):
  python backend/scripts/lineage_cli.py build               # 缝合重生 data/lineage/graph.json
  python backend/scripts/lineage_cli.py build --with-catalog # 额外读活库 (诊断用)
  python backend/scripts/lineage_cli.py impact <table>      # 删/迁前自动 fan-in (全消费方)
  python backend/scripts/lineage_cli.py provenance <table>  # 溯源到采集接口+PIT锚
  python backend/scripts/lineage_cli.py dead                # 无消费方的表 (停采候选/待挖)
  python backend/scripts/lineage_cli.py show <table>        # impact+provenance 合并视图

impact/provenance/dead/show 读 data/lineage/graph.json (没有则即时 build, 提示先 build)。

2026-09-08: data/lineage/graph.json 不再被 git 跟踪 (它 100% 由 registry+源码派生,
build_lineage_graph(catalog=False) 约 4 秒可确定性重生)。随之退役的还有 check_lineage_drift
提交门与本 CLI 的 `--from-index` —— 后者的存在理由就是给那道门提供「从 exact staged
snapshot 重生」的比对基准, 门没了它写出的索引版缓存反而会与工作树对不上。

owner: services/lineage/。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.lineage import build_lineage_graph, impact, provenance, dead_tables  # noqa: E402
from services.lineage.model import LineageGraph  # noqa: E402

GRAPH_PATH = REPO / "data" / "lineage" / "graph.json"


def _write_graph(graph: LineageGraph, *, path: Path = GRAPH_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = graph.to_dict(generated_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_graph() -> LineageGraph:
    if not GRAPH_PATH.exists():
        print(f"[lineage] {GRAPH_PATH} 不存在, 即时 build (建议先 chunkyctl lineage build 落盘)", file=sys.stderr)
        return build_lineage_graph()
    return LineageGraph.from_dict(json.loads(GRAPH_PATH.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    build_p = sub.add_parser("build", help="缝合重生 graph.json")
    build_p.add_argument(
        "--with-catalog", action="store_true",
        help="额外读活库 information_schema (诊断/交互查询用)",
    )
    for c in ("impact", "provenance", "show"):
        p = sub.add_parser(c)
        p.add_argument("table")
    sub.add_parser("dead", help="无消费方的表")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        g = build_lineage_graph(catalog=args.with_catalog)
        _write_graph(g)
        n_tab = len(g.nodes_of_kind("table"))
        n_con = len(g.nodes_of_kind("consumer"))
        mode = "catalog" if args.with_catalog else "registry-only"
        print(f"[lineage] build OK ({mode}) → {GRAPH_PATH.relative_to(REPO)}: "
              f"{len(g.nodes)} 节点 ({n_tab} 表/{n_con} 消费方) / {len(g.edges)} 边")
        return 0

    g = _load_graph()
    if args.cmd == "impact":
        print(json.dumps(impact(g, args.table), ensure_ascii=False, indent=2))
    elif args.cmd == "provenance":
        print(json.dumps(provenance(g, args.table), ensure_ascii=False, indent=2))
    elif args.cmd == "dead":
        dead = dead_tables(g)
        print(json.dumps({"dead_table_count": len(dead), "tables": dead}, ensure_ascii=False, indent=2))
    elif args.cmd == "show":
        print(json.dumps({"impact": impact(g, args.table), "provenance": provenance(g, args.table)},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
