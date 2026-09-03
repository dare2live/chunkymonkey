"""血缘路由中枢 CLI (M5-T2) — 数据模块的字典+总指挥查询入口。

用法 (经 chunkyctl lineage <cmd> 转发, 或直接):
  python backend/scripts/lineage_cli.py build               # 缝合重生 data/lineage/graph.json
  python backend/scripts/lineage_cli.py build --from-index  # 从 exact staged git index 重生
  python backend/scripts/lineage_cli.py build --with-catalog # 额外读活库 (诊断用, 非提交门用)
  python backend/scripts/lineage_cli.py impact <table>      # 删/迁前自动 fan-in (全消费方)
  python backend/scripts/lineage_cli.py provenance <table>  # 溯源到采集接口+PIT锚
  python backend/scripts/lineage_cli.py dead                # 无消费方的表 (停采候选/待挖)
  python backend/scripts/lineage_cli.py show <table>        # impact+provenance 合并视图

impact/provenance/dead/show 读 data/lineage/graph.json (没有则即时 build, 提示先 build)。

--from-index (#12(ii), 2026-09-04): check_lineage_drift 门比对的基准是「从 exact staged
snapshot 重生」，但普通的 `build` 只重生工作树 —— 两者不一定是同一件事 (worktree 里可能
有别的、无关的、还没 stage 的改动)。`--from-index` 用与 scripts/safe_commit.sh 同一招
(git checkout-index --all 导出暂存索引到临时目录, 在那棵树上跑 build) 补上这条命令, 结果
写回工作树 data/lineage/graph.json 并提示 git add —— 提交的东西保证就是门会算出的东西。

owner: docs/MASTER_TOPLEVEL_DESIGN.md + docs/engineering_governance.md + services/lineage/。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def _build_from_index() -> int:
    """从 exact staged git index (非工作树) 重生, 与 check_lineage_drift 门的比对基准同源。

    checkout-index 导出暂存快照到临时目录 (与 scripts/safe_commit.sh 的 STAGED_INDEX_DIR
    同一招), 在该快照上以子进程跑 build (PYTHONPATH 指向快照 backend, 保证 builder 的
    REPO/CONFIG 解析到快照而非工作树), 结果写回工作树 data/lineage/graph.json 并提示
    git add —— 于是"从 exact staged snapshot 重生"有了一条真能敲的命令。
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="chunkymonkey-lineage-index-"))
    try:
        co = subprocess.run(
            ["git", "checkout-index", "--all", f"--prefix={tmp_dir}/"],
            cwd=str(REPO), capture_output=True, text=True, check=False,
        )
        if co.returncode != 0:
            print(f"[lineage] git checkout-index failed: {co.stderr.strip()}", file=sys.stderr)
            return 1
        init = subprocess.run(["git", "init", "-q"], cwd=str(tmp_dir), capture_output=True, text=True, check=False)
        if init.returncode != 0:
            print(f"[lineage] git init on staged snapshot failed: {init.stderr.strip()}", file=sys.stderr)
            return 1
        add = subprocess.run(["git", "add", "-f", "-A"], cwd=str(tmp_dir), capture_output=True, text=True, check=False)
        if add.returncode != 0:
            print(f"[lineage] git add on staged snapshot failed: {add.stderr.strip()}", file=sys.stderr)
            return 1

        staged_cli = tmp_dir / "backend" / "scripts" / "lineage_cli.py"
        if not staged_cli.is_file():
            print(f"[lineage] staged snapshot 缺 {staged_cli.relative_to(tmp_dir)}", file=sys.stderr)
            return 1
        build = subprocess.run(
            [sys.executable, str(staged_cli), "build"],
            cwd=str(tmp_dir),
            env={**os.environ, "PYTHONPATH": str(tmp_dir / "backend")},
            capture_output=True, text=True, check=False,
        )
        if build.stdout:
            print(build.stdout, end="")
        if build.stderr:
            print(build.stderr, file=sys.stderr, end="")
        if build.returncode != 0:
            return build.returncode

        staged_graph = tmp_dir / "data" / "lineage" / "graph.json"
        if not staged_graph.exists():
            print("[lineage] staged build 未产出 graph.json", file=sys.stderr)
            return 1
        GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
        GRAPH_PATH.write_text(staged_graph.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[lineage] --from-index OK → 写回工作树 {GRAPH_PATH.relative_to(REPO)}; 请 git add 后提交")
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    build_p = sub.add_parser("build", help="缝合重生 graph.json")
    build_p.add_argument(
        "--from-index", action="store_true",
        help="从 exact staged git index (非工作树) 重生, 与 check_lineage_drift 门比对基准同源",
    )
    build_p.add_argument(
        "--with-catalog", action="store_true",
        help="额外读活库 information_schema (诊断/交互查询用; 提交门/--from-index 不用这个)",
    )
    for c in ("impact", "provenance", "show"):
        p = sub.add_parser(c)
        p.add_argument("table")
    sub.add_parser("dead", help="无消费方的表")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        if args.from_index:
            if args.with_catalog:
                print("[lineage] --from-index 与 --with-catalog 不能同用: from-index 就是要和"
                      "提交门(catalog=False)对齐, 掺活库会让两边又对不上。", file=sys.stderr)
                return 1
            return _build_from_index()
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
