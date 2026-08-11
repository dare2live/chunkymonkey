#!/usr/bin/env python3
"""功能地图生成器 — 机器可枚举事实的派生视图 (FEATURE_MAP.md + feature_map.json).

职责边界 (红线: 不做第二真相源):
  本工具只输出"从代码/配置机器枚举"的存在性事实 — 入口面 / 数据域 / 产表 writer /
  依赖热点 / 计数。人工判断层 (坑 / 权重 / 状态 / 为什么重要) 永远在 PROJECT_INDEX.md。
  FEATURE_MAP.md 勿手改 — 重生成: scripts/chunkyctl map

真相源 (4 路, 全自动):
  1. 入口面: scripts/chunkyctl usage + configs/launchd/*.plist + backend/main.py 路由注册
  2. 数据域: backend/config/sync_registry.yaml
  3. 产表 writer: 正则扫 backend/{services,scripts,routers}/**.py 的 CREATE/INSERT/MERGE
     -> 表→writer 文件映射; 多 writer 表 = Platform Runtime Contract 单 writer 原则审查对象
  4. 依赖热点: .codegraph/codegraph.db 直查 (import 节点聚合 + calls 边 fan-in,
     泛型名按"同名定义分布在 >2 文件"自动剔除, 不靠手维护名单)

写盘策略: body 漂移才写 (时间戳行不参与比对), 防每日噪音 commit。--check 漂移即 exit 1。
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
MD_OUT = REPO / "FEATURE_MAP.md"
JSON_OUT = REPO / "data" / "reports" / "feature_map.json"
SNAPSHOT_PREFIX = "> Snapshot:"

# SQL 写表语句 (writer 判定口径: 建表/插入/合并; DELETE/DROP 属维护不算 owner)
WRITE_RE = re.compile(
    r"(?i)\b(?:CREATE(?:\s+OR\s+REPLACE)?\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?"
    r"|INSERT\s+(?:OR\s+(?:REPLACE|IGNORE)\s+)?INTO"
    r"|MERGE\s+INTO)\s+[\"'`]?((?:fact|mart|dim|raw|stg)_[a-zA-Z0-9_]*)"
)
ROUTE_RE = re.compile(r"@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']")
INCLUDE_RE = re.compile(r"include_router\(\s*([\w.]+?)(?:\.router)?\s*(?:,\s*prefix\s*=\s*[\"']([^\"']*)[\"'])?[,)]")
ROUTER_PREFIX_RE = re.compile(r"APIRouter\([^)]*?prefix\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL)
IMPORT_MOD_RE = re.compile(r"(?:from|import)\s+((?:services|routers|scripts)(?:\.\w+)+)")
# f-string 动态表名写点 (INSERT INTO {table} 形态) — 静态无法归属, 必须显式列出而非静默漏
DYNAMIC_WRITE_RE = re.compile(
    r"(?i)\b(?:CREATE(?:\s+OR\s+REPLACE)?\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?"
    r"|INSERT\s+(?:OR\s+(?:REPLACE|IGNORE)\s+)?INTO"
    r"|MERGE\s+INTO)\s+[\"'`]?\{"
)
CG_STATS_PREFIX = "> Codegraph:"
# codegraph 索引派生的排名表 — 与计数行同源同因, 同样不参与漂移判定 (见 _body)。
CG_VOLATILE_SUBSECTIONS = (
    "### 被 import 最多的模块",
    "### 跨文件 fan-in 最高的文件",
)


def tracked_files(repo: Path) -> set[str] | None:
    """git index 内文件集 (地图只描述已 track 代码, 防 untracked WIP 幻影 writer)."""
    import subprocess
    r = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None
    return set(r.stdout.splitlines())


def scan_table_writers(repo: Path, tracked: set[str] | None = None) -> tuple[dict[str, list[str]], dict[str, int]]:
    """表 -> writer 文件列表 (排除 tests/untracked); 返回 (映射, 动态写点文件->次数)."""
    writers: dict[str, set[str]] = defaultdict(set)
    dynamic: dict[str, int] = defaultdict(int)
    for sub in ("backend/services", "backend/scripts", "backend/routers"):
        for py in sorted((repo / sub).rglob("*.py")):
            rel = py.relative_to(repo).as_posix()
            if "/tests/" in rel or (tracked is not None and rel not in tracked):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in WRITE_RE.finditer(text):
                name = m.group(1)
                tail = text[m.end(): m.end() + 1]
                if tail == "{" or name.endswith("_"):  # f-string 部分动态 (fact_{x} 形态)
                    dynamic[rel] += 1
                    continue
                writers[name].add(rel)
            n_dyn = len(DYNAMIC_WRITE_RE.findall(text))  # 全动态 ({table} 形态)
            if n_dyn:
                dynamic[rel] += n_dyn
    return {t: sorted(fs) for t, fs in sorted(writers.items())}, dict(sorted(dynamic.items()))


def scan_routes(repo: Path) -> dict[str, dict]:
    """router 模块 -> {prefix, endpoints:[(method,path)]}.

    prefix = main.py include_router 注册前缀 (别名惯例 `<mod>_router` 剥后缀归位)
           + router 文件内 APIRouter(prefix=...) 自带前缀 (两处都可能出现, 拼接).
    """
    prefixes: dict[str, str] = {}
    main_py = repo / "backend" / "main.py"
    if main_py.exists():
        for m in INCLUDE_RE.finditer(main_py.read_text(encoding="utf-8", errors="replace")):
            mod = m.group(1).split(".")[-1]
            if mod.endswith("_router"):  # `from routers.market import router as market_router`
                mod = mod[: -len("_router")]
            prefixes[mod] = m.group(2) or ""
    out: dict[str, dict] = {}
    routers_dir = repo / "backend" / "routers"
    if routers_dir.exists():
        for py in sorted(routers_dir.glob("*.py")):
            text = py.read_text(encoding="utf-8", errors="replace")
            eps = ROUTE_RE.findall(text)
            if eps:
                infile = ROUTER_PREFIX_RE.search(text)
                out[py.stem] = {
                    "prefix": prefixes.get(py.stem, "") + (infile.group(1) if infile else ""),
                    "endpoints": [[m.upper(), p] for m, p in eps],
                }
    return out


def scan_chunkyctl(repo: Path) -> list[list[str]]:
    """枚举 bash wrapper 的活跃 case 分支，排除其显式退役命令。"""
    src = (repo / "scripts" / "chunkyctl").read_text(encoding="utf-8", errors="replace")
    retired_match = re.search(r"^RETIRED_COMMANDS=\(([^)]*)\)\s*$", src, re.MULTILINE)
    retired = set(retired_match.group(1).split()) if retired_match else set()
    cmds = []
    for m in re.finditer(r"^\s{2}([a-z][a-z-]*)\)\s*$", src, re.MULTILINE):
        command = m.group(1)
        if command not in retired:
            cmds.append(command)
    helps = dict(re.findall(r"^\s+(\w[\w-]*)\s{2,}(.+)$", src, re.MULTILINE))
    return [[c, helps.get(c, "").lstrip("= ")] for c in cmds]


def scan_launchd(repo: Path) -> list[dict]:
    jobs = []
    plist_paths = {
        *list((repo / "configs" / "launchd").glob("*.plist")),
        *list((repo / "backend" / "scripts" / "launchd").glob("*.plist")),
    }
    for plist in sorted(plist_paths):
        try:
            d = plistlib.loads(plist.read_bytes())
        except Exception:  # noqa: BLE001 — 单文件坏不挡全图, 显式标注
            jobs.append({"label": plist.name, "schedule": "PARSE_ERROR", "program": ""})
            continue
        cal = d.get("StartCalendarInterval") or {}
        if isinstance(cal, list):
            cal = cal[0] if cal else {}
        sched = f"{cal.get('Hour', '?'):0>2}:{cal.get('Minute', '?'):0>2}" if cal else d.get("StartInterval", "manual")
        prog = " ".join(map(str, d.get("ProgramArguments", []))).replace("/Users/dp/Documents/M/stock/chunkymonkey/", "")
        prog = prog[:90] + ("…" if len(prog) > 90 else "")
        jobs.append({"label": d.get("Label", plist.stem), "schedule": str(sched), "program": prog})
    return jobs


def load_registry(repo: Path) -> list[dict]:
    path = repo / "backend" / "config" / "sync_registry.yaml"
    if not path.exists():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = []
    for name, spec in sorted((cfg.get("domains") or {}).items()):
        rows.append({
            "domain": name,
            "source": spec.get("source", "?"),
            "api": spec.get("api", "?"),
            "table": spec.get("target_table", "?"),
            "mode": spec.get("batch_mode", "?"),
            "sla_days": spec.get("freshness_sla_trading_days", "—"),
        })
    return rows


def codegraph_facts(repo: Path) -> dict:
    """codegraph.db 直查; 任一步失败整节降级为 error 标注 (不挡地图生成)."""
    db = repo / ".codegraph" / "codegraph.db"
    if not db.exists():
        return {"error": "codegraph.db 不存在 (跑 codegraph index)"}
    con = None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM nodes LIMIT 1")
    except sqlite3.Error:
        if con is not None:
            con.close()
        try:
            con = sqlite3.connect(str(db))
            con.execute("SELECT 1 FROM nodes LIMIT 1")
        except sqlite3.Error as e:
            return {"error": f"codegraph.db 打不开: {e}"}
    try:
        stats = dict(con.execute(
            "SELECT kind, COUNT(*) FROM edges GROUP BY kind").fetchall())
        node_count = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        imports: dict[str, int] = defaultdict(int)
        for (sig,) in con.execute("SELECT signature FROM nodes WHERE kind='import'"):
            for m in IMPORT_MOD_RE.finditer(sig or ""):
                imports[m.group(1)] += 1
        top_imports = sorted(imports.items(), key=lambda x: (-x[1], x[0]))[:15]  # 展示截断
        # fan-in: 跨文件 calls 边。codegraph 按符号名解析有两类假边:
        #   (a) 同名多处定义错绑 → 只取全库唯一定义名 (HAVING=1)
        #   (b) stdlib 遮蔽名 (resolve/exists 等 Path 方法被绑到项目内同名函数,
        #       实测 resolve 272 条假边) → caller 文件必须 import 过目标模块 (按目标
        #       文件 stem 查 caller 的 import 节点 signature), 不靠手维护黑名单
        pairs = con.execute("""
            WITH uniq AS (
              SELECT name FROM nodes WHERE kind IN ('function','method','class')
              GROUP BY name HAVING COUNT(DISTINCT file_path) = 1
            )
            SELECT DISTINCT t.file_path, s.file_path
            FROM edges e
            JOIN nodes s ON e.source = s.id
            JOIN nodes t ON e.target = t.id
            WHERE e.kind = 'calls' AND s.file_path <> t.file_path
              AND length(t.name) > 3
              AND t.name IN (SELECT name FROM uniq)
              AND t.file_path NOT LIKE '%tests%'
              AND t.file_path NOT LIKE 'design/%'
        """).fetchall()
        import_blob: dict[str, str] = defaultdict(str)
        for fp, sig in con.execute("SELECT file_path, signature FROM nodes WHERE kind='import'"):
            import_blob[fp] += (sig or "") + "\n"
        fanin_count: dict[str, int] = defaultdict(int)
        for t_fp, s_fp in pairs:
            if Path(t_fp).stem in import_blob.get(s_fp, ""):
                fanin_count[t_fp] += 1
        fan_in = sorted(fanin_count.items(), key=lambda x: (-x[1], x[0]))[:12]  # 展示截断
        return {
            "nodes": node_count,
            "edges": stats,
            "top_imports": [[m, c] for m, c in top_imports],
            "fan_in": [[f, c] for f, c in fan_in],
        }
    except sqlite3.Error as e:
        return {"error": f"codegraph 查询失败: {e}"}
    finally:
        con.close()


def big_modules(repo: Path, tracked: set[str] | None = None) -> list[list]:
    """LOC top10 (.py, 排除 tests/untracked) — God module 候选的机器事实面."""
    sizes = []
    for py in (repo / "backend").rglob("*.py"):
        rel = py.relative_to(repo).as_posix()
        if "/tests/" in rel or (tracked is not None and rel not in tracked):
            continue
        try:
            n = sum(1 for _ in py.open(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        sizes.append([rel, n])
    return sorted(sizes, key=lambda x: -x[1])[:10]  # 展示截断


def render_md(d: dict) -> str:
    L: list[str] = []
    add = L.append
    add("# FEATURE_MAP — 机器生成功能地图")
    add("")
    add("> 由 `scripts/chunkyctl map` (backend/scripts/build_feature_map.py) 重生成, **勿手改**。")
    add("> 只列机器可枚举事实 (入口/数据域/产表 writer/依赖热点/计数); 人工判断层"
        " (坑/权重/状态) 在 `PROJECT_INDEX.md`。机器版: `data/reports/feature_map.json` (本地, 不入 git)。")
    add(f"{SNAPSHOT_PREFIX} {d['generated_at']}")
    add("")

    add("## 1. 入口面")
    add("")
    add("### chunkyctl 子命令")
    add("")
    add("| 命令 | 说明 |")
    add("|---|---|")
    for c, h in d["chunkyctl"]:
        add(f"| `{c}` | {h} |")
    add("")
    add("### launchd 定时任务")
    add("")
    add("| Label | 时刻 | 入口 |")
    add("|---|---|---|")
    for j in d["launchd"]:
        add(f"| {j['label']} | {j['schedule']} | `{j['program']}` |")
    add("")
    add("### API 路由 (regex 口径: @router 装饰器)")
    add("")
    add("| router | prefix | 端点数 |")
    add("|---|---|---|")
    for name, r in sorted(d["routes"].items()):
        add(f"| {name} | `{r['prefix'] or '—'}` | {len(r['endpoints'])} |")
    add("")
    add("端点全列表在 json (`routes` 键)。")
    add("")

    add("## 2. 数据域 (sync_registry)")
    add("")
    add("| 域 | 源 | api | 表 | 模式 | SLA(交易日) |")
    add("|---|---|---|---|---|---|")
    for r in d["registry"]:
        add(f"| {r['domain']} | {r['source']} | {r['api']} | {r['table']} | {r['mode']} | {r['sla_days']} |")
    add("")

    add("## 3. 产表 writer (单 writer 契约审查素材)")
    add("")
    multi = {t: fs for t, fs in d["table_writers"].items() if len(fs) > 1}
    single = {t: fs for t, fs in d["table_writers"].items() if len(fs) == 1}
    n_dyn = sum(d["dynamic_write_files"].values())
    add(f"统计: 表 {len(d['table_writers'])} 张 | 单 writer {len(single)} | "
        f"多 writer {len(multi)} | 动态表名写点 {n_dyn} 处 ({len(d['dynamic_write_files'])} 文件)")
    add("")
    add("口径免责: 静态正则扫描, 含历史/backfill 一次性脚本与字符串内 SQL 样例;"
        " **多 writer 计数 ≠ 违规待修清单** — 升级为问题需逐表人工确认运行时并发写。")
    add("")
    add("### 动态表名写点 (f-string, 静态不可归属 — 这些文件写的表不在下方普查内)")
    add("")
    add("| 文件 | 写点数 |")
    add("|---|---|")
    for f, n in d["dynamic_write_files"].items():
        add(f"| {f} | {n} |")
    add("")
    add("### 多 writer 表 (>1 文件写同一张表)")
    add("")
    add("| 表 | writer 数 | writer 文件 |")
    add("|---|---|---|")
    for t, fs in sorted(multi.items(), key=lambda x: (-len(x[1]), x[0])):
        add(f"| {t} | {len(fs)} | {'<br>'.join(fs)} |")
    add("")
    add("### 单 writer 表")
    add("")
    add("| 表 | writer |")
    add("|---|---|")
    for t, fs in sorted(single.items()):
        add(f"| {t} | {fs[0]} |")
    add("")

    add("## 4. 依赖热点 (codegraph 派生)")
    add("")
    cg = d["codegraph"]
    if "error" in cg:
        add(f"codegraph 不可达: {cg['error']}")
    else:
        add(f"{CG_STATS_PREFIX} 节点 {cg['nodes']:,} | calls 边 {cg['edges'].get('calls', 0):,} | "
            f"imports 边 {cg['edges'].get('imports', 0):,} (每次 codegraph sync 波动, 不参与漂移判定)")
        add("")
        add("### 被 import 最多的模块 (top 15)")
        add("")
        add("| 模块 | import 处数 |")
        add("|---|---|")
        for m, c in cg["top_imports"]:
            add(f"| {m} | {c} |")
        add("")
        add("### 跨文件 fan-in 最高的文件 (近似口径: 唯一定义名 + caller 实际 import 目标模块双过滤)")
        add("")
        add("| 文件 | 调用方文件数 |")
        add("|---|---|")
        for f, c in cg["fan_in"]:
            add(f"| {f} | {c} |")
    add("")
    add("### LOC top 10 (God module 候选)")
    add("")
    add("| 文件 | 行数 |")
    add("|---|---|")
    for f, n in d["big_modules"]:
        add(f"| {f} | {n} |")
    add("")

    add("## 5. 概览")
    add("")
    add(f"- chunkyctl 子命令 {len(d['chunkyctl'])} | launchd 任务 {len(d['launchd'])} | "
        f"router {len(d['routes'])} (端点 {sum(len(r['endpoints']) for r in d['routes'].values())})")
    add(f"- sync_registry 数据域 {len(d['registry'])}")
    add(f"- 产表 {len(d['table_writers'])} (多 writer {len(multi)})")
    add("")
    return "\n".join(L) + "\n"


def _body(text: str, *, include_rankings: bool = False) -> str:
    """漂移比对体: 剔除时间戳行 + 计数行; 排名表按 ``include_rankings`` 取舍。

    计数行永远剔除 (它每次 sync 必变)。排名表**与计数行同源同因**: 全量索引与增量
    索引的 calls 边数不同 (2026-08-11 实测 12,619 vs 12,434), top-N 尾部名次因此翻转,
    于是 worktree 说「无漂移」而 safe_commit 的 fresh 快照说「漂移」, 连续两刀假红。
    """
    out: list[str] = []
    skipping = False
    for ln in text.splitlines():
        if ln.startswith(SNAPSHOT_PREFIX) or ln.startswith(CG_STATS_PREFIX):
            continue
        if not include_rankings and ln.startswith(CG_VOLATILE_SUBSECTIONS):
            skipping = True
            continue
        if skipping:
            if ln.startswith(("### ", "## ")):
                skipping = False
            else:
                continue
        out.append(ln)
    return "\n".join(out)


def _cg_stats_line(text: str) -> str:
    for ln in text.splitlines():
        if ln.startswith(CG_STATS_PREFIX):
            return ln
    return ""


def bodies_drifted(old: str, new: str) -> bool:
    """两侧索引口径相同就连排名表一起比; 口径不同才放过排名表。

    2026-08-11 独立审查 finding #2 指出: 无条件整表排除虽然消灭了假红, 却把这两张表
    变成了**永久检测盲区** —— 热点整体换掉也不会红。审查建议的「比 rank-1 / 比 top-N
    集合」并不成立: 实测那次抖动恰恰就是集合成员变化 (尾行 `accepted_schema.py 7`
    ↔ `frontend/src/components/Card.tsx 8`), 按集合比同样会假红。

    正解是**按口径条件化**: codegraph 计数行相同 = 两侧出自同一索引口径, 此时排名差异
    只能来自真实源变化, 该比; 计数行不同 = 一侧全量一侧增量 (safe_commit 的 fresh 快照
    对 worktree 增量索引就是这种情形), 排名差异是口径噪音, 不该比。盲区从「永久」缩成
    「仅跨索引口径比对时」。
    """
    stats_old, stats_new = _cg_stats_line(old), _cg_stats_line(new)
    same_index = bool(stats_new) and stats_old == stats_new
    return _body(old, include_rankings=same_index) != _body(new, include_rankings=same_index)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="只比对漂移, 漂移 exit 1, 不写盘")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    import datetime
    tracked = tracked_files(REPO)
    d = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),  # Phase ψ.5 allowlist: 文档元数据时间戳非 trade_date
        "generated_from": "backend/scripts/build_feature_map.py",
        "chunkyctl": scan_chunkyctl(REPO),
        "launchd": scan_launchd(REPO),
        "routes": scan_routes(REPO),
        "registry": load_registry(REPO),
        "codegraph": codegraph_facts(REPO),
        "big_modules": big_modules(REPO, tracked),
    }
    d["table_writers"], d["dynamic_write_files"] = scan_table_writers(REPO, tracked)

    md = render_md(d)
    old = MD_OUT.read_text(encoding="utf-8") if MD_OUT.exists() else ""
    drifted = bodies_drifted(old, md)

    if args.check:
        if drifted:
            print("FEATURE_MAP.md 漂移 — 跑 scripts/chunkyctl map 重生成", file=sys.stderr)
            return 1
        if not args.quiet:
            print("FEATURE_MAP.md fresh")
        return 0

    if drifted or not JSON_OUT.exists():  # json 是本地缓存 (不入 git), 缺失即补建
        if drifted:
            MD_OUT.write_text(md, encoding="utf-8")
        JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
        JSON_OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        if not args.quiet:
            print(f"FEATURE_MAP.md 已重生成 (表 {len(d['table_writers'])} / "
                  f"路由 {len(d['routes'])} / 域 {len(d['registry'])})")
    elif not args.quiet:
        print("FEATURE_MAP.md fresh (无漂移, 未写盘)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
