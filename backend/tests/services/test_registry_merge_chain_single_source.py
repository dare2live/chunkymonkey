"""防回归: sync_registry.yaml 三层继承链只能有一份实现 (2026-08-30 事故补).

事故: `target_db` 从 defaults 下沉到 sources.<source> 时, check_continuity_integrity.py
与 update_watermark_sla.py 各自手写了一份 `dict(defaults); .update(source_cfg); .update(entry)`
合并链, 没跟着补中间的 sources 层 —— `ValueError: margin: missing outer contract fields:
target_db`, 连续性门崩溃拦死所有 sync, 日更 watermark SLA 步骤 blocked。根因不是"少写一行",
是"同一条继承链存在第二份实现" —— 唯一正版是 services.data_sources.sync_runner.domain_spec()
("stable public read seam", docstring 自称 37 个调用方)。两个门已改吃 domain_spec() (见
git 6bf908bba), 但**没有任何东西阻止下一个人再写第三份**, 且这两个门属 governance_gates.yaml
的 system_health 组, commit 路径不跑、CI 也不跑 (查 live 数据与 config 生效性) —— 那次回归在
"完整 CI 1921 passed 全绿"的情况下溜了过去, 直到真跑一次采集才炸出来。

三段防线, 全部纯静态 / 不连网 / 不碰 live DuckDB:
  1. test_inheritance_precedence_* — 继承链语义本身 (合成小 registry, 三层覆盖顺序)。
  2. test_real_registry_every_domain_resolves_target_db — 真实 registry 关键不变量
     (正是上次炸掉的那个字段); 未来再有字段从 defaults 下沉而某处漏补, 这条会红。
  3. test_no_other_module_rebuilds_the_merge_chain — 静态扫描 backend/ 下 .py (排除
     tests 与 sync_runner.py 自身), 抓"同一函数体内既有 dict(defaults) 又有 .update(),
     却没有调用 domain_spec()"这个形态 (= 那次回归两处代码的真实长相, 见下方
     _BAD_SHAPE_FOR_SELF_TEST 常量, 逐字抄自 git show 6bf908bba 的 diff 上下文)。
     本测试文件末尾的 test_detector_catches_the_real_historical_bug_shape 用这段抄录
     反向验证检测器本身抓得住 —— 防止这道门是"看起来在工作"的伪绿 (mio 协议 #11.5)。
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
import yaml

from services.data_sources.sync_runner import domain_spec

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
REGISTRY_PATH = BACKEND_DIR / "config" / "sync_registry.yaml"
SYNC_RUNNER_PATH = (BACKEND_DIR / "services" / "data_sources" / "sync_runner.py").resolve()


# ── 1. 继承链语义本身 (合成小 registry, 不依赖真实 yaml 的具体值) ─────────


def test_inheritance_precedence_domain_overrides_source_overrides_defaults():
    """域级覆盖源级、源级覆盖全局、三层都没有的键取不到。"""
    registry = {
        "defaults": {
            "only_in_defaults": "d0",
            "overridden_by_source": "d1",
            "overridden_by_domain": "d1",
        },
        "sources": {
            "vendor_x": {
                "only_in_source": "s0",
                "overridden_by_source": "s1",
                "overridden_by_domain": "s1",
            },
        },
        "domains": {
            "my_domain": {
                "source": "vendor_x",
                "only_in_domain": "e0",
                "overridden_by_domain": "e1",
            },
        },
    }

    spec = domain_spec(registry, "my_domain")

    # 只在 defaults 声明的键要透传下来
    assert spec["only_in_defaults"] == "d0"
    # 只在 source 层声明的键要透传下来 (这正是上次炸掉的那类字段: target_db)
    assert spec["only_in_source"] == "s0"
    # 只在 domain 层声明的键要透传下来
    assert spec["only_in_domain"] == "e0"
    # 源级覆盖全局
    assert spec["overridden_by_source"] == "s1"
    # 域级覆盖源级 (且传递覆盖全局)
    assert spec["overridden_by_domain"] == "e1"
    # domain_spec 自己写入的标识键
    assert spec["domain"] == "my_domain"
    # 三层都没有的键就是取不到, 不能凭空出现
    assert "nowhere_declared" not in spec


def test_inheritance_precedence_domain_without_source_falls_back_to_defaults_only():
    """域没有声明 source (或 source 在 sources 表里没有条目) 时, 只叠 defaults → entry。"""
    registry = {
        "defaults": {"shared": "d0", "only_defaults": "kept"},
        "sources": {"vendor_x": {"shared": "s0"}},
        "domains": {
            "no_source_domain": {"shared": "e0"},
        },
    }

    spec = domain_spec(registry, "no_source_domain")

    assert spec["only_defaults"] == "kept"
    assert spec["shared"] == "e0"  # 域级仍覆盖 defaults, 只是中间没有 source 那层


def test_domain_spec_rejects_unregistered_domain():
    """未注册的域必须报错, 不能悄悄返回空/部分合并结果 (宪法 v2 第 7/9 条)。"""
    registry = {"defaults": {}, "sources": {}, "domains": {"known": {}}}
    with pytest.raises(KeyError):
        domain_spec(registry, "unknown_domain")


# ── 2. 真实 registry 的关键不变量 (正是上次炸掉的那个字段) ─────────────────


def _load_real_registry() -> dict:
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict) and raw.get("domains"), (
        f"{REGISTRY_PATH} 未产出非空 domains — registry 本身读不出来, 后续断言无意义"
    )
    return raw


def test_real_registry_every_domain_resolves_target_db():
    """每个已注册域经 domain_spec 合并后都必须能取到 target_db。

    target_db 正是这次事故里从 defaults 下沉到 sources.<source> 却漏补中间层的字段。
    这条断言直接复刻那次炸掉的症状: 若将来又有字段从 defaults 下沉而某处 (包括
    domain_spec 自身被改坏) 没跟上, 这条会红, 而不必等真跑一次采集才发现。
    """
    raw = _load_real_registry()
    missing: list[str] = []
    for domain in raw["domains"]:
        spec = domain_spec(raw, domain)
        if not spec.get("target_db"):
            missing.append(domain)
    assert not missing, (
        "以下域经 domain_spec() 合并后取不到 target_db (三层继承链断裂 — "
        "参照 2026-08-30 target_db 下沉 sources.<source> 时两个门崩溃的事故): "
        f"{missing}"
    )


# ── 3. 静态防重复: 别处再重建这条合并链 ────────────────────────────────────


def _mentions_defaults(node: ast.AST) -> bool:
    """expr 子树里是否出现字面量 'defaults' 或名字含 defaults 的标识符。

    覆盖两种历史真实写法: `dict(defaults)` (defaults 是提前赋值的局部变量) 与
    `dict(raw.get("defaults") or {})` (内联从 registry dict 取 defaults 键)。
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and "defaults" in n.id.lower():
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value == "defaults":
            return True
    return False


