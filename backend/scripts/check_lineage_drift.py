"""血缘漂移门 (M5-T2) — 重生 vs 提交版 diff (剔时间戳), 漂移=拦 (mythos §13 派生物连跑两次必稳)。

闭环第2腿 "机器对账现实": graph.json 是 registry+代码的投影, 现实改了但 graph 没重生 = 漂移。
commit 时拦 (wired into safe_commit) — 改 schema/registry/builder/消费方后须 lineage build 重生。

#12(i) (2026-09-04): 重生用 catalog=False (登记表, 不连活库) —— 本门是暂存树的纯函数,
不再因为日更建新表 / 写者持锁 (形态面重建 64 min) 而红/炸。活库 vs 登记表的差交给 runtime
lineage_catalog_drift (system_health, 只报不拦) 承接, 不在这道 diff_correctness 门里。

#12(iii) 诊断: 漂移时打印 (a) 节点/边 id 的增删集合 (各最多 20 条, 定位改了哪张表/哪条边),
(b) 若能确定"真实工作树"(见 CHUNKYMONKEY_REAL_REPO), 列出血缘扫描范围 (backend/assets/
scripts) 内 index≠worktree 的文件 —— 这正是 "改了没暂存" 这类漂移的直接病因, 此前门只打印
一句"漂移", 定位不到是哪个文件 (安全提交脚本要 stash 才能提交, 根因就是这个空白)。

CHUNKYMONKEY_REAL_REPO: safe_commit.sh 把本脚本的暂存快照拷贝跑在一个临时 checkout-index
目录里, 脚本自己的 __file__ 算出的 REPO 落在那个临时目录, 不是真实工作树 —— 用这个环境变量
把真实工作树路径显式传进来, 诊断 (b) 才能问对 git 仓库 "index vs worktree" 而不是问一个刚
git-add 完、从无后续修改的一次性 throwaway repo (那样问永远是空的)。未设置时退回脚本自己的
REPO (standalone 直接跑时两者本来就是同一个仓库, 语义仍对)。

退出码: 0=一致 (无漂移) / 2=漂移 (须 chunkyctl lineage build --from-index 重生并提交) /
       3=graph.json 缺失。
比对剔除 meta.generated_at (唯一必然波动字段, mythos §13); 其余图体须逐字一致。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.lineage import build_lineage_graph  # noqa: E402
from services.lineage.builder import SCAN_DIRS  # noqa: E402

GRAPH_PATH = REPO / "data" / "lineage" / "graph.json"
_MAX_LISTED = 20


def _comparable(payload: dict) -> str:
    """剔除 meta.generated_at, 其余确定性序列化供逐字比对。"""
    body = {k: v for k, v in payload.items() if k != "meta"}
    meta = {k: v for k, v in (payload.get("meta") or {}).items() if k != "generated_at"}
    body["meta"] = meta
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def _node_ids(payload: dict) -> set[str]:
    return {n["id"] for n in payload.get("nodes", [])}


def _edge_keys(payload: dict) -> set[tuple[str, str, str]]:
    return {(e["kind"], e["src"], e["dst"]) for e in payload.get("edges", [])}


def _print_diff(label: str, added: set, removed: set) -> None:
    if not added and not removed:
        return
    print(f"  {label}: +{len(added)} / -{len(removed)}", file=sys.stderr)
    for x in sorted(added)[:_MAX_LISTED]:
        print(f"    + {x if isinstance(x, str) else ':'.join(x)}", file=sys.stderr)
    if len(added) > _MAX_LISTED:
        print(f"    ... 其余 {len(added) - _MAX_LISTED} 个新增未列出", file=sys.stderr)
    for x in sorted(removed)[:_MAX_LISTED]:
        print(f"    - {x if isinstance(x, str) else ':'.join(x)}", file=sys.stderr)
    if len(removed) > _MAX_LISTED:
        print(f"    ... 其余 {len(removed) - _MAX_LISTED} 个删除未列出", file=sys.stderr)


def _real_repo() -> Path:
    """真实工作树路径 —— 见模块 docstring CHUNKYMONKEY_REAL_REPO 一节。"""
    override = os.environ.get("CHUNKYMONKEY_REAL_REPO")
    return Path(override) if override else REPO


def _unstaged_lineage_inputs() -> list[str]:
    """血缘扫描范围 (SCAN_DIRS) 内 index≠worktree 的 tracked 文件 (诊断 iii-b)。

    只在能定位真实工作树、且那里确实是个 git 仓库时才跑；查不了就诚实地不报，不猜。
    """
    real_repo = _real_repo()
    if not (real_repo / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "--", *SCAN_DIRS],
            cwd=str(real_repo), capture_output=True, text=True, check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return sorted({ln.strip() for ln in proc.stdout.splitlines() if ln.strip()})


def main() -> int:
    if not GRAPH_PATH.exists():
        print(f"[lineage-drift] FAIL: {GRAPH_PATH.relative_to(REPO)} 缺失 — 先跑 chunkyctl lineage build --from-index", file=sys.stderr)
        return 3
    committed = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    fresh = build_lineage_graph(catalog=False).to_dict(generated_at=None)

    if _comparable(committed) == _comparable(fresh):
        print(f"[lineage-drift] PASS: graph.json 与现实一致 ({committed.get('meta', {}).get('node_count')} 节点)")
        return 0

    # 漂移 — 报节点/边计数差 + 增删集合 + 疑似病因输入文件 (定位用, #12(iii))
    cm, fm = committed.get("meta", {}), fresh.get("meta", {})
    print("[lineage-drift] FAIL: graph.json 漂移 (registry/schema/消费方 改了但血缘未重生)", file=sys.stderr)
    print(f"  committed: {cm.get('node_count')} 节点 / {cm.get('edge_count')} 边", file=sys.stderr)
    print(f"  现实重生 : {fm.get('node_count')} 节点 / {fm.get('edge_count')} 边", file=sys.stderr)

    _print_diff("节点", _node_ids(fresh) - _node_ids(committed), _node_ids(committed) - _node_ids(fresh))
    _print_diff(
        "边",
        _edge_keys(fresh) - _edge_keys(committed),
        _edge_keys(committed) - _edge_keys(fresh),
    )

    unstaged = _unstaged_lineage_inputs()
    if unstaged:
        print("  这些血缘输入 index≠worktree (worktree 的改动还没 stage, 门看不到):", file=sys.stderr)
        for f in unstaged[:_MAX_LISTED]:
            print(f"    * {f}", file=sys.stderr)
        if len(unstaged) > _MAX_LISTED:
            print(f"    ... 其余 {len(unstaged) - _MAX_LISTED} 个未列出", file=sys.stderr)
        print("    先 git add 这些文件再重生, 否则 build 出来的图还是不含它们的改动。", file=sys.stderr)

    print("  修复: chunkyctl lineage build --from-index (从 exact staged snapshot 重生) 并 git add data/lineage/graph.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
