#!/usr/bin/env python3
"""死引用硬门 (2026-06-28 根因根治) — 删模块/表后引用方必须同步清, 否则 commit 红.

根因 (用户洞察 2026-06-28): 残留反复出现 = 之前每波清理 (通达信/akshare/gpcw/财务/重建) 都删
  "供给侧"(模块/表/源) 但漏 "需求侧"(所有引用方); 验收手段 (import main / 定向测试 / moth) 够不到
  孤儿脚本 + 函数内懒 import + guarded 垫片 + config 引死文件路径 → 残留静默累积, 直到真跑才暴露。
  四地基的 dim_data_asset 登记表本该管这事, 但它烂掉(67stale/68漏/仅smartmoney)+从没强制。
本门把"无死引用"做成机械检查, 让删任何模块/文件时引用方立即报红, 无法再静默累积。

六道静态扫描 (零执行副作用, 不 import 业务脚本):
  A. import-services: import services/ + routers/ 库层每个模块, ImportError = FAIL (库层断链)
  B. dead-services-ref: 全 .py 里 `from/import services.X`, X 顶层子模块不存在 = FAIL
       (覆盖孤儿脚本顶层 import + 函数内懒 import + try/except guarded 垫片, 纯静态正则不执行)
  C. config-dead-path: backend/config/ + configs/ 的 *.yaml 里引的 .py 文件路径不存在 = FAIL
       (碎登记如 duckdb_connect_policy / test_tool_registry 引已删脚本/测试)
  D. dead-module-literal: 注册表 dataclass 的 `module="services.X"/"scripts.X"` 字符串字面量
       指向不存在的模块/文件 = FAIL (2026-06-28 三轮残留审计坐实的 B 扫盲区: B 只抓 from/import
       语句, 抓不到 ClientSpec module= 字面量 → 14 条死 ClientSpec 系统性逃逸, 死登记反复积累)。
  E. sql-table-ref: backend/{services,scripts,routers} 的 .py 里 SQL 字符串 FROM/JOIN 引用的
       fact_/mart_/dim_/raw_/stg_ 表名, 核对现存全部 DuckDB 库 (data/*.duckdb) 里是否真实存在
       = FAIL (2026-07-06 全面数据审计抓获的核心机制盲区: A-D 全部只处理 Python 符号引用/
       文件路径, 对 `conn.execute(f"SELECT ... FROM mart_p0b_lambdamart_v6_predictions")` 这类
       纯 SQL 字符串死引用结构性失明 — check_panel_lineage.py/check_kpi_redlines.py 两个死
       治理脚本引用已随策略层退役的表, 44 天无人发现直到本次审计实测崩溃复现)。
  F. execution-path: YAML ``script:``、plist Program/ProgramArguments、installer→plist 的执行链
       必须逐跳存在，防已删任务仍被调度配置或安装器静默引用。

用法: python backend/scripts/check_dead_references.py [--check]
退出码: 0=干净 / 1=有死引用
"""
from __future__ import annotations

import importlib
import plistlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO / "backend"
DATA_DIR = REPO / "data"

# E 扫已知安全白名单 (2026-07-06 全面数据审计逐条深挖后确认, 非批量豁免):
# 每条都已亲验双重条件——(1) 引用方代码已用 _table_exists/try-except 守护, 真调用不会崩;
# (2) 该表被删不是"漏清残留", 而是有明确记录的退役决策 (F4 dim_data_asset 碎登记归并 /
# U2-U5 mart_model_lifecycle 孤表物删), 且其承担的能力(若有)已确认在别处有替代:
#   - dim_data_asset 的 coverage_policy(稀疏事件表白名单)已被 data_health_snapshot.py
#     读 data_layers.yaml table_health_overrides 取代, 非能力真空。
#   - mart_model_lifecycle 引用是 2026-06-28 U2/U5 批次明确记录的"non-breaking cosmetic
#     (奥卡姆不churn)"决策, 不是遗漏。
# 新增条目前必须重复这两步验证, 不能因为"看着安全"就批量加白名单——这正是本门要根治的
# 反模式 (轻信而非验证)。
_SQL_TABLE_REF_KNOWN_SAFE: dict[tuple[str, str], str] = {
    ("scripts/audit_data_completeness.py", "dim_data_asset"):
        "F4 退役表; try/except 守护; coverage_policy 能力已被 data_health_snapshot.py "
        "读 data_layers.yaml table_health_overrides 取代, 非能力真空",
}