def _is_dict_defaults_call(call: ast.Call) -> bool:
    """形如 dict(defaults) / dict(raw.get("defaults") or {}) 的调用。"""
    if not (isinstance(call.func, ast.Name) and call.func.id == "dict"):
        return False
    if not call.args:
        return False
    return _mentions_defaults(call.args[0])


def _is_update_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "update"


def _is_domain_spec_call(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Name) and call.func.id == "domain_spec":
        return True
    return isinstance(call.func, ast.Attribute) and call.func.attr == "domain_spec"


def _functions_rebuilding_merge_chain(source: str, filename: str) -> list[tuple[int, str]]:
    """扫描一份源码, 返回「函数体内 dict(defaults)+.update() 但不调用 domain_spec()」的命中。

    判据刻意宽松 (只看形状, 不看变量具体来自哪个 registry), 因为这道门要抓的不是
    "这段代码错了", 是"这段代码在重新实现 domain_spec 已经实现过的那条继承链" ——
    即便它这次凑巧写对了, 下次配置结构一变还是会漏。真正该被信任的实现只有一处。
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    offenses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        has_dict_defaults = False
        has_update = False
        has_domain_spec = False
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            if _is_dict_defaults_call(call):
                has_dict_defaults = True
            if _is_update_call(call):
                has_update = True
            if _is_domain_spec_call(call):
                has_domain_spec = True
        if has_dict_defaults and has_update and not has_domain_spec:
            offenses.append((node.lineno, node.name))
    return offenses


def _candidate_py_files() -> list[Path]:
    files = []
    for path in sorted(BACKEND_DIR.rglob("*.py")):
        if path.resolve() == SYNC_RUNNER_PATH:
            continue  # 正版实现本身天然长这个形状, 不能拿自己当违规
        if "__pycache__" in path.parts:
            continue
        rel_parts = path.relative_to(BACKEND_DIR).parts
        if "tests" in rel_parts:
            continue
        files.append(path)
    return files


def test_no_other_module_rebuilds_the_merge_chain():
    """backend/ 下不许有第二处手写 defaults→sources→entry 三层合并链。

    命中形态 (逐字抄自这次事故的真实代码, git show 6bf908bba):

        contract_spec = dict(defaults)
        contract_spec.update(source_cfg)   # 或 sources_cfg.get(spec.get("source")) or {}
        contract_spec.update(entry)

    正确写法是调用 services.data_sources.sync_runner.domain_spec(registry, domain)。
    """
    all_offenses: list[str] = []
    for path in _candidate_py_files():
        source = path.read_text(encoding="utf-8")
        for lineno, funcname in _functions_rebuilding_merge_chain(source, str(path)):
            rel = path.relative_to(REPO_ROOT)
            all_offenses.append(f"{rel}:{lineno} 函数 {funcname}()")

    assert not all_offenses, (
        "以下函数疑似手写重建了 defaults→sources[source]→domain 三层合并链 "
        "(dict(defaults) + .update(...) 但没有调用 domain_spec()) —— "
        "请改用 services.data_sources.sync_runner.domain_spec(registry, domain), "
        "不要重建合并链 (2026-08-30 事故: 两处这样的重建各漏补一层, 连续性门崩溃 + "
        "watermark SLA blocked):\n  " + "\n  ".join(all_offenses)
    )


# ── 反向验证: 检测器真的抓得住这次事故的真实形态, 不是伪绿 ─────────────────

# 逐字抄自 git show 6bf908bba 的 diff 上下文 (fix 前的 check_continuity_integrity.py
# load_domain_specs 函数体), 只做变量改名脱敏; 结构与真实事故代码一致。
_BAD_SHAPE_FOR_SELF_TEST = textwrap.dedent(
    """
    def load_domain_specs(registry_path=None):
        raw = {}
        defaults = raw.get("defaults") or {}
        sources = raw.get("sources") or {}
        specs = []
        for domain, entry in (raw.get("domains") or {}).items():
            entry = entry or {}
            source_cfg = sources.get(entry.get("source")) or {}
            default_db = source_cfg.get("target_db") or defaults.get("target_db", "tushare_raw")
            contract_spec = dict(defaults)
            contract_spec.update(source_cfg)
            contract_spec.update(entry)
            contract_spec["domain"] = domain
            specs.append(contract_spec)
        return specs
    """
)

# 对照组: 同样的函数改成调用 domain_spec() 之后, 不应该再被命中。
_GOOD_SHAPE_FOR_SELF_TEST = textwrap.dedent(
    """
    def load_domain_specs(registry_path=None):
        raw = {}
        specs = []
        for domain, entry in (raw.get("domains") or {}).items():
            contract_spec = domain_spec(raw, domain)
            specs.append(contract_spec)
        return specs
    """
)


def test_detector_catches_the_real_historical_bug_shape():
    """反例验证: 检测器必须命中事故发生时的真实代码形态, 否则这道门是伪绿。

    这不是测生产代码, 是测上面 `_functions_rebuilding_merge_chain` 这个检测器自身 ——
    对应「务必自己造一个反例验证这道检测真的能抓住它」的要求, 不依赖手工临时改生产
    文件再跑一次(那一步已经在本刀交付时人工做过一遍并截图证据), 而是把同一个反例
    钉进测试, 让它长期留在回归套件里自证。
    """
    offenses = _functions_rebuilding_merge_chain(_BAD_SHAPE_FOR_SELF_TEST, "<bad_shape>")
    assert offenses == [(2, "load_domain_specs")], (
        f"检测器没抓住事故发生时的真实代码形态 (dict(defaults) + .update() 无 "
        f"domain_spec()), 这道门是伪绿: {offenses!r}"
    )

    clean = _functions_rebuilding_merge_chain(_GOOD_SHAPE_FOR_SELF_TEST, "<good_shape>")
    assert clean == [], (
        f"检测器对已经改用 domain_spec() 的正确写法也误报了, 判据太宽会阻塞正常改动: {clean!r}"
    )
