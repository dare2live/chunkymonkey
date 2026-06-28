#!/usr/bin/env python3
"""死引用硬门 (2026-06-28 根因根治) — 删模块/表后引用方必须同步清, 否则 commit 红.

根因 (用户洞察 2026-06-28): 残留反复出现 = 之前每波清理 (通达信/akshare/gpcw/财务/重建) 都删
  "供给侧"(模块/表/源) 但漏 "需求侧"(所有引用方); 验收手段 (import main / 定向测试 / moth) 够不到
  孤儿脚本 + 函数内懒 import + guarded 垫片 + config 引死文件路径 → 残留静默累积, 直到真跑才暴露。
  四地基的 dim_data_asset 登记表本该管这事, 但它烂掉(67stale/68漏/仅smartmoney)+从没强制。
本门把"无死引用"做成机械检查, 让删任何模块/文件时引用方立即报红, 无法再静默累积。

三道静态扫描 (零执行副作用, 不 import 业务脚本):
  A. import-services: import services/ + routers/ 库层每个模块, ImportError = FAIL (库层断链)
  B. dead-services-ref: 全 .py 里 `from/import services.X`, X 顶层子模块不存在 = FAIL
       (覆盖孤儿脚本顶层 import + 函数内懒 import + try/except guarded 垫片, 纯静态正则不执行)
  C. config-dead-path: backend/config/ + configs/ 的 *.yaml 里引的 .py 文件路径不存在 = FAIL
       (碎登记如 duckdb_connect_policy / test_tool_registry 引已删脚本/测试)
  D. dead-module-literal: 注册表 dataclass 的 `module="services.X"/"scripts.X"` 字符串字面量
       指向不存在的模块/文件 = FAIL (2026-06-28 三轮残留审计坐实的 B 扫盲区: B 只抓 from/import
       语句, 抓不到 ClientSpec module= 字面量 → 14 条死 ClientSpec 系统性逃逸, 死登记反复积累)。

用法: python backend/scripts/check_dead_references.py [--check]
退出码: 0=干净 / 1=有死引用
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO / "backend"

# C 扫描的 path-token 正则: backend/ 前缀可选, services|scripts|tests 下的 .py
_PY_PATH_RE = re.compile(r"(?:backend/)?(?:services|scripts|tests)/[\w/]+\.py")
_SERVICES_IMPORT_RE = re.compile(r"(?:from|import)\s+services\.([a-zA-Z0-9_.]+)")
# D 扫: 注册表 dataclass 的 module="services.X"/"scripts.X"/"routers.X" 字符串字面量 (= 或 :)
_MODULE_LITERAL_RE = re.compile(r"""\bmodule\s*[=:]\s*["']((?:services|scripts|routers)\.[\w.]+)["']""")


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


def main() -> int:
    all_fails: list[str] = []
    all_fails += scan_a_import_services()
    all_fails += scan_b_dead_services_ref()
    all_fails += scan_c_config_dead_path()
    all_fails += scan_d_dead_module_literal()

    if all_fails:
        print(f"[dead-references] FAIL: {len(all_fails)} 处死引用 (删模块/文件后引用方未清)\n")
        for f in all_fails:
            print(f"  ✗ {f}")
        print("\n修法: 删引用方 / repoint 到现存模块/文件 / 若该引用方也是残留则一并删。"
              "\n  (这是 2026-06-28 根因根治门: 删供给侧必同步删需求侧, 不再靠手工 grep。)")
        return 1
    print("[dead-references] PASS: 0 死引用 (import-services + dead-services-ref + config-dead-path 全绿)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