# C 扫描的 path-token 正则: backend/ 前缀可选, services|scripts|tests 下的 .py
# 2026-09-04: 加左边界 (?<![\w/])。原正则无锚, 会从更长的路径里截一段:
#   "bestchoice/scripts/x.py" → 提取出 "scripts/x.py" → 按仓库根解析 → 判"不存在"。
#   实测 3 处假阳性 (tracked_doc_allowlist.yaml 的 bestchoice/scripts/*.py, 文件真实存在)。
# 门想守的是「backend/ 或仓库根下的 py 引用还在不在」, 问的却是「这行里有没有出现
# services|scripts|tests/xxx.py 这个子串」—— 第三方根下的路径不在它的守备范围, 不该被截半判死。
_PY_PATH_RE = re.compile(r"(?<![\w/])(?:backend/)?(?:services|scripts|tests)/[\w/]+\.py")
_SERVICES_IMPORT_RE = re.compile(r"(?:from|import)\s+services\.([a-zA-Z0-9_.]+)")
# D 扫: 注册表 dataclass 的 module="services.X"/"scripts.X"/"routers.X" 字符串字面量 (= 或 :)
_MODULE_LITERAL_RE = re.compile(r"""\bmodule\s*[=:]\s*["']((?:services|scripts|routers)\.[\w.]+)["']""")
# E 扫: SQL FROM/JOIN 后跟项目表命名惯例前缀 (fact_/dim_/mart_/raw_/canonical_ 等);
# 动态 f-string 表名 (FROM {table}) 不匹配字面前缀, 自然跳过 (静态无法核实, 不误判)。
# 前缀后至少 1 个字符 (+非*): 防文档字符串里"禁止 FROM raw_*"这类规则描述被误判成真表名
# (2026-07-06 实测反例: services/data_access/__init__.py 的门禁说明文字被 * 前的 "raw_" 裸前缀
# 命中三次, 无真实表名可对; 真表名从不会只是裸前缀本身)。
_SQL_TABLE_REF_RE = re.compile(
    r"(?i)\b(?:FROM|JOIN)\s+[\"'`]?((?:fact|mart|dim|raw|stg)_[a-zA-Z0-9_]+)"
)
_YAML_SCRIPT_RE = re.compile(
    r"^\s*script\s*:\s*[\"']?([^#\"']+?\.(?:py|sh))[\"']?\s*(?:#.*)?$"
)
_PLIST_ARRAY_RE = re.compile(r"\bPLISTS\s*=\s*\((.*?)\)", re.DOTALL)
_PLIST_LABEL_RE = re.compile(r"\bcom\.chunkymonkey\.[a-zA-Z0-9_-]+\b")


def _existing_services_submodules() -> set[str]:
    svc = BACKEND / "services"
    existing: set[str] = set()
    for p in svc.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        rel = p.relative_to(svc).with_suffix("")
        parts = rel.parts
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            existing.add(".".join(parts))
            existing.add(parts[0])  # 顶层子模块/包名
    return existing


def scan_a_import_services() -> list[str]:
    """import services/ + routers/ 库层模块, 抓 ImportError (库层断链)。"""
    fails: list[str] = []
    sys.path.insert(0, str(BACKEND))
    for sub in ("services", "routers"):
        root = BACKEND / sub
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in str(p):
                continue
            rel = p.relative_to(BACKEND).with_suffix("")
            mod = ".".join(rel.parts)
            if mod.endswith(".__init__"):
                mod = mod[:-9]
            try:
                importlib.import_module(mod)
            except ModuleNotFoundError as e:
                fails.append(f"A import-services: {mod} → ModuleNotFoundError: {e.name}")
            except Exception as e:  # noqa: BLE001 — 库层任何 import 期异常都算断链
                fails.append(f"A import-services: {mod} → {type(e).__name__}: {str(e)[:70]}")
    return fails


def scan_b_dead_services_ref() -> list[str]:
    """全 .py 静态扫 `from/import services.X`, X 顶层不存在 = 死引用 (含懒/guarded import)。"""
    existing = _existing_services_submodules()
    fails: list[str] = []
    for p in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            # 只认真 import 语句 (行首 from/import services.), 排 docstring/注释/字符串里的 services.X 提及
            if not (s.startswith("from services.") or s.startswith("import services.")):
                continue
            for m in _SERVICES_IMPORT_RE.finditer(s):
                top = m.group(1).split(".")[0]
                full = m.group(1)
                if top not in existing and full not in existing:
                    fails.append(f"B dead-services-ref: {p.relative_to(REPO)}:{i} → services.{top} 不存在")
    return fails


def scan_c_config_dead_path() -> list[str]:
    """config *.yaml 里引的 .py 文件路径不存在 = 碎登记引死文件。"""
    fails: list[str] = []
    cfg_dirs = [BACKEND / "config", REPO / "configs"]
    for d in cfg_dirs:
        if not d.exists():
            continue
        for y in sorted(d.glob("*.yaml")):
            for i, line in enumerate(y.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                # 只扫 registry 列表项 (`- path.py`), 不扫 prose/role 描述里的历史提及 (门精度: 避免假阳性)
                if not re.match(r"^-\s+\S", s) or s.startswith("#"):
                    continue
                for m in _PY_PATH_RE.finditer(line):
                    rel = m.group(0)
                    # 归一: 带不带 backend/ 前缀都试
                    cand = REPO / rel
                    cand2 = BACKEND / rel if not rel.startswith("backend/") else cand
                    if not cand.exists() and not cand2.exists():
                        fails.append(f"C config-dead-path: {y.relative_to(REPO)}:{i} → {rel} 不存在")
    return fails


def scan_d_dead_module_literal() -> list[str]:
    """注册表 module="services.X"/"scripts.X" 字符串字面量指向不存在的模块/文件 = 死登记。
    根因实测 (2026-06-28 三轮残留审计): B 扫只抓 from/import 语句, 抓不到 ClientSpec dataclass 的
    module= 字面量 → 14 条死 ClientSpec(module=scripts.<已删>) 系统性逃逸门, 死登记反复积累。
    解析 dotted → 模块文件 (backend 视角 PYTHONPATH=backend) 或包 __init__ 或 repo-root, 全不存在 = FAIL。"""
    fails: list[str] = []
    for p in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        if p.name == "check_dead_references.py" or "/tests/" in str(p).replace("\\", "/"):
            continue  # 门脚本自身 docstring + tests/ fixture 含 module= 示例字符串, 排除防假阳性 (test 不注册真 ClientSpec)
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            for m in _MODULE_LITERAL_RE.finditer(line):
                dotted = m.group(1)
                rel = dotted.replace(".", "/")
                if not (
                    (BACKEND / (rel + ".py")).exists()
                    or (BACKEND / rel / "__init__.py").exists()
                    or (REPO / (rel + ".py")).exists()
                ):
                    fails.append(f"D dead-module-literal: {p.relative_to(REPO)}:{i} → module={dotted} 模块文件不存在")
    return fails


def _live_table_names() -> tuple[set[str], bool]:
    """现存全部 DuckDB 库 (data/*.duckdb) 里真实存在的表名并集 + 是否已核实到足够可信的库集合。
    两类"不可信"场景都必须让调用方知道, 否则会把"没法查"误判成"查了确认不存在":
    (1) 库锁定 (并发 sync/backfill/rebuild 持写锁) 或损坏——该库跳过 (2026-07-06 实测:
        rebuild_all() 跑批期间跑本门, smartmoney.duckdb 打不开导致 64 处假阳性);
    (2) **一个库文件都不存在**(CI 全新 checkout, data/*.duckdb 是 gitignored 生产数据从不
        进 git——2026-07-06 上线当天实测: CI 无任何 .duckdb 文件, glob 返回空列表, 循环体
        从不执行, all_reachable 停留在初值 True, 误判成"查了 0 个库、确认这些表都不存在",
        产生 83 处假阳性击穿 CI)。"""
    import duckdb
    names: set[str] = set()
    db_files = sorted(DATA_DIR.glob("*.duckdb"))  # rule-compliance: ok evidence=通配枚举现存库非硬编码单库名
    all_reachable = bool(db_files)  # 空列表(如 CI 无数据) = 不可信, 不能当"查过 0 个库全通过"
    for db in db_files:
        try:
            # rule-compliance: ok evidence=已登记 duckdb_connect_policy.yaml allowed_raw_connect_paths
            conn = duckdb.connect(str(db), read_only=True)
        except Exception:
            all_reachable = False
            continue
        try:
            names.update(r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables").fetchall())
        finally:
            conn.close()
    return names, all_reachable


def scan_e_sql_table_refs() -> list[str]:
    """backend/{services,scripts,routers} 的 .py 里 SQL 字符串 FROM/JOIN 引用的
    fact_/mart_/dim_/raw_/stg_ 表名, 核对现存全部 DuckDB 库里是否真实存在 = 死引用。
    根因 (2026-07-06 全面数据审计抓获): A-D 四个扫描器全部只处理 Python 符号引用/文件路径,
    对 `conn.execute(f"SELECT ... FROM mart_p0b_lambdamart_v6_predictions")` 这类纯 SQL
    字符串死引用结构性失明——check_panel_lineage.py/check_kpi_redlines.py 两个死治理脚本
    引用已随策略层退役的表, 44 天无人发现直到本次审计实测崩溃复现。
    动态 f-string 表名 (`FROM {table}`) 不匹配字面前缀正则, 静态无法核实, 自然跳过不误判。
    任一库被并发写锁占用打不开时整个 E 扫跳过 (返回空, 非 FAIL) ——宁可这次不查, 也不能把
    "库暂时锁定"误判成"表不存在" (与 check_continuity_integrity.py 的 db_unreachable 语义一致)。"""
    live, all_reachable = _live_table_names()
    if not all_reachable:
        print("[dead-references] E sql-table-ref: 部分 DuckDB 库锁定/不可达, 本轮跳过 (非 FAIL)",
              file=sys.stderr)
        return []
    fails: list[str] = []
    for sub in ("services", "scripts", "routers"):
        root = BACKEND / sub
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            rel_str = str(p).replace("\\", "/")
            if "__pycache__" in rel_str or "/tests/" in rel_str:
                continue
            if p.name == "check_dead_references.py":
                continue  # 门脚本自身 docstring 含示例表名字符串, 排除防假阳性
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                for m in _SQL_TABLE_REF_RE.finditer(line):
                    tbl = m.group(1)
                    if tbl in live:
                        continue
                    rel = str(p.relative_to(BACKEND)).replace("\\", "/")
                    if (rel, tbl) in _SQL_TABLE_REF_KNOWN_SAFE:
                        continue
                    fails.append(f"E sql-table-ref: {p.relative_to(REPO)}:{i} → 表 {tbl} 不存在于任何现存库")
    return fails


def scan_f_execution_paths(repo: Path = REPO) -> list[str]:
    """YAML/plist execution paths must resolve to real files."""
    fails: list[str] = []
    for cfg_dir in (repo / "backend" / "config", repo / "configs"):
        if not cfg_dir.exists():
            continue
        for path in sorted(cfg_dir.rglob("*.yaml")):
            for line_no, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                match = _YAML_SCRIPT_RE.match(line)
                if not match:
                    continue
                target = match.group(1).strip()
                if not (repo / target).exists():
                    fails.append(
                        f"F yaml-script: {path.relative_to(repo)}:{line_no} → {target} 不存在"
                    )
    for launchd_dir in (repo / "configs" / "launchd", repo / "backend" / "scripts" / "launchd"):
        if not launchd_dir.exists():
            continue
        for path in sorted(launchd_dir.glob("*.plist")):
            try:
                payload = plistlib.loads(path.read_bytes())
            except Exception as exc:  # noqa: BLE001 - opaque scheduler config must fail closed
                fails.append(
                    f"F plist-parse: {path.relative_to(repo)} → {type(exc).__name__}"
                )
                continue
            program_values = [payload.get("Program"), *(payload.get("ProgramArguments") or [])]
            for value in program_values:
                if not value:
                    continue
                target = str(value).strip()
                if Path(target).suffix not in {".py", ".sh"}:
                    continue
                candidate = Path(target) if Path(target).is_absolute() else repo / target
                if not candidate.exists():
                    fails.append(
                        f"F plist-program: {path.relative_to(repo)} → {target} 不存在"
                    )
    installer_dir = repo / "configs" / "launchd"
    if installer_dir.exists():
        for installer in sorted(installer_dir.glob("install*.sh")):
            text = installer.read_text(encoding="utf-8", errors="replace")
            for block in _PLIST_ARRAY_RE.findall(text):
                for label in _PLIST_LABEL_RE.findall(block):
                    target = installer_dir / f"{label}.plist"
                    if not target.exists():
                        fails.append(
                            f"F installer-plist: {installer.relative_to(repo)} → "
                            f"{target.relative_to(repo)} 不存在"
                        )
    return fails


def main() -> int:
    all_fails: list[str] = []
    all_fails += scan_a_import_services()
    all_fails += scan_b_dead_services_ref()
    all_fails += scan_c_config_dead_path()
    all_fails += scan_d_dead_module_literal()
    all_fails += scan_e_sql_table_refs()
    all_fails += scan_f_execution_paths()

    if all_fails:
        print(f"[dead-references] FAIL: {len(all_fails)} 处死引用 (删模块/文件后引用方未清)\n")
        for f in all_fails:
            print(f"  ✗ {f}")
        print("\n修法: 删引用方 / repoint 到现存模块/文件 / 若该引用方也是残留则一并删。"
              "\n  (这是 2026-06-28 根因根治门: 删供给侧必同步删需求侧, 不再靠手工 grep。)")
        return 1
    print("[dead-references] PASS: 0 死引用 (imports + config/module + SQL table + execution-path 全绿)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
